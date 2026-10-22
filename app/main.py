import os
import errno
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
from urllib.parse import urlparse, parse_qs
from itertools import islice

import yt_dlp
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from app import storage
from app import __version__
from app.auth import AccessControl
from app.threads import ThreadsIE
from app import source_access
from app import destinations, recovery
from app.deferred import DeferredDownloads

ROOT = Path(__file__).parent
DATA = Path(os.environ.get('DOWNLOAD_DIR', ROOT.parent / 'data')).resolve()
DATA.mkdir(parents=True, exist_ok=True)
@asynccontextmanager
async def lifespan(application):
    initialize_recovery()
    try:
        yield
    finally:
        deferred_downloads.stop()
        stop_playlist_loads()
        stop_previews()


app = FastAPI(title='AAASoft Video Studio', version=__version__, lifespan=lifespan)
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
MAX_PLAYLIST = 1000
MAX_HISTORY = 1100
playlist_cache = {}
playlist_lock = threading.Lock()
playlist_loads = {}
playlist_load_lock = threading.Lock()
playlist_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='playlist')
folder_dialog_lock = threading.Lock()
deferred_downloads = DeferredDownloads(lambda: source_access.youtube.remaining(), lambda job_id, req: resume_deferred(job_id, req), delay_for=lambda job_id, req: recovery_delay(job_id, req))

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


class PlaylistDownloadRequest(BaseModel):
    token: str = Field(min_length=1, max_length=64)
    video_ids: list[Annotated[str, Field(max_length=11)]] | None = Field(default=None, max_length=1000)
    format: str = 'mp4'
    quality: str = '720'
    youtube_concurrency: int = Field(default=1, ge=1, le=2)


class RemoveJobsRequest(BaseModel):
    job_ids: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(min_length=1, max_length=1100)


class FolderRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


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
    settings = settings.copy()
    playlist_cancel = settings.pop('_playlist_cancel', None)
    phase = settings.pop('_playlist_phase', None)
    if not source_access.youtube_url(url):
        with video_downloader(settings) as dl:
            return dl.extract_info(url, download=download)
    if phase:
        phase('waiting_metadata', 0)
    with source_access.youtube.request(download, wait=playlist_cancel is not None, cancel=playlist_cancel, metadata=not download):
        if phase:
            phase('fetching', 0)
        logger = source_access.SourceLogger()
        settings = source_access.authenticated_settings(settings) | {'logger': logger, 'sleep_interval_requests': 1, 'extractor_retries': 1}
        try:
            with video_downloader(settings) as dl:
                try:
                    result = dl.extract_info(url, download=download)
                except Exception as first:
                    if settings.get('lazy_playlist') or not recovery.youtube_fallback(first) or logger.saw_rate_limit or source_access.youtube.remaining():
                        raise
                    alternate = settings.copy()
                    alternate['extractor_args'] = {**settings.get('extractor_args', {}), 'youtube': {'player_client': ['web', 'web_safari']}}
                    with video_downloader(alternate) as fallback:
                        result = fallback.extract_info(url, download=download)
                if settings.get('lazy_playlist') and result and result.get('_type') == 'playlist':
                    # Playlist pages are lazy network requests: keep the downloader and source gate open.
                    entries = []
                    for entry in islice(result.get('entries') or [], MAX_PLAYLIST + 1):
                        if playlist_cancel is not None and playlist_cancel.is_set():
                            raise source_access.SourceCancelled('Playlist loading cancelled.')
                        entries.append(entry)
                        if phase and len(entries) % 10 == 0:
                            phase('reading_entries', len(entries))
                    result['entries'] = entries
                if settings.get('lazy_playlist') and logger.terminal_rate_limit:
                    raise source_access.SourceRateLimited(600)
                if settings.get('lazy_playlist') and result and logger.saw_error:
                    raise HTTPException(422, 'The playlist list could not be fully loaded. Check source access and load it again; no videos were queued.')
                return result
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


def make_thumbnail(file, cache_dir=None):
    directory = cache_dir or file.parent / '_preview'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / 'cover.jpg'
    if not target.is_file():
        temporary = directory / 'cover.tmp.jpg'
        try:
            subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(file), '-frames:v', '1', '-vf', 'scale=640:-2', '-q:v', '3', str(temporary)], capture_output=True, timeout=30, check=True)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def cached_playable(file, media, cache_dir=None):
    if file.suffix == '.mp4' and media.get('video_codec') == 'h264' and media.get('video_pixel_format') == 'yuv420p' and media.get('audio_codec') in (None, 'aac', 'mp3'):
        return file
    if file.suffix in ('.mp3', '.m4a', '.wav'):
        return file
    target = (cache_dir or file.parent / '_preview') / 'browser.mp4'
    return target if target.is_file() else None


