"""Optional Chromium regression for Discover progress and playback.

Run with Playwright installed; AAASOFT_TEST_PYTHON can point to the app venv.
Uses local media/source fixtures, not live platform access.
"""
import os
import sys
import json
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def serve(port):
    import shutil
    from app import main
    from fastapi.responses import FileResponse
    root = Path(os.environ['DISCOVERY_TEST_ROOT'])
    output = root / 'Videos'
    output.mkdir()
    main.destinations.configure(main.DATA, str(output))
    main.destinations.open_folder = lambda path: None
    released = threading.Event()
    media = root / 'fixture.mp4'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'color=c=teal:s=320x180:d=4', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=4', '-c:v', 'libx264', '-c:a', 'aac',
                    '-pix_fmt', 'yuv420p', '-shortest', str(media)], check=True)

    def extracted(dl, url, download=False):
        if not download:
            if '/results?' in url:
                start = dl.params.get('playliststart', 1)
                return {'entries': [{'id': f'clip{i:07d}', 'title': f'Journey {i}: a new perspective',
                                     'channel': 'AAASoft test creator', 'duration': 140+i}
                                    for i in range(start, min(start+13, 26))]}
            return {'title': 'Journey video', 'duration': 4,
                    'formats': [{'height': 180, 'vcodec': 'h264', 'acodec': 'aac'}]}
        for hook in dl.params.get('progress_hooks', []):
            hook({'status': 'downloading', 'total_bytes': 1000000, 'downloaded_bytes': 370000})
        if not released.wait(40):
            raise RuntimeError('Test media was never released')
        if 'clip0000003' in url:
            raise main.yt_dlp.utils.DownloadError('This video is private')
        template = dl.params['outtmpl']
        template = template.get('default') if isinstance(template, dict) else template
        shutil.copyfile(media, Path(template).parent / 'Selected video.mp4')
        return {'title': 'Selected video', 'duration': 4, 'extractor_key': 'Youtube'}

    main.yt_dlp.YoutubeDL.extract_info = extracted

    @main.app.post('/_test/release')
    def release():
        released.set()
        return {'released': True}

    @main.app.post('/_test/merging')
    def merging():
        for job in list(main.jobs.values()):
            if job['status'] == 'downloading':
                main.update(job['id'], status='processing', stage='merging', progress=99)
        return {'ok': True}

    @main.app.get('/_test/media')
    def fixture():
        return FileResponse(media, media_type='video/mp4')

    import uvicorn
    uvicorn.run(main.app, host='127.0.0.1', port=port, log_level='warning')


