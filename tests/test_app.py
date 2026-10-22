import threading
import subprocess
import os
import tempfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
# Import-time recovery must never touch the developer's real history database.
with tempfile.TemporaryDirectory(prefix='streamvault-test-init-') as initial_data:
    with patch.dict(os.environ, {'DOWNLOAD_DIR': initial_data}):
        from app import main

client = TestClient(main.app)

@pytest.mark.parametrize('url', ['http://youtube.com/watch?v=x', 'https://127.0.0.1/a', 'https://youtube.com.evil.com/a', 'https://user:pass@youtube.com/a', 'https://youtube.com:8080/a', 'https://youtube.com:bad/a', 'https://[invalid/a'])
def test_rejects_unsupported_urls(url):
    assert client.post('/api/analyze', json={'url': url}).status_code == 400


def test_dashboard_and_health():
    assert 'AAASoft' in client.get('/').text
    assert client.get('/static/app.js').status_code == 200
    logo = client.get('/static/brand/aaasoft-mark.png')
    assert logo.status_code == 200
    assert logo.content.startswith(b'\x89PNG\r\n\x1a\n')
    assert client.get('/api/health').json()['ffmpeg']


def test_analysis_normalizes_formats(monkeypatch):
    monkeypatch.setattr(main.yt_dlp.YoutubeDL, 'extract_info', lambda *a, **k: {'title': '<unsafe>', 'formats': [{'height': 720, 'vcodec': 'h264'}, {'height': 1080, 'vcodec': 'h264'}, {'height': 720, 'vcodec': 'h264'}]})
    r = client.post('/api/analyze', json={'url': 'https://youtu.be/test'})
    assert r.status_code == 200
    assert r.json()['qualities'] == [1080, 720]
    assert r.json()['title'] == '<unsafe>'


def test_invalid_format_and_missing_file():
    assert client.post('/api/download', json={'url': 'https://youtu.be/test', 'format': 'exe'}).status_code == 400
    assert client.get('/api/jobs/missing/file').status_code == 404


def test_missing_runtime_is_reported(monkeypatch):
    monkeypatch.setattr(main.shutil, 'which', lambda name: '/usr/bin/ffmpeg' if name == 'ffmpeg' else None)
    result = client.get('/api/health').json()
    assert result['ffmpeg'] is True
    assert result['js_runtimes'] == []


def test_source_403_is_actionable_and_does_not_expose_urls(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path)
    def rejected(*args, **kwargs):
        raise main.yt_dlp.utils.DownloadError('HTTP Error 403: Forbidden https://media.example/video?token=private')
    monkeypatch.setattr(main.yt_dlp.YoutubeDL, 'extract_info', rejected)
    job_id = 'test_rejected'
    main.jobs[job_id] = {'id': job_id, 'status': 'queued', 'created': 0}
    try:
        main.run_download(job_id, main.DownloadRequest(url='https://youtu.be/test'))
        job = main.jobs[job_id]
        assert job['status'] == 'failed'
        assert 'HTTP 403' in job['error']
        assert 'JavaScript runtime' in job['error']
        assert 'private' not in job['error']
        assert not (tmp_path / job_id).exists()
        response = client.post('/api/analyze', json={'url': 'https://youtu.be/test'})
        assert response.status_code == 422
        assert 'HTTP 403' in response.json()['detail']
    finally:
        main.jobs.pop(job_id, None)


@pytest.mark.parametrize('output', ['mp4', 'webm', 'mkv', 'mp3', 'm4a', 'wav'])
@pytest.mark.parametrize('direct', [False, True])
def test_real_download_and_conversion(tmp_path, monkeypatch, output, direct):
    # A tiny local fixture tests actual yt-dlp transfer and FFmpeg conversion without relying on platform access.
    source = tmp_path / 'fixture.mp4'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=purple:s=320x240:d=1', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(source)], check=True)
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    storage = tmp_path / 'downloads'
    storage.mkdir()
    monkeypatch.setattr(main, 'DATA', storage)
    job_id = 'test_' + output
    main.jobs[job_id] = {'id': job_id, 'status': 'queued', 'created': 0, 'format': output}
    chosen = tmp_path / 'Selected videos'
    if direct:
        chosen.mkdir()
        main.jobs[job_id]['output_dir'] = str(chosen)
    try:
        req = main.DownloadRequest(url=f'http://127.0.0.1:{server.server_port}/fixture.mp4', format=output, quality='720')
        main.run_download(job_id, req)
        assert main.jobs[job_id]['status'] == 'completed', main.jobs[job_id]
        assert main.jobs[job_id]['has_audio'] is True
        media_file = main.saved_path(main.jobs[job_id])
        if direct:
            assert media_file.parent == chosen
            assert not list((storage / job_id).glob('*.' + output))
            assert not (chosen / '.aaasoft-work' / job_id).exists()
        assert main.storage.statistics(storage)['total'] == 1
        response = client.get(f'/api/jobs/{job_id}/file')
        assert response.status_code == 200
        assert len(response.content) > 1000
        assert client.post(f'/api/jobs/{job_id}/preview').status_code == 202
        stream = client.get(f'/api/jobs/{job_id}/stream', headers={'Range': 'bytes=0-99'})
        assert stream.status_code == 206
        assert len(stream.content) == 100
        assert stream.headers['content-range'].startswith('bytes 0-99/')
        assert 'attachment' not in stream.headers.get('content-disposition', '')
        assert main.jobs[job_id]['preview_available'] is True
        if output in ('mp4', 'webm', 'mkv'):
            assert stream.headers['content-type'] == 'video/mp4'
            preview = main.playable_file(media_file, main.jobs[job_id], **main.preview_kwargs(main.jobs[job_id]))
            assert main.describe_media(preview)['video_codec'] == 'h264'
            poster = client.get(f'/api/jobs/{job_id}/thumbnail')
            assert poster.status_code == 200
            assert poster.headers['content-type'] == 'image/jpeg'
            assert poster.content.startswith(b'\xff\xd8')
            # Pre-update downloads gain a local thumbnail without re-downloading the source.
            (storage / job_id / '_preview' / 'cover.jpg').unlink()
            assert client.get(f'/api/jobs/{job_id}/thumbnail').status_code == 200
        else:
            assert stream.headers['content-type'].startswith('audio/')
            assert client.get(f'/api/jobs/{job_id}/thumbnail').status_code == 404
        monkeypatch.setenv('STREAMVAULT_PASSWORD', 'test-owner-password')
        assert client.get(f'/api/jobs/{job_id}/stream').status_code == 401
        assert client.get(f'/api/jobs/{job_id}/thumbnail').status_code == 401
        monkeypatch.delenv('STREAMVAULT_PASSWORD')
        assert client.delete(f'/api/jobs/{job_id}').status_code == 200
        assert client.get(f'/api/jobs/{job_id}/stream').status_code == 404
        assert not (storage / job_id).exists()
        if direct:
            assert media_file.is_file()  # Library removal never deletes the user's direct-save original.
        assert main.storage.statistics(storage)['total'] == 1
    finally:
        main.jobs.pop(job_id, None)
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
def isolate_history(tmp_path, monkeypatch):
    class ImmediatePreview:
        def submit(self, function, *args):
            function(*args)
        def shutdown(self, **kwargs):
            pass
    monkeypatch.setattr(main, 'preview_pool', ImmediatePreview())
    monkeypatch.setattr(main, 'DATA', tmp_path)
    monkeypatch.setattr(main, 'jobs', {})
    monkeypatch.setattr(main, 'analysis_cache', {})
    monkeypatch.setattr(main, 'playlist_cache', {})
    monkeypatch.setattr(main, 'playlist_loads', {})
    monkeypatch.setattr(main, 'playlist_pool', ImmediatePreview())
    monkeypatch.setattr(main, 'preview_locks', {})
    monkeypatch.setattr(main.source_access, 'youtube', main.source_access.YoutubeAccess())
    monkeypatch.delenv('STREAMVAULT_PASSWORD', raising=False)
    monkeypatch.delenv('STREAMVAULT_PUBLIC', raising=False)
    monkeypatch.delenv('STREAMVAULT_USERNAME', raising=False)
    monkeypatch.delenv('STREAMVAULT_SYSTEM_CERTS', raising=False)
    monkeypatch.delenv('STREAMVAULT_COOKIE_FILE', raising=False)
    monkeypatch.delenv('STREAMVAULT_DESKTOP', raising=False)
    deferred = main.DeferredDownloads(lambda: main.source_access.youtube.remaining(), lambda job_id, req: main.resume_deferred(job_id, req), delay_for=lambda job_id, req: main.recovery_delay(job_id, req))
    monkeypatch.setattr(main, 'deferred_downloads', deferred)
    yield
    deferred.stop()


def test_history_survives_restart_and_interrupted_jobs_can_retry(tmp_path, monkeypatch):
    completed = {'id': 'completed', 'url': 'https://youtu.be/test', 'title': 'Saved video', 'format': 'mp4', 'quality': '720', 'status': 'completed', 'created': 0, 'filename': 'video.mp4', 'progress': 100}
    folder = tmp_path / completed['id']
    folder.mkdir()
    (folder / completed['filename']).write_bytes(b'saved-media')
    interrupted = completed | {'id': 'interrupted', 'status': 'downloading', 'progress': 45}
    main.storage.save(tmp_path, completed)
    main.storage.save(tmp_path, interrupted)
    restored = main.storage.restore(tmp_path)
    monkeypatch.setattr(main, 'jobs', restored)
    assert client.get('/api/jobs/completed/file').content == b'saved-media'
    assert restored['interrupted']['status'] == 'failed'
    assert 'restart' in restored['interrupted']['error']
    submitted = []
    monkeypatch.setattr(main.pool, 'submit', lambda *args: submitted.append(args))
    response = client.post('/api/jobs/interrupted/retry')
    assert response.status_code == 202
    assert response.json()['status'] == 'queued'
    assert 'error' not in response.json()
    assert submitted[0][1] == 'interrupted'
    assert client.post('/api/jobs/interrupted/retry').status_code == 409
    assert client.delete('/api/jobs/completed').status_code == 200
    assert 'completed' not in main.storage.restore(tmp_path)
    assert not folder.exists()


def test_protected_routes_and_static_files(monkeypatch):
    monkeypatch.setenv('STREAMVAULT_PASSWORD', 'test-password')
    monkeypatch.setenv('STREAMVAULT_USERNAME', 'owner')
    for path in ['/', '/api/jobs', '/static/app.js', '/docs']:
        assert client.get(path).status_code == 401
        assert client.get(path, auth=('owner', 'test-password')).status_code == 200
    assert client.get('/api/jobs', auth=('owner', 'wrong')).status_code == 401
    assert client.post('/api/jobs/missing/retry').status_code == 401


def test_public_mode_fails_closed_without_password(monkeypatch):
    monkeypatch.setenv('STREAMVAULT_PUBLIC', 'true')
    assert client.get('/').status_code == 503
    assert client.get('/api/jobs').status_code == 503