def playable_file(file, media, progress=None, cache_dir=None):
    ready = cached_playable(file, media, cache_dir)
    if ready:
        return ready
    directory = cache_dir or file.parent / '_preview'
    directory.mkdir(parents=True, exist_ok=True)
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
    if 'unable to connect to proxy' in message or 'tunnel connection failed' in message:
        return 'The network proxy rejected the connection. Check proxy or firewall settings on the computer running the app.'
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
    return FileResponse(ROOT / 'static' / 'index.html', headers={'Cache-Control': 'no-store'})


@app.get('/help')
def help_index():
    return FileResponse(ROOT / 'static' / 'help' / 'index.html')


@app.get('/help/{topic}')
def help_article(topic: str):
    if not re.fullmatch(r'[a-z][a-z-]{1,60}', topic):
        raise HTTPException(404, 'Help article not found.')
    file = ROOT / 'static' / 'help' / (topic+'.html')
    if not file.is_file():
        raise HTTPException(404, 'Help article not found.')
    return FileResponse(file)


@app.get('/api/health')
def health():
    with lock:
        active = sum(j['status'] in ('queued', 'downloading', 'processing') for j in jobs.values())
    return {'application': 'StreamVault', 'version': app.version, 'status': 'ok', 'capabilities': {'youtube_playlists': True, 'direct_save': destinations.enabled()}, 'ffmpeg': bool(shutil.which('ffmpeg')), 'ffprobe': bool(shutil.which('ffprobe')), 'js_runtimes': list(options()['js_runtimes']), 'yt_dlp_version': yt_dlp.version.__version__, 'youtube_ejs': importlib.util.find_spec('yt_dlp_ejs') is not None, 'youtube_cooldown_seconds': int(source_access.youtube.remaining()), 'active': active}


def check_source_cooldown(url):
    if source_access.youtube_url(url) and source_access.youtube.remaining():
        exc = source_access.SourceRateLimited(source_access.youtube.remaining())
        raise HTTPException(429, str(exc), headers={'Retry-After': str(exc.retry_after)})


def local_desktop(request):
    return destinations.enabled() and request.client is not None and request.client.host in ('127.0.0.1', '::1') and request.url.hostname in ('localhost', '127.0.0.1', '::1')


def require_local_desktop(request):
    if not local_desktop(request):
        raise HTTPException(403, 'Folder selection is available only in the local desktop launcher, on this computer.')


@app.get('/api/download-location')
def download_location(request: Request):
    local = local_desktop(request)
    path = destinations.selected(DATA) if local else None
    return {'direct_enabled': local, 'path': path, 'configured': bool(path), 'available': bool(path and Path(path).is_dir())}


@app.post('/api/download-location')
def set_download_location(req: FolderRequest, request: Request):
    require_local_desktop(request)
    try:
        destinations.configure(DATA, req.path)
        initialize_recovery()
    except destinations.DestinationError as exc:
        raise HTTPException(400, str(exc))
    return download_location(request)


@app.post('/api/download-location/pick')
def pick_download_location(request: Request):
    require_local_desktop(request)
    if not folder_dialog_lock.acquire(blocking=False):
        raise HTTPException(409, 'The folder dialog is already open. Choose or cancel that dialog first.')
    try:
        path = destinations.picker(destinations.selected(DATA))
        if path:
            destinations.configure(DATA, path)
            initialize_recovery()
        return download_location(request) | {'cancelled': not bool(path)}
    except (destinations.DestinationError, subprocess.TimeoutExpired, OSError) as exc:
        raise HTTPException(503, str(exc) if isinstance(exc, destinations.DestinationError) else 'The folder dialog could not finish. Enter a folder path instead.')
    finally:
        folder_dialog_lock.release()


@app.post('/api/download-location/open')
def open_selected_download_folder(request: Request):
    require_local_desktop(request)
    path = destinations.selected(DATA)
    if not path or not Path(path).is_dir():
        raise HTTPException(409, 'Choose an available download folder first.')
    try:
        destinations.open_folder(Path(path))
    except (destinations.DestinationError, OSError):
        raise HTTPException(503, 'The file manager could not open. Open the displayed folder path manually.')
    return {'opened': True}


def destination_snapshot():
    try:
        return destinations.snapshot(DATA)
    except destinations.DestinationError as exc:
        raise HTTPException(409, str(exc))


@app.post('/api/analyze')
def analyze(req: VideoRequest):
    validate_url(req.url)
    try:
        info = extract_video(req.url, False, options())
        if not info or info.get('_type') in ('playlist', 'multi_video') or info.get('is_live'):
            raise HTTPException(400, 'Please choose a single, non-live video.')
        heights = sorted({f['height'] for f in info.get('formats', []) if f.get('height') and f.get('vcodec') != 'none'}, reverse=True)
        result = {'title': info.get('title', 'Untitled video'), 'author': info.get('uploader', 'Unknown creator'), 'duration': info.get('duration'), 'thumbnail': info.get('thumbnail'), 'platform': info.get('extractor_key'), 'qualities': heights, 'url': req.url}
        result['previous_downloads'] = storage.downloaded_count(DATA, req.url)
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
        if jobs[job_id]['status'] != previous_status or jobs[job_id]['status'] in ('completed', 'failed') or 'planned_filename' in values or 'retry_at' in values or 'recovery_pending' in values:
            storage.save(DATA, jobs[job_id])


