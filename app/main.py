import os
import importlib.util
import json
import shutil
import ssl
import subprocess
import threading
import time
import uuid
import re
from typing import Annotated
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
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
@asynccontextmanager
async def lifespan(application):
    try:
        yield
    finally:
        stop_previews()


app = FastAPI(title='StreamVault', version='1.2.0', lifespan=lifespan)
app.add_middleware(AccessControl)
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
HOSTS = ('youtube.com', 'youtu.be', 'dailymotion.com', 'dai.ly', 'facebook.com', 'fb.watch', 'tiktok.com', 'instagram.com', 'threads.net', 'threads.com', 'twitter.com', 'x.com')
jobs = storage.restore(DATA)
analysis_cache = {}
lock = threading.Lock()
preview_locks = {}
pool = ThreadPoolExecutor(max_workers=2)
preview_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='preview')
preview_processes = set()
preview_process_lock = threading.Lock()
previews_stopping = threading.Event()
MAX_ACTIVE = 50

class VideoRequest(BaseModel):
    url: str = Field(max_length=2048)

class DownloadRequest(VideoRequest):
    format: str = 'mp4'
    quality: str = '1080'


class BulkDownloadRequest(BaseModel):
    urls: list[Annotated[str, Field(max_length=2048)]] = Field(min_length=1, max_length=50)
    format: str = 'mp4'
    quality: str = '1080'
    youtube_concurrency: int = Field(default=1, ge=1, le=2)


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
        logger = source_access.SourceLogger()
        settings = source_access.authenticated_settings(settings) | {'logger': logger, 'sleep_interval_requests': 1, 'extractor_retries': 1}
        try:
            with video_downloader(settings) as dl:
                return dl.extract_info(url, download=download)
        except Exception as exc:
            if source_access.is_rate_limit(exc) or logger.terminal_rate_limit or (logger.saw_rate_limit and source_access.is_bot_verification(exc)):
                source_access.youtube.limited()
                raise source_access.SourceRateLimited(source_access.youtube.remaining()) from None
            raise


def describe_media(file):
    if not shutil.which('ffprobe'):
        return {}

    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,codec_name,pix_fmt,height:format=duration', '-of', 'json', str(file)], capture_output=True, text=True, timeout=20, check=True)
        details = json.loads(result.stdout)
        streams = details.get('streams', [])
        return {'has_audio': any(s.get('codec_type') == 'audio' for s in streams), 'actual_height': max((s.get('height', 0) for s in streams if s.get('codec_type') == 'video'), default=None),
                'video_codec': next((s.get('codec_name') for s in streams if s.get('codec_type') == 'video'), None),
                'video_pixel_format': next((s.get('pix_fmt') for s in streams if s.get('codec_type') == 'video'), None),
                'audio_codec': next((s.get('codec_name') for s in streams if s.get('codec_type') == 'audio'), None),
                'duration': float((details.get('format') or {}).get('duration') or 0)}
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


def cached_playable(file, media):
    if file.suffix == '.mp4' and media.get('video_codec') == 'h264' and media.get('video_pixel_format') == 'yuv420p' and media.get('audio_codec') in (None, 'aac', 'mp3'):
        return file
    if file.suffix in ('.mp3', '.m4a', '.wav'):
        return file
    target = file.parent / '_preview' / 'browser.mp4'
    return target if target.is_file() else None