def test_cross_site_mutations_are_rejected():
    assert client.post('/api/jobs/missing/retry', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/jobs/missing/retry', headers={'Origin': 'http://testserver'}).status_code == 404


def test_low_disk_space_returns_retriable_failure(tmp_path, monkeypatch):
    from collections import namedtuple
    usage = namedtuple('Usage', 'total used free')
    monkeypatch.setattr(main.shutil, 'disk_usage', lambda path: usage(1024, 1000, 24))
    main.jobs['low-disk'] = {'id': 'low-disk', 'status': 'queued', 'created': 0}
    main.run_download('low-disk', main.DownloadRequest(url='https://youtu.be/test'))
    assert main.jobs['low-disk']['status'] == 'failed'
    assert 'disk space' in main.jobs['low-disk']['error']
    assert main.storage.restore(tmp_path)['low-disk']['status'] == 'failed'


def test_login_failures_are_throttled(monkeypatch):
    from app.auth import AccessControl
    from fastapi import FastAPI
    protected = FastAPI()
    protected.add_middleware(AccessControl)
    @protected.get('/')
    def index():
        return {'ok': True}
    monkeypatch.setenv('STREAMVAULT_PASSWORD', 'test-password')
    session = TestClient(protected)
    for _ in range(12):
        assert session.get('/').status_code == 401  # Browser challenges should not lock out the owner.
    for _ in range(10):
        assert session.get('/', auth=('admin', 'wrong')).status_code == 401
    response = session.get('/', auth=('admin', 'wrong'))
    assert response.status_code == 429
    assert response.headers['Retry-After'] == '60'


@pytest.mark.parametrize(('message','expected'), [
    ('This video is DRM protected', 'DRM-protected'),
    ('Video is not available in your country', 'region'),
    ('Sign in to confirm your age', 'age verification'),
    ('Private video', 'private access'),
    ('This video has been removed', 'removed'),
    ('HTTP Error 429: Too Many Requests', 'HTTP 429'),
    ('Unsupported URL: https://threads.net/example', 'not supported'),
])
def test_source_restrictions_are_explained(message, expected):
    assert expected in main.download_error(RuntimeError(message))


@pytest.mark.parametrize('known_height', [None, 480])
def test_video_only_with_unknown_resolution_downloads_and_respects_cap(tmp_path, monkeypatch, known_height):
    source = tmp_path / 'silent.mp4'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=purple:s=320x480:d=1', '-c:v', 'libx264', str(source)], check=True)
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}/silent.mp4'
    def extract(self, *args, **kwargs):
        return self.process_ie_result({'id': 'silent', 'title': 'Silent source', 'formats': [{'format_id': 'video-only', 'url': url, 'ext': 'mp4', 'vcodec': 'h264', 'acodec': 'none', 'height': known_height}]}, download=True)
    monkeypatch.setattr(main.yt_dlp.YoutubeDL, 'extract_info', extract)
    main.jobs['silent'] = {'id': 'silent', 'status': 'queued', 'created': 0}
    try:
        main.run_download('silent', main.DownloadRequest(url=url, quality='360'))
        job = main.jobs['silent']
        assert job['status'] == 'completed', job
        assert job['actual_height'] == 360
        assert job['has_audio'] is False
        assert client.get('/api/jobs/silent/file').status_code == 200
    finally:
        server.shutdown()
        server.server_close()


def test_system_trust_does_not_disable_certificate_validation(monkeypatch):
    import ssl
    monkeypatch.setenv('STREAMVAULT_SYSTEM_CERTS', '1')
    settings = main.options()
    with main.yt_dlp.YoutubeDL(settings) as downloader:
        handlers = list(downloader._request_director.handlers.values())
        assert handlers
        assert all(handler.verify for handler in handlers)
        native_handlers = [handler for handler in handlers if hasattr(handler, '_make_sslcontext')]
        assert native_handlers
        assert all(handler._make_sslcontext().verify_mode == ssl.CERT_REQUIRED for handler in native_handlers)


def threads_page(post, recommendation=None):
    import json
    data = {'require': [{'data': {'media': post}}], 'recommendation': recommendation}
    return '<script data-test="fixture" type="application/json">' + json.dumps(data) + '</script>'


def test_threads_share_extracts_only_target_quoted_video(monkeypatch):
    from types import SimpleNamespace
    from app.threads import ThreadsIE
    post = {'code': 'Target123', 'video_versions': [], 'caption': {'text': '<Public post>'}, 'user': {'username': 'owner'},
            'text_post_app_info': {'share_info': {'quoted_attachment_post': {
                'code': 'Original123', 'has_audio': True,
                'video_versions': [{'url': 'https://video.cdninstagram.com/quoted.mp4'}]}}}}
    recommendation = {'code': 'Other123', 'video_versions': [{'url': 'https://video.cdninstagram.com/wrong.mp4'}]}
    page = threads_page(post, recommendation)
    monkeypatch.setattr(ThreadsIE, '_download_webpage_handle', lambda *a, **k: (page, SimpleNamespace(url='https://www.threads.com/@owner/post/Target123?share=1')))
    result = main.analyze(main.VideoRequest(url='https://www.threads.com/share/Share123/'))
    assert result['platform'] == 'Threads'
    assert result['title'] == '<Public post>'
    with main.video_downloader(main.options()) as downloader:
        info = downloader.extract_info('https://www.threads.com/share/Share123/', download=False)
    assert info['id'] == 'Target123'
    assert info['formats'][0]['url'] == 'https://video.cdninstagram.com/quoted.mp4'
    assert info['uploader'] == 'owner'


@pytest.mark.parametrize('host', ['threads.com', 'threads.net'])
def test_threads_direct_post_is_registered(monkeypatch, host):
    from types import SimpleNamespace
    from app.threads import ThreadsIE
    url = f'https://www.{host}/@owner/post/Direct123'
    post = {'code': 'Direct123', 'video_versions': [{'url': 'https://video.fbcdn.net/direct.mp4'}], 'caption': {'text': 'Direct video'}}
    monkeypatch.setattr(ThreadsIE, '_download_webpage_handle', lambda *a, **k: (threads_page(post), SimpleNamespace(url=url)))
    result = main.analyze(main.VideoRequest(url=url))
    assert result['platform'] == 'Threads'
    assert result['title'] == 'Direct video'


@pytest.mark.parametrize('case,expected', [
    ('missing', 'sign-in'), ('text', 'no publicly available video'),
    ('multiple', 'single video'), ('bad_media', 'video files are unavailable'),
    ('bad_redirect', 'public post'),
])
def test_threads_rejects_unavailable_ambiguous_or_untrusted_sources(monkeypatch, case, expected):
    from types import SimpleNamespace
    from app.threads import ThreadsIE
    post = {'code': 'Target123', 'video_versions': []}
    url = 'https://www.threads.com/@owner/post/Target123'
    if case == 'missing':
        post['code'] = 'Recommendation123'
        post['video_versions'] = [{'url': 'https://video.fbcdn.net/unrelated.mp4'}]
    if case == 'multiple':
        post['carousel_media'] = [{'video_versions': [{'url': 'https://video.fbcdn.net/video.mp4'}]} for _ in range(2)]
    if case == 'bad_media':
        post['video_versions'] = [{'url': 'https://127.0.0.1/private.mp4'}]
    if case == 'bad_redirect':
        url = 'https://example.org/@owner/post/Target123'
    monkeypatch.setattr(ThreadsIE, '_download_webpage_handle', lambda *a, **k: (threads_page(post), SimpleNamespace(url=url)))
    result = client.post('/api/analyze', json={'url': 'https://www.threads.com/share/Share123/'})
    assert result.status_code == 422
    assert expected in result.json()['detail']


@pytest.mark.parametrize('url', ['http://video.fbcdn.net/a.mp4', 'https://fbcdn.net.evil.com/a.mp4', 'https://user:pass@video.fbcdn.net/a.mp4', 'https://video.fbcdn.net:444/a.mp4', 'https://127.0.0.1/a.mp4'])
def test_threads_media_urls_must_use_trusted_https_cdn(url):
    from app.threads import public_media_url
    assert public_media_url(url) is False


def test_analyzed_thumbnail_is_retained_in_queued_history(monkeypatch):
    monkeypatch.setattr(main.yt_dlp.YoutubeDL, 'extract_info', lambda *a, **k: {'title': 'A saved moment', 'thumbnail': 'https://i.ytimg.com/fixture.jpg', 'duration': 42, 'extractor_key': 'Youtube', 'formats': []})
    monkeypatch.setattr(main.pool, 'submit', lambda *a, **k: None)
    url = 'https://youtu.be/fixture'
    assert client.post('/api/analyze', json={'url': url}).status_code == 200
    response = client.post('/api/download', json={'url': url})
    assert response.status_code == 202
    job = response.json()
    assert job['thumbnail'] == 'https://i.ytimg.com/fixture.jpg'
    assert job['title'] == 'A saved moment'
    assert main.storage.restore(main.DATA)[job['id']]['thumbnail'] == job['thumbnail']


def test_youtube_bot_verification_is_distinct_from_private_access():
    message = main.download_error(RuntimeError("Sign in to confirm you're not a bot. Use --cookies"))
    assert 'bot verification' in message
    assert 'private access' not in message
    assert 'HTTP 429' in main.download_error(RuntimeError("HTTP Error 429: Sign in to confirm you're not a bot"))


def test_youtube_429_warning_cools_analysis_download_and_retry(monkeypatch):
    calls = []
    def rejected(downloader, *args, **kwargs):
        calls.append(args)
        downloader.params['logger'].warning('[youtube] Unable to download webpage: HTTP Error 429: Too Many Requests')
        raise RuntimeError("Sign in to confirm you're not a bot")
    monkeypatch.setattr(main.yt_dlp.YoutubeDL, 'extract_info', rejected)
    url = 'https://youtu.be/blocked'
    response = client.post('/api/analyze', json={'url': url})
    assert response.status_code == 429
    assert 1 <= int(response.headers['retry-after']) <= 600
    assert 'HTTP 429' in response.json()['detail']
    assert client.post('/api/analyze', json={'url': url}).status_code == 429
    assert client.post('/api/download', json={'url': url}).status_code == 429
    main.jobs['blocked'] = {'id': 'blocked', 'status': 'failed', 'created': 0, 'url': url, 'format': 'mp4', 'quality': '720'}
    assert client.post('/api/jobs/blocked/retry').status_code == 429
    assert main.jobs['blocked']['status'] == 'failed'
    assert len(calls) == 1  # Blocked retries never contact the source again.
    assert client.get('/api/health').json()['youtube_cooldown_seconds'] > 0


def test_youtube_cooldown_expires_without_affecting_other_sources(monkeypatch):
    access = main.source_access.youtube
    with access.state:
        access.until = main.time.monotonic() - 1
    monkeypatch.setattr(main.yt_dlp.YoutubeDL, 'extract_info', lambda *a, **k: {'title': 'Available', 'formats': []})
    assert client.post('/api/analyze', json={'url': 'https://youtu.be/available'}).status_code == 200
    access.limited()
    assert client.post('/api/analyze', json={'url': 'https://www.tiktok.com/@public/video/123'}).status_code == 200


def test_youtube_parallel_analysis_reports_busy_without_blocking(monkeypatch):
    access = main.source_access.youtube
    access.metadata_slot.acquire()
    try:
        response = client.post('/api/analyze', json={'url': 'https://youtu.be/available'})
        assert response.status_code == 429
        assert 'already running' in response.json()['detail']
        assert response.headers['retry-after'] == '5'
    finally:
        access.metadata_slot.release()


def test_cookie_file_is_opt_in_local_and_never_returned_in_health(tmp_path, monkeypatch):
    cookie = tmp_path / 'private-cookies.txt'
    cookie.write_text('# Netscape HTTP Cookie File\n')
    assert 'cookiefile' not in main.source_access.authenticated_settings(main.options())
    monkeypatch.setenv('STREAMVAULT_COOKIE_FILE', str(cookie))
    assert main.source_access.authenticated_settings(main.options())['cookiefile'] == str(cookie)
    assert str(cookie) not in client.get('/api/health').text
    monkeypatch.setenv('STREAMVAULT_PUBLIC', 'true')
    with pytest.raises(RuntimeError, match='local desktop mode'):
        main.source_access.authenticated_settings(main.options())
    monkeypatch.delenv('STREAMVAULT_PUBLIC')
    cookie.unlink()
    with pytest.raises(RuntimeError, match='file is missing'):
        main.source_access.authenticated_settings(main.options())


def test_video_id_containing_429_does_not_trigger_source_cooldown():
    main.source_access.SourceLogger().warning('[youtube] Video 429abcd: Unable to fetch GVS PO Token')
    assert main.source_access.youtube.remaining() == 0


def failed_job(job_id='retry'):
    return {'id': job_id, 'url': 'https://youtu.be/fixture', 'title': 'A saved moment', 'format': 'mp4', 'quality': '720', 'created': 0, 'status': 'failed', 'error': 'Previous failure', 'filename': 'stale.mp4', 'size': 100, 'preview_available': True}


def test_executor_failure_does_not_leave_queued_job(monkeypatch):
    def rejected(*args): raise RuntimeError('executor unavailable')
    monkeypatch.setattr(main.pool, 'submit', rejected)
    response = client.post('/api/download', json={'url': 'https://youtu.be/fixture'})
    assert response.status_code == 503
    job = next(iter(main.jobs.values()))
    assert job['status'] == 'failed'
    assert main.storage.restore(main.DATA)[job['id']]['status'] == 'failed'


@pytest.mark.parametrize('retry', [False, True])
def test_failed_history_write_does_not_publish_pending_job(monkeypatch, retry):
    if retry: main.jobs['retry'] = failed_job()
    def rejected(*args): raise OSError('disk full private-path')
    monkeypatch.setattr(main.storage, 'save', rejected)
    response = client.post('/api/jobs/retry/retry') if retry else client.post('/api/download', json={'url': 'https://youtu.be/fixture'})
    assert response.status_code == 503
    assert 'private-path' not in response.text
    assert main.jobs['retry']['status'] == 'failed' if retry else not main.jobs