@app.post('/api/playlist/analyze')
def analyze_playlist(req: VideoRequest):
    return build_playlist(req)


def playlist_url(url):
    validate_url(url)
    parsed = urlparse(url)
    ids = parse_qs(parsed.query).get('list', [])
    if not source_access.youtube_url(url) or len(ids) != 1 or not re.fullmatch(r'[A-Za-z0-9_-]{2,200}', ids[0]):
        raise HTTPException(400, 'Paste a YouTube playlist URL containing list=PLAYLIST_ID.')
    if ids[0].startswith('RD'):
        raise HTTPException(400, 'YouTube Mix/radio lists change continuously. Use a saved playlist instead.')
    return 'https://www.youtube.com/playlist?list=' + ids[0]


def build_playlist(req, cancel=None, phase=None):
    url = playlist_url(req.url)
    try:
        info = extract_video(url, False, options() | {'noplaylist': False, 'extract_flat': 'in_playlist', 'lazy_playlist': True,
                                                    'playlistend': MAX_PLAYLIST + 1, 'ignoreerrors': True} |
                             ({'_playlist_cancel': cancel, '_playlist_phase': phase} if cancel is not None else {}))
        if cancel is not None and cancel.is_set():
            raise source_access.SourceCancelled('Playlist loading cancelled.')
        if not info or info.get('_type') != 'playlist':
            raise HTTPException(422, 'This playlist is unavailable. Check its visibility and source access.')
        entries = list(islice(info.get('entries') or [], MAX_PLAYLIST + 1))
        reported = info.get('playlist_count')
        reported = reported if isinstance(reported, int) and reported >= 0 else len(entries)
        if max(len(entries), reported) > MAX_PLAYLIST:
            raise HTTPException(400, f'This playlist exceeds {MAX_PLAYLIST} entries. Split it into smaller playlists; no partial playlist was queued.')
        videos, seen, unavailable, duplicates = [], set(), 0, 0
        for position, entry in enumerate(entries, 1):
            video_id = entry.get('id', '') if isinstance(entry, dict) else ''
            if not isinstance(video_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id) or entry.get('availability') in ('private', 'premium_only', 'subscriber_only', 'needs_auth') or entry.get('title') in ('[Private video]', '[Deleted video]') or entry.get('is_live') or entry.get('live_status') in ('is_live', 'is_upcoming', 'post_live'):
                unavailable += 1
                continue
            if video_id in seen:
                duplicates += 1
                continue
            seen.add(video_id)
            videos.append({'id': video_id, 'url': 'https://www.youtube.com/watch?v=' + video_id,
                           'title': str(entry.get('title') or 'YouTube video')[:500], 'duration': entry.get('duration'),
                           'position': position, 'platform': 'YouTube', 'thumbnail': entry.get('thumbnail') or
                           next((t.get('url') for t in reversed(entry.get('thumbnails') or []) if isinstance(t, dict) and isinstance(t.get('url'), str) and t['url'].startswith('https://')), None) or
                           'https://i.ytimg.com/vi/' + video_id + '/hqdefault.jpg'})
        if not videos:
            raise HTTPException(422, 'No available videos were found in this playlist. Check its visibility and source access.')
        token = uuid.uuid4().hex
        result = {'token': token, 'title': str(info.get('title') or 'YouTube playlist')[:500], 'url': url, 'videos': videos,
                  'unavailable': unavailable, 'duplicates_removed': duplicates, 'entries_found': len(entries),
                  'unlisted': max(0, reported - len(entries))}
        with playlist_lock:
            now = time.monotonic()
            for old in list(playlist_cache):
                if now - playlist_cache[old]['created'] > 1800:
                    playlist_cache.pop(old)
            if len(playlist_cache) >= 10:
                playlist_cache.pop(next(iter(playlist_cache)))
            playlist_cache[token] = {'created': now, 'data': result, 'queued': None}
        return result
    except HTTPException:
        raise
    except source_access.SourceCancelled:
        raise
    except (source_access.SourceRateLimited, source_access.SourceBusy) as exc:
        raise HTTPException(429, str(exc), headers={'Retry-After': str(exc.retry_after)})
    except Exception as exc:
        raise HTTPException(422, download_error(exc))


