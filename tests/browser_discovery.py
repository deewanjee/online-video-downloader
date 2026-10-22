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
            if 'tiktok.com/@' in url or url.startswith('tiktokuser:'):
                if '/video/' not in url:
                    return {'_type':'playlist','title':'TikTok fixture creator','entries':[{'id':str(7000000000000000000+i),'title':f'Creator post {i}','duration':4,'thumbnail':f'https://p16.tiktokcdn.com/demo/{i}.jpg'} for i in range(1,64)]}
                video_id=url.rsplit('/',1)[-1]
                if video_id=='7000000000000000002':raise main.yt_dlp.utils.DownloadError('Private video')
                if video_id=='7000000000000000003':return {'formats':[{'vcodec':'none','acodec':'aac'}]}
                return {'title':'TikTok public video','duration':4,'formats':[{'height':180,'vcodec':'h264','acodec':'aac'}],'uploader_id':'creator','channel_id':'MS4wLjABAAAA'+'A'*64}
            if 'list=' in url:
                return {'_type':'playlist','title':'Playlist with previous downloads','entries':[{'id':f'clip{i:07d}','title':'[Private video]' if i==3 else f'Playlist video {i}','availability':'private' if i==3 else 'public'} for i in range(1,6)]}
            if '/results?' in url:
                if 'sp=EgIQAw' in url:return {'entries':[{'id':'PLhistory123456','title':'Playlist result','playlist_count':5}]}
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

    @main.app.post('/_test/tiktok-history')
    def history():
        job={'id':'past-tiktok','url':'https://www.tiktok.com/@creator/video/7000000000000000001','title':'Previously saved TikTok','status':'completed','created':1}
        main.storage.save(main.DATA,job);main.storage.delete(main.DATA,job['id'])
        return {'seeded':True}

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
                    page.emulate_media(reduced_motion='reduce')
                    errors, embeds = [], []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.route('https://i.ytimg.com/**', lambda route: route.fulfill(content_type='image/svg+xml', body='<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#49758a"/><path d="M0 350 180 120 360 300 470 160 640 360" fill="#254d62"/><circle cx="480" cy="80" r="35" fill="#e5d4a9"/></svg>'))

                    def embed(route):
                        embeds.append(route.request.url)
                        route.fulfill(content_type='text/html', body='<html><body style="background:#122033;color:white"><h1>Source preview fixture</h1><video controls autoplay muted src="'+base+'/_test/media"></video></body></html>')
                    page.route('https://www.youtube-nocookie.com/embed/**', embed)
                    page.route('https://www.dailymotion.com/embed/video/**', embed)
                    page.route('https://www.tiktok.com/player/v1/**',embed)
                    page.route('https://p16.tiktokcdn.com/**',lambda route:route.fulfill(content_type='image/svg+xml',body='<svg xmlns="http://www.w3.org/2000/svg" width="200" height="160"><rect width="200" height="160" fill="#3b6172"/></svg>'))
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
                    page.wait_for_function("document.querySelector('#discovery-message').textContent.includes('already queued or downloaded')")
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
                    assert first.is_hidden()
                    page.locator('[data-discovery-tab=downloaded]').click()
                    assert first.is_visible()
                    assert first.locator('.primary').inner_text() == 'Already downloaded'
                    assert first.locator('.primary').is_disabled()
                    page.check('#allow-repeat')
                    assert first.locator('.primary').inner_text() == 'Download again'
                    assert first.locator('.primary').is_enabled()
                    page.uncheck('#allow-repeat')
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
                    page.locator('[data-view=completed]').click()
                    assert page.locator('#status-filter').input_value()=='completed'
                    assert page.locator('#all-jobs .job.completed').count()==2
                    assert page.evaluate("getComputedStyle(document.getElementById('all-jobs')).gridTemplateColumns.split(' ').length===5")
                    page.set_viewport_size({'width':1920,'height':1050})
                    assert page.evaluate("getComputedStyle(document.getElementById('all-jobs')).gridTemplateColumns.split(' ').length===6")
                    page.set_viewport_size({'width':1440,'height':1050})
                    if screenshot:
                        page.locator('#all-jobs .job').first.focus()
                        page.screenshot(path=str(Path(screenshot).with_name('completed-library-v33.png')),full_page=True)
                    page.locator('#all-jobs .job').first.focus()
                    page.locator('#all-jobs .job').first.hover()
                    before_repeat=len(page.request.get(base+'/api/jobs').json())
                    page.locator('#all-jobs .job').first.locator('.redownload-button').click()
                    page.wait_for_selector('#repeat-dialog[open]')
                    page.click('#repeat-cancel')
                    assert len(page.request.get(base+'/api/jobs').json())==before_repeat
                    page.locator('[data-library-tab=pending]').click()
                    assert page.locator('#all-jobs .job.completed').count()==0
                    page.locator('[data-view=discover]').click()
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
                    # Playlist history remains visible after library deletion and excludes old copies.
                    page.locator('[data-view=home]').click()
                    page.locator('#playlist-panel summary').click()
                    page.fill('#playlist-url','https://www.youtube.com/playlist?list=PLhistory123456')
                    page.click('#playlist-load')
                    page.wait_for_selector('#playlist-result:not([hidden])')
                    assert page.locator('#playlist-videos input:checked').count()==2
                    for video_id in ('clip0000001','clip0000002'):
                        box=page.locator('#playlist-videos input[value='+video_id+']')
                        assert box.is_disabled()
                        assert 'Already downloaded' in box.locator('..').inner_text()
                    if screenshot:page.screenshot(path=str(Path(screenshot).with_name('playlist-history-v33.png')),full_page=True)
                    page.check('#allow-repeat')
                    assert page.locator('#playlist-videos input[value=clip0000001]').is_enabled()
                    assert page.locator('#playlist-videos input[value=clip0000003]').is_disabled()
                    page.uncheck('#allow-repeat')
                    page.fill('#url','https://www.youtube.com/watch?v=clip0000001')
                    page.click('#analyze-button')
                    page.wait_for_selector('#video-result:not([hidden])')
                    assert page.locator('#download-button').is_disabled()
                    assert page.locator('#download-button').inner_text()=='Already downloaded'
                    page.check('#allow-repeat')
                    assert page.locator('#download-button').is_enabled()
                    page.uncheck('#allow-repeat')
                    response=page.request.post(base+'/api/download',data={'url':'https://youtu.be/clip0000001?si=tracking'})
                    assert response.status==409
                    page.locator('[data-view=activity]').click()
                    page.wait_for_selector('#activity-rows tr a')
                    assert 'clip0000001' in page.locator('#activity-rows').inner_text()
                    if screenshot:page.screenshot(path=str(Path(screenshot).with_name('activity-log-v33.png')),full_page=True)
                    records=page.request.get(base+'/api/activity/export?format=json').json()
                    assert any(item['event']=='completed' for item in records)
                    assert any(item['event']=='duplicate_skipped' and 'clip0000001' in item['url'] for item in records)
                    assert any(item['event']=='removed' for item in records)
                    assert page.request.get(base+'/api/activity/export?format=csv').headers['content-type'].startswith('text/csv')
                    assert page.evaluate("getComputedStyle(document.getElementById('activity-search')).fontSize==='12px'")
                    assert page.evaluate("getComputedStyle(document.querySelector('.discovery-card h2')).fontSize==='14px'")
                    page.locator('[data-view=discover]').click()
                    page.select_option('#discovery-kind','playlists')
                    page.click('#discovery-submit')
                    page.wait_for_selector('.discovery-card[data-result-kind=playlists]')
                    assert page.locator('.discovery-card[data-result-kind=playlists]').is_visible()
                    assert page.locator('[data-discovery-tab=downloaded]').is_hidden()
                    # Validate provider allowlist and Dailymotion embed construction.
                    assert page.evaluate("discoveryEmbedURL({provider:'youtube',id:'../bad'})") is None
                    assert page.evaluate("discoveryEmbedURL({provider:'other',id:'clip0000001'})") is None
                    page.evaluate("playDiscovered({id:'x123abc',provider:'dailymotion',platform:'Dailymotion',url:'https://www.dailymotion.com/video/x123abc',title:'Dailymotion preview'})")
                    page.wait_for_selector('#source-preview-dialog[open] iframe')
                    assert 'https://www.dailymotion.com/embed/video/x123abc?autoplay=1' in embeds
                    page.keyboard.press('Escape')
                    page.wait_for_function("document.querySelector('#source-preview-frame').children.length===0")
                    page.locator('[data-view=discover]').click()
                    # TikTok account workspace: auto-check, history, paging, selection and real saves.
                    page.request.post(base+'/_test/tiktok-history')
                    page.locator('[data-view=sources]').click()
                    assert page.locator('#source-tabs button').count()==7
                    page.fill('#account-name','@creator')
                    page.click('#account-load')
                    page.wait_for_function("document.querySelector('#account-status').textContent.includes('Checks complete')",timeout=30000)
                    assert page.locator('#account-videos .account-card').count()==30
                    assert page.evaluate("getComputedStyle(document.getElementById('account-videos')).gridTemplateColumns.split(' ').length===5")
                    page.set_viewport_size({'width':1920,'height':1050})
                    assert page.evaluate("getComputedStyle(document.getElementById('account-videos')).gridTemplateColumns.split(' ').length===6")
                    page.set_viewport_size({'width':1440,'height':1050})
                    assert '63 listed' in page.locator('#account-counts').inner_text()
                    assert '61 available' in page.locator('#account-counts').inner_text()
                    assert page.locator('#account-videos input[value="7000000000000000001"]').is_disabled()
                    assert page.locator('#account-videos input[value="7000000000000000002"]').is_disabled()
                    assert page.locator('#account-videos input[value="7000000000000000003"]').is_disabled()
                    if screenshot:page.screenshot(path=str(Path(screenshot).with_name('tiktok-account-v34.png')),full_page=True)
                    card=page.locator('.account-card[data-video-id="7000000000000000004"]')
                    card.locator('.account-preview').click()
                    page.wait_for_selector('#source-preview-dialog[open] iframe')
                    assert any('/player/v1/7000000000000000004?' in url for url in embeds)
                    assert page.locator('#source-preview-download').is_hidden()
                    page.click('#source-preview-close')
                    page.select_option('#account-filter','available')
                    page.wait_for_function("document.querySelector('#account-videos input[value=\"7000000000000000002\"]')===null")
                    page.locator('#account-videos input[value="7000000000000000004"]').check()
                    page.locator('#account-pagination').get_by_role('button',name='Next',exact=True).click()
                    page.wait_for_function("document.querySelector('#account-pagination').textContent.includes('Page 2')")
                    assert page.locator('#account-selected').inner_text()=='1 selected'
                    page.click('#account-select-all')
                    page.wait_for_function("document.querySelector('#account-selected').textContent==='60 selected'")
                    page.click('#account-clear')
                    page.locator('#account-pagination').get_by_role('button',name='Previous',exact=True).click()
                    page.wait_for_selector('#account-videos input[value="7000000000000000004"]')
                    page.locator('#account-videos input[value="7000000000000000004"]').check()
                    page.locator('#account-videos input[value="7000000000000000005"]').check()
                    page.click('#account-queue')
                    page.wait_for_function("document.querySelector('#account-status').textContent.includes('2 videos queued')")
                    page.wait_for_function("fetch('/api/jobs').then(r=>r.json()).then(rows=>rows.filter(j=>j.url.includes('tiktok.com') && j.status==='completed').length===2)",timeout=20000)
                    page.locator('[data-source=tiktok] .source-single').get_by_role('button',name='View TikTok completed videos').click()
                    assert page.locator('#source-filter').input_value()=='TikTok'
                    page.wait_for_function("document.querySelectorAll('#all-jobs .job.completed').length===2")
                    page.locator('[data-view=sources]').click()
                    page.locator('#source-tabs [data-source=instagram]').click()
                    assert page.locator('#source-panels [data-source=instagram]').is_visible()
                    assert not page.locator('#source-panels [data-source=tiktok]').is_visible()
                    page.locator('#source-tabs [data-source=tiktok]').click()
                    assert page.locator('#account-name').input_value()=='@creator'
                    page.set_viewport_size({'width': 390, 'height': 844})
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    assert not errors, errors
                    browser.close()
                    print('PASS: TikTok account checks, source workspaces, account paging/selection/preview/history/saves, exact platform filter, archive, persistent duplicate prevention, playlist history, retained/exportable activity, restored compact typography, preview without queueing, frame teardown, individual queues, live progress, duplicate guard, merging, completion, failure, saved playback, paging/search reconciliation, history-only badge, embed allowlist and responsive width; no JavaScript errors.')
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