def test_retry_resets_stale_completion_metadata(monkeypatch):
    main.jobs['retry'] = failed_job()
    monkeypatch.setattr(main.pool, 'submit', lambda *args: None)
    response = client.post('/api/jobs/retry/retry')
    assert response.status_code == 202
    for key in ('error', 'filename', 'size', 'preview_available'):
        assert key not in response.json()


def test_worker_options_failure_becomes_failed_job(monkeypatch):
    main.jobs['retry'] = failed_job() | {'status': 'queued'}
    def rejected(): raise RuntimeError('setup failed')
    monkeypatch.setattr(main, 'options', rejected)
    main.run_download('retry', main.DownloadRequest(url='https://youtu.be/fixture'))
    assert main.jobs['retry']['status'] == 'failed'


def test_restart_leftovers_are_removed_and_failure_cleanup_precedes_retry(tmp_path, monkeypatch):
    main.jobs['retry'] = failed_job() | {'status': 'queued'}
    folder = tmp_path / 'retry'
    (folder / '_preview').mkdir(parents=True)
    (folder / 'stale.mp4').write_bytes(b'stale')
    def rejected(*args):
        assert not (folder / 'stale.mp4').exists()
        assert not (folder / '_preview').exists()
        (folder / 'partial.part').write_bytes(b'partial')
        raise RuntimeError('network failure')
    update = main.update
    def observe(job_id, **values):
        if values.get('status') == 'failed': assert not folder.exists()
        return update(job_id, **values)
    monkeypatch.setattr(main, 'extract_video', rejected)
    monkeypatch.setattr(main, 'update', observe)
    main.run_download('retry', main.DownloadRequest(url='https://youtu.be/fixture'))
    assert main.jobs['retry']['status'] == 'failed'


def test_corrupt_download_is_not_marked_completed(tmp_path, monkeypatch):
    main.jobs['retry'] = failed_job() | {'status': 'queued'}
    def corrupt(*args):
        (tmp_path / 'retry' / 'broken.mp4').write_bytes(b'not a media file')
        return {'title': 'Broken file'}
    monkeypatch.setattr(main, 'extract_video', corrupt)
    main.run_download('retry', main.DownloadRequest(url='https://youtu.be/fixture'))
    assert main.jobs['retry']['status'] == 'failed'
    assert 'not valid media' in main.jobs['retry']['error']
    assert not (tmp_path / 'retry').exists()


def test_missing_original_is_shown_as_retriable_history(monkeypatch):
    main.jobs['retry'] = failed_job() | {'status': 'completed'}
    response = client.get('/api/jobs')
    assert response.status_code == 200
    assert response.json()[0]['status'] == 'failed'
    assert 'saved file is missing' in response.json()[0]['error']
    monkeypatch.setattr(main.pool, 'submit', lambda *args: None)
    assert client.post('/api/jobs/retry/retry').status_code == 202


def test_history_file_cannot_escape_job_folder(tmp_path):
    (tmp_path / 'outside.txt').write_text('private-data')
    main.jobs['retry'] = failed_job() | {'status': 'completed', 'filename': '../outside.txt'}
    assert client.get('/api/jobs/retry/file').status_code == 404


def test_ffprobe_is_required_for_download_validation(monkeypatch):
    which = main.shutil.which
    monkeypatch.setattr(main.shutil, 'which', lambda tool: None if tool == 'ffprobe' else which(tool))
    assert client.get('/api/health').json()['ffprobe'] is False
    assert client.post('/api/download', json={'url': 'https://youtu.be/fixture'}).status_code == 503


def test_h264_444_receives_browser_compatible_preview(tmp_path):
    original = tmp_path / 'original.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=s=160x120:d=1', '-c:v', 'libx264', '-pix_fmt', 'yuv444p', str(original)], check=True)
    before = original.read_bytes()
    media = main.describe_media(original)
    assert media['video_pixel_format'] == 'yuv444p'
    preview = main.playable_file(original, media)
    assert preview != original
    assert main.describe_media(preview)['video_pixel_format'] == 'yuv420p'
    assert original.read_bytes() == before


def test_unrelated_thumbnail_requests_are_not_serialized(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    started, release = threading.Event(), threading.Event()
    for job_id in ('slow', 'fast'):
        folder = tmp_path / job_id; folder.mkdir()
        (folder / 'stale.mp4').write_bytes(b'fixture')
        main.jobs[job_id] = failed_job(job_id) | {'status': 'completed'}
    def poster(file):
        if file.parent.name == 'slow':
            started.set()
            assert release.wait(5)
        return file.parent / 'poster.jpg'
    monkeypatch.setattr(main, 'make_thumbnail', poster)
    with ThreadPoolExecutor(max_workers=2) as workers:
        slow = workers.submit(main.get_thumbnail, 'slow')
        try:
            assert started.wait(2)
            fast = workers.submit(main.get_thumbnail, 'fast')
            assert fast.result(timeout=2).media_type == 'image/jpeg'
        finally:
            release.set()
        assert slow.result(timeout=2).media_type == 'image/jpeg'


def test_preview_timeout_keeps_original_available(tmp_path, monkeypatch):
    import shutil
    original = tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=s=160x120:d=1', '-c:v', 'libx264', '-pix_fmt', 'yuv444p', str(original)], check=True)
    main.jobs['retry'] = failed_job() | {'status': 'queued'}
    def extracted(*args):
        shutil.copyfile(original, tmp_path / 'retry' / 'download.mp4')
        return {'title': 'Saved original'}
    def failed_preview(*args): raise subprocess.TimeoutExpired('ffmpeg', 300)
    monkeypatch.setattr(main, 'extract_video', extracted)
    monkeypatch.setattr(main, 'playable_file', failed_preview)
    main.run_download('retry', main.DownloadRequest(url='https://youtu.be/fixture'))
    assert client.post('/api/jobs/retry/preview').status_code == 202
    assert main.jobs['retry']['status'] == 'completed'
    assert main.jobs['retry']['preview_available'] is False
    assert client.get('/api/jobs/retry/file').content == original.read_bytes()


def test_recoverable_youtube_429_warning_allows_native_fallback(monkeypatch):
    def recovered(downloader, *args, **kwargs):
        downloader.params['logger'].warning('[youtube] Unable to download webpage: HTTP Error 429: Too Many Requests')
        return {'title': 'Recovered by native extractor', 'formats': [{'height': 720, 'vcodec': 'h264'}]}
    monkeypatch.setattr(main.yt_dlp.YoutubeDL, 'extract_info', recovered)
    response = client.post('/api/analyze', json={'url': 'https://youtu.be/recovered'})
    assert response.status_code == 200
    assert main.source_access.youtube.remaining() == 0
    monkeypatch.setattr(main.pool, 'submit', lambda *args: None)
    assert client.post('/api/download', json={'url': 'https://youtu.be/recovered'}).status_code == 202


def test_terminal_youtube_429_without_logger_starts_cooldown(monkeypatch):
    def failed(*args, **kwargs):
        raise main.yt_dlp.utils.DownloadError('HTTP Error 429: Too Many Requests')
    monkeypatch.setattr(main.yt_dlp.YoutubeDL, 'extract_info', failed)
    response = client.post('/api/analyze', json={'url': 'https://youtu.be/limited'})
    assert response.status_code == 429
    assert main.source_access.youtube.remaining() > 0


def test_incidental_youtube_429_does_not_hide_private_video_error(monkeypatch):
    def failed(downloader, *args, **kwargs):
        downloader.params['logger'].warning('[youtube] Unable to download webpage: HTTP Error 429')
        raise main.yt_dlp.utils.DownloadError('Private video')
    monkeypatch.setattr(main.yt_dlp.YoutubeDL, 'extract_info', failed)
    response = client.post('/api/analyze', json={'url': 'https://youtu.be/private'})
    assert response.status_code == 422
    assert 'private access' in response.json()['detail']
    assert main.source_access.youtube.remaining() == 0


def test_recovered_youtube_error_log_does_not_enable_cooldown(monkeypatch):
    def recovered(downloader, *args, **kwargs):
        downloader.params['logger'].error('[youtube] HTTP Error 429')
        return {'title': 'Recovered', 'formats': []}
    monkeypatch.setattr(main.yt_dlp.YoutubeDL, 'extract_info', recovered)
    assert client.post('/api/analyze', json={'url': 'https://youtu.be/recovered'}).status_code == 200
    assert main.source_access.youtube.remaining() == 0


def test_youtube_warning_then_real_transfer_can_complete(tmp_path, monkeypatch):
    source = tmp_path / 'fixture.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=s=160x120:d=1', '-c:v', 'libx264', str(source)], check=True)
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    def recovered(downloader, *args, **kwargs):
        downloader.params['logger'].warning('[youtube] Unable to download webpage: HTTP Error 429')
        return downloader.process_ie_result({'id': 'recovered', 'title': 'Recovered transfer', 'formats': [{'format_id': 'video', 'url': f'http://127.0.0.1:{server.server_port}/fixture.mp4', 'ext': 'mp4', 'vcodec': 'h264', 'acodec': 'none', 'height': 120}]}, download=True)
    monkeypatch.setattr(main.yt_dlp.YoutubeDL, 'extract_info', recovered)
    main.jobs['recovered'] = {'id': 'recovered', 'status': 'queued', 'created': 0, 'format': 'mp4'}
    try:
        main.run_download('recovered', main.DownloadRequest(url='https://youtu.be/recovered', quality='360'))
        assert main.jobs['recovered']['status'] == 'completed', main.jobs['recovered']
        assert client.get('/api/jobs/recovered/file').status_code == 200
        assert main.source_access.youtube.remaining() == 0
    finally:
        server.shutdown();server.server_close()


def test_bulk_queue_accepts_multiple_links_and_removes_duplicates(monkeypatch):
    submitted = []
    monkeypatch.setattr(main.pool, 'submit', lambda *args: submitted.append(args))
    urls = ['https://youtu.be/one', 'https://youtu.be/two', 'https://youtu.be/one']
    response = client.post('/api/download/bulk', json={'urls': urls, 'quality': '720'})
    assert response.status_code == 202
    assert response.json()['added'] == 2
    assert response.json()['duplicates_removed'] == 1
    assert len(submitted) == 2
    assert len(main.storage.restore(main.DATA)) == 2
    assert {job['url'] for job in main.jobs.values()} == set(urls)


@pytest.mark.parametrize('payload,status', [
    ({'urls':['https://youtu.be/one','http://localhost/private']},400),
    ({'urls':['https://youtu.be/one'],'format':'exe'},400),
    ({'urls':['https://youtu.be/one']*51},422),
])
def test_bulk_validation_is_atomic(payload, status, monkeypatch):
    monkeypatch.setattr(main.pool, 'submit', lambda *args: pytest.fail('Invalid batch was submitted'))
    assert client.post('/api/download/bulk', json=payload).status_code == status
    assert not main.jobs
    assert not main.storage.restore(main.DATA)


def test_bulk_queue_checks_capacity_before_adding_anything(monkeypatch):
    main.jobs.update({str(i):{'id':str(i),'status':'queued','created':i} for i in range(49)})
    response = client.post('/api/download/bulk', json={'urls':['https://youtu.be/one','https://youtu.be/two']})
    assert response.status_code == 429
    assert '1 queue/history slots' in response.json()['detail']
    assert len(main.jobs) == 49


def test_bulk_storage_failure_does_not_leave_queued_jobs(monkeypatch):
    def rejected(*args):raise main.storage.sqlite3.OperationalError('disk full')
    monkeypatch.setattr(main.storage, 'save_many', rejected)
    response = client.post('/api/download/bulk', json={'urls':['https://youtu.be/one','https://youtu.be/two']})
    assert response.status_code == 503
    assert not main.jobs


def test_bulk_worker_failure_marks_every_submission_failed(monkeypatch):
    def rejected(*args):raise RuntimeError('worker unavailable')
    monkeypatch.setattr(main.pool, 'submit', rejected)
    response = client.post('/api/download/bulk', json={'urls':['https://youtu.be/one','https://youtu.be/two']})
    assert response.status_code == 202
    assert response.json()['submission_failures'] == 2
    assert all(job['status']=='failed' for job in main.jobs.values())


