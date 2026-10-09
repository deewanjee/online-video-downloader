import os
import importlib.util
import json
import shutil
import ssl
import subprocess
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
from app import storage
from app.auth import AccessControl
from app.threads import ThreadsIE
from app import source_access

ROOT = Path(__file__).parent
DATA = Path(os.environ.get('DOWNLOAD_DIR', ROOT.parent / 'data')).resolve()
DATA.mkdir(parents=True, exist_ok=True)
app = FastAPI(title='StreamVault', version='1.1.0')
app.add_middleware(AccessControl)
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
HOSTS = ('youtube.com', 'youtu.be', 'dailymotion.com', 'dai.ly', 'facebook.com', 'fb.watch', 'tiktok.com', 'instagram.com', 'threads.net', 'threads.com', 'twitter.com', 'x.com')
jobs = storage.restore(DATA)
analysis_cache = {}
lock = threading.Lock()
preview_lock = threading.Lock()
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
    settings = {'quiet': True, 'no_warnings': False, 'noplaylist': True, 'socket_timeout': 25, 'retries': 2, 'cachedir': False, 'ignoreconfig': True, 'js_runtimes': runtimes}
    if os.environ.get('STREAMVAULT_SYSTEM_CERTS', '').lower() in ('1', 'true', 'yes'):
        # yt-dlp's supported option uses the OS trust store; TLS verification stays enabled.
        settings['compat_opts'] = {'no-certifi'}
        ca_file = ssl.get_default_verify_paths().cafile
        if ca_file and not any(os.environ.get(name) for name in ('SSL_CERT_FILE', 'CURL_CA_BUNDLE', 'REQUESTS_CA_BUNDLE')):
            os.environ.setdefault('CURL_CA_BUNDLE', ca_file)
    return settings


def video_downloader(settings):
    downloader = yt_dlp.YoutubeDL(settings, auto_init=False)
    downloader.add_info_extractor(ThreadsIE())
    downloader.add_default_info_extractors()
    return downloader


def extract_video(url, download, settings):
    if not source_access.youtube_url(url):
        with video_downloader(settings) as dl:
            return dl.extract_info(url, download=download)
    with source_access.youtube.request(download):
        settings = source_access.authenticated_settings(settings) | {'logger': source_access.SourceLogger(), 'sleep_interval_requests': 1, 'extractor_retries': 1}
        try:
            with video_downloader(settings) as dl:
                return dl.extract_info(url, download=download)
        except Exception:
            if source_access.youtube.remaining():
                raise source_access.SourceRateLimited(source_access.youtube.remaining()) from None
            raise


def describe_media(file):
    if not shutil.which('ffprobe'):
        return {}

    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,codec_name,height', '-of', 'json', str(file)], capture_output=True, text=True, timeout=20, check=True)
        streams = json.loads(result.stdout).get('streams', [])
        return {'has_audio': any(s.get('codec_type') == 'audio' for s in streams), 'actual_height': max((s.get('height', 0) for s in streams if s.get('codec_type') == 'video'), default=None),
                'video_codec': next((s.get('codec_name') for s in streams if s.get('codec_type') == 'video'), None),
                'audio_codec': next((s.get('codec_name') for s in streams if s.get('codec_type') == 'audio'), None)}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def cap_resolution(file, height, media):
    # Some sources omit resolution metadata; enforce the requested cap after probing.
    if media.get('actual_height', 0) is not None and media.get('actual_height', 0) > height:
        scaled = file.with_name(file.stem + '.scaled' + file.suffix)
        subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(file), '-vf', f'scale=-2:{height}', '-c:a', 'copy', str(scaled)], capture_output=True, timeout=300, check=True)
        scaled.replace(file)
        return describe_media(file)
    return media