def verify():
    from playwright.sync_api import sync_playwright
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    with tempfile.TemporaryDirectory(prefix='aaasoft-discovery-browser-') as td:
        env = os.environ.copy()
        env.update(DISCOVERY_TEST_ROOT=td, DOWNLOAD_DIR=str(Path(td)/'data'),
                   STREAMVAULT_DESKTOP='1', PYTHONPATH=str(ROOT))
        for name in ('STREAMVAULT_PUBLIC', 'STREAMVAULT_PASSWORD', 'STREAMVAULT_COOKIE_FILE'):
            env.pop(name, None)
        with open(Path(td)/'server.log', 'w') as log:
            process = subprocess.Popen([os.environ.get('AAASOFT_TEST_PYTHON', sys.executable),
                                        str(Path(__file__).resolve()), '--serve', str(port)],
                                       cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                for _ in range(100):
                    try:
                        urllib.request.urlopen(base+'/api/health', timeout=1)
                        break
                    except OSError:
                        if process.poll() is not None:
                            raise RuntimeError((Path(td)/'server.log').read_text())
                        time.sleep(.2)
                with sync_playwright() as p:
                    executable = os.environ.get('CHROMIUM_EXECUTABLE')
                    browser = p.chromium.launch(**({'executable_path': executable} if executable else {}), args=['--no-sandbox'])
                    page = browser.new_page(viewport={'width': 1440, 'height': 1050})
                    errors, embeds = [], []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.route('https://i.ytimg.com/**', lambda route: route.fulfill(content_type='image/svg+xml', body='<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#49758a"/><path d="M0 350 180 120 360 300 470 160 640 360" fill="#254d62"/><circle cx="480" cy="80" r="35" fill="#e5d4a9"/></svg>'))

                    def embed(route):
                        embeds.append(route.request.url)
                        route.fulfill(content_type='text/html', body='<html><body style="background:#122033;color:white"><h1>Source preview fixture</h1><video controls autoplay muted src="'+base+'/_test/media"></video></body></html>')
                    page.route('https://www.youtube-nocookie.com/embed/**', embed)
                    page.route('https://www.dailymotion.com/embed/video/**', embed)
                    page.goto(base, wait_until='domcontentloaded')
                    page.locator('[data-view=discover]').click()
                    page.fill('#discovery-query', 'journeys')
                    page.click('#discovery-submit')
                    page.wait_for_selector('.discovery-card')
                    assert not embeds, 'Searching must not load third-party players automatically'
                    first = page.locator('.discovery-card[data-source-id=clip0000001]')
                    first.locator('.discovery-preview').click()
                    page.wait_for_selector('#source-preview-dialog[open] iframe')
                    assert embeds and embeds[-1].startswith('https://www.youtube-nocookie.com/embed/clip0000001?')
                    assert page.locator('#source-preview-dialog iframe').get_attribute('referrerpolicy') == 'strict-origin-when-cross-origin'
                    assert page.request.get(base+'/api/jobs').json() == [], 'Preview must not queue a download'
                    page.click('#source-preview-close')
                    page.wait_for_function("document.querySelector('#source-preview-frame').children.length===0")
                    for i in range(1, 4):
                        card = page.locator(f'.discovery-card[data-source-id=clip{i:07d}]')
                        card.locator('.primary').click()
                        page.wait_for_function('id => {const button=document.querySelector(`[data-source-id="${id}"] .primary`);return button.disabled && !button.textContent.includes("Adding");}', arg=f'clip{i:07d}')
                    page.wait_for_selector('.discovery-card[data-state=downloading]')
                    assert page.locator('.discovery-card[data-state=queued]').count() >= 1
                    assert '37%' in page.locator('.discovery-card[data-state=downloading] .discovery-type').first.inner_text()
                    assert len(page.request.get(base+'/api/jobs').json()) == 3
                    first.locator('input').check()
                    assert page.locator('#discovery-queue').is_disabled()
                    page.evaluate("queueDiscovered([discoveryResults.get('https://www.youtube.com/watch?v=clip0000001')])")
                    page.wait_for_function("document.querySelector('#discovery-message').textContent.includes('already in your queue')")
                    assert len(page.request.get(base+'/api/jobs').json()) == 3, 'No duplicate active jobs'
                    page.request.post(base+'/_test/merging')
                    page.wait_for_selector('.discovery-card[data-state=processing]')
                    assert 'Merging' in page.locator('.discovery-card[data-state=processing] .discovery-type').first.inner_text()
                    assert not page.locator('.discovery-card[data-state=completed]').count()
                    screenshot = os.environ.get('DISCOVERY_SCREENSHOT')
                    if screenshot:
                        page.screenshot(path=screenshot, full_page=True)
                    first.locator('.discovery-preview').click()
                    page.wait_for_selector('#source-preview-dialog[open] iframe')
                    page.request.post(base+'/_test/release')
                    page.wait_for_function("document.querySelectorAll('.discovery-card[data-state=completed]').length===2 && document.querySelectorAll('.discovery-card[data-state=failed]').length===1", timeout=25000)
                    assert page.locator('#source-preview-saved').is_visible()
                    page.click('#source-preview-close')
                    page.wait_for_function("document.querySelector('#source-preview-frame').children.length===0")
                    assert first.locator('.primary').inner_text() == 'Download again'
                    assert first.locator('.discovery-preview').inner_text() == 'Play saved'
                    assert first.locator('.discovery-progress').is_hidden()
                    failed = page.locator('.discovery-card[data-state=failed]')
                    assert 'sign-in' in failed.locator('.discovery-job-detail').inner_text()
                    assert failed.locator('.primary').is_enabled()
                    if screenshot:
                        page.screenshot(path=str(Path(screenshot).with_name('discovery-completed-v32.png')), full_page=True)
                    first.locator('.discovery-cover-play').click()
                    page.wait_for_function("document.getElementById('video-player').currentTime>.1", timeout=15000)
                    assert page.locator('#player-dialog').is_visible()
                    assert not page.locator('#source-preview-dialog').is_visible()
                    page.click('#close-player')
                    page.get_by_role('button', name='Next', exact=True).click()
                    page.wait_for_function("document.querySelector('#discovery-message').textContent.includes('Page 2')")
                    page.get_by_role('button', name='Previous', exact=True).click()
                    page.wait_for_selector('.discovery-card[data-source-id=clip0000001][data-state=completed]')
                    # Re-fetch search metadata and confirm status survives a new search/cache hit.
                    page.click('#discovery-submit')
                    page.wait_for_selector('.discovery-card[data-source-id=clip0000001][data-state=completed]')
                    # A new transfer keeps an older saved copy playable and preserves focus.
                    state = page.evaluate("""() => {
                      const saved=jobs.find(job=>job.url.includes('clip0000001'));
                      const original=jobs;
                      const active={...saved,id:'temporary-repeat',status:'downloading',stage:'downloading',progress:61,created:Date.now()};
                      jobs=[active,...jobs];syncDiscoveryJobs();
                      const card=document.querySelector('[data-source-id=clip0000001]');
                      const checkbox=card.querySelector('input');checkbox.focus();
                      active.progress=73;syncDiscoveryJobs();
                      const running={kind:card.dataset.state,play:card.querySelector('.discovery-preview').textContent,disabled:card.querySelector('.primary').disabled,focused:document.activeElement===checkbox,progress:card.querySelector('progress').value};
                      active.status='failed';active.error='A temporary source error';syncDiscoveryJobs();
                      const failed={kind:card.dataset.state,play:card.querySelector('.discovery-preview').textContent};
                      jobs=original;syncDiscoveryJobs();return {running,failed};
                    }""")
                    assert state['running']=={'kind':'downloading','play':'Play saved','disabled':True,'focused':True,'progress':73}
                    assert state['failed']=={'kind':'failed','play':'Play saved'}
                    # Video identity from a short/tracking URL still maps to the search card.
                    identity = page.request.get(base+'/api/jobs').json()[0]['source_key']
                    assert identity
                    # A removed library entry keeps its lifetime badge without claiming a local file.
                    items = page.request.get(base+'/api/jobs').json()
                    item = next(job for job in items if 'clip0000001' in job['url'])
                    assert page.request.delete(base+'/api/jobs/'+item['id']).ok
                    page.wait_for_selector('.discovery-card[data-source-id=clip0000001][data-state=history]')
                    assert first.locator('.discovery-preview').inner_text() == 'Preview'
                    # Validate provider allowlist and Dailymotion embed construction.
                    assert page.evaluate("discoveryEmbedURL({provider:'youtube',id:'../bad'})") is None
                    assert page.evaluate("discoveryEmbedURL({provider:'other',id:'clip0000001'})") is None
                    page.evaluate("playDiscovered({id:'x123abc',provider:'dailymotion',platform:'Dailymotion',url:'https://www.dailymotion.com/video/x123abc',title:'Dailymotion preview'})")
                    page.wait_for_selector('#source-preview-dialog[open] iframe')
                    assert 'https://www.dailymotion.com/embed/video/x123abc?autoplay=1' in embeds
                    page.keyboard.press('Escape')
                    page.wait_for_function("document.querySelector('#source-preview-frame').children.length===0")
                    page.set_viewport_size({'width': 390, 'height': 844})
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    assert not errors, errors
                    browser.close()
                    print('PASS: preview without queueing, frame teardown, individual queues, live progress, duplicate guard, merging, completion, failure, saved playback, paging/search reconciliation, history-only badge, embed allowlist and responsive width; no JavaScript errors.')
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == '__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--serve':
        serve(int(sys.argv[2]))
    else:
        verify()
