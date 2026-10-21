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
    assert 'StreamVault' in client.get('/').text
    assert client.get('/static/app.js').status_code == 200
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
def test_real_download_and_conversion(tmp_path, monkeypatch, output):
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
    try:
        req = main.DownloadRequest(url=f'http://127.0.0.1:{server.server_port}/fixture.mp4', format=output, quality='720')
        main.run_download(job_id, req)
        assert main.jobs[job_id]['status'] == 'completed', main.jobs[job_id]
        assert main.jobs[job_id]['has_audio'] is True
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
            preview = main.playable_file(storage / job_id / main.jobs[job_id]['filename'], main.jobs[job_id])
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
    finally:
        main.jobs.pop(job_id, None)
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
def isolate_history(tmp_path, monkeypatch):
    class ImmediatePreview:
        def submit(self, function, *args):
            function(*args)
    monkeypatch.setattr(main, 'preview_pool', ImmediatePreview())
    monkeypatch.setattr(main, 'DATA', tmp_path)
    monkeypatch.setattr(main, 'jobs', {})
    monkeypatch.setattr(main, 'analysis_cache', {})
    monkeypatch.setattr(main, 'playlist_cache', {})
    monkeypatch.setattr(main, 'preview_locks', {})
    monkeypatch.setattr(main.source_access, 'youtube', main.source_access.YoutubeAccess())
    monkeypatch.delenv('STREAMVAULT_PASSWORD', raising=False)
    monkeypatch.delenv('STREAMVAULT_PUBLIC', raising=False)
    monkeypatch.delenv('STREAMVAULT_USERNAME', raising=False)
    monkeypatch.delenv('STREAMVAULT_SYSTEM_CERTS', raising=False)
    monkeypatch.delenv('STREAMVAULT_COOKIE_FILE', raising=False)


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
    access.slot.acquire()
    try:
        response = client.post('/api/analyze', json={'url': 'https://youtu.be/available'})
        assert response.status_code == 429
        assert 'already running' in response.json()['detail']
        assert response.headers['retry-after'] == '5'
    finally:
        access.slot.release()


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
            assert main.source_access.youtube.slot.active == 1
            yield playlist_entry(1)
            assert main.source_access.youtube.slot.active == 1
            yield playlist_entry(2)
        return {'_type': 'playlist', 'entries': pages()}
    monkeypatch.setattr(main.yt_dlp.YoutubeDL, 'extract_info', extracted)
    result = client.post('/api/playlist/analyze', json={'url': 'https://youtube.com/watch?v=clip0000001&list=PLfixture&index=3'})
    assert result.status_code == 200, result.text
    assert len(result.json()['videos']) == 2
    assert main.source_access.youtube.slot.active == 0


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
