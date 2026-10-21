const $ = id => document.getElementById(id);
const UI_VERSION = '2.0.0';
const audioFormats = ['mp3', 'm4a', 'wav'];
function icon(name) {
  const paths = {play:'<path d="m8 5 11 7-11 7z"/>',arrow:'<path d="M6 18 18 6M6 6h12v12"/>',download:'<path d="M12 3v12m-5-5 5 5 5-5M5 16v5h14v-5"/>'};
  const span = document.createElement('span');
  span.innerHTML = `<svg class="ui-icon" viewBox="0 0 24 24" fill="${name==='play'?'currentColor':'none'}" stroke="currentColor" stroke-width="1.7" aria-hidden="true">${paths[name]}</svg>`;
  return span;
}
function analyzeLabel() { $('analyze-button').replaceChildren(document.createTextNode('Find my video '),icon('arrow')); }
const platforms = [['YouTube','▶','#efdfd7','#d63f35'],['Instagram','◎','#f0dce1','#bb4b71'],['TikTok','♪','#d8e3de','#263b30'],['Facebook','f','#dce3ed','#4268a5'],['X / Twitter','𝕏','#e3e4db','#30382f'],['Dailymotion','d','#d9e7e6','#367a7b'],['Threads','@','#e5dfd4','#595344']];
for (const id of ['platform-grid','all-platforms']) {
  for (const [name,icon,bg,color] of (id === 'all-platforms' ? [...platforms,['Other','◎','#e9edf4','#657189']] : platforms)) {
    const el = document.createElement('div'); el.className = 'platform';
    el.innerHTML = `<span class="platform-logo" style="background:${bg};color:${color}">${icon}</span><div><strong>${name}</strong><small>${name === 'Threads' ? 'Public posts & share links' : 'Public video links'}</small></div>`;
    if (name === 'YouTube') el.querySelector('.platform-logo').innerHTML = '<svg class="ui-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="m8 5 11 7-11 7z"/></svg>';
    if (id === 'all-platforms') {
      el.dataset.platform = name; el.hidden = name === 'Other';
      const counter = document.createElement('div'); counter.className = 'platform-counter';
      const value = document.createElement('span'); value.className = 'platform-download-count'; value.textContent = '0';
      const caption = document.createElement('small'); caption.textContent = 'Lifetime downloads'; counter.append(value,caption); el.append(counter);
    }
    $(id).append(el);
  }
}
let analyzed = null, jobs = [], busy = false, playingId = null;
let analysisVersion = 0, analysisController = null, refreshing = null, refreshAgain = false;
let noticeTimer = null, toastTimer = null;
let playerVersion = 0, previewTimer = null, previewController = null;
let originalFallback = null, originalTimer = null;
const rendered = new Map();
function view(name) {
  for (const n of ['home','downloads','platforms']) $(n).hidden = n !== name;
  document.querySelectorAll('.nav').forEach(el => { el.classList.toggle('active',el.dataset.view === name); el.setAttribute('aria-current', el.dataset.view === name ? 'page' : 'false'); });
  $('page-label').textContent = {home:'Overview',downloads:'Video library',platforms:'Platform insights'}[name];
  window.scrollTo(0,0);
}
document.querySelectorAll('.nav').forEach(el => el.onclick = () => view(el.dataset.view));
$('view-all').onclick = () => view('downloads'); $('see-platforms').onclick = () => view('platforms');
function notice(message,retryAfter = 0) {
  clearInterval(noticeTimer); noticeTimer = null;
  clearTimeout(toastTimer); $('toast').hidden = !message || !$('home').hidden; $('toast').textContent = message;
  if (message) toastTimer = setTimeout(() => { $('toast').hidden = true; },7000);
  const box = $('notice'); box.hidden = !message; box.setAttribute('aria-live','polite'); box.textContent = message;
  if (!(retryAfter > 0) || !message.includes('YouTube is limiting requests')) return;
  const until = Date.now()+retryAfter*1000;
  const update = () => {
    const seconds = Math.max(0,Math.ceil((until-Date.now())/1000));
    if (seconds) {
      box.setAttribute('aria-live','off');
      box.textContent = 'YouTube request limit: local wait '+duration(seconds)+'. Source verification may still be required afterwards.';
    } else {
      clearInterval(noticeTimer); noticeTimer = null; box.setAttribute('aria-live','polite');
      box.textContent = 'The local wait has ended. You may try once; YouTube can still require verification or continue limiting requests.';
    }
  };
  update(); noticeTimer = setInterval(update,1000);
}
async function api(path,options = {}) {
  const controller = new AbortController(), callerSignal = options.signal;
  const cancel = () => controller.abort();
  callerSignal?.addEventListener('abort',cancel,{once:true});
  if (callerSignal?.aborted) cancel();
  const timer = setTimeout(cancel,options.timeout || 60000);
  try {
    const response = await fetch(path,{...options,signal:controller.signal});
    if (response.status === 204) return null;
    const body = await response.text(); let data;
    try { data = JSON.parse(body); } catch { throw Error('The server returned an unreadable response (HTTP '+response.status+'). Check the server connection and retry.'); }
    if (!response.ok) {
      const message = response.status === 404 && path.startsWith('/api/playlist/') && data.detail === 'Not Found'
        ? 'The running AAASoft Video Studio server does not have the playlist API. Stop the old server with Ctrl+C, start AAASoft Video Studio from the updated project folder, then refresh with Ctrl+F5. If this persists, replace all project files with the latest update.'
        : typeof data.detail === 'string' ? data.detail : 'Request could not be completed (HTTP '+response.status+').';
      const error = Error(message);
      const seconds = Number(response.headers.get('Retry-After'));
      if (Number.isFinite(seconds) && seconds > 0) error.retryAfter = seconds;
      throw error;
    }
    return data;
  } catch(e) {
    if (e.name === 'AbortError' && !callerSignal?.aborted) throw Error('The request timed out. Check the server window before retrying; a download may still be running.');
    if (e instanceof TypeError) throw Error('Cannot reach AAASoft Video Studio. Check that the server window is open.');
    throw e;
  } finally { clearTimeout(timer); callerSignal?.removeEventListener('abort',cancel); }
}
function duration(s) { return Math.floor(s/60)+':'+String(Math.floor(s%60)).padStart(2,'0'); }
function sourceName(j) {
  try { return j.source_platform || j.platform || new URL(j.url).hostname.replace(/^www\./,''); } catch { return 'Your collection'; }
}
function endpoint(j,kind) { return '/api/jobs/'+encodeURIComponent(j.id)+'/'+kind; }
function image(parent,url,label,fallback = null) {
  if (!url) return;
  try { const parsed = new URL(url,location.origin); if (!['https:','http:'].includes(parsed.protocol) || parsed.username || parsed.password || (parsed.origin !== location.origin && parsed.protocol !== 'https:')) return; } catch { return; }
  const img = document.createElement('img'); img.alt = label; img.loading = 'lazy'; img.referrerPolicy = 'no-referrer';
  img.onerror = () => {
    if (fallback && img.src !== new URL(fallback,location.origin).href) { img.src = fallback; fallback = null; }
    else img.hidden = true;
  }; img.src = url; parent.append(img);
}
function placeholder(parent,label) {
  const box = document.createElement('div'); box.className = 'cover-placeholder';
  const text = document.createElement('span'); text.textContent = label; box.append(text); parent.append(box);
}
$('analyze-form').onsubmit = async event => {
  event.preventDefault(); if (busy) return;
  try {
    const url = new URL($('url').value.trim());
    if (/(^|\.)(youtube\.com|youtu\.be)$/.test(url.hostname) && url.searchParams.has('list')) {
      $('playlist-panel').open = true; $('playlist-url').value = url.href;
      $('playlist-panel').scrollIntoView({behavior:'smooth',block:'start'}); $('playlist-form').requestSubmit(); return;
    }
  } catch {}
  busy = true; analyzed = null;
  const version = ++analysisVersion, url = $('url').value.trim();
  analysisController = new AbortController();
  $('video-result').hidden = true; notice(''); $('analyze-button').disabled = true; $('analyze-button').textContent = 'Finding…';
  try {
    const result = await api('/api/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url}),signal:analysisController.signal});
    if (version !== analysisVersion || $('url').value.trim() !== url) return;
    analyzed = result;
    $('analysis-history').hidden = !result.previous_downloads;
    $('analysis-history').textContent = '✓ Downloaded before · '+result.previous_downloads+' successful downloads';
    $('download-button').textContent = result.previous_downloads ? '↓ Download again' : '↓ Add to queue';
    $('video-title').textContent = analyzed.title; $('video-platform').textContent = analyzed.platform;
    $('video-meta').textContent = analyzed.author+(analyzed.duration ? ' · '+duration(analyzed.duration) : '');
    $('analysis-cover').replaceChildren(); placeholder($('analysis-cover'), 'Your next good find'); image($('analysis-cover'),analyzed.thumbnail,'Thumbnail for '+analyzed.title);
    $('quality').replaceChildren();
    const qualities = [2160,1440,1080,720,480,360].filter(h => !analyzed.qualities.length || h <= Math.max(...analyzed.qualities));
    for (const q of qualities.length ? qualities : [360]) $('quality').add(new Option(q+'p'+(q === 2160 ? ' · 4K' : q === 1080 ? ' · Full HD' : ''),q));
    $('quality').value = qualities.includes(1080) ? '1080' : String(qualities[0] || 360); $('video-result').hidden = false;
  } catch(e) { if (version === analysisVersion && e.name !== 'AbortError') notice(e.message,e.retryAfter); }
  finally { if (version === analysisVersion) { busy = false; analysisController = null; $('analyze-button').disabled = false; analyzeLabel(); } }
};
$('url').oninput = () => { analysisVersion++; analysisController?.abort(); analysisController = null; busy = false; analyzed = null; $('video-result').hidden = true; $('analyze-button').disabled = false; analyzeLabel(); notice(''); };
$('format').onchange = () => { $('quality-label').hidden = audioFormats.includes($('format').value); };
$('download-button').onclick = async () => {
  if (!analyzed) return; $('download-button').disabled = true;
  try {
    await api('/api/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:analyzed.url,format:$('format').value,quality:$('quality').value})});
    notice('Added to your collection. Play or save it when processing is complete.'); await refresh();
  } catch(e) { notice(e.message,e.retryAfter); } finally { $('download-button').disabled = false; }
};
$('bulk-format').onchange = () => { $('bulk-quality-label').hidden = audioFormats.includes($('bulk-format').value); };
$('bulk-form').onsubmit = async event => {
  event.preventDefault();
  const urls = $('bulk-urls').value.trim().split(/\s+/).filter(Boolean), box = $('bulk-message');
  box.hidden = false;
  if (!urls.length || urls.length > 50) { box.textContent = 'Paste between 1 and 50 video links, one per line.'; return; }
  $('bulk-button').disabled = true; box.textContent = 'Adding your videos to the queue…';
  try {
    const result = await api('/api/download/bulk',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({urls,format:$('bulk-format').value,quality:$('bulk-quality').value,youtube_concurrency:Number($('bulk-concurrency').value)})});
    box.textContent = result.added+' videos added.'+(result.duplicates_removed?' '+result.duplicates_removed+' repeated links skipped.':'')+(result.submission_failures?' '+result.submission_failures+' jobs could not start; use Retry after restarting the app.':' Originals will be ready to save before optional preview encoding.');
    $('bulk-urls').value = ''; await refresh();
  } catch(e) { box.textContent = e.message; }
  finally { $('bulk-button').disabled = false; }
};
let playlist = null, playlistVersion = 0, playlistController = null, playlistQueuing = false;
function playlistSelection() {
  const boxes = [...$('playlist-videos').querySelectorAll('input[type=checkbox]')];
  const selected = boxes.filter(box => box.checked);
  $('playlist-selected').textContent = selected.length+' / '+boxes.length+' selected';
  $('playlist-select-all').checked = boxes.length > 0 && selected.length === boxes.length;
  $('playlist-select-all').indeterminate = selected.length > 0 && selected.length < boxes.length;
  $('playlist-queue').textContent = selected.length === boxes.length ? '↓ Queue entire playlist' : '↓ Queue '+selected.length+' selected videos';
  $('playlist-queue').disabled = !selected.length || playlistQueuing;
  return selected.map(box => box.value);
}
$('playlist-select-all').onchange = () => {
  $('playlist-videos').querySelectorAll('input[type=checkbox]').forEach(box => { box.checked = $('playlist-select-all').checked; });
  playlistSelection();
};
$('playlist-format').onchange = () => { $('playlist-quality-label').hidden = audioFormats.includes($('playlist-format').value); };
$('playlist-url').oninput = () => {
  if (playlistQueuing) return;
  playlistVersion++; playlistController?.abort(); playlistController = null; playlist = null;
  $('playlist-result').hidden = true; $('playlist-message').hidden = true; $('playlist-load').disabled = false; $('playlist-cancel').hidden = true;
};
$('playlist-cancel').onclick = () => {
  playlistVersion++; playlistController?.abort(); playlistController = null;
  $('playlist-load').disabled = false; $('playlist-cancel').hidden = true;
  $('playlist-message').textContent = 'Playlist loading cancelled. Downloads already in progress continue.';
};
function playlistPause(signal) {
  return new Promise((resolve,reject) => {
    const cancel = () => { clearTimeout(timer); signal.removeEventListener('abort',cancel); reject(new DOMException('Cancelled','AbortError')); };
    const timer = setTimeout(() => { signal.removeEventListener('abort',cancel); resolve(); },1000);
    signal.addEventListener('abort',cancel,{once:true}); if (signal.aborted) cancel();
  });
}
$('playlist-form').onsubmit = async event => {
  event.preventDefault(); if (playlistQueuing) return;
  const version = ++playlistVersion, box = $('playlist-message');
  playlistController?.abort(); const controller = new AbortController(); playlistController = controller; playlist = null;
  let loadId = null, finished = false;
  $('playlist-cancel').hidden = false;
  $('playlist-result').hidden = true; $('playlist-load').disabled = true; box.hidden = false;
  box.textContent = 'Waiting for a YouTube slot, then loading your playlist… Downloads continue normally.';
  try {
    const started = await api('/api/playlist/loads',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:$('playlist-url').value.trim()})});
    loadId = started.id;
    if (version !== playlistVersion) return;
    let result;
    while (true) {
      const state = await api('/api/playlist/loads/'+encodeURIComponent(loadId),{signal:controller.signal});
      if (version !== playlistVersion) return;
      if (state.status === 'ready') { result = state.result; finished = true; break; }
      if (state.status === 'failed') { finished = true; throw Error(state.detail || 'Playlist loading failed.'); }
      if (state.status === 'cancelled') { finished = true; throw Error('Playlist loading cancelled.'); }
      await playlistPause(controller.signal);
    }
    playlist = result; $('playlist-title').textContent = result.title;
    $('playlist-summary').textContent = result.videos.length+' available videos'+(result.unavailable?' · '+result.unavailable+' unavailable/live entries skipped':'')+(result.unlisted?' · '+result.unlisted+' entries not listed by YouTube (may be hidden or unavailable)':'')+(result.duplicates_removed?' · '+result.duplicates_removed+' duplicates skipped':'')+'.';
    const fragment = document.createDocumentFragment();
    for (const video of result.videos) {
      const row = document.createElement('label'); row.className = 'playlist-video';
      const check = document.createElement('input'); check.type = 'checkbox'; check.value = video.id; check.checked = true; check.onchange = playlistSelection;
      const title = document.createElement('span'); title.textContent = video.position+'. '+video.title;
      const meta = document.createElement('small'); meta.textContent = video.duration ? duration(video.duration) : '';
      row.append(check,title,meta); fragment.append(row);
    }
    $('playlist-videos').replaceChildren(fragment); $('playlist-result').hidden = false;
    playlistSelection(); box.textContent = 'Choose your format and queue the whole playlist, or select individual videos.';
  } catch(e) { if (version === playlistVersion && e.name !== 'AbortError') box.textContent = e.message; }
  finally {
    if (loadId && !finished) api('/api/playlist/loads/'+encodeURIComponent(loadId),{method:'DELETE'}).catch(() => {});
    if (version === playlistVersion) { $('playlist-load').disabled = false; $('playlist-cancel').hidden = true; playlistController = null; }
  }
};
$('playlist-queue').onclick = async () => {
  if (!playlist || playlistQueuing) return;
  const video_ids = playlistSelection(); if (!video_ids.length) return;
  playlistQueuing = true; $('playlist-queue').disabled = true; $('playlist-load').disabled = true; $('playlist-url').disabled = true;
  $('playlist-message').hidden = false; $('playlist-message').textContent = 'Adding selected playlist videos…';
  try {
    const result = await api('/api/playlist/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:playlist.token,video_ids,format:$('playlist-format').value,quality:$('playlist-quality').value,youtube_concurrency:Number($('playlist-concurrency').value)})});
    $('playlist-message').textContent = result.added+' playlist videos added. Track each video in My library.'+(result.submission_failures?' '+result.submission_failures+' jobs need Retry after restarting the app.':'');
    playlist = null; $('playlist-result').hidden = true; await refresh();
  } catch(e) { $('playlist-message').textContent = e.message; }
  finally { playlistQueuing = false; $('playlist-load').disabled = false; $('playlist-url').disabled = false; playlistSelection(); }
};
function stopPlayer() {
  playerVersion++; clearTimeout(previewTimer); previewController?.abort(); previewController = null;
  clearTimeout(originalTimer); originalFallback = null;
  $('compatible-preview').hidden = true; $('compatible-preview').onclick = null;
  for (const id of ['video-player','audio-player']) { const media = $(id); media.pause(); media.removeAttribute('src'); media.load(); }
  playingId = null;
}
async function playJob(j) {
  stopPlayer(); playingId = j.id; $('player-title').textContent = j.title; $('player-save').href = endpoint(j,'file'); $('player-error').hidden = true;
  const version = playerVersion, audio = audioFormats.includes(j.format);
  $('video-player').hidden = true; $('audio-player').hidden = true; $('preview-wait').hidden = false;
  $('preview-status').textContent = 'Checking your browser preview…'; $('preview-progress').value = 0;
  const player = $(audio ? 'audio-player' : 'video-player');
  if (!audio) player.poster = endpoint(j,'thumbnail');
  if (!$('player-dialog').open) $('player-dialog').showModal();
  previewController = new AbortController();
  async function check(start) {
    if (version !== playerVersion || !previewController) return;
    try {
      const state = await api(endpoint(j,'preview'),{method:start?'POST':'GET',signal:previewController.signal,timeout:30000});
      if (version !== playerVersion) return;
      if (state.status === 'ready') {
        $('preview-wait').hidden = true; player.hidden = false; player.src = state.stream_url; player.load();
        player.play().catch(() => {}); return;
      }
      if (state.status === 'failed') throw Error(state.error || 'Preview could not finish. Save original is available.');
      $('preview-progress').value = state.progress || 0;
      $('preview-status').textContent = state.status === 'processing' ? 'Preparing preview · '+(state.progress || 0)+'%'+(state.started?' · '+duration(Math.max(0,Date.now()/1000-state.started))+' elapsed':'') : 'Preview queued. Your original is already ready to save.';
      previewTimer = setTimeout(() => check(false),2000);
    } catch(e) {
      if (version !== playerVersion || e.name === 'AbortError') return;
      $('preview-wait').hidden = true; $('player-error').hidden = false; $('player-error').textContent = e.message;
    }
  }
  if (!audio && ['mp4','webm'].includes(j.format)) {
    originalFallback = () => {
      if (version !== playerVersion) return;
      $('compatible-preview').hidden = true;
      clearTimeout(originalTimer); originalFallback = null;
      player.pause(); player.removeAttribute('src'); player.load(); player.hidden = true;
      $('preview-wait').hidden = false; check(true);
    };
    $('compatible-preview').hidden = false; $('compatible-preview').onclick = originalFallback;
    $('preview-wait').hidden = true; player.hidden = false;
    player.src = endpoint(j,'stream')+'?original=true'; player.load(); player.play().catch(() => {});
    originalTimer = setTimeout(() => { if (version === playerVersion && player.readyState < 2) originalFallback?.(); },15000);
    return;
  }
  await check(true);
}
for (const id of ['video-player','audio-player']) $(id).onerror = () => {
  if (originalFallback) { originalFallback(); return; }
  if (!playingId) return; $('player-error').hidden = false;
  $('player-error').textContent = 'This preview could not be played in your browser. Use Save original to play the downloaded file on your device.';
};
$('video-player').onloadeddata = () => { clearTimeout(originalTimer); originalFallback = null; };
$('close-player').onclick = () => $('player-dialog').close(); $('player-dialog').onclose = stopPlayer;
$('player-dialog').onclick = event => { if (event.target !== $('player-dialog')) return; const rect = $('player-dialog').getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) $('player-dialog').close(); };
const cardCaches = new Map();
const paginationState = {recent:{page:1,size:12},library:{page:1,size:24}};
function createCard(j) {
  const row = document.createElement('article'); row.className = 'job '+j.status;
  row.dataset.jobId = j.id; row.tabIndex = 0; row.setAttribute('aria-label',j.title+' · '+j.status+' · Focus for details');
  const cover = document.createElement('div'); cover.className = 'cover'; placeholder(cover,sourceName(j));
  const local = j.status === 'completed' && !audioFormats.includes(j.format) ? endpoint(j,'thumbnail') : null;
  image(cover,j.thumbnail || local,'Thumbnail for '+j.title,local);
  const stages = {extracting:'Getting video',downloading:'Downloading',merging:'Merging audio + video',converting:'Converting format',resizing:'Resizing video',verifying:'Checking file'};
  const badge = document.createElement('span');
  const downloaded = j.status === 'completed' || j.previous_downloads > 0;
  badge.className = 'status-badge'+(downloaded?' downloaded-before':j.status === 'failed'?' failed':'');
  badge.textContent = j.status === 'completed' ? '✓ Downloaded' : downloaded ? '✓ Downloaded before' : {failed:'Needs attention',queued:'Queued',processing:stages[j.stage] || 'Finishing file',downloading:(stages[j.stage] || 'Downloading')+' · '+j.progress+'%'}[j.status] || j.status;
  cover.append(badge);
  if (j.duration) { const time = document.createElement('span'); time.className = 'cover-duration'; time.textContent = duration(j.duration); cover.append(time); }
  const toggle = document.createElement('button'); toggle.className = 'details-toggle'; toggle.textContent = 'ⓘ'; toggle.setAttribute('aria-label','Show details for '+j.title);
  toggle.onclick = () => { row.classList.toggle('details-open'); toggle.setAttribute('aria-expanded',String(row.classList.contains('details-open'))); }; cover.append(toggle);
  cover.onclick = event => { if (event.target.tagName === 'IMG' || event.target.closest('.cover-placeholder')) row.classList.toggle('details-open'); };
  row.onkeydown = event => { if (event.target !== row) return; if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); row.classList.toggle('details-open'); } if (event.key === 'Escape') { row.classList.remove('details-open'); row.blur(); } };
  const detail = document.createElement('div'); detail.className = 'job-details';
  const title = document.createElement('div'); title.className = 'job-title'; title.textContent = j.title; title.title = j.title;
  const meta = document.createElement('div'); meta.className = 'job-meta'; meta.textContent = sourceName(j)+' · '+j.format.toUpperCase()+' · '+(audioFormats.includes(j.format) ? 'Audio' : (j.actual_height || j.quality)+'p')+(j.size?' · '+(j.size/1024/1024).toFixed(1)+' MB':'')+(j.duration?' · '+duration(j.duration):''); meta.title = meta.textContent;
  const status = document.createElement('div'); status.className = 'status'+(j.status === 'failed'?' failed':'');
  status.textContent = j.error || (j.status === 'completed' ? j.preview_status === 'processing'?'Preview encoding '+(j.preview_progress || 0)+'%':j.preview_status === 'queued'?'Original ready · Preview queued':j.preview_status === 'failed'?'Original ready · Preview can be retried':'Ready to play & save' : j.status === 'queued'?'Waiting for a download slot':(stages[j.stage] || 'Downloading')+(j.stage_started?' · '+duration(Math.max(0,Date.now()/1000-j.stage_started))+' elapsed':''));
  status.title = status.textContent+(j.playlist_title?' · '+j.playlist_title+' #'+j.playlist_position:'');
  detail.append(title,meta,status);
  if (['queued','downloading','processing'].includes(j.status)) { const progress = document.createElement('progress'); progress.max = 100; progress.value = j.progress; progress.setAttribute('aria-label','Download progress'); row.append(progress); }
  const actions = document.createElement('div'); actions.className = 'job-actions';
  if (j.status === 'completed') {
    const play = document.createElement('button'); play.className = 'play-button'; play.append(icon('play'),document.createTextNode(' Play')); play.onclick = () => playJob(j); actions.append(play);
    const save = document.createElement('a'); save.href = endpoint(j,'file'); save.textContent = '↓ Save'; save.setAttribute('aria-label','Save original '+j.title); actions.append(save);
    const again = document.createElement('button'); again.className = 'redownload-button'; again.textContent = '↻ Again'; again.setAttribute('aria-label','Download again '+j.title);
    again.onclick = async () => { again.disabled = true; try { await api(endpoint(j,'redownload'),{method:'POST'}); notice('A fresh download was added. Your existing file is still available.'); await refresh(); } catch(e) { notice(e.message,e.retryAfter); view('home'); } finally { again.disabled = false; } }; actions.append(again);
  }
  if (j.status === 'failed') { const retry = document.createElement('button'); retry.className = 'retry-button'; retry.textContent = j.recoverable?'↻ Resume':'↻ Retry'; retry.onclick = async () => { retry.disabled = true; try { await api(endpoint(j,'retry'),{method:'POST'}); await refresh(); } catch(e) { notice(e.message,e.retryAfter); view('home'); } finally { retry.disabled = false; } }; actions.append(retry); }
  if (['failed','completed'].includes(j.status)) { const remove = document.createElement('button'); remove.className = 'remove-button'; remove.textContent = '×'; remove.setAttribute('aria-label','Remove '+j.title); remove.onclick = async () => { try { await api('/api/jobs/'+encodeURIComponent(j.id),{method:'DELETE'}); if (playingId === j.id) $('player-dialog').close(); await refresh(); } catch(e) { notice(e.message); view('home'); } }; actions.append(remove); }
  row.append(cover,detail,actions); return row;
}
function render(target,list) {
  const signature = JSON.stringify(list); if (rendered.get(target) === signature) return; rendered.set(target,signature);
  const container = $(target), cache = cardCaches.get(target) || new Map(); cardCaches.set(target,cache);
  if (!list.length) { cache.clear(); container.innerHTML = '<div class="empty"><span class="empty-icon">▻</span><h3>Your library starts here.</h3><p>Add a video link, or change your search and filters.</p></div>'; return; }
  container.querySelector('.empty')?.remove();
  const ids = new Set(list.map(j => j.id));
  for (const [id,cached] of cache) if (!ids.has(id)) { cached.node.remove(); cache.delete(id); }
  list.forEach((j,index) => {
    const signature = JSON.stringify(j), cached = cache.get(j.id);
    const focused = cached?.node.contains(document.activeElement);
    let node = cached?.node;
    if (!cached || (cached.signature !== signature && (!focused || cached.status !== j.status))) {
      node = createCard(j); if (cached?.node.classList.contains('details-open')) node.classList.add('details-open');
      if (cached) cached.node.replaceWith(node);
      cache.set(j.id,{node,signature,status:j.status});
      if (focused) node.focus({preventScroll:true});
    }
    if (container.children[index] !== node) container.insertBefore(node,container.children[index] || null);
  });
}
function paginate(key,list,target,controls) {
  const state = paginationState[key], pages = Math.max(1,Math.ceil(list.length/state.size));
  state.page = Math.max(1,Math.min(state.page,pages));
  const start = (state.page-1)*state.size; render(target,list.slice(start,start+state.size));
  const signature = [state.page,state.size,list.length].join(':'); if (rendered.get(controls) === signature) return; rendered.set(controls,signature);
  const nav = $(controls); nav.replaceChildren();
  const summary = document.createElement('span'); summary.className = 'page-summary'; summary.textContent = list.length?'Showing '+(start+1)+'–'+Math.min(start+state.size,list.length)+' of '+list.length+' videos':'0 videos';
  const buttons = document.createElement('div'); buttons.className = 'page-controls';
  function button(text,page,label,disabled = false) {
    const el = document.createElement('button'); el.type = 'button'; el.textContent = text; el.disabled = disabled; el.setAttribute('aria-label',label);
    if (page === state.page && /^\d+$/.test(text)) el.setAttribute('aria-current','page');
    el.onclick = () => { state.page = page; renderAll(); $(target).scrollIntoView({behavior:'smooth',block:'start'}); }; buttons.append(el);
  }
  button('‹',state.page-1,'Previous page',state.page === 1);
  const visible = [...new Set([1,pages,state.page-1,state.page,state.page+1].filter(p => p >= 1 && p <= pages))].sort((a,b) => a-b);
  let previous = 0;
  for (const page of visible) { if (previous && page-previous > 1) { const dots = document.createElement('span'); dots.className = 'page-ellipsis'; dots.textContent = '…'; buttons.append(dots); } button(String(page),page,'Page '+page); previous = page; }
  button('›',state.page+1,'Next page',state.page === pages);
  const label = document.createElement('label'); label.className = 'page-size'; label.append(document.createTextNode('Per page'));
  const size = document.createElement('select'); size.setAttribute('aria-label','Videos per page');
  for (const value of [12,24,48]) size.add(new Option(String(value),value)); size.value = String(state.size);
  size.onchange = () => { state.size = Number(size.value); state.page = 1; renderAll(); }; label.append(size);
  nav.append(summary,buttons,label);
}
function renderAll() {
  paginate('recent',jobs,'recent-jobs','recent-pagination');
  const term = $('search').value.trim().toLowerCase(), filter = $('status-filter').value;
  const filtered = jobs.filter(j => (j.title+' '+j.url+' '+sourceName(j)).toLowerCase().includes(term) && (filter === 'all' || filter === 'downloading' && ['queued','downloading','processing'].includes(j.status) || j.status === filter));
  paginate('library',filtered,'all-jobs','library-pagination');
  for (const id of ['total-count','recent-count','nav-count']) $(id).textContent = jobs.length;
  $('ready-count').textContent = jobs.filter(j => j.status === 'completed').length; $('active-count').textContent = jobs.filter(j => ['queued','downloading','processing'].includes(j.status)).length;
}
function renderStats(stats) {
  $('lifetime-total').textContent = stats?stats.total.toLocaleString():'—'; $('insights-total').textContent = stats?stats.total.toLocaleString():'—';
  for (const card of $('all-platforms').children) {
    const value = stats?.platforms.find(p => p.name === card.dataset.platform)?.downloads || 0;
    card.querySelector('.platform-download-count').textContent = stats?value.toLocaleString():'—';
    if (card.dataset.platform === 'Other') card.hidden = value === 0;
  }
  $('stats-status').textContent = stats?'Includes repeat downloads · Saved on this server':'Counters unavailable · Check the server connection and installed version';
}
function refresh() {
  refreshAgain = true;
  if (refreshing) return refreshing;
  refreshing = (async () => {
    do {
      refreshAgain = false;
      try { const [items,h,stats] = await Promise.all([api('/api/jobs',{timeout:10000}),api('/api/health',{timeout:10000}),api('/api/stats',{timeout:10000}).catch(() => null)]); jobs = items; renderAll(); renderStats(stats); $('health').textContent = h.version !== UI_VERSION ? '● Restart AAASoft Video Studio to apply update' : !h.ffmpeg ? '● FFmpeg missing' : h.ffprobe === false ? '● FFprobe missing' : !h.youtube_ejs ? '● YouTube dependency missing' : !h.js_runtimes?.length ? '● JavaScript runtime missing' : h.youtube_cooldown_seconds > 0 ? '● YouTube wait '+Math.ceil(h.youtube_cooldown_seconds/60)+'m' : '● Engine online'; }
      catch(e) { $('health').textContent = '● Connection problem'; $('health').title = e.message; }
    } while (refreshAgain);
  })().finally(() => { refreshing = null; });
  return refreshing;
}
$('search').oninput = () => { paginationState.library.page = 1; renderAll(); }; $('status-filter').onchange = () => { paginationState.library.page = 1; renderAll(); };
$('theme').onclick = () => { document.body.classList.toggle('dark'); try { localStorage.setItem('sv-theme',document.body.classList.contains('dark') ? 'dark' : 'light'); } catch {} };
try { if (localStorage.getItem('sv-theme') === 'dark') document.body.classList.add('dark'); } catch {}
refresh(); setInterval(refresh,2000);