def playable_file(file, media, progress=None):
    ready = cached_playable(file, media)
    if ready:
        return ready
    directory = file.parent / '_preview'
    directory.mkdir(exist_ok=True)
    target = directory / 'browser.mp4'
    if not target.is_file():
        temporary = directory / 'browser.tmp.mp4'
        try:
            encode_preview(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-threads', '2', '-i', str(file), '-map', '0:v:0', '-map', '0:a:0?', '-vf', r'scale=-2:trunc(min(720\,ih)/2)*2', '-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', '-progress', 'pipe:1', '-nostats', str(temporary)], media.get('duration'), progress)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def encode_preview(command, duration, progress):
    if previews_stopping.is_set():
        raise RuntimeError('Preview worker is stopping')
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    with preview_process_lock:
        preview_processes.add(process)
        if previews_stopping.is_set():
            process.kill()
    def terminate():
        try:
            process.kill()
        except OSError:
            pass
    timer = threading.Timer(1800, terminate)
    timer.daemon = True
    timer.start()
    try:
        for line in process.stdout:
            if progress and duration and line.startswith('out_time_us='):
                try:
                    progress(max(0, min(99, int(int(line.split('=', 1)[1]) / 1_000_000 / duration * 100))))
                except ValueError:
                    pass
        if process.wait() != 0:
            raise subprocess.CalledProcessError(process.returncode, 'ffmpeg')
    finally:
        timer.cancel()
        if process.poll() is None:
            terminate()
        process.wait()
        process.stdout.close()
        with preview_process_lock:
            preview_processes.discard(process)


def stop_previews():
    previews_stopping.set()
    preview_pool.shutdown(wait=False, cancel_futures=True)
    with preview_process_lock:
        for process in tuple(preview_processes):
            try:
                process.kill()
            except OSError:
                pass


def download_error(exc):
    # Classify errors without sending source URLs, credentials, or local paths to the browser.
    message = str(exc).lower()
    if isinstance(exc, (source_access.SourceRateLimited, source_access.SourceBusy)):
        return str(exc)
    known_errors = ('Choose individual, non-live video links. Playlists and channels are not supported by the bulk queue.', 'The downloaded file is not valid media.', 'The source has no video track. Choose an audio format instead.', 'The source has no audio track. Choose a video format instead.',
                    'The configured session cookie file is missing. Check STREAMVAULT_COOKIE_FILE locally.', 'Session cookies are available only in local desktop mode.')
    for error in known_errors:
        if message == error.lower():
            return error
    if 'netscape format cookies file' in message or 'failed to load cookies' in message:
        return 'The local session cookie file could not be read. Export a valid Netscape-format cookie file and check its permissions.'
    if source_access.is_rate_limit(message):
        return 'The source is limiting download requests (HTTP 429). Wait before retrying; repeated retries can prolong the limit.'
    if source_access.is_bot_verification(message):
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
    with lock:
        active = sum(j['status'] in ('queued', 'downloading', 'processing') for j in jobs.values())
    return {'application': 'StreamVault', 'version': app.version, 'status': 'ok', 'ffmpeg': bool(shutil.which('ffmpeg')), 'ffprobe': bool(shutil.which('ffprobe')), 'js_runtimes': list(options()['js_runtimes']), 'youtube_ejs': importlib.util.find_spec('yt_dlp_ejs') is not None, 'youtube_cooldown_seconds': int(source_access.youtube.remaining()), 'active': active}


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
    recovered = None
    def progress(event):
        if event['status'] == 'downloading':
            total = event.get('total_bytes') or event.get('total_bytes_estimate') or 0
            info = event.get('info_dict') or {}
            metadata = {key: info[key] for key in ('title', 'thumbnail', 'duration') if info.get(key) is not None}
            update(job_id, status='downloading', stage='downloading', progress=min(99, round(event.get('downloaded_bytes', 0) / total * 100)) if total else 0, speed=event.get('speed'), eta=event.get('eta'), **metadata)
        elif event['status'] == 'finished':
            update(job_id, status='processing', stage='merging', stage_started=time.time(), progress=99)
    def postprocess(event):
        if event.get('status') == 'started':
            name = event.get('postprocessor', '')
            update(job_id, status='processing', stage='merging' if 'Merger' in name else 'converting', stage_started=time.time(), progress=99)
    try:
        recovered = recover_file(folder, req) if jobs[job_id].get('recoverable') else None
        opts = ({} if recovered else options()) | {'outtmpl': str(folder / '%(title).120B.%(ext)s'), 'progress_hooks': [progress], 'postprocessor_hooks': [postprocess], 'max_filesize': 2 * 1024**3, 'playlistend': 1}
        if req.format in ('mp3', 'm4a', 'wav'):
            opts |= {'format': 'bestaudio/best', 'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': req.format, 'preferredquality': '192'}]}
        else:
            opts |= {'format': f'bv*[height<=?{req.quality}]+ba/b[height<=?{req.quality}]/bv*[height<=?{req.quality}]/bv*+ba/b/bv*', 'merge_output_format': req.format, 'postprocessors': [{'key': 'FFmpegVideoConvertor', 'preferedformat': req.format}]}
            if req.format == 'mp4':
                opts['format'] = f'bv*[ext=mp4][vcodec^=avc1][height<=?{req.quality}]+ba[ext=m4a]/b[ext=mp4][vcodec^=avc1][height<=?{req.quality}]/' + opts['format']
        # Interrupted attempts can leave stale originals and browser previews behind.
        if folder.exists() and not recovered:
            shutil.rmtree(folder)
        folder.mkdir(parents=True, exist_ok=True)
        if not recovered and shutil.disk_usage(DATA).free < 256 * 1024**2:
            raise RuntimeError('Insufficient free disk space')
        if recovered:
            info = {'title': jobs[job_id].get('title', recovered.stem), 'thumbnail': jobs[job_id].get('thumbnail'), 'extractor_key': jobs[job_id].get('platform')}
        else:
            update(job_id, status='downloading', stage='extracting', stage_started=time.time())
            info = extract_video(req.url, True, opts)
        if not info or info.get('_type') in ('playlist', 'multi_video') or info.get('is_live'):
            raise RuntimeError('Choose individual, non-live video links. Playlists and channels are not supported by the bulk queue.')
        update(job_id, status='processing', stage='verifying', stage_started=time.time(), progress=99)
        files = [p for p in folder.iterdir() if p.suffix == '.' + req.format]
        if not files:
            raise RuntimeError('No output file')
        file = recovered or max(files, key=lambda p: p.stat().st_size)
        media = describe_media(file)
        if not media or not file.stat().st_size:
            raise RuntimeError('The downloaded file is not valid media.')
        if req.format in ('mp4', 'mkv', 'webm') and not media.get('video_codec'):
            raise RuntimeError('The source has no video track. Choose an audio format instead.')
        if req.format in ('mp3', 'm4a', 'wav') and not media.get('has_audio'):
            raise RuntimeError('The source has no audio track. Choose a video format instead.')
        if req.format in ('mp4', 'mkv', 'webm'):
            if media.get('actual_height', 0) and media['actual_height'] > int(req.quality):
                update(job_id, stage='resizing', stage_started=time.time())
            media = cap_resolution(file, int(req.quality), media)
        preview_available = bool(cached_playable(file, media))
        duration = media.pop('duration', 0) or info.get('duration')
        update(job_id, status='completed', progress=100, title=info.get('title', file.stem), filename=file.name, size=file.stat().st_size,
               thumbnail=info.get('thumbnail'), duration=duration, platform=info.get('extractor_key'), preview_available=preview_available,
               preview_status='ready' if preview_available else 'not_requested', preview_progress=100 if preview_available else 0, stage='ready', recoverable=False, **media)
    except Exception as exc:
        message = 'Not enough free disk space. Free space and retry.' if 'disk space' in str(exc).lower() else download_error(exc)
        if req.format in ('mp3', 'm4a', 'wav') and 'requested format' in str(exc).lower():
            message = 'No compatible audio stream is available. The source may contain video only; try MP4.'
        # Finish cleanup before Retry can see a failed job and start a new attempt.
        if not recovered:
            shutil.rmtree(folder, ignore_errors=True)
        update(job_id, status='failed', error=message)