def load_playlist(load_id, req):
    with playlist_load_lock:
        item = playlist_loads[load_id]
        item.update(status='loading', started=time.time(), phase='waiting_metadata', entries=0)
    try:
        if item['cancel'].is_set():
            raise source_access.SourceCancelled()
        def phase(name, entries):
            with playlist_load_lock:
                item.update(phase=name, entries=entries)
        result = build_playlist(req, item['cancel'], phase)
        with playlist_load_lock:
            item.update(status='cancelled' if item['cancel'].is_set() else 'ready', result=result)
    except source_access.SourceCancelled:
        with playlist_load_lock:
            item['status'] = 'cancelled'
    except Exception as exc:
        with playlist_load_lock:
            item.update(status='cancelled' if item['cancel'].is_set() else 'failed', detail=exc.detail if isinstance(exc, HTTPException) else download_error(exc))


def stop_playlist_loads():
    with playlist_load_lock:
        for item in playlist_loads.values():
            item['cancel'].set()
    playlist_pool.shutdown(wait=False, cancel_futures=True)


@app.post('/api/playlist/loads', status_code=202)
def start_playlist_load(req: VideoRequest):
    playlist_url(req.url)
    check_source_cooldown(req.url)
    with playlist_load_lock:
        finished = [key for key, item in playlist_loads.items() if item['status'] in ('ready', 'failed', 'cancelled')]
        for key in finished:
            if time.monotonic() - playlist_loads[key]['created'] > 1800:
                playlist_loads.pop(key)
        if len(playlist_loads) >= 10:
            disposable = next((key for key in finished if key in playlist_loads), None)
            if disposable is None:
                raise HTTPException(429, 'Playlist loading queue is full. Wait for a load to finish or cancel it.')
            playlist_loads.pop(disposable)
        load_id = uuid.uuid4().hex
        playlist_loads[load_id] = {'status': 'queued', 'created': time.monotonic(), 'cancel': threading.Event()}
    try:
        playlist_pool.submit(load_playlist, load_id, req)
    except RuntimeError:
        with playlist_load_lock:
            playlist_loads.pop(load_id, None)
        raise HTTPException(503, 'Playlist worker unavailable. Restart StreamVault.')
    return {'id': load_id, 'status': 'queued'}


@app.get('/api/playlist/loads/{load_id}')
def playlist_load_status(load_id: str):
    with playlist_load_lock:
        item = playlist_loads.get(load_id)
        if not item:
            raise HTTPException(404, 'Playlist load expired. Load the playlist again.')
        return {'id': load_id, **{k: item[k] for k in ('status', 'result', 'detail', 'phase', 'started', 'entries') if k in item}}


@app.delete('/api/playlist/loads/{load_id}', status_code=204)
def cancel_playlist_load(load_id: str):
    with playlist_load_lock:
        item = playlist_loads.get(load_id)
        if item and item['status'] not in ('ready', 'failed', 'cancelled'):
            item['cancel'].set()
            item['status'] = 'cancelling'


@app.post('/api/playlist/download', status_code=202)
def download_playlist(req: PlaylistDownloadRequest):
    # Serialize consumption of one preview token, including duplicate HTTP submissions.
    with playlist_lock:
        cached = playlist_cache.get(req.token)
        if not cached or time.monotonic() - cached['created'] > 1800:
            raise HTTPException(410, 'Playlist preview expired. Load the playlist again.')
        if cached['queued'] is not None:
            return cached['queued']
        data = cached['data']
        selected = set(req.video_ids) if req.video_ids is not None else {v['id'] for v in data['videos']}
        if not selected or not selected.issubset({v['id'] for v in data['videos']}):
            raise HTTPException(400, 'Select at least one video from the loaded playlist.')
        videos = [v for v in data['videos'] if v['id'] in selected]
        result = queue_batch([v['url'] for v in videos], req, MAX_PLAYLIST,
                             {v['url']: {k: v[k] for k in ('title', 'duration', 'thumbnail', 'platform')} |
                              {'playlist_title': data['title'], 'playlist_position': v['position']} for v in videos})
        cached['queued'] = result
        return result


def recovery_delay(job_id, req):
    with lock:
        job = jobs.get(job_id)
        if not job:
            return 0
        waiting = job.get('stage') == 'retry_wait'
        busy = waiting and any(j['id'] != job_id and j['status'] in ('queued','downloading','processing')
                               and j.get('stage') != 'retry_wait' for j in jobs.values())
    return max(1 if busy else 0, source_access.youtube.remaining() if source_access.youtube_url(req.url) else 0)


def recovery_enabled():
    try:
        return json.loads((DATA / 'recovery-settings.json').read_text(encoding='utf-8')).get('enabled', True) is True
    except (OSError, ValueError, AttributeError):
        return True


