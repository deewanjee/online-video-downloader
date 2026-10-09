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
    monkeypatch.setattr(main, 'DATA', tmp_path)
    monkeypatch.setattr(main, 'jobs', {})
    monkeypatch.setattr(main, 'analysis_cache', {})
    monkeypatch.delenv('STREAMVAULT_PASSWORD', raising=False)
    monkeypatch.delenv('STREAMVAULT_PUBLIC', raising=False)
    monkeypatch.delenv('STREAMVAULT_USERNAME', raising=False)
    monkeypatch.delenv('STREAMVAULT_SYSTEM_CERTS', raising=False)


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


def test_video_only_with_unknown_resolution_downloads_and_respects_cap(tmp_path, monkeypatch):
    source = tmp_path / 'silent.mp4'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=purple:s=320x480:d=1', '-c:v', 'libx264', str(source)], check=True)
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}/silent.mp4'
    def extract(self, *args, **kwargs):
        return self.process_ie_result({'id': 'silent', 'title': 'Silent source', 'formats': [{'format_id': 'video-only', 'url': url, 'ext': 'mp4', 'vcodec': 'h264', 'acodec': 'none'}]}, download=True)
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
