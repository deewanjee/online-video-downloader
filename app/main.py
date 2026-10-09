import os
import importlib.util
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).parent
DATA = Path(os.environ.get('DOWNLOAD_DIR', ROOT.parent / 'data')).resolve()
DATA.mkdir(parents=True, exist_ok=True)
app = FastAPI(title='StreamVault', version='1.0.0')
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
HOSTS = ('youtube.com', 'youtu.be', 'dailymotion.com', 'dai.ly', 'facebook.com', 'fb.watch', 'tiktok.com', 'instagram.com', 'threads.net', 'threads.com', 'twitter.com', 'x.com')
jobs = {}
lock = threading.Lock()
pool = ThreadPoolExecutor(max_workers=2)

class VideoRequest(BaseModel):
    url: str = Field(max_length=2048)

class DownloadRequest(VideoRequest):
    format: str = 'mp4'
    quality: str = '1080'


def validate_url(url):
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        port = parsed.port
    except ValueError:
        raise HTTPException(400, 'Invalid video URL.')
    if parsed.scheme != 'https' or parsed.username or parsed.password or port not in (None, 443) or not any(host == h or host.endswith('.' + h) for h in HOSTS):
        raise HTTPException(400, 'Use an HTTPS video link from a supported platform.')
    return url


def options():
    runtimes = {name: {'path': path} for name in ('deno', 'node') if (path := shutil.which(name))}
    return {'quiet': True, 'no_warnings': False, 'noplaylist': True, 'socket_timeout': 25, 'retries': 2, 'cachedir': False, 'ignoreconfig': True, 'js_runtimes': runtimes}


def download_error(exc):
    # Classify errors without sending source URLs, credentials, or local paths to the browser.
    message = str(exc).lower()
    if '403' in message or 'forbidden' in message:
        return 'The video server rejected the download (HTTP 403). Update yt-dlp with its default dependencies, check the JavaScript runtime, and retry. If it persists, check source access and the terminal warnings.'
    if 'requested format' in message:
        return 'The selected quality is unavailable. Analyze the video again or try a lower resolution.'
    if 'sign in' in message or 'login' in message or 'private video' in message:
        return 'This video requires access or sign-in. Try a publicly accessible video.'
    if 'timed out' in message or 'timeout' in message:
        return 'The source connection timed out. Check your connection and retry.'
    return 'Download failed. Check the terminal output for source access, format, or conversion errors.'


@app.get('/')
def index():
    return FileResponse(ROOT / 'static' / 'index.html')


@app.get('/api/health')
def health():
    return {'status': 'ok', 'ffmpeg': bool(shutil.which('ffmpeg')), 'js_runtimes': list(options()['js_runtimes']), 'youtube_ejs': importlib.util.find_spec('yt_dlp_ejs') is not None, 'active': sum(j['status'] in ('queued', 'downloading', 'processing') for j in jobs.values())}


@app.post('/api/analyze')
def analyze(req: VideoRequest):
    validate_url(req.url)
    try:
        with yt_dlp.YoutubeDL(options()) as dl:
            info = dl.extract_info(req.url, download=False)
        if not info or info.get('_type') in ('playlist', 'multi_video') or info.get('is_live'):
            raise HTTPException(400, 'Please choose a single, non-live video.')
        heights = sorted({f['height'] for f in info.get('formats', []) if f.get('height') and f.get('vcodec') != 'none'}, reverse=True)
        return {'title': info.get('title', 'Untitled video'), 'author': info.get('uploader', 'Unknown creator'), 'duration': info.get('duration'), 'thumbnail': info.get('thumbnail'), 'platform': info.get('extractor_key'), 'qualities': heights, 'url': req.url}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, download_error(exc))


def update(job_id, **values):
    with lock:
        jobs[job_id].update(values)


def run_download(job_id, req):
    folder = DATA / job_id
    folder.mkdir()
    def progress(event):
        if event['status'] == 'downloading':
            total = event.get('total_bytes') or event.get('total_bytes_estimate') or 0
            update(job_id, status='downloading', progress=min(99, round(event.get('downloaded_bytes', 0) / total * 100)) if total else 0, speed=event.get('speed'), eta=event.get('eta'))
        elif event['status'] == 'finished':
            update(job_id, status='processing', progress=99)
    opts = options() | {'outtmpl': str(folder / '%(title).120B.%(ext)s'), 'progress_hooks': [progress], 'max_filesize': 2 * 1024**3}
    if req.format in ('mp3', 'm4a', 'wav'):
        opts |= {'format': 'bestaudio/best', 'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': req.format, 'preferredquality': '192'}]}
    else:
        opts |= {'format': f'bv*[height<=?{req.quality}]+ba/b[height<=?{req.quality}]', 'merge_output_format': req.format, 'postprocessors': [{'key': 'FFmpegVideoConvertor', 'preferedformat': req.format}]}
    try:
        update(job_id, status='downloading')
        with yt_dlp.YoutubeDL(opts) as dl:
            info = dl.extract_info(req.url, download=True)
        files = [p for p in folder.iterdir() if p.suffix == '.' + req.format]
        if not files:
            raise RuntimeError('No output file')
        file = max(files, key=lambda p: p.stat().st_size)
        update(job_id, status='completed', progress=100, title=info.get('title', file.stem), filename=file.name, size=file.stat().st_size)
    except Exception as exc:
        update(job_id, status='failed', error=download_error(exc))
        shutil.rmtree(folder, ignore_errors=True)


@app.post('/api/download', status_code=202)
def download(req: DownloadRequest):
    validate_url(req.url)
    if req.format not in ('mp4', 'webm', 'mkv', 'mp3', 'm4a', 'wav') or req.quality not in ('2160', '1440', '1080', '720', '480', '360'):
        raise HTTPException(400, 'Unsupported format or quality.')
    if not shutil.which('ffmpeg'):
        raise HTTPException(503, 'Install FFmpeg to enable downloads and conversion.')
    with lock:
        if sum(j['status'] in ('queued', 'downloading', 'processing') for j in jobs.values()) >= 10:
            raise HTTPException(429, 'Queue is full. Wait for a download to finish.')
        if len(jobs) >= 100:
            raise HTTPException(429, 'History is full. Remove older downloads first.')
        job_id = uuid.uuid4().hex
        jobs[job_id] = {'id': job_id, 'url': req.url, 'title': 'Preparing download', 'format': req.format, 'quality': req.quality, 'status': 'queued', 'progress': 0, 'created': time.time()}
    pool.submit(run_download, job_id, req)
    return jobs[job_id].copy()


@app.get('/api/jobs')
def list_jobs():
    with lock:
        return [j.copy() for j in sorted(jobs.values(), key=lambda j: j['created'], reverse=True)]


@app.get('/api/jobs/{job_id}/file')
def get_file(job_id: str):
    job = jobs.get(job_id)
    if not job or job['status'] != 'completed':
        raise HTTPException(404, 'Download is not available.')
    file = DATA / job_id / job['filename']
    if not file.is_file():
        raise HTTPException(404, 'File no longer exists.')
    return FileResponse(file, filename=job['filename'])


@app.delete('/api/jobs/{job_id}')
def remove_job(job_id: str):
    with lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, 'Download not found.')
        if job['status'] not in ('completed', 'failed'):
            raise HTTPException(409, 'Wait for the download to finish before removing it.')
        del jobs[job_id]
    shutil.rmtree(DATA / job_id, ignore_errors=True)
    return {'removed': True}