def test_bulk_honors_youtube_cooldown_without_partial_batch():
    main.source_access.youtube.limited()
    response = client.post('/api/download/bulk', json={'urls':['https://www.tiktok.com/@public/video/1','https://youtu.be/limited']})
    assert response.status_code == 429
    assert not main.jobs


def test_background_preview_does_not_block_download_completion(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import shutil
    source = tmp_path/'source.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=s=160x120:d=1','-c:v','libx264','-pix_fmt','yuv444p',str(source)],check=True)
    def extracted(url, *args):
        shutil.copyfile(source, tmp_path/url.rsplit('/',1)[-1]/'video.mp4')
        return {'title':'Saved original'}
    started,release=threading.Event(),threading.Event()
    def slow_preview(file, media, progress=None):
        started.set();assert release.wait(8)
        return file
    monkeypatch.setattr(main,'extract_video',extracted)
    monkeypatch.setattr(main,'playable_file',slow_preview)
    for job_id in ('first','second','third'):
        main.jobs[job_id]=failed_job(job_id)|{'status':'queued'}
    with ThreadPoolExecutor(max_workers=1) as previews, ThreadPoolExecutor(max_workers=2) as downloads:
        monkeypatch.setattr(main,'preview_pool',previews)
        try:
            downloads.submit(main.run_download,'first',main.DownloadRequest(url='https://youtu.be/first')).result(timeout=3)
            assert main.jobs['first']['status']=='completed'
            assert main.jobs['first']['preview_status']=='not_requested'
            assert client.post('/api/jobs/first/preview').status_code==202
            assert started.wait(2)
            assert client.post('/api/jobs/first/preview').status_code==202
            for name in ('second','third'):
                downloads.submit(main.run_download,name,main.DownloadRequest(url='https://youtu.be/'+name)).result(timeout=3)
                assert main.jobs[name]['status']=='completed'
            assert client.get('/api/jobs/first/file').status_code==200
            assert client.get('/api/jobs/first/stream?original=true',headers={'Range':'bytes=0-99'}).status_code==206
        finally:release.set()


def test_interrupted_merged_file_is_recovered_without_source_download(tmp_path, monkeypatch):
    folder=tmp_path/'recover';folder.mkdir()
    original=folder/'merged.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=s=160x120:d=1','-c:v','libx264',str(original)],check=True)
    content=original.read_bytes()
    job=failed_job('recover')|{'status':'processing','duration':1}
    main.storage.save(tmp_path,job)
    monkeypatch.setattr(main,'jobs',main.storage.restore(tmp_path))
    assert main.jobs['recover']['recoverable'] is True
    monkeypatch.setattr(main.pool,'submit',lambda *args:None)
    monkeypatch.setattr(main,'extract_video',lambda *args:pytest.fail('Recovered file must not be downloaded again'))
    assert client.post('/api/jobs/recover/retry').status_code==202
    main.run_download('recover',main.DownloadRequest(url='https://youtu.be/recover'))
    assert main.jobs['recover']['status']=='completed'
    assert client.get('/api/jobs/recover/file').content==content


def test_restart_resets_preview_without_failing_completed_original(tmp_path):
    job=failed_job('finished')|{'status':'completed','preview_status':'processing','preview_progress':40}
    main.storage.save(tmp_path,job)
    restored=main.storage.restore(tmp_path)['finished']
    assert restored['status']=='completed'
    assert restored['preview_status']=='not_requested'
    assert restored['preview_progress']==0