def make_thumbnail(file):
    directory = file.parent / '_preview'
    directory.mkdir(exist_ok=True)
    target = directory / 'cover.jpg'
    if not target.is_file():
        temporary = directory / 'cover.tmp.jpg'
        try:
            subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(file), '-frames:v', '1', '-vf', 'scale=640:-2', '-q:v', '3', str(temporary)], capture_output=True, timeout=30, check=True)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def playable_file(file, media):
    if file.suffix == '.mp4' and media.get('video_codec') == 'h264' and media.get('audio_codec') in (None, 'aac', 'mp3'):
        return file
    if file.suffix in ('.mp3', '.m4a', '.wav'):
        return file
    directory = file.parent / '_preview'
    directory.mkdir(exist_ok=True)
    target = directory / 'browser.mp4'
    if not target.is_file():
        temporary = directory / 'browser.tmp.mp4'
        try:
            subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(file), '-map', '0:v:0', '-map', '0:a:0?', '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', str(temporary)], capture_output=True, timeout=300, check=True)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def download_error(exc):
    # Classify errors without sending source URLs, credentials, or local paths to the browser.
    message = str(exc).lower()
    if isinstance(exc, (source_access.SourceRateLimited, source_access.SourceBusy)):
        return str(exc)
    if 'session cookie file is missing' in message or 'session cookies are available only' in message:
        return str(exc)
    if source_access.is_rate_limit(message):
        return 'The source is limiting download requests (HTTP 429). Wait before retrying; repeated retries can prolong the limit.'
    if 'confirm you\u2019re not a bot' in message or "confirm you're not a bot" in message:
        return 'YouTube requires bot verification. Open the video in your browser and complete verification. The desktop app can use your own local session cookie file; this does not guarantee download access.'
    if 'threads' in message:
        if 'contains multiple videos' in message:
            return 'This Threads post contains multiple videos. Choose a post containing a single video.'
        if 'no publicly available video' in message:
            return 'This Threads post has no publicly available video.'
        if 'public video files are unavailable' in message:
            return 'Threads public video files are unavailable. Check the post on Threads and retry later.'
        if 'share link did not resolve' in message:
            return 'This Threads share link did not resolve to a public post. Copy the direct post link from Threads.'
    if 'certificate_verify_failed' in message or 'certificate verify failed' in message:
        return 'The source certificate could not be verified. Check the trusted certificate setup for your network or managed proxy; TLS verification must remain enabled.'
    if 'drm' in message:
        return 'This video is DRM-protected and cannot be downloaded by StreamVault.'
    if any(text in message for text in ('not available in your country', 'geo restricted', 'geographic restriction', 'geo-restricted')):
        return 'This video is unavailable in your region. Download access follows the source restrictions.'
    if any(text in message for text in ('private video', 'sign in', 'login required', 'login-required', 'log in', 'age-restricted', 'confirm your age')):
        return 'This video requires sign-in, age verification, or private access. Try a publicly accessible video.'
    if any(text in message for text in ('video unavailable', 'has been removed', 'has been deleted', 'not found', 'http error 404')):
        return 'The video is unavailable or has been removed. Check the link and its availability on the source website.'
    if 'unsupported url' in message:
        return 'This video link is not supported by the current downloader. Try the direct video link; this platform may need a future update.'
    if '403' in message or 'forbidden' in message:
        return 'The video server rejected the download (HTTP 403). Update yt-dlp with its default dependencies, check the JavaScript runtime, and retry. If it persists, check source access and the terminal warnings.'
    if 'requested format' in message:
        return 'The selected quality is unavailable. Analyze the video again or try a lower resolution.'
    if 'login' in message:
        return 'This video requires access or sign-in. Try a publicly accessible video.'
    if 'timed out' in message or 'timeout' in message:
        return 'The source connection timed out. Check your connection and retry.'
    return 'Download failed. Check the terminal output for source access, format, or conversion errors.'


@app.get('/')
def index():
    return FileResponse(ROOT / 'static' / 'index.html')


@app.get('/api/health')
def health():
    return {'application': 'StreamVault', 'status': 'ok', 'ffmpeg': bool(shutil.which('ffmpeg')), 'js_runtimes': list(options()['js_runtimes']), 'youtube_ejs': importlib.util.find_spec('yt_dlp_ejs') is not None, 'youtube_cooldown_seconds': int(source_access.youtube.remaining()), 'active': sum(j['status'] in ('queued', 'downloading', 'processing') for j in jobs.values())}


def check_source_cooldown(url):
    if source_access.youtube_url(url) and source_access.youtube.remaining():
        exc = source_access.SourceRateLimited(source_access.youtube.remaining())
        raise HTTPException(429, str(exc), headers={'Retry-After': str(exc.retry_after)})


@app.post('/api/analyze')
def analyze(req: VideoRequest):
    validate_url(req.url)
    try:
        info = extract_video(req.url, False, options())
        if not info or info.get('_type') in ('playlist', 'multi_video') or info.get('is_live'):
            raise HTTPException(400, 'Please choose a single, non-live video.')
        heights = sorted({f['height'] for f in info.get('formats', []) if f.get('height') and f.get('vcodec') != 'none'}, reverse=True)
        result = {'title': info.get('title', 'Untitled video'), 'author': info.get('uploader', 'Unknown creator'), 'duration': info.get('duration'), 'thumbnail': info.get('thumbnail'), 'platform': info.get('extractor_key'), 'qualities': heights, 'url': req.url}
        with lock:
            if len(analysis_cache) >= 100:
                analysis_cache.pop(next(iter(analysis_cache)))
            analysis_cache[req.url] = (time.monotonic(), {key: result[key] for key in ('title', 'thumbnail', 'duration', 'platform')})
        return result
    except HTTPException:
        raise
    except (source_access.SourceRateLimited, source_access.SourceBusy) as exc:
        raise HTTPException(429, str(exc), headers={'Retry-After': str(exc.retry_after)})
    except Exception as exc:
        raise HTTPException(422, download_error(exc))


def update(job_id, **values):
    with lock:
        previous_status = jobs[job_id]['status']
        jobs[job_id].update(values)
        if jobs[job_id]['status'] != previous_status or jobs[job_id]['status'] in ('completed', 'failed'):
            storage.save(DATA, jobs[job_id])