def schedule_recovery(job_id, req, issue, message, recoverable=False):
    job = jobs[job_id]
    attempts = max(0, job.get('auto_attempts', 0) - int(bool(job.get('recovery_pending'))))
    if not recovery_enabled() or not issue['retryable'] or attempts >= issue['limit']:
        return False
    if destinations.enabled() and not job.get('output_dir'):
        try:
            destination = destinations.snapshot(DATA)
        except destinations.DestinationError:
            update(job_id, next_action='Choose an available download folder to enable recovery for this older video.')
            return False
        update(job_id, **destination, legacy_recovery=bool(job.get('recoverable')))
    delay = max(recovery.DELAYS[min(attempts, len(recovery.DELAYS)-1)], (job.get('retry_at') or 0)-time.time())
    if issue['code'] == 'rate_limit':
        if not source_access.youtube.remaining():
            source_access.youtube.limited()
        delay = max(delay, source_access.youtube.remaining())
    update(job_id, status='queued', stage='retry_wait', progress=0, speed=None, eta=None,
           auto_attempts=attempts+1, recovery_pending=True, retry_at=time.time()+delay, error=None, last_error=message,
           error_code=issue['code'], next_action=issue['action'], recoverable=recoverable)
    deferred_downloads.defer(job_id, req, delay=delay)
    return True


def initialize_recovery():
    if not recovery_enabled():
        return
    for job in list(jobs.values()):
        guard = media_lock(job['id'])
        if not guard.acquire(blocking=False):
            continue
        try:
            job = jobs.get(job['id'])
            if not job or job['status'] != 'failed':
                continue
            req = DownloadRequest(url=job.get('url',''), format=job.get('format','mp4'), quality=job.get('quality','720'))
            validate_url(req.url)
            issue = recovery.classify(job.get('last_error') or job.get('error',''))
            schedule_recovery(job['id'], req, issue, job.get('error',''), job.get('recoverable',False))
        except (HTTPException, OSError, storage.sqlite3.Error, ValueError):
            continue
        finally:
            guard.release()


@app.get('/api/recovery')
def recovery_status():
    with lock:
        held = [j.copy() for j in jobs.values() if j.get('stage') in ('retry_wait','source_wait') and j['status']=='queued']
        failed = [j.copy() for j in jobs.values() if j['status']=='failed']
    return {'enabled':recovery_enabled(), 'max_attempts':recovery.MAX_RETRIES, 'waiting':len(held),
            'needs_action':len(failed), 'next_retry_at':min(((j.get('retry_at') or time.time()) for j in held), default=None),
            'items':[{'id':j['id'],'title':j.get('title','Video'),'status':j['status'], 'stage':j.get('stage'),
                      'attempts':j.get('auto_attempts',0),'retry_at':j.get('retry_at'),
                      'reason':j.get('last_error') or j.get('error'),
                      'action':j.get('next_action') or recovery.classify(j.get('error',''))['action']} for j in held+failed]}


class RecoverySettings(BaseModel):
    enabled: bool


@app.post('/api/recovery/settings')
def set_recovery_settings(req: RecoverySettings):
    # Reuse atomic configuration writes without storing any credentials.
    import tempfile
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=DATA,delete=False) as handle:
            temporary = Path(handle.name)
            json.dump({'enabled':req.enabled},handle)
        temporary.replace(DATA/'recovery-settings.json')
    except OSError:
        raise HTTPException(503, 'Recovery settings could not be saved. Check free disk space and folder permissions.') from None
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    if req.enabled:
        initialize_recovery()
    else:
        for job in list(jobs.values()):
            guard = media_lock(job['id'])
            if not guard.acquire(blocking=False):
                continue
            try:
                job = jobs.get(job['id'])
                if job and job['status']=='queued' and job.get('stage')=='retry_wait':
                    deferred_downloads.discard(job['id'])
                    update(job['id'], status='failed', recovery_pending=False, auto_attempts=max(0,job.get('auto_attempts',0)-int(bool(job.get('recovery_pending')))), error=job.get('last_error') or 'Automatic recovery is paused. Retry manually when ready.')
            finally:
                guard.release()
    return recovery_status()


def pause_for_source(job_id, req, seconds):
    if not source_access.youtube.remaining():
        source_access.youtube.limited(seconds)
    update(job_id, status='queued', stage='retry_wait' if jobs[job_id].get('recovery_pending') else 'source_wait', retry_at=time.time() + seconds,
           progress=0, speed=None, eta=None, error=None)
    deferred_downloads.defer(job_id, req)


def resume_deferred(job_id, req):
    guard = media_lock(job_id)
    if not guard.acquire(blocking=False):
        deferred_downloads.defer(job_id, req, delay=1)
        return
    try:
        with lock:
            job = jobs.get(job_id)
            if not job or job['status'] != 'queued' or job.get('stage') not in ('source_wait','retry_wait'):
                return
        if job.get('stage') == 'retry_wait' and not recovery_enabled():
            update(job_id, status='failed', recovery_pending=False,
                   auto_attempts=max(0,job.get('auto_attempts',0)-int(bool(job.get('recovery_pending')))),
                   error=job.get('last_error') or 'Automatic recovery is paused. Retry manually when ready.')
            return
    finally:
        guard.release()
    try:
        submit_job(job_id, req)
    except (HTTPException, OSError, storage.sqlite3.Error, KeyError):
        pass  # A worker failure is visible, or a queued job was removed concurrently.