def recover_file(folder, req):
    for file in sorted(folder.glob('*.' + req.format), key=lambda p: p.stat().st_size, reverse=True):
        if re.search(r'\.f\d+|\.scaled$', file.stem):
            continue
        media = describe_media(file)
        expected = jobs[folder.name].get('duration') or 0
        if media.get('duration', 0) < max(0.1, expected - 2):
            continue
        has_track = media.get('video_codec') if req.format in ('mp4', 'mkv', 'webm') else media.get('has_audio')
        if has_track:
            return file
    return None


def submit_job(job_id, req):
    try:
        pool.submit(run_download, job_id, req)
    except RuntimeError:
        update(job_id, status='failed', error='The download worker could not start. Restart StreamVault and retry.')
        raise HTTPException(503, 'The download worker is unavailable. Restart StreamVault and retry.')


def save_queued(job):
    try:
        storage.save(DATA, job)
    except (OSError, storage.sqlite3.Error):
        raise HTTPException(503, 'Download history could not be saved. Check free disk space and folder permissions.')


@app.post('/api/download', status_code=202)
def download(req: DownloadRequest):
    validate_url(req.url)
    check_source_cooldown(req.url)
    if req.format not in ('mp4', 'webm', 'mkv', 'mp3', 'm4a', 'wav') or req.quality not in ('2160', '1440', '1080', '720', '480', '360'):
        raise HTTPException(400, 'Unsupported format or quality.')
    if not all(shutil.which(tool) for tool in ('ffmpeg', 'ffprobe')):
        raise HTTPException(503, 'Install FFmpeg and FFprobe to enable verified downloads and conversion.')
    with lock:
        if sum(j['status'] in ('queued', 'downloading', 'processing') for j in jobs.values()) >= MAX_ACTIVE:
            raise HTTPException(429, 'Queue is full. Wait for a download to finish.')
        if len(jobs) >= 100:
            raise HTTPException(429, 'History is full. Remove older downloads first.')
        job_id = uuid.uuid4().hex
        job = {'id': job_id, 'url': req.url, 'title': 'Preparing download', 'format': req.format, 'quality': req.quality, 'status': 'queued', 'progress': 0, 'created': time.time()}
        cached = analysis_cache.get(req.url)
        if cached and time.monotonic() - cached[0] < 900:
            job.update(cached[1])
        save_queued(job)
        jobs[job_id] = job
        result = job.copy()
    submit_job(job_id, req)
    return result