def test_parallel_youtube_setting_allows_two_bounded_transfers(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(main.pool,'submit',lambda *args:None)
    response=client.post('/api/download/bulk',json={'urls':['https://youtu.be/first','https://youtu.be/second'],'youtube_concurrency':2})
    assert response.status_code==202
    barrier=threading.Barrier(2)
    def extracted(*args,**kwargs):
        barrier.wait(timeout=3)
        return {'title':'Parallel transfer'}
    monkeypatch.setattr(main.yt_dlp.YoutubeDL,'extract_info',extracted)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures=[workers.submit(main.extract_video,'https://youtu.be/'+name,True,main.options()) for name in ('first','second')]
        assert all(f.result(timeout=4)['title']=='Parallel transfer' for f in futures)
    assert main.source_access.youtube.slot.active==0


def test_bulk_parallel_setting_is_bounded():
    assert client.post('/api/download/bulk',json={'urls':['https://youtu.be/one'],'youtube_concurrency':3}).status_code==422
    assert not main.jobs


def test_bulk_history_transaction_rolls_back_all_rows(tmp_path):
    with main.storage.connect(tmp_path) as db:
        db.execute("CREATE TRIGGER reject_bad BEFORE INSERT ON jobs WHEN NEW.id='bad' BEGIN SELECT RAISE(ABORT,'reject'); END")
    with pytest.raises(main.storage.sqlite3.IntegrityError):
        main.storage.save_many(tmp_path,[failed_job('good'),failed_job('bad')])
    assert not main.storage.restore(tmp_path)


def test_recovery_ignores_separate_stream_components(tmp_path,monkeypatch):
    folder=tmp_path/'retry';folder.mkdir()
    fragment=folder/'video.f137.mp4';fragment.write_bytes(b'fragment'*30)
    merged=folder/'video.mp4';merged.write_bytes(b'merged')
    main.jobs['retry']=failed_job()|{'duration':10}
    monkeypatch.setattr(main,'describe_media',lambda file:{'duration':10,'video_codec':'h264','has_audio':True})
    assert main.recover_file(folder,main.DownloadRequest(url='https://youtu.be/fixture'))==merged


def test_recovery_rejects_truncated_duration(tmp_path,monkeypatch):
    folder=tmp_path/'retry';folder.mkdir();(folder/'video.mp4').write_bytes(b'partial')
    main.jobs['retry']=failed_job()|{'duration':100}
    monkeypatch.setattr(main,'describe_media',lambda file:{'duration':5,'video_codec':'h264','has_audio':True})
    assert main.recover_file(folder,main.DownloadRequest(url='https://youtu.be/fixture')) is None


@pytest.mark.parametrize('url',['https://www.youtube.com/playlist?list=PLfixture','https://www.youtube.com/@channel'])
def test_bulk_requires_individual_video_links(url):
    assert client.post('/api/download/bulk',json={'urls':[url]}).status_code==400
    assert not main.jobs


def test_shutdown_terminates_active_preview_process(monkeypatch):
    import sys,time
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(main,'previews_stopping',threading.Event())
    monkeypatch.setattr(main,'preview_processes',set())
    with ThreadPoolExecutor(max_workers=1) as workers:
        monkeypatch.setattr(main,'preview_pool',workers)
        future=workers.submit(main.encode_preview,[sys.executable,'-c','import time; time.sleep(60)'],None,None)
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            with main.preview_process_lock:
                if main.preview_processes:break
            time.sleep(.01)
        main.stop_previews()
        with pytest.raises(subprocess.CalledProcessError):future.result(timeout=3)
    assert not main.preview_processes


def test_removed_queued_preview_does_not_recreate_files(tmp_path,monkeypatch):
    folder=tmp_path/'finished';folder.mkdir();(folder/'stale.mp4').write_bytes(b'fixture')
    main.jobs['finished']=failed_job('finished')|{'status':'completed'}
    tasks=[]
    class Pending:
        def submit(self,*args):tasks.append(args)
    monkeypatch.setattr(main,'preview_pool',Pending())
    monkeypatch.setattr(main,'describe_media',lambda file:{'video_codec':'av1'})
    assert client.post('/api/jobs/finished/preview').status_code==202
    assert client.delete('/api/jobs/finished').status_code==200
    function,*args=tasks[0];function(*args)
    assert not folder.exists()
    assert 'finished' not in main.jobs


def playlist_entry(number, **extra):
    return {'id': f'clip{number:07d}', 'title': f'Playlist video {number}', 'duration': 60, **extra}


def load_test_playlist(monkeypatch, entries=None):
    monkeypatch.setattr(main, 'extract_video', lambda *args: {'_type': 'playlist', 'title': '<My playlist>', 'entries': entries if entries is not None else [playlist_entry(1), playlist_entry(2)]})
    response = client.post('/api/playlist/analyze', json={'url': 'https://www.youtube.com/playlist?list=PLfixture'})
    assert response.status_code == 200, response.text
    return response.json()


def test_playlist_normalizes_skips_unavailable_and_deduplicates(monkeypatch):
    entries = [playlist_entry(1), None, playlist_entry(2, title='[Private video]'), playlist_entry(1),
               playlist_entry(3, availability='needs_auth'), playlist_entry(4, is_live=True), playlist_entry(5)]
    result = load_test_playlist(monkeypatch, entries)
    assert result['title'] == '<My playlist>'
    assert result['unavailable'] == 4
    assert result['duplicates_removed'] == 1
    assert [v['position'] for v in result['videos']] == [1, 7]
    assert result['videos'][0]['url'] == 'https://www.youtube.com/watch?v=clip0000001'
    assert not main.jobs  # Loading the preview never starts a transfer.


@pytest.mark.parametrize('url', ['https://www.facebook.com/playlist?list=PLx', 'https://youtube.com/watch?v=clip0000001',
    'https://youtube.com/playlist?list=RDmix', 'https://youtube.com/playlist?list=../../bad',
    'https://youtube.com/playlist?list=PLone&list=PLtwo', 'http://youtube.com/playlist?list=PLx'])
def test_playlist_rejects_invalid_or_unbounded_inputs(monkeypatch, url):
    monkeypatch.setattr(main, 'extract_video', lambda *a: pytest.fail('Invalid URL contacted source'))
    assert client.post('/api/playlist/analyze', json={'url': url}).status_code == 400


def test_playlist_canonicalizes_watch_link_and_keeps_lazy_pages_inside_source_gate(monkeypatch):
    def extracted(downloader, url, download):
        assert url == 'https://www.youtube.com/playlist?list=PLfixture'
        assert download is False
        assert downloader.params['noplaylist'] is False
        assert downloader.params['extract_flat'] == 'in_playlist'
        assert downloader.params['playlistend'] == 1001
        def pages():
            assert main.source_access.youtube.metadata_slot.active == 1
            yield playlist_entry(1)
            assert main.source_access.youtube.metadata_slot.active == 1
            yield playlist_entry(2)
        return {'_type': 'playlist', 'entries': pages()}
    monkeypatch.setattr(main.yt_dlp.YoutubeDL, 'extract_info', extracted)
    result = client.post('/api/playlist/analyze', json={'url': 'https://youtube.com/watch?v=clip0000001&list=PLfixture&index=3'})
    assert result.status_code == 200, result.text
    assert len(result.json()['videos']) == 2
    assert main.source_access.youtube.metadata_slot.active == 0


def test_playlist_queues_all_over_bulk_limit_with_metadata_and_idempotent_retry(monkeypatch):
    data = load_test_playlist(monkeypatch, [playlist_entry(i) for i in range(60)])
    submitted = []
    monkeypatch.setattr(main.pool, 'submit', lambda *args: submitted.append(args))
    payload = {'token': data['token'], 'format': 'mp3', 'youtube_concurrency': 2}
    response = client.post('/api/playlist/download', json=payload)
    assert response.status_code == 202, response.text
    assert response.json()['added'] == 60
    assert len(submitted) == 60
    assert [j['playlist_position'] for j in main.jobs.values()] == list(range(1, 61))
    assert all(j['playlist_title'] == '<My playlist>' and j['format'] == 'mp3' for j in main.jobs.values())
    assert len(main.storage.restore(main.DATA)) == 60
    assert main.source_access.youtube.slot.limit == 2
    repeated = client.post('/api/playlist/download', json=payload)
    assert repeated.json() == response.json()
    assert len(submitted) == 60  # A timeout/repeated click cannot duplicate this import.


def test_playlist_selection_preserves_source_order_and_failed_video_does_not_block_rest(monkeypatch):
    data = load_test_playlist(monkeypatch, [playlist_entry(i) for i in range(4)])
    submitted = []
    def submit(fn, job_id, req):
        submitted.append(req.url)
        main.update(job_id, status='failed' if req.url.endswith('1') else 'completed')
    monkeypatch.setattr(main.pool, 'submit', submit)
    response = client.post('/api/playlist/download', json={'token': data['token'], 'video_ids': ['clip0000003', 'clip0000001']})
    assert response.status_code == 202
    assert submitted == ['https://www.youtube.com/watch?v=clip0000001', 'https://www.youtube.com/watch?v=clip0000003']
    assert [j['status'] for j in main.jobs.values()] == ['failed', 'completed']


@pytest.mark.parametrize('selection', [[], ['not_in_list'], ['clip0000001', 'not_in_list']])
def test_playlist_selection_must_come_from_preview(monkeypatch, selection):
    data = load_test_playlist(monkeypatch)
    assert client.post('/api/playlist/download', json={'token': data['token'], 'video_ids': selection}).status_code == 400
    assert not main.jobs


def test_playlist_expiry_and_restart_require_reload(monkeypatch):
    assert client.post('/api/playlist/download', json={'token': 'missing'}).status_code == 410
    data = load_test_playlist(monkeypatch)
    main.playlist_cache[data['token']]['created'] -= 1801
    assert client.post('/api/playlist/download', json={'token': data['token']}).status_code == 410
    assert not main.jobs


def test_playlist_oversize_is_not_silently_truncated(monkeypatch):
    def entries():
        for i in range(2000):
            if i > 1000:
                pytest.fail('Unbounded playlist extraction')
            yield playlist_entry(i)
    monkeypatch.setattr(main, 'extract_video', lambda *a: {'_type': 'playlist', 'entries': entries()})
    response = client.post('/api/playlist/analyze', json={'url': 'https://youtube.com/playlist?list=PLlarge'})
    assert response.status_code == 400
    assert 'exceeds 1000' in response.json()['detail']
    assert not main.jobs


def test_playlist_capacity_failure_keeps_preview_reusable(monkeypatch):
    data = load_test_playlist(monkeypatch)
    monkeypatch.setattr(main, 'MAX_PLAYLIST', 1)
    assert client.post('/api/playlist/download', json={'token': data['token']}).status_code == 429
    assert not main.jobs
    assert main.playlist_cache[data['token']]['queued'] is None
    monkeypatch.setattr(main.pool, 'submit', lambda *a: None)
    response = client.post('/api/playlist/download', json={'token': data['token'], 'video_ids': ['clip0000001']})
    assert response.status_code == 202


def test_playlist_transaction_failure_is_atomic_and_retriable(monkeypatch):
    data = load_test_playlist(monkeypatch)
    def rejected(*args): raise main.storage.sqlite3.OperationalError('disk full')
    monkeypatch.setattr(main.storage, 'save_many', rejected)
    assert client.post('/api/playlist/download', json={'token': data['token']}).status_code == 503
    assert not main.jobs
    assert main.playlist_cache[data['token']]['queued'] is None


def test_playlist_cooldown_stops_import_without_new_source_requests(monkeypatch):
    data = load_test_playlist(monkeypatch)
    main.source_access.youtube.limited()
    assert client.post('/api/playlist/download', json={'token': data['token']}).status_code == 429
    assert not main.jobs


def test_playlist_page_rate_limit_starts_cooldown_even_when_extractor_ignores_errors(monkeypatch):
    def limited(downloader, *args, **kwargs):
        downloader.params['logger'].error('HTTP Error 429: Too Many Requests')
        return {'_type': 'playlist', 'entries': []}
    monkeypatch.setattr(main.yt_dlp.YoutubeDL, 'extract_info', limited)
    response = client.post('/api/playlist/analyze', json={'url': 'https://youtube.com/playlist?list=PLtest'})
    assert response.status_code == 429
    assert main.source_access.youtube.remaining() > 0
    assert response.headers['retry-after']


def test_playlist_preview_cache_is_bounded(monkeypatch):
    for i in range(12):
        load_test_playlist(monkeypatch)
    assert len(main.playlist_cache) == 10


def test_playlist_reports_hidden_entries_without_claiming_complete_listing(monkeypatch):
    monkeypatch.setattr(main, 'extract_video', lambda *a: {'_type': 'playlist', 'playlist_count': 5, 'entries': [playlist_entry(1), playlist_entry(2)]})
    response = client.post('/api/playlist/analyze', json={'url': 'https://youtube.com/playlist?list=PLhidden'})
    assert response.status_code == 200
    assert response.json()['unlisted'] == 3


def test_playlist_page_failure_does_not_offer_partial_import(monkeypatch):
    def partial_page(downloader, *args, **kwargs):
        def entries():
            yield playlist_entry(1)
            downloader.params['logger'].error('Unable to download next playlist page')
        return {'_type': 'playlist', 'entries': entries()}
    monkeypatch.setattr(main.yt_dlp.YoutubeDL, 'extract_info', partial_page)
    response = client.post('/api/playlist/analyze', json={'url': 'https://youtube.com/playlist?list=PLpartial'})
    assert response.status_code == 422
    assert 'fully loaded' in response.json()['detail']
    assert not main.playlist_cache


@pytest.mark.parametrize('entries', [[], [None], [playlist_entry(1, availability='private')]])
def test_empty_or_unavailable_playlist_cannot_be_queued(monkeypatch, entries):
    monkeypatch.setattr(main, 'extract_video', lambda *a: {'_type': 'playlist', 'entries': entries})
    response = client.post('/api/playlist/analyze', json={'url': 'https://youtube.com/playlist?list=PLempty'})
    assert response.status_code == 422
    assert not main.playlist_cache


def test_supplied_playlist_watch_url_reaches_existing_api(monkeypatch):
    captured = []
    def listing(url, download, settings):
        captured.append((url, download))
        return {'_type': 'playlist', 'entries': [{'id': 'Smjl5LtkLC4', 'title': 'Video'}]}
    monkeypatch.setattr(main, 'extract_video', listing)
    response = client.post('/api/playlist/analyze', json={'url': 'https://www.youtube.com/watch?v=Smjl5LtkLC4&list=PLOpqmtbg1STVxAqdA-ndhhR4Q4U876aPx'})
    assert response.status_code == 200
    assert captured == [('https://www.youtube.com/playlist?list=PLOpqmtbg1STVxAqdA-ndhhR4Q4U876aPx', False)]
    assert response.json()['videos'][0]['id'] == 'Smjl5LtkLC4'


def test_dashboard_is_not_cached_and_reports_playlist_capability():
    assert client.get('/').headers['cache-control'] == 'no-store'
    info = client.get('/api/health').json()
    assert info['version'] == main.__version__
    assert info['capabilities']['youtube_playlists'] is True


@pytest.mark.parametrize('version', ['1.2.0', '1.3.0', None])
def test_launcher_does_not_silently_reopen_stale_server(monkeypatch, capsys, version):
    from app import desktop
    monkeypatch.setattr('sys.argv', ['streamvault'])
    monkeypatch.setattr(desktop, 'server_info', lambda port: {'application': 'StreamVault', 'version': version})
    monkeypatch.setattr(desktop.webbrowser, 'open', lambda *a: pytest.fail('Stale server reopened'))
    with pytest.raises(SystemExit) as error:
        desktop.main()
    assert error.value.code == 2
    assert 'Stop the previous server with Ctrl+C' in capsys.readouterr().err


def test_launcher_reuses_matching_version(monkeypatch):
    from app import desktop
    opened = []
    monkeypatch.setattr('sys.argv', ['streamvault'])
    monkeypatch.setattr(desktop, 'server_info', lambda port: {'application': 'StreamVault', 'version': main.__version__})
    monkeypatch.setattr(desktop.webbrowser, 'open', opened.append)
    desktop.main()
    assert opened == ['http://127.0.0.1:8000']


def test_launcher_protected_server_does_not_claim_version_match(monkeypatch, capsys):
    from app import desktop
    monkeypatch.setattr('sys.argv', ['streamvault'])
    monkeypatch.setattr(desktop, 'server_info', lambda port: {'application': 'StreamVault', 'authentication_required': True})
    monkeypatch.setattr(desktop.webbrowser, 'open', lambda *a: None)
    desktop.main()
    assert 'stop its server with Ctrl+C' in capsys.readouterr().out


@pytest.mark.parametrize('payload,status,expected', [
    ({'application': 'StreamVault', 'version': '1.2.0'}, 200, {'application': 'StreamVault', 'version': '1.2.0'}),
    ({'application': 'another-app'}, 200, None),
    ([], 200, None),
    ({}, 401, {'application': 'StreamVault', 'authentication_required': True}),
    ({}, 404, None),
])
def test_launcher_reads_actual_loopback_identity_without_proxy(payload, status, expected, monkeypatch):
    import json
    from http.server import BaseHTTPRequestHandler
    from app import desktop
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == '/api/health'
            self.send_response(status)
            if status == 401:
                self.send_header('WWW-Authenticate', 'Basic realm="StreamVault"')
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    try:
        assert desktop.server_info(server.server_port) == expected
        assert desktop.probe(server.server_port) is bool(expected)
    finally:
        server.shutdown(); server.server_close()


def test_background_playlist_load_returns_pollable_result(monkeypatch):
    load_test_playlist(monkeypatch)
    response = client.post('/api/playlist/loads', json={'url': 'https://youtube.com/playlist?list=PLtest'})
    assert response.status_code == 202
    result = client.get('/api/playlist/loads/' + response.json()['id'])
    assert result.json()['status'] == 'ready'
    assert len(result.json()['result']['videos']) == 2
    assert 'cancel' not in result.json()


def test_background_playlist_load_error_is_pollable(monkeypatch):
    def failed(*args): raise main.yt_dlp.utils.DownloadError('Video unavailable')
    monkeypatch.setattr(main, 'extract_video', failed)
    started = client.post('/api/playlist/loads', json={'url': 'https://youtube.com/playlist?list=PLtest'})
    state = client.get('/api/playlist/loads/' + started.json()['id']).json()
    assert state['status'] == 'failed'
    assert 'unavailable' in state['detail']


def wait_until(check, timeout=3):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check(): return
        time.sleep(.01)
    assert check(), 'Condition did not become true'


def test_busy_playlist_waits_for_other_metadata_without_retry(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    access = main.source_access.youtube
    access.metadata_slot.acquire()
    extracted = []
    def playlist(downloader, url, download):
        assert '_playlist_cancel' not in downloader.params
        extracted.append(url)
        return {'_type': 'playlist', 'entries': [playlist_entry(1)]}
    monkeypatch.setattr(main.yt_dlp.YoutubeDL, 'extract_info', playlist)
    with ThreadPoolExecutor(max_workers=1) as worker:
        monkeypatch.setattr(main, 'playlist_pool', worker)
        try:
            response = client.post('/api/playlist/loads', json={'url': 'https://youtube.com/playlist?list=PLbusy'})
            assert response.status_code == 202
            load_id = response.json()['id']
            wait_until(lambda: len(access.metadata_slot.waiters) == 1)
            assert not extracted
            assert client.get('/api/playlist/loads/' + load_id).json()['status'] == 'loading'
            assert client.get('/api/health').status_code == 200
        finally:
            access.metadata_slot.release()
        wait_until(lambda: main.playlist_loads[load_id]['status'] == 'ready')
    assert len(extracted) == 1
    assert access.metadata_slot.active == 0


def test_queued_playlist_can_cancel_without_contacting_youtube(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    access = main.source_access.youtube
    access.metadata_slot.acquire()
    monkeypatch.setattr(main.yt_dlp.YoutubeDL, 'extract_info', lambda *a, **kw: pytest.fail('Cancelled playlist contacted YouTube'))
    with ThreadPoolExecutor(max_workers=1) as worker:
        monkeypatch.setattr(main, 'playlist_pool', worker)
        try:
            started = client.post('/api/playlist/loads', json={'url': 'https://youtube.com/playlist?list=PLcancel'})
            load_id = started.json()['id']
            wait_until(lambda: len(access.metadata_slot.waiters) == 1)
            assert client.delete('/api/playlist/loads/' + load_id).status_code == 204
            wait_until(lambda: main.playlist_loads[load_id]['status'] == 'cancelled')
            assert access.metadata_slot.active == 1
            assert not access.metadata_slot.waiters
        finally:
            access.metadata_slot.release()
    assert not main.playlist_cache


def test_source_gate_is_fifo_and_new_download_cannot_overtake_waiting_playlist():
    from concurrent.futures import ThreadPoolExecutor
    access = main.source_access.youtube
    access.slot.acquire()
    order = []
    def request(name, download):
        with access.request(download, wait=not download):
            order.append(name)
    with ThreadPoolExecutor(max_workers=2) as workers:
        try:
            first = workers.submit(request, 'playlist', False)
            wait_until(lambda: len(access.slot.waiters) == 1)
            second = workers.submit(request, 'next download', True)
            wait_until(lambda: len(access.slot.waiters) == 2)
            assert access.slot.acquire(blocking=False) is False
        finally:
            access.slot.release()
        first.result(timeout=3); second.result(timeout=3)
    assert order == ['playlist', 'next download']
    assert access.slot.active == 0


def test_background_playlist_respects_cooldown_before_start():
    main.source_access.youtube.limited()
    response = client.post('/api/playlist/loads', json={'url': 'https://youtube.com/playlist?list=PLtest'})
    assert response.status_code == 429
    assert not main.playlist_loads


def test_playlist_load_queue_is_bounded_and_worker_failure_does_not_strand_it(monkeypatch):
    monkeypatch.setattr(main.playlist_pool, 'submit', lambda *a: None)
    for _ in range(10):
        assert client.post('/api/playlist/loads', json={'url': 'https://youtube.com/playlist?list=PLtest'}).status_code == 202
    assert client.post('/api/playlist/loads', json={'url': 'https://youtube.com/playlist?list=PLtest'}).status_code == 429
    assert len(main.playlist_loads) == 10
    main.playlist_loads.clear()
    def failed(*a): raise RuntimeError('stopped')
    monkeypatch.setattr(main.playlist_pool, 'submit', failed)
    assert client.post('/api/playlist/loads', json={'url': 'https://youtube.com/playlist?list=PLtest'}).status_code == 503
    assert not main.playlist_loads


def test_missing_load_is_readable_and_cancel_is_idempotent():
    assert client.get('/api/playlist/loads/missing').status_code == 404
    assert client.delete('/api/playlist/loads/missing').status_code == 204


def test_shutdown_releases_waiting_playlist_and_preserves_held_metadata_slot(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    access = main.source_access.youtube
    access.metadata_slot.acquire()
    with ThreadPoolExecutor(max_workers=1) as worker:
        monkeypatch.setattr(main, 'playlist_pool', worker)
        try:
            response = client.post('/api/playlist/loads', json={'url': 'https://youtube.com/playlist?list=PLshutdown'})
            load_id = response.json()['id']
            wait_until(lambda: len(access.metadata_slot.waiters) == 1)
            main.stop_playlist_loads()
            wait_until(lambda: main.playlist_loads[load_id]['status'] == 'cancelled')
            assert access.metadata_slot.active == 1
        finally:
            access.metadata_slot.release()


def test_lifetime_counts_survive_history_removal_and_restore(tmp_path):
    import json, sqlite3
    # Simulate an existing database without the new events table.
    job = failed_job('legacy') | {'status': 'completed', 'url': 'https://youtu.be/Smjl5LtkLC4'}
    with sqlite3.connect(tmp_path / 'history.sqlite3') as db:
        db.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        db.execute('INSERT INTO jobs VALUES (?, ?)', ('legacy', json.dumps(job)))
    for _ in range(2):
        main.storage.restore(tmp_path)
        assert main.storage.statistics(tmp_path)['total'] == 1
    main.storage.delete(tmp_path, 'legacy')
    assert not main.storage.restore(tmp_path)
    assert main.storage.statistics(tmp_path)['total'] == 1
    assert main.storage.downloaded_count(tmp_path, 'https://www.youtube.com/watch?v=Smjl5LtkLC4&list=PLtest') == 1


@pytest.mark.parametrize('url,platform', [
    ('https://youtu.be/Smjl5LtkLC4', 'YouTube'), ('https://www.instagram.com/reel/post1/', 'Instagram'),
    ('https://vm.tiktok.com/post1', 'TikTok'), ('https://fb.watch/post1', 'Facebook'),
    ('https://twitter.com/name/status/123', 'X / Twitter'), ('https://dai.ly/x123', 'Dailymotion'),
    ('https://www.threads.net/@name/post/123', 'Threads'),
])
def test_completed_counters_use_source_family_and_ignore_preview_updates(tmp_path, url, platform):
    job = failed_job() | {'status':'completed', 'url':url, 'completion_id':'attempt1'}
    main.storage.save(tmp_path, job)
    main.storage.save(tmp_path, job | {'preview_status':'ready'})
    main.storage.restore(tmp_path)
    stats = main.storage.statistics(tmp_path)
    assert stats['total'] == 1
    assert {p['name']:p['downloads'] for p in stats['platforms']}[platform] == 1


@pytest.mark.parametrize('status', ['queued','downloading','processing','failed'])
def test_unfinished_jobs_never_increment_lifetime_counts(tmp_path, status):
    main.storage.save(tmp_path, failed_job() | {'status':status})
    assert main.storage.statistics(tmp_path)['total'] == 0


def test_successful_new_attempts_count_once_each(tmp_path):
    job = failed_job() | {'status':'completed','completion_id':'first'}
    main.storage.save(tmp_path, job)
    main.storage.save(tmp_path, job)
    main.storage.save(tmp_path, job | {'status':'failed'})
    main.storage.save(tmp_path, job | {'completion_id':'second'})
    main.storage.save(tmp_path, job | {'completion_id':'second','preview_status':'ready'})
    assert main.storage.statistics(tmp_path)['total'] == 2


@pytest.mark.parametrize('one,two', [
    ('https://youtu.be/Smjl5LtkLC4?t=20','https://www.youtube.com/watch?v=Smjl5LtkLC4&list=PLtest'),
    ('https://youtube.com/shorts/Smjl5LtkLC4','https://youtube.com/watch?v=Smjl5LtkLC4'),
    ('https://twitter.com/name/status/123?s=20','https://x.com/other/status/123'),
    ('https://www.instagram.com/reel/ABC/?igsh=xyz','https://instagram.com/reel/ABC'),
    ('https://threads.net/@name/post/ABC?xmt=1','https://threads.com/@name/post/ABC'),
    ('https://dai.ly/x123','https://www.dailymotion.com/video/x123_title'),
])
def test_download_badge_identity_ignores_share_tracking(one,two):
    assert main.storage.video_key(one) == main.storage.video_key(two)


def test_completion_history_and_event_commit_atomically(tmp_path):
    with main.storage.connect(tmp_path) as db:
        db.execute("CREATE TRIGGER reject_event BEFORE INSERT ON download_events BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(main.storage.sqlite3.IntegrityError):
        main.storage.save(tmp_path, failed_job() | {'status':'completed'})
    assert not main.storage.restore(tmp_path)
    assert main.storage.statistics(tmp_path)['total'] == 0


def test_redownload_creates_new_job_and_preserves_existing_media(monkeypatch):
    job = failed_job('original') | {'status':'completed'}
    main.jobs[job['id']] = job.copy()
    main.storage.save(main.DATA, job)
    folder = main.DATA / job['id']; folder.mkdir(); (folder / job['filename']).write_bytes(b'original media')
    submitted = []
    monkeypatch.setattr(main.pool, 'submit', lambda *args: submitted.append(args))
    response = client.post('/api/jobs/original/redownload')
    assert response.status_code == 202
    assert response.json()['id'] != 'original'
    assert response.json()['url'] == job['url']
    assert len(submitted) == 1
    assert main.jobs['original'] == job
    assert (folder / job['filename']).read_bytes() == b'original media'
    assert client.get('/api/stats').json()['total'] == 1
    assert len(main.jobs) == 2


def test_redownload_rejects_active_jobs_and_missing_history():
    assert client.post('/api/jobs/missing/redownload').status_code == 404
    main.jobs['active'] = failed_job('active') | {'status':'downloading'}
    assert client.post('/api/jobs/active/redownload').status_code == 409


def test_deleted_download_is_recognized_when_analyzed_again(monkeypatch):
    job = failed_job() | {'status':'completed', 'url':'https://youtu.be/Smjl5LtkLC4'}
    main.storage.save(main.DATA,job); main.storage.delete(main.DATA,job['id'])
    monkeypatch.setattr(main, 'extract_video', lambda *args: {'title':'Same video','formats':[]})
    response = client.post('/api/analyze',json={'url':'https://youtube.com/watch?v=Smjl5LtkLC4'})
    assert response.status_code == 200
    assert response.json()['previous_downloads'] == 1


def test_analytics_and_redownload_keep_owner_authentication(monkeypatch):
    monkeypatch.setenv('STREAMVAULT_PASSWORD', 'local-test-owner')
    assert client.get('/api/stats').status_code == 401
    assert client.post('/api/jobs/anything/redownload').status_code == 401
    assert client.get('/api/stats',auth=('admin','local-test-owner')).status_code == 200


def local_desktop_client(tmp_path, monkeypatch):
    data = tmp_path / 'app-data'; data.mkdir()
    chosen = tmp_path / 'My videos'; chosen.mkdir()
    monkeypatch.setattr(main, 'DATA', data)
    monkeypatch.setenv('STREAMVAULT_DESKTOP', '1')
    local = TestClient(main.app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 52000))
    return local, data, chosen


def test_desktop_requires_location_then_captures_it_for_each_new_batch(tmp_path, monkeypatch):
    local, data, chosen = local_desktop_client(tmp_path, monkeypatch)
    monkeypatch.setattr(main.pool, 'submit', lambda *args: None)
    assert local.get('/api/download-location').json()['direct_enabled'] is True
    assert local.post('/api/download',json={'url':'https://youtu.be/one'}).status_code == 409
    assert not main.jobs
    selected = local.post('/api/download-location', json={'path':str(chosen)})
    assert selected.status_code == 200 and selected.json()['available']
    assert main.destinations.selected(data) == str(chosen)
    queued = local.post('/api/download/bulk',json={'urls':['https://youtu.be/one','https://youtu.be/two']})
    assert queued.status_code == 202
    assert all(j['output_dir'] == str(chosen) for j in main.jobs.values())
    other = tmp_path / 'Other folder'; other.mkdir()
    assert local.post('/api/download-location',json={'path':str(other)}).status_code == 200
    fresh = local.post('/api/download',json={'url':'https://youtu.be/three'}).json()
    assert fresh['output_dir'] == str(other)
    assert next(iter(main.jobs.values()))['output_dir'] == str(chosen)


def test_local_folder_picker_selection_cancellation_and_failure(tmp_path, monkeypatch):
    local, data, chosen = local_desktop_client(tmp_path, monkeypatch)
    monkeypatch.setattr(main.destinations, 'picker', lambda initial: str(chosen))
    assert local.post('/api/download-location/pick').json()['path'] == str(chosen)
    monkeypatch.setattr(main.destinations, 'picker', lambda initial: None)
    cancelled = local.post('/api/download-location/pick').json()
    assert cancelled['cancelled'] and cancelled['path'] == str(chosen)
    def failed(*args):raise main.destinations.DestinationError('Enter a folder path instead.')
    monkeypatch.setattr(main.destinations, 'picker', failed)
    assert local.post('/api/download-location/pick').status_code == 503
    assert not main.folder_dialog_lock.locked()
    assert main.destinations.selected(data) == str(chosen)


def test_only_one_native_folder_dialog_can_open(tmp_path, monkeypatch):
    local, _, _ = local_desktop_client(tmp_path, monkeypatch)
    main.folder_dialog_lock.acquire()
    try:
        monkeypatch.setattr(main.destinations, 'picker', lambda *args:pytest.fail('Second dialog opened'))
        assert local.post('/api/download-location/pick').status_code == 409
    finally:main.folder_dialog_lock.release()


@pytest.mark.parametrize('kind',['relative','missing','file','app-data'])
def test_invalid_download_locations_do_not_change_config(tmp_path, monkeypatch, kind):
    local, data, chosen = local_desktop_client(tmp_path, monkeypatch)
    main.destinations.configure(data,str(chosen))
    file = tmp_path / 'not-a-folder';file.write_text('keep')
    values={'relative':'relative/folder','missing':str(tmp_path/'missing'),'file':str(file),'app-data':str(data)}
    assert local.post('/api/download-location',json={'path':values[kind]}).status_code == 400
    assert main.destinations.selected(data) == str(chosen)


@pytest.mark.parametrize('host,peer',[('http://127.0.0.1:8000','198.51.100.8'),('http://attacker.example','127.0.0.1')])
def test_remote_or_rebound_hosts_cannot_change_local_folders(tmp_path, monkeypatch, host, peer):
    _, data, chosen = local_desktop_client(tmp_path, monkeypatch)
    main.destinations.configure(data,str(chosen))
    remote = TestClient(main.app,base_url=host,client=(peer,52000))
    assert remote.get('/api/download-location').json()['path'] is None
    assert remote.post('/api/download-location',json={'path':str(chosen)}).status_code == 403
    assert remote.post('/api/download-location/pick').status_code == 403
    assert remote.post('/api/download-location/open').status_code == 403


def test_public_mode_cannot_use_local_folder_controls(tmp_path, monkeypatch):
    local, data, chosen = local_desktop_client(tmp_path, monkeypatch)
    main.destinations.configure(data,str(chosen))
    monkeypatch.setenv('STREAMVAULT_PUBLIC','true');monkeypatch.setenv('STREAMVAULT_PASSWORD','local-owner-test')
    auth=('admin','local-owner-test')
    assert not local.get('/api/download-location',auth=auth).json()['direct_enabled']
    assert local.post('/api/download-location/pick',auth=auth).status_code == 403
    assert main.destinations.snapshot(data) == {}


def test_folder_controls_keep_same_origin_protection(tmp_path, monkeypatch):
    local, _, chosen = local_desktop_client(tmp_path, monkeypatch)
    assert local.post('/api/download-location',json={'path':str(chosen)},headers={'Origin':'https://attacker.example'}).status_code == 403


def test_publish_renames_once_and_never_overwrites_existing_file(tmp_path, monkeypatch):
    local, data, chosen = local_desktop_client(tmp_path, monkeypatch)
    job=failed_job('direct01')|{'status':'processing','output_dir':str(chosen)}
    main.jobs[job['id']]=job
    work=main.destinations.work_folder(data,job);work.mkdir(parents=True)
    source=work/'Video.mp4';source.write_bytes(b'verified original');inode=source.stat().st_ino
    existing=chosen/'Video [direct01].mp4';existing.write_bytes(b'user file')
    monkeypatch.setattr(main.shutil,'copy2',lambda *args:pytest.fail('Same-volume original was copied'))
    output=main.publish_original(job['id'],source)
    assert output.read_bytes()==b'verified original' and output.stat().st_ino==inode
    assert existing.read_bytes()==b'user file'
    assert not source.exists()
    assert main.storage.restore(data)[job['id']]['planned_filename']==output.name


def test_folder_change_does_not_accept_extra_client_job_path(tmp_path, monkeypatch):
    local, data, chosen = local_desktop_client(tmp_path, monkeypatch)
    main.destinations.configure(data,str(chosen))
    monkeypatch.setattr(main.pool,'submit',lambda *args:None)
    response=local.post('/api/download',json={'url':'https://youtu.be/one','output_dir':'/not-selected'})
    assert response.status_code==202
    assert response.json()['output_dir']==str(chosen)


def tiny_video(path):
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=s=160x120:d=1','-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True)


def test_published_original_recovers_after_restart_without_source_request(tmp_path, monkeypatch):
    local, data, chosen = local_desktop_client(tmp_path, monkeypatch)
    file=chosen/'Saved.mp4';tiny_video(file)
    job=failed_job('recovered')|{'status':'failed','recoverable':True,'output_dir':str(chosen),'planned_filename':file.name,'duration':1}
    main.jobs[job['id']]=job
    monkeypatch.setattr(main,'extract_video',lambda *args:pytest.fail('Already published file re-downloaded'))
    main.run_download(job['id'],main.DownloadRequest(url=job['url'],quality='720'))
    assert main.jobs[job['id']]['status']=='completed'
    assert main.saved_path(main.jobs[job['id']])==file
    assert len(list(chosen.glob('*.mp4')))==1
    assert local.get('/api/jobs/recovered/stream?original=true').status_code==200


def test_legacy_merged_original_can_finish_into_selected_folder_on_retry(tmp_path, monkeypatch):
    local, data, chosen = local_desktop_client(tmp_path, monkeypatch)
    main.destinations.configure(data,str(chosen))
    job=failed_job('legacy_move')|{'recoverable':True,'duration':1}
    main.jobs[job['id']]=job
    folder=data/job['id'];folder.mkdir();old=folder/'Merged.mp4';tiny_video(old)
    def immediate(function,*args):function(*args)
    monkeypatch.setattr(main.pool,'submit',immediate)
    monkeypatch.setattr(main,'extract_video',lambda *args:pytest.fail('Valid legacy merge re-downloaded'))
    assert local.post('/api/jobs/legacy_move/retry').status_code==202
    assert main.jobs[job['id']]['status']=='completed',main.jobs[job['id']]
    assert main.saved_path(main.jobs[job['id']]).parent==chosen
    assert not old.exists()


def test_direct_saved_file_opening_is_local_and_uses_recorded_path(tmp_path, monkeypatch):
    local, data, chosen = local_desktop_client(tmp_path, monkeypatch)
    main.destinations.configure(data,str(chosen))
    job=failed_job('saved')|{'status':'completed','output_dir':str(chosen),'filename':'Saved.mp4'}
    (chosen/job['filename']).write_bytes(b'original')
    main.jobs[job['id']]=job
    opened=[];monkeypatch.setattr(main.destinations,'open_folder',opened.append)
    assert local.post('/api/jobs/saved/open-folder').status_code==200
    assert local.post('/api/download-location/open').status_code==200
    assert opened==[chosen,chosen]
    assert client.post('/api/jobs/saved/open-folder').status_code==403


def test_playlist_listing_runs_while_both_media_transfers_are_busy(monkeypatch):
    access=main.source_access.youtube
    access.slot.set_limit(2);access.slot.acquire();access.slot.acquire()
    def extracted(dl,*args,**kwargs):
        assert access.slot.active==2
        assert access.metadata_slot.active==1
        assert '_playlist_phase' not in dl.params
        return {'_type':'playlist','entries':[playlist_entry(1)]}
    monkeypatch.setattr(main.yt_dlp.YoutubeDL,'extract_info',extracted)
    try:
        response=client.post('/api/playlist/loads',json={'url':'https://youtube.com/playlist?list=PLready'})
        state=client.get('/api/playlist/loads/'+response.json()['id']).json()
        assert state['status']=='ready'
        assert state['phase']=='fetching' and state['started']>0
        assert access.slot.active==2 and access.metadata_slot.active==0
    finally:access.slot.release();access.slot.release()


def test_playlist_missing_thumbnail_gets_youtube_poster(monkeypatch):
    result=load_test_playlist(monkeypatch,[playlist_entry(1),playlist_entry(2,thumbnails=[{'url':'https://i.ytimg.com/example.jpg'}])])
    assert result['videos'][0]['thumbnail']=='https://i.ytimg.com/vi/clip0000001/hqdefault.jpg'
    assert result['videos'][1]['thumbnail']=='https://i.ytimg.com/example.jpg'


def test_bulk_removal_deduplicates_and_preserves_selected_folder_files_and_counts(tmp_path, monkeypatch):
    local, data, chosen = local_desktop_client(tmp_path, monkeypatch)
    external = failed_job('outside') | {'status':'completed','output_dir':str(chosen),'filename':'Keep.mp4'}
    (chosen/'Keep.mp4').write_bytes(b'user-owned original')
    legacy = failed_job('inside') | {'status':'completed'}
    folder = data/legacy['id'];folder.mkdir();(folder/legacy['filename']).write_bytes(b'app-owned original')
    active = failed_job('active') | {'status':'downloading'}
    failed = failed_job('failed')
    for job in (external,legacy,active,failed):
        main.jobs[job['id']]=job;main.storage.save(data,job)
    response=local.post('/api/jobs/remove',json={'job_ids':['outside','inside','failed','outside','active','missing']})
    assert response.status_code==200
    result=response.json()
    assert result['removed']==['outside','inside','failed']
    assert [(e['id'],e['status']) for e in result['failed']]==[('active',409),('missing',404)]
    assert (chosen/'Keep.mp4').read_bytes()==b'user-owned original'
    assert not folder.exists()
    assert set(main.jobs)=={'active'}
    assert main.storage.statistics(data)['total']==2
    assert set(main.storage.restore(data))=={'active'}


def test_bulk_removal_skips_preparing_preview_and_continues():
    for id in ['locked','free']:main.jobs[id]=failed_job(id)
    guard=main.media_lock('locked');guard.acquire()
    try:
        result=client.post('/api/jobs/remove',json={'job_ids':['locked','free']}).json()
        assert result['removed']==['free'] and result['failed'][0]['status']==409
        assert 'locked' in main.jobs
    finally:guard.release()


def test_bulk_removal_storage_failure_keeps_history_and_media(tmp_path, monkeypatch):
    job=failed_job('keep')|{'status':'completed'}
    main.jobs['keep']=job;main.storage.save(main.DATA,job)
    folder=main.DATA/'keep';folder.mkdir();file=folder/job['filename'];file.write_bytes(b'keep')
    def reject(*args):raise main.storage.sqlite3.OperationalError('private diagnostic')
    monkeypatch.setattr(main.storage,'delete',reject)
    result=client.post('/api/jobs/remove',json={'job_ids':['keep']}).json()
    assert not result['removed'] and result['failed'][0]['status']==503
    assert 'private' not in result['failed'][0]['detail']
    assert main.jobs['keep']['status']=='completed' and file.read_bytes()==b'keep'


@pytest.mark.parametrize('ids',[[],['x']*1101,['x'*65]])
def test_bulk_removal_bounds(ids):
    assert client.post('/api/jobs/remove',json={'job_ids':ids}).status_code==422


def test_bulk_removal_keeps_authentication_and_same_origin_guards(monkeypatch):
    main.jobs['keep']=failed_job('keep')
    monkeypatch.setenv('STREAMVAULT_PASSWORD','local-owner')
    assert client.post('/api/jobs/remove',json={'job_ids':['keep']}).status_code==401
    assert client.post('/api/jobs/remove',json={'job_ids':['keep']},auth=('admin','local-owner'),headers={'Origin':'https://attacker.example'}).status_code==403
    assert 'keep' in main.jobs


def test_waiting_youtube_jobs_keep_queue_without_another_source_request(monkeypatch):
    main.source_access.youtube.limited()
    monkeypatch.setattr(main,'extract_video',lambda *args:pytest.fail('Source contacted during cooldown'))
    for id in ['wait1','wait2','wait3']:
        job=failed_job(id)|{'status':'queued','stage':'queued'}
        main.jobs[id]=job
        main.run_download(id,main.DownloadRequest(url=job['url']))
    assert len(main.deferred_downloads.pending)==3
    assert all(j['status']=='queued' and j['stage']=='source_wait' and j['retry_at']>main.time.time() and j['error'] is None for j in main.jobs.values())
    restored = main.storage.restore(main.DATA)
    assert all(j['status']=='failed' and 'restart' in j['error'] for j in restored.values())  # Restart still requires an explicit Retry.
    assert all(j['status']=='queued' for j in main.jobs.values())


def test_source_429_holds_current_job_and_other_queued_jobs(monkeypatch):
    calls=[]
    def rejected(*args):
        calls.append(args);main.source_access.youtube.limited()
        raise main.source_access.SourceRateLimited(main.source_access.youtube.remaining())
    monkeypatch.setattr(main,'extract_video',rejected)
    for id in ['first','second']:
        job=failed_job(id)|{'status':'queued'};main.jobs[id]=job
        main.run_download(id,main.DownloadRequest(url=job['url']))
    assert len(calls)==1
    assert main.jobs['first']['stage']=='retry_wait' and main.jobs['first']['auto_attempts']==1
    assert main.jobs['second']['stage']=='source_wait'
    assert all(j['status']=='queued' for j in main.jobs.values())
    assert main.storage.statistics(main.DATA)['total']==0


def test_cooldown_dispatcher_deduplicates_and_releases_after_expiry():
    remaining=[600];sent=[];finished=threading.Event()
    def submit(id,req):
        sent.append((id,req))
        if len(sent)==2:finished.set()
    deferred=main.DeferredDownloads(lambda:remaining[0],submit)
    try:
        deferred.defer('one','old');deferred.defer('one','new');deferred.defer('two','second')
        assert not sent
        with deferred.condition:
            remaining[0]=0;deferred.condition.notify_all()
        assert finished.wait(2)
        assert sent==[('one','new'),('two','second')]
        assert not deferred.pending
    finally:deferred.stop()
    assert not deferred.thread.is_alive()


def test_cooldown_dispatcher_shutdown_does_not_submit_waiting_jobs():
    sent=[]
    deferred=main.DeferredDownloads(lambda:600,lambda *args:sent.append(args))
    deferred.defer('one','request');deferred.stop()
    assert not sent and not deferred.pending and not deferred.thread.is_alive()


def test_cooldown_does_not_hold_other_platform_downloads(monkeypatch):
    main.source_access.youtube.limited()
    calls=[]
    def rejected(*args):calls.append(args);raise RuntimeError('Source unavailable')
    monkeypatch.setattr(main,'extract_video',rejected)
    job=failed_job('other')|{'status':'queued','url':'https://www.tiktok.com/@public/video/123'};main.jobs['other']=job
    main.run_download('other',main.DownloadRequest(url=job['url']))
    assert len(calls)==1 and main.jobs['other']['status']=='failed'
    assert not main.deferred_downloads.pending


def test_expired_cooldown_resubmits_retained_job_only_once(monkeypatch):
    job=failed_job('waiting')|{'status':'queued','stage':'source_wait'};main.jobs[job['id']]=job
    request=main.DownloadRequest(url=job['url']);submitted=[];ready=threading.Event()
    def submit(function,*args):submitted.append((function,args));ready.set()
    monkeypatch.setattr(main.pool,'submit',submit)
    main.source_access.youtube.limited()
    main.deferred_downloads.defer(job['id'],request)
    with main.source_access.youtube.state:main.source_access.youtube.until=0
    with main.deferred_downloads.condition:main.deferred_downloads.condition.notify_all()
    assert ready.wait(2) and len(submitted)==1
    assert submitted[0][0] is main.run_download and submitted[0][1][0]==job['id']


def test_removing_queued_job_cancels_deferred_and_executor_work(monkeypatch):
    job=failed_job('queued_remove')|{'status':'queued','stage':'source_wait'};main.jobs[job['id']]=job
    main.source_access.youtube.limited();request=main.DownloadRequest(url=job['url'])
    main.deferred_downloads.defer(job['id'],request)
    assert client.post('/api/jobs/remove',json={'job_ids':[job['id']]}).json()['removed']==[job['id']]
    assert job['id'] not in main.deferred_downloads.pending
    monkeypatch.setattr(main,'extract_video',lambda *args:pytest.fail('Removed queued job fetched'))
    main.run_download(job['id'],request)  # An executor Future already queued before removal is harmless.
    assert job['id'] not in main.jobs


def test_queued_removal_race_cannot_delete_running_worker_files(monkeypatch):
    job=failed_job('worker_lock')|{'status':'queued'};main.jobs[job['id']]=job
    entered=threading.Event();release=threading.Event()
    def paused(*args):entered.set();assert release.wait(2)
    monkeypatch.setattr(main,'perform_download',paused)
    worker=threading.Thread(target=main.run_download,args=(job['id'],main.DownloadRequest(url=job['url'])))
    worker.start()
    try:
        assert entered.wait(2)
        result=client.post('/api/jobs/remove',json={'job_ids':[job['id']]}).json()
        assert not result['removed'] and result['failed'][0]['status']==409
        assert job['id'] in main.jobs
    finally:release.set();worker.join(timeout=2)


@pytest.mark.parametrize('message,code,automatic',[
    ('HTTP Error 503: upstream unavailable','network',True),
    ('The source connection timed out','network',True),
    ('HTTP Error 429: Too Many Requests','rate_limit',True),
    ('Video unavailable','unavailable',True),
    ('The video is unavailable or has been removed.','unavailable',True),
    ('Video has been removed','removed',False),
    ('Private video - Sign in','access',False),
    ('This video is DRM-protected','restricted',False),
    ('Not available in your country','restricted',False),
    ('Not enough free disk space','storage',False),
    ('The selected folder is not writable','storage',False),
    ('Requested format is not available','other',False),
])
def test_recovery_classifies_temporary_and_actionable_errors(message,code,automatic):
    issue=main.recovery.classify(message)
    assert issue['code']==code and issue['retryable'] is automatic
    assert issue['action']


def test_unavailable_uses_one_alternate_client_with_original_options(monkeypatch):
    calls=[]
    def extracted(dl,url,download=False):
        calls.append(dl.params)
        if len(calls)==1:raise main.yt_dlp.utils.DownloadError('[youtube] example: Video unavailable')
        return {'title':'Available via alternate client','formats':[]}
    monkeypatch.setattr(main.yt_dlp.YoutubeDL,'extract_info',extracted)
    result=main.extract_video('https://youtu.be/example',False,main.options()|{'outtmpl':'keep-this'})
    assert result['title']=='Available via alternate client' and len(calls)==2
    assert calls[1]['extractor_args']['youtube']['player_client']==['web','web_safari']
    assert calls[1]['outtmpl']['default']=='keep-this'
    assert main.source_access.youtube.metadata_slot.active==0


@pytest.mark.parametrize('reason',['Private video','Video unavailable: removed by uploader','Video unavailable: not available in your country','HTTP Error 429: Too Many Requests'])
def test_client_fallback_never_retries_restricted_or_rate_limited_responses(monkeypatch,reason):
    calls=[]
    def extracted(*args,**kwargs):calls.append(1);raise RuntimeError(reason)
    monkeypatch.setattr(main.yt_dlp.YoutubeDL,'extract_info',extracted)
    with pytest.raises(Exception):main.extract_video('https://youtu.be/example',False,main.options())
    assert len(calls)==1


def test_automatic_retry_budget_is_bounded_and_survives_restart(monkeypatch):
    held=[]
    monkeypatch.setattr(main.deferred_downloads,'defer',lambda *args,**kwargs:held.append((args,kwargs)))
    monkeypatch.setattr(main,'extract_video',lambda *args:(_ for _ in ()).throw(RuntimeError('Connection timed out')))
    job=failed_job('auto_bounded')|{'status':'queued'};main.jobs[job['id']]=job
    request=main.DownloadRequest(url=job['url'])
    for attempt in range(4):main.run_download(job['id'],request)
    assert len(held)==3 and job['status']=='failed' and job['auto_attempts']==3
    assert job['recovery_exhausted'] and job['error_code']=='network'
    restored=main.storage.restore(main.DATA);assert restored[job['id']]['auto_attempts']==3
    main.initialize_recovery();assert len(held)==3


def test_recovery_scheduled_after_other_first_pass_work(monkeypatch):
    job=failed_job('retry_end')|{'status':'failed','error':'Connection timed out'};main.jobs[job['id']]=job
    other=failed_job('other_first')|{'status':'queued'};main.jobs[other['id']]=other
    held=[];monkeypatch.setattr(main.deferred_downloads,'defer',lambda *args,**kwargs:held.append(args))
    assert main.schedule_recovery(job['id'],main.DownloadRequest(url=job['url']),main.recovery.classify(job['error']),job['error'])
    assert main.recovery_delay(job['id'],main.DownloadRequest(url=job['url']))>=1
    main.jobs[other['id']]['status']='completed'
    assert main.recovery_delay(job['id'],main.DownloadRequest(url=job['url']))==0
    assert job['stage']=='retry_wait' and job['auto_attempts']==1


def test_recovery_pause_resume_and_manual_retry_reset(monkeypatch):
    job=failed_job('settings_retry')|{'error':'Connection timed out'};main.jobs[job['id']]=job
    monkeypatch.setattr(main.pool,'submit',lambda *args:None)
    assert client.post('/api/recovery/settings',json={'enabled':True}).status_code==200
    assert job['stage']=='retry_wait'
    response=client.post('/api/recovery/settings',json={'enabled':False})
    assert response.json()['enabled'] is False and job['status']=='failed'
    assert job['id'] not in main.deferred_downloads.pending
    assert client.post('/api/jobs/settings_retry/retry').status_code==202
    assert main.jobs[job['id']]['auto_attempts']==0
    assert not main.recovery_enabled()


def test_recovery_reconsiders_eligible_legacy_failures_only(monkeypatch):
    temporary=failed_job('temporary')|{'error':'The video is unavailable or has been removed.'}
    permanent=failed_job('permanent')|{'error':'This video requires sign-in, age verification, or private access.'}
    for job in [temporary,permanent]:main.jobs[job['id']]=job
    main.initialize_recovery()
    assert temporary['status']=='queued' and temporary['auto_attempts']==1
    assert permanent['status']=='failed'
    data=client.get('/api/recovery').json();assert data['waiting']==1 and data['needs_action']==1
    assert all(item['action'] for item in data['items'])


def test_recovery_retains_persisted_future_wait_on_restart(monkeypatch):
    job=failed_job('future_wait')|{'error':'Interrupted by a server restart.','retry_at':main.time.time()+500}
    main.jobs[job['id']]=job
    main.initialize_recovery()
    assert job['retry_at']>=main.time.time()+495


def test_recovery_and_help_keep_owner_access_controls(monkeypatch):
    monkeypatch.setenv('STREAMVAULT_PASSWORD','local-owner')
    assert client.get('/api/recovery').status_code==401
    assert client.get('/help').status_code==401
    assert client.post('/api/recovery/settings',json={'enabled':False},auth=('admin','local-owner'),headers={'Origin':'https://other.example'}).status_code==403


def test_separate_english_help_documents_are_served_and_downloadable():
    topics=['getting-started','single-video','bulk-downloads','playlists','formats-quality','download-location','playback','library-selection','automatic-recovery','troubleshooting','updates-backups']
    assert client.get('/help').status_code==200
    for topic in topics:
        document=client.get('/help/'+topic)
        assert document.status_code==200 and '<html lang="en">' in document.text
        assert '/static/help/'+topic+'.md' in document.text
        assert client.get('/static/help/'+topic+'.md').status_code==200
    assert client.get('/help/does-not-exist').status_code==404
    assert client.get('/static/product.js').status_code==200
    assert client.get('/static/product.css').status_code==200


def test_pausing_unstarted_recovery_does_not_burn_single_unavailable_attempt():
    job=failed_job('pending_once')|{'error':'Video unavailable'};main.jobs[job['id']]=job
    main.initialize_recovery()
    assert job['auto_attempts']==1 and job['recovery_pending']
    assert client.post('/api/recovery/settings',json={'enabled':False}).status_code==200
    assert job['auto_attempts']==0 and not job['recovery_pending']
    assert client.post('/api/recovery/settings',json={'enabled':True}).status_code==200
    assert job['status']=='queued' and job['auto_attempts']==1


def test_restart_retains_pending_budget_without_an_extra_consumed_attempt():
    job=failed_job('pending_restart')|{'error':'Interrupted by a server restart.','last_error':'Video unavailable','auto_attempts':1,'recovery_pending':True}
    main.jobs[job['id']]=job
    main.initialize_recovery()
    assert job['status']=='queued' and job['auto_attempts']==1 and job['recovery_pending']


def test_deferred_timing_is_per_job_and_other_sources_can_proceed():
    sent=[];ready=threading.Event()
    deferred=main.DeferredDownloads(lambda:600,lambda id,req:(sent.append(id),ready.set()),delay_for=lambda id,req:600 if id=='youtube' else 0)
    try:
        deferred.defer('youtube','yt');deferred.defer('other','tk')
        assert ready.wait(2) and sent==['other']
        assert 'youtube' in deferred.pending
    finally:deferred.stop()


@pytest.mark.parametrize('installed,expected',[('2026.8.19',False),('2026.10.21',True),('2026.11.1',True)])
def test_launcher_detects_outdated_extractor_without_downgrading_newer(tmp_path,monkeypatch,installed,expected):
    from app import dependencies
    lock=tmp_path/'requirements.lock';lock.write_text('yt-dlp==2026.10.21\nyt-dlp-ejs==0.8.0\ncertifi==2026.10.19\n')
    monkeypatch.setattr(dependencies,'version',lambda name:{'yt-dlp':installed,'yt-dlp-ejs':'0.8.0','certifi':'2026.10.19'}[name])
    assert dependencies.ready(lock) is expected


def test_network_proxy_rejection_has_a_distinct_actionable_message():
    error=RuntimeError("Unable to connect to proxy: Tunnel connection failed: 403 Forbidden")
    assert 'network proxy' in main.download_error(error)
    issue=main.recovery.classify(error)
    assert issue['code']=='proxy' and not issue['retryable']


def test_legacy_auto_recovery_waits_for_confirmed_desktop_destination(tmp_path,monkeypatch):
    local,data,chosen=local_desktop_client(tmp_path,monkeypatch)
    job=failed_job('legacy_auto_folder')|{'error':'Connection timed out','recoverable':True};main.jobs[job['id']]=job
    main.initialize_recovery()
    assert job['status']=='failed' and not job.get('output_dir') and 'folder' in job['next_action']
    assert local.post('/api/download-location',json={'path':str(chosen)}).status_code==200
    assert job['status']=='queued' and job['output_dir']==str(chosen) and job['legacy_recovery']