def run_download(job_id, req):
    # Deletion and worker startup share one guard: removed queued tasks never fetch.
    if job_id not in jobs:
        return
    guard = media_lock(job_id)
    with guard:
        if job_id in jobs:
            perform_download(job_id, req)


def perform_download(job_id, req):
    job = jobs.get(job_id)
    if job is None:
        return
    folder = None
    recovered = None
    verified = None
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
        job = jobs[job_id]
        if source_access.youtube_url(req.url) and not job.get('recoverable'):
            remaining = source_access.youtube.remaining()
            if remaining:
                pause_for_source(job_id, req, remaining)
                return
        if job.get('recovery_pending'):
            if not recovery_enabled():
                update(job_id, status='failed', recovery_pending=False, auto_attempts=max(0,job.get('auto_attempts',0)-1), error=job.get('last_error') or 'Automatic recovery is paused.')
                return
            update(job_id, recovery_pending=False)
        folder = destinations.work_folder(DATA, job)
        if job.get('recoverable'):
            planned = job.get('planned_filename')
            if job.get('output_dir') and planned:
                candidate = saved_path(job | {'filename': planned})
                if candidate and recoverable_media(candidate, req, job):
                    recovered = candidate
            recovered = recovered or recover_file(folder, req)
            if not recovered and job.get('legacy_recovery'):
                recovered = recover_file(DATA / job_id, req)
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
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            if job.get('output_dir'):
                raise destinations.DestinationError('The selected folder is not writable. Check the drive and folder permissions, then Retry.') from None
            raise
        if not recovered and shutil.disk_usage(folder).free < 256 * 1024**2:
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
        if not files and not recovered:
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
        verified = file
        if job.get('output_dir'):
            file = publish_original(job_id, file)
            verified = file
        preview_available = bool(cached_playable(file, media, **preview_kwargs(job)))
        duration = media.pop('duration', 0) or info.get('duration')
        update(job_id, status='completed', progress=100, completed_at=time.time(), completion_id=uuid.uuid4().hex, title=info.get('title', file.stem), filename=file.name, size=file.stat().st_size,
               thumbnail=info.get('thumbnail'), duration=duration, platform=info.get('extractor_key'), preview_available=preview_available,
               preview_status='ready' if preview_available else 'not_requested', preview_progress=100 if preview_available else 0, stage='ready', recoverable=False, legacy_recovery=False, retry_at=None, error=None, last_error=None, error_code=None, recovery_exhausted=False, next_action=None, **media)
        if job.get('output_dir'):
            shutil.rmtree(folder, ignore_errors=True)
            try:
                folder.parent.rmdir()
            except OSError:
                pass  # Other downloads may still be using this staging directory.
    except Exception as exc:
        message = str(exc) if isinstance(exc, destinations.DestinationError) else 'Not enough free disk space. Free space and retry.' if 'disk space' in str(exc).lower() else download_error(exc)
        if req.format in ('mp3', 'm4a', 'wav') and 'requested format' in str(exc).lower():
            message = 'No compatible audio stream is available. The source may contain video only; try MP4.'
        # Finish cleanup before Retry can see a failed job and start a new attempt.
        if folder is not None and not recovered and not verified:
            shutil.rmtree(folder, ignore_errors=True)
        issue = recovery.classify(exc)
        if schedule_recovery(job_id, req, issue, message, bool(recovered or verified)):
            return
        exhausted = issue['retryable'] and jobs[job_id].get('auto_attempts',0) >= issue['limit']
        action = 'Automatic attempt limit reached. Check source availability and local setup, then Retry manually.' if exhausted else 'Automatic recovery is paused. Enable it or Retry manually when ready.' if issue['retryable'] else issue['action']
        update(job_id, status='failed', error=message, recoverable=bool(recovered or verified),
               error_code=issue['code'], next_action=action, last_error=message, recovery_exhausted=exhausted)


def preview_kwargs(job):
    return {'cache_dir': DATA / job['id'] / '_preview'} if job.get('output_dir') else {}