@app.post('/api/download/bulk', status_code=202)
def bulk_download(req: BulkDownloadRequest):
    urls = list(dict.fromkeys(url.strip() for url in req.urls))
    requests = [DownloadRequest(url=url, format=req.format, quality=req.quality) for url in urls]
    for item in requests:
        validate_url(item.url)
        if source_access.youtube_url(item.url) and (urlparse(item.url).path == '/playlist' or urlparse(item.url).path.startswith(('/@', '/channel/', '/user/', '/c/'))):
            raise HTTPException(400, 'Paste individual video links, not a playlist or channel URL.')
        check_source_cooldown(item.url)
    if req.format not in ('mp4', 'webm', 'mkv', 'mp3', 'm4a', 'wav') or req.quality not in ('2160', '1440', '1080', '720', '480', '360'):
        raise HTTPException(400, 'Unsupported format or quality.')
    if not all(shutil.which(tool) for tool in ('ffmpeg', 'ffprobe')):
        raise HTTPException(503, 'Install FFmpeg and FFprobe to enable downloads.')
    pending = []
    with lock:
        available = min(MAX_ACTIVE - sum(j['status'] in ('queued', 'downloading', 'processing') for j in jobs.values()), 100 - len(jobs))
        if len(requests) > available:
            raise HTTPException(429, f'Only {max(0, available)} queue/history slots are available. Submit fewer links or remove old history.')
        for item in requests:
            job = {'id': uuid.uuid4().hex, 'url': item.url, 'title': 'Queued video', 'format': item.format, 'quality': item.quality, 'created': time.time(), 'status': 'queued', 'stage': 'queued', 'progress': 0}
            cached = analysis_cache.get(item.url)
            if cached and time.monotonic() - cached[0] < 900:
                job.update(cached[1])
            pending.append(job)
        try:
            storage.save_many(DATA, pending)
        except (OSError, storage.sqlite3.Error):
            raise HTTPException(503, 'Bulk queue could not be saved. Check free disk space and folder permissions.')
        jobs.update({job['id']: job for job in pending})
        source_access.youtube.slot.set_limit(req.youtube_concurrency)
    failures = 0
    for job, item in zip(pending, requests):
        try:
            submit_job(job['id'], item)
        except HTTPException:
            failures += 1
    return {'jobs': [job.copy() for job in pending], 'added': len(pending), 'duplicates_removed': len(req.urls) - len(urls), 'submission_failures': failures}