def run_download(job_id, req):
    folder = DATA / job_id
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
        opts |= {'format': f'bv*[height<=?{req.quality}]+ba/b[height<=?{req.quality}]/bv*[height<=?{req.quality}]', 'merge_output_format': req.format, 'postprocessors': [{'key': 'FFmpegVideoConvertor', 'preferedformat': req.format}]}
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(DATA).free < 256 * 1024**2:
            raise RuntimeError('Insufficient free disk space')
        update(job_id, status='downloading')
        info = extract_video(req.url, True, opts)
        files = [p for p in folder.iterdir() if p.suffix == '.' + req.format]
        if not files:
            raise RuntimeError('No output file')
        file = max(files, key=lambda p: p.stat().st_size)
        media = describe_media(file)
        if req.format in ('mp4', 'mkv', 'webm'):
            media = cap_resolution(file, int(req.quality), media)
        preview_available = False
        if req.format in ('mp4', 'mkv', 'webm'):
            try:
                make_thumbnail(file)
            except (OSError, subprocess.SubprocessError):
                pass  # A missing poster must never turn a successful download into a failure.
        try:
            playable_file(file, media)
            preview_available = True
        except (OSError, subprocess.SubprocessError):
            pass
        update(job_id, status='completed', progress=100, title=info.get('title', file.stem), filename=file.name, size=file.stat().st_size,
               thumbnail=info.get('thumbnail'), duration=info.get('duration'), platform=info.get('extractor_key'), preview_available=preview_available, **media)
    except Exception as exc:
        message = 'Not enough free disk space. Free space and retry.' if 'disk space' in str(exc).lower() else download_error(exc)
        update(job_id, status='failed', error=message)
        shutil.rmtree(folder, ignore_errors=True)


@app.post('/api/download', status_code=202)
def download(req: DownloadRequest):
    validate_url(req.url)
    check_source_cooldown(req.url)
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
        cached = analysis_cache.get(req.url)
        if cached and time.monotonic() - cached[0] < 900:
            jobs[job_id].update(cached[1])
        storage.save(DATA, jobs[job_id])
        result = jobs[job_id].copy()
    pool.submit(run_download, job_id, req)
    return result


@app.post('/api/jobs/{job_id}/retry', status_code=202)
def retry_job(job_id: str):
    with lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, 'Download not found.')
        if job['status'] != 'failed':
            raise HTTPException(409, 'Only failed downloads can be retried.')
        if sum(j['status'] in ('queued', 'downloading', 'processing') for j in jobs.values()) >= 10:
            raise HTTPException(429, 'Queue is full. Wait for a download to finish.')
        req = DownloadRequest(url=job['url'], format=job['format'], quality=job['quality'])
        validate_url(req.url)
        check_source_cooldown(req.url)
        if not shutil.which('ffmpeg'):
            raise HTTPException(503, 'Install FFmpeg to enable downloads and conversion.')
        job.update(status='queued', progress=0, speed=None, eta=None)
        job.pop('error', None)
        storage.save(DATA, job)
        result = job.copy()
    pool.submit(run_download, job_id, req)
    return result


@app.get('/api/jobs')
def list_jobs():
    with lock:
        return [j.copy() for j in sorted(jobs.values(), key=lambda j: j['created'], reverse=True)]


@app.get('/api/jobs/{job_id}/file')
def get_file(job_id: str):
    file, job = completed_file(job_id)
    return FileResponse(file, filename=job['filename'])


def completed_file(job_id):
    job = jobs.get(job_id)
    if not job or job['status'] != 'completed':
        raise HTTPException(404, 'Download is not available.')
    file = DATA / job_id / job['filename']
    if not file.is_file():
        raise HTTPException(404, 'File no longer exists.')
    return file, job


@app.get('/api/jobs/{job_id}/thumbnail')
def get_thumbnail(job_id: str):
    file, job = completed_file(job_id)
    if job.get('format') not in ('mp4', 'mkv', 'webm'):
        raise HTTPException(404, 'No video thumbnail is available.')
    try:
        with preview_lock:
            thumbnail = make_thumbnail(file)
        return FileResponse(thumbnail, media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=86400'})
    except (OSError, subprocess.SubprocessError):
        raise HTTPException(404, 'Thumbnail is unavailable.')


@app.get('/api/jobs/{job_id}/stream')
def stream_file(job_id: str):
    file, job = completed_file(job_id)
    try:
        with preview_lock:
            preview = playable_file(file, job if 'video_codec' in job else describe_media(file))
        media_type = {'.mp4': 'video/mp4', '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.wav': 'audio/wav'}[preview.suffix]
        return FileResponse(preview, media_type=media_type, headers={'Cache-Control': 'private, no-cache'})
    except (OSError, KeyError, subprocess.SubprocessError):
        raise HTTPException(422, 'A browser preview could not be prepared. Save the original file to play it on your device.')


@app.delete('/api/jobs/{job_id}')
def remove_job(job_id: str):
    with lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, 'Download not found.')
        if job['status'] not in ('completed', 'failed'):
            raise HTTPException(409, 'Wait for the download to finish before removing it.')
        storage.delete(DATA, job_id)
        del jobs[job_id]
    shutil.rmtree(DATA / job_id, ignore_errors=True)
    return {'removed': True}