def publish_original(job_id, file):
    job = jobs[job_id]
    root = Path(job['output_dir']).resolve()
    if file.parent.resolve() == root:
        return file
    target = None
    for _ in range(5):
        name = file.stem + ' [' + job_id[:8] + ('-' + uuid.uuid4().hex[:4] if _ else '') + ']' + file.suffix
        candidate = root / name
        try:
            with candidate.open('xb'):
                pass
            target = candidate
            break
        except FileExistsError:
            continue
        except OSError:
            raise destinations.DestinationError('The selected folder cannot be written. Reconnect the drive or check its permissions, then Retry.') from None
    if target is None:
        raise destinations.DestinationError('A unique output filename could not be reserved. Retry the download.')
    try:
        update(job_id, planned_filename=target.name, stage='saving')
        # New downloads are staged on this same filesystem: rename, not a second copy.
        try:
            file.replace(target)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            # Only legacy recovery/mounted filesystem boundaries need a move via copy.
            shutil.copy2(file, target)
            file.unlink()
    except Exception:
        if target.is_file() and (file.exists() or target.stat().st_size == 0):
            target.unlink()
        raise
    return target


def recoverable_media(file, req, job):
    media = describe_media(file)
    expected = job.get('duration') or 0
    if media.get('duration', 0) < max(0.1, expected - 2):
        return False
    return bool(media.get('video_codec') if req.format in ('mp4', 'mkv', 'webm') else media.get('has_audio'))


def recover_file(folder, req):
    for file in sorted(folder.glob('*.' + req.format), key=lambda p: p.stat().st_size, reverse=True):
        if re.search(r'\.f\d+|\.scaled$', file.stem):
            continue
        if recoverable_media(file, req, jobs[folder.name]):
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
    destination = destination_snapshot()
    with lock:
        if sum(j['status'] in ('queued', 'downloading', 'processing') for j in jobs.values()) >= MAX_ACTIVE:
            raise HTTPException(429, 'Queue is full. Wait for a download to finish.')
        if len(jobs) >= MAX_HISTORY:
            raise HTTPException(429, 'History is full. Remove older downloads first.')
        job_id = uuid.uuid4().hex
        job = {'id': job_id, 'url': req.url, 'title': 'Preparing download', 'format': req.format, 'quality': req.quality, 'status': 'queued', 'progress': 0, 'created': time.time()}
        job.update(destination)
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
    return queue_batch(req.urls, req)


def queue_batch(input_urls, req, active_limit=MAX_ACTIVE, metadata=None):
    urls = list(dict.fromkeys(url.strip() for url in input_urls))
    requests = [DownloadRequest(url=url, format=req.format, quality=req.quality) for url in urls]
    for item in requests:
        validate_url(item.url)
        if source_access.youtube_url(item.url) and (urlparse(item.url).path == '/playlist' or urlparse(item.url).path.startswith(('/@', '/channel/', '/user/', '/c/'))):
            raise HTTPException(400, 'Use the YouTube playlist panel for playlists. Bulk download accepts individual video links, not channels.')
        check_source_cooldown(item.url)
    if req.format not in ('mp4', 'webm', 'mkv', 'mp3', 'm4a', 'wav') or req.quality not in ('2160', '1440', '1080', '720', '480', '360'):
        raise HTTPException(400, 'Unsupported format or quality.')
    if not all(shutil.which(tool) for tool in ('ffmpeg', 'ffprobe')):
        raise HTTPException(503, 'Install FFmpeg and FFprobe to enable downloads.')
    destination = destination_snapshot()
    pending = []
    with lock:
        available = min(active_limit - sum(j['status'] in ('queued', 'downloading', 'processing') for j in jobs.values()), MAX_HISTORY - len(jobs))
        if len(requests) > available:
            raise HTTPException(429, f'Only {max(0, available)} queue/history slots are available. Submit fewer links or remove old history.')
        for item in requests:
            job = {'id': uuid.uuid4().hex, 'url': item.url, 'title': 'Queued video', 'format': item.format, 'quality': item.quality, 'created': time.time(), 'status': 'queued', 'stage': 'queued', 'progress': 0}
            job.update(destination)
            cached = analysis_cache.get(item.url)
            if cached and time.monotonic() - cached[0] < 900:
                job.update(cached[1])
            if metadata:
                job.update(metadata.get(item.url, {}))
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
    return {'jobs': [job.copy() for job in pending], 'added': len(pending), 'duplicates_removed': len(input_urls) - len(urls), 'submission_failures': failures}


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
        if not pending.get('output_dir'):
            pending.update(destination_snapshot())
            if pending.get('output_dir') and pending.get('recoverable'):
                pending['legacy_recovery'] = True
        pending.update(status='queued', stage='queued', auto_attempts=0, recovery_pending=False, recovery_exhausted=False, last_error=None, error_code=None, next_action=None, retry_at=None, progress=0, speed=None, eta=None)
        save_queued(pending)
        jobs[job_id] = pending
        result = pending.copy()
    submit_job(job_id, req)
    return result