@app.post('/api/jobs/{job_id}/retry', status_code=202)
def retry_job(job_id: str):
    with lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, 'Download not found.')
        if job['status'] != 'failed':
            raise HTTPException(409, 'Only failed downloads can be retried.')
        if sum(j['status'] in ('queued', 'downloading', 'processing') for j in jobs.values()) >= MAX_ACTIVE:
            raise HTTPException(429, 'Queue is full. Wait for a download to finish.')
        req = DownloadRequest(url=job['url'], format=job['format'], quality=job['quality'])
        validate_url(req.url)
        check_source_cooldown(req.url)
        if not all(shutil.which(tool) for tool in ('ffmpeg', 'ffprobe')):
            raise HTTPException(503, 'Install FFmpeg and FFprobe to enable verified downloads and conversion.')
        pending = {key: value for key, value in job.items() if key not in ('error', 'filename', 'size', 'preview_available', 'preview_status', 'preview_error', 'preview_progress', 'video_codec', 'video_pixel_format', 'audio_codec', 'actual_height', 'has_audio')}
        pending.update(status='queued', progress=0, speed=None, eta=None)
        save_queued(pending)
        jobs[job_id] = pending
        result = pending.copy()
    submit_job(job_id, req)
    return result


@app.get('/api/jobs')
def list_jobs():
    with lock:
        for job in jobs.values():
            if job['status'] == 'completed' and not saved_path(job):
                changed = job | {'status': 'failed', 'error': 'The saved file is missing. Use Retry to download it again.', 'preview_available': False}
                save_queued(changed)
                job.update(changed)
        return [j.copy() for j in sorted(jobs.values(), key=lambda j: j['created'], reverse=True)]


@app.get('/api/jobs/{job_id}/file')
def get_file(job_id: str):
    file, job = completed_file(job_id)
    return FileResponse(file, filename=job['filename'])


def completed_file(job_id):
    job = jobs.get(job_id)
    if not job or job['status'] != 'completed':
        raise HTTPException(404, 'Download is not available.')
    file = saved_path(job)
    if not file:
        raise HTTPException(404, 'File no longer exists.')
    return file, job


def saved_path(job):
    if not job.get('filename'):
        return None
    folder = (DATA / job['id']).resolve()
    file = (folder / job['filename']).resolve()
    return file if file.is_relative_to(folder) and file.is_file() else None


def media_lock(job_id):
    with lock:
        if job_id not in jobs:
            raise HTTPException(404, 'Download not found.')
        return preview_locks.setdefault(job_id, threading.Lock())


