import threading
import subprocess
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
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
    main.jobs[job_id] = {'id': job_id, 'status': 'queued', 'created': 0}
    try:
        req = main.DownloadRequest(url=f'http://127.0.0.1:{server.server_port}/fixture.mp4', format=output, quality='720')
        main.run_download(job_id, req)
        assert main.jobs[job_id]['status'] == 'completed', main.jobs[job_id]
        response = client.get(f'/api/jobs/{job_id}/file')
        assert response.status_code == 200
        assert len(response.content) > 1000
        assert client.delete(f'/api/jobs/{job_id}').status_code == 200
        assert not (storage / job_id).exists()
    finally:
        main.jobs.pop(job_id, None)
        server.shutdown()
        server.server_close()