@app.get('/api/jobs')
def list_jobs():
    counts = storage.video_counts(DATA)
    with lock:
        for job in jobs.values():
            if job['status'] == 'completed' and not saved_path(job):
                changed = job | {'status': 'failed', 'error': 'The saved file is missing. Use Retry to download it again.', 'preview_available': False}
                save_queued(changed)
                job.update(changed)
        return [j.copy() | {'previous_downloads': counts.get(storage.video_key(j.get('url', '')), 0), 'source_platform': storage.platform_name(j.get('url', ''))} for j in sorted(jobs.values(), key=lambda j: j['created'], reverse=True)]


@app.get('/api/stats')
def statistics():
    return storage.statistics(DATA)


@app.post('/api/jobs/{job_id}/redownload', status_code=202)
def redownload(job_id: str):
    with lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, 'Download not found.')
        if job['status'] not in ('completed', 'failed'):
            raise HTTPException(409, 'This video is already queued or downloading.')
        req = DownloadRequest(url=job['url'], format=job['format'], quality=job['quality'])
    return download(req)


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
    folder = Path(job['output_dir']).resolve() if job.get('output_dir') else (DATA / job['id']).resolve()
    file = (folder / job['filename']).resolve()
    return file if file.is_relative_to(folder) and file.is_file() else None


def media_lock(job_id):
    with lock:
        if job_id not in jobs:
            raise HTTPException(404, 'Download not found.')
        return preview_locks.setdefault(job_id, threading.Lock())


@app.get('/api/jobs/{job_id}/thumbnail')
def get_thumbnail(job_id: str):
    file, job = completed_file(job_id)
    cached = (preview_kwargs(job).get('cache_dir') or file.parent / '_preview') / 'cover.jpg'
    if cached.is_file():
        return FileResponse(cached, media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=86400'})
    if jobs[job_id].get('preview_status') == 'processing':
        raise HTTPException(404, 'Thumbnail is being prepared.')
    with media_lock(job_id):
        file, job = completed_file(job_id)
        if job.get('format') not in ('mp4', 'mkv', 'webm'):
            raise HTTPException(404, 'No video thumbnail is available.')
        try:
            thumbnail = make_thumbnail(file, **preview_kwargs(job))
            return FileResponse(thumbnail, media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=86400'})
        except (OSError, subprocess.SubprocessError):
            raise HTTPException(404, 'Thumbnail is unavailable.')


@app.get('/api/jobs/{job_id}/stream')
def stream_file(job_id: str, original: bool = False):
    file, job = completed_file(job_id)
    types = {'.mp4': 'video/mp4', '.webm': 'video/webm', '.mkv': 'video/x-matroska', '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.wav': 'audio/wav'}
    if original:
        return FileResponse(file, media_type=types.get(file.suffix, 'application/octet-stream'), headers={'Cache-Control': 'private, no-cache'})
    preview = cached_playable(file, job if 'video_pixel_format' in job else describe_media(file), **preview_kwargs(job))
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
                playable_file(file, media, progress=progress, **preview_kwargs(job))
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
    if cached_playable(file, media, **preview_kwargs(job)):
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


@app.post('/api/jobs/{job_id}/open-folder')
def open_download_folder(job_id: str, request: Request):
    require_local_desktop(request)
    file, _ = completed_file(job_id)
    try:
        destinations.open_folder(file.parent)
    except (destinations.DestinationError, OSError):
        raise HTTPException(503, 'The file manager could not open. Open the displayed download folder path manually.')
    return {'opened': True}


@app.delete('/api/jobs/{job_id}')
def remove_job(job_id: str):
    guard = media_lock(job_id)
    if not guard.acquire(blocking=False):
        raise HTTPException(409, 'This download or preview is running. Wait for it to finish before removing it.')
    try:
        with lock:
            job = jobs.get(job_id)
            if not job:
                raise HTTPException(404, 'Download not found.')
            if job['status'] not in ('completed', 'failed', 'queued'):
                raise HTTPException(409, 'Wait for the download to finish before removing it.')
            try:
                storage.delete(DATA, job_id)
            except (OSError, storage.sqlite3.Error):
                raise HTTPException(503, 'History could not be updated. Check free disk space and folder permissions.') from None
            del jobs[job_id]
            preview_locks.pop(job_id, None)
        deferred_downloads.discard(job_id)
        shutil.rmtree(DATA / job_id, ignore_errors=True)
        if job.get('output_dir'):
            try:
                shutil.rmtree(destinations.work_folder(DATA, job), ignore_errors=True)
            except destinations.DestinationError:
                pass
    finally:
        guard.release()
    return {'removed': True}


@app.post('/api/jobs/remove')
def remove_jobs(req: RemoveJobsRequest):
    removed, failed = [], []
    for job_id in dict.fromkeys(req.job_ids):
        try:
            remove_job(job_id)
            removed.append(job_id)
        except HTTPException as exc:
            failed.append({'id': job_id, 'status': exc.status_code, 'detail': exc.detail})
    return {'removed': removed, 'failed': failed}