@app.get('/api/jobs/{job_id}/thumbnail')
def get_thumbnail(job_id: str):
    file, _ = completed_file(job_id)
    cached = file.parent / '_preview' / 'cover.jpg'
    if cached.is_file():
        return FileResponse(cached, media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=86400'})
    if jobs[job_id].get('preview_status') == 'processing':
        raise HTTPException(404, 'Thumbnail is being prepared.')
    with media_lock(job_id):
        file, job = completed_file(job_id)
        if job.get('format') not in ('mp4', 'mkv', 'webm'):
            raise HTTPException(404, 'No video thumbnail is available.')
        try:
            thumbnail = make_thumbnail(file)
            return FileResponse(thumbnail, media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=86400'})
        except (OSError, subprocess.SubprocessError):
            raise HTTPException(404, 'Thumbnail is unavailable.')


@app.get('/api/jobs/{job_id}/stream')
def stream_file(job_id: str, original: bool = False):
    file, job = completed_file(job_id)
    types = {'.mp4': 'video/mp4', '.webm': 'video/webm', '.mkv': 'video/x-matroska', '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.wav': 'audio/wav'}
    if original:
        return FileResponse(file, media_type=types.get(file.suffix, 'application/octet-stream'), headers={'Cache-Control': 'private, no-cache'})
    preview = cached_playable(file, job if 'video_pixel_format' in job else describe_media(file))
    if not preview:
        raise HTTPException(409, 'Prepare the browser preview first. The original file is already available.', headers={'Retry-After': '2'})
    return FileResponse(preview, media_type=types.get(preview.suffix, 'application/octet-stream'), headers={'Cache-Control': 'private, no-cache'})


def preview_state(job):
    return {'status': job.get('preview_status', 'not_requested'), 'progress': job.get('preview_progress', 0), 'error': job.get('preview_error'), 'started': job.get('preview_started'), 'stream_url': f"/api/jobs/{job['id']}/stream"}


def build_preview(job_id):
    try:
        with media_lock(job_id):
            file, job = completed_file(job_id)
            update(job_id, preview_status='processing', preview_started=time.time(), preview_progress=0)
            try:
                if shutil.disk_usage(DATA).free < 256 * 1024**2:
                    raise OSError('low disk space')
                media = job if 'video_pixel_format' in job else describe_media(file)
                last = -1
                def progress(percent):
                    nonlocal last
                    if percent != last:
                        last = percent
                        update(job_id, preview_progress=percent)
                playable_file(file, media, progress=progress)
                update(job_id, preview_available=True, preview_status='ready', preview_progress=100, preview_error=None)
            except Exception:
                update(job_id, preview_available=False, preview_status='failed', preview_progress=0,
                       preview_error='Browser preview could not finish. Save original is available; you can try preparing the preview again later.')
    except HTTPException:
        pass  # Removed queued jobs do not recreate files.


@app.post('/api/jobs/{job_id}/preview', status_code=202)
def prepare_preview(job_id: str):
    file, job = completed_file(job_id)
    if job.get('preview_status') in ('queued', 'processing'):
        return preview_state(job)
    media = job if 'video_pixel_format' in job else describe_media(file)
    if cached_playable(file, media):
        update(job_id, preview_available=True, preview_status='ready', preview_progress=100)
        return preview_state(job)
    with lock:
        job = jobs.get(job_id)
        if not job or job['status'] != 'completed':
            raise HTTPException(404, 'Download is not available.')
        if job.get('preview_status') in ('queued', 'processing'):
            return preview_state(job)
        pending = job | {'preview_available': False, 'preview_status': 'queued', 'preview_progress': 0, 'preview_error': None}
        save_queued(pending)
        job.update(pending)
    try:
        preview_pool.submit(build_preview, job_id)
    except RuntimeError:
        update(job_id, preview_available=False, preview_status='failed', preview_error='Preview worker is unavailable. Restart StreamVault; the original remains available.')
    return preview_state(jobs.get(job_id, job))


@app.get('/api/jobs/{job_id}/preview')
def get_preview(job_id: str):
    _, job = completed_file(job_id)
    return preview_state(job)


@app.delete('/api/jobs/{job_id}')
def remove_job(job_id: str):
    guard = media_lock(job_id)
    if not guard.acquire(blocking=False):
        raise HTTPException(409, 'This file is being prepared. Save the original now, or remove it after preparation finishes.')
    try:
        with lock:
            job = jobs.get(job_id)
            if not job:
                raise HTTPException(404, 'Download not found.')
            if job['status'] not in ('completed', 'failed'):
                raise HTTPException(409, 'Wait for the download to finish before removing it.')
            storage.delete(DATA, job_id)
            del jobs[job_id]
            preview_locks.pop(job_id, None)
        shutil.rmtree(DATA / job_id, ignore_errors=True)
    finally:
        guard.release()
    return {'removed': True}
