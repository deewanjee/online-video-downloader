const $ = id => document.getElementById(id);
const UI_VERSION = '3.6.3';
const audioFormats = ['mp3', 'm4a', 'wav'];
function icon(name) {
  const paths = {play:'<path d="m8 5 11 7-11 7z"/>',arrow:'<path d="M6 18 18 6M6 6h12v12"/>',download:'<path d="M12 3v12m-5-5 5 5 5-5M5 16v5h14v-5"/>'};
  const span = document.createElement('span');
  span.innerHTML = `<svg class="ui-icon" viewBox="0 0 24 24" fill="${name==='play'?'currentColor':'none'}" stroke="currentColor" stroke-width="1.7" aria-hidden="true">${paths[name]}</svg>`;
  return span;
}
function analyzeLabel() { $('analyze-button').replaceChildren(document.createTextNode('Find video '),icon('arrow')); }
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
let analyzed = null, jobs = [], busy = false, playingId = null, singleQueuing=false;
let analysisVersion = 0, analysisController = null, refreshing = null, refreshAgain = false;
let noticeTimer = null, toastTimer = null;
let locationSettings = null, destinationPromise = null, locationInputDirty = false;
let playerVersion = 0, previewTimer = null, previewController = null;
let originalFallback = null, originalTimer = null;
const rendered = new Map();
function view(name,tab='pending') {
  const requested=name;
  if(name==='completed'){name='downloads';tab='completed';}
  if(name==='downloads')$('status-filter').value=tab;
  for (const n of ['home','discover','downloads','platforms','recovery','activity','sources']) $(n).hidden = n !== name;
  document.querySelectorAll('.nav').forEach(el => { el.classList.toggle('active',el.dataset.view === requested); el.setAttribute('aria-current', el.dataset.view === requested ? 'page' : 'false'); });
  $('page-label').textContent = {home:'Dashboard',discover:'Discover',downloads:'Video library',platforms:'Platform insights',recovery:'Recovery center',activity:'Activity log',sources:'Platform workspaces'}[name];
  window.scrollTo(0,0);
  if (name==='downloads')renderAll();
  if(name==='sources' && typeof refreshSources==='function')refreshSources();
  if (name==='activity' && typeof loadActivity==='function')loadActivity();
  if (name === 'recovery' && typeof loadRecovery === 'function') loadRecovery();
}
document.querySelectorAll('.nav').forEach(el => el.onclick = () => view(el.dataset.view));
$('home-discover').onclick = () => {view('discover');$('discovery-query').focus();};
$('home-library').onclick = () => view('downloads');
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
function applyDownloadLocation(settings) {
  locationSettings = settings;
  $('location-help').classList.remove('location-warning');
  $('location-path').textContent = settings.direct_enabled ? settings.path || 'No folder selected' : 'Browser download';
  $('location-path').title = settings.path || '';
  $('location-help').textContent = settings.direct_enabled ? settings.configured ? settings.available ? 'New downloads save directly here. No second Save step is needed.' : 'This folder is unavailable. Reconnect the drive or choose another folder.' : 'Choose a folder before adding downloads. Files will be saved there automatically.' : 'Server mode: files are prepared on the server; Save to device uses your browser. For direct laptop saving, use Start StreamVault.bat locally.';
  $('location-choose').hidden = !settings.direct_enabled; $('location-manual').hidden = !settings.direct_enabled;
  $('location-open').hidden = !settings.direct_enabled || !settings.configured;
  if (settings.path && !locationInputDirty && document.activeElement !== $('location-input')) $('location-input').value = settings.path;
}
async function chooseDownloadFolder() {
  if (destinationPromise) return destinationPromise;
  destinationPromise = (async () => {
    $('location-choose').disabled = true; $('location-message').hidden = false;
    $('location-message').textContent = 'Choose a folder in the desktop dialog. Your browser will wait here.';
    try {
      const result = await api('/api/download-location/pick',{method:'POST',timeout:150000});
      locationInputDirty = false; applyDownloadLocation(result);
      $('location-message').textContent = result.cancelled ? 'Folder selection cancelled.' : 'Folder confirmed. New downloads will be saved directly here.';
      renderAll(); return result.configured && result.available;
    } catch(e) {
      $('location-message').textContent = e.message; $('location-manual').open = true; $('location-input').focus(); return false;
    } finally { $('location-choose').disabled = false; }
  })();
  try { return await destinationPromise; } finally { destinationPromise = null; }
}
async function ensureDownloadLocation() {
  if (!locationSettings) applyDownloadLocation(await api('/api/download-location'));
  if (locationSettings.direct_enabled && (!locationSettings.configured || !locationSettings.available)) {
    if (!await chooseDownloadFolder()) throw Error('Choose an available download folder before adding videos.');
  }
}
$('location-choose').onclick = chooseDownloadFolder;
$('location-open').onclick = async () => { try { await api('/api/download-location/open',{method:'POST'}); } catch(e) { notice(e.message); } };
$('location-input').oninput = () => { locationInputDirty = true; };
$('location-form').onsubmit = async event => {
  event.preventDefault(); $('location-save').disabled = true; $('location-message').hidden = false;
  try {
    const result = await api('/api/download-location',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:$('location-input').value.trim()})});
    locationInputDirty = false; applyDownloadLocation(result); $('location-message').textContent = 'Folder confirmed. New downloads will be saved directly here.'; renderAll();
  } catch(e) { $('location-message').textContent = e.message; }
  finally { $('location-save').disabled = false; }
};
async function openJobFolder(j) { try { await api(endpoint(j,'open-folder'),{method:'POST'}); } catch(e) { notice(e.message); } }
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
    $('quality').value = qualities.includes(1080) ? '1080' : String(qualities[0] || 360); $('video-result').hidden = false; if(typeof syncSingleDownload==='function')syncSingleDownload();
  } catch(e) { if (version === analysisVersion && e.name !== 'AbortError') notice(e.message,e.retryAfter); }
  finally { if (version === analysisVersion) { busy = false; analysisController = null; $('analyze-button').disabled = false; analyzeLabel(); } }
};
$('url').oninput = () => { analysisVersion++; analysisController?.abort(); analysisController = null; busy = false; analyzed = null; $('video-result').hidden = true; $('analyze-button').disabled = false; analyzeLabel(); notice(''); };
$('format').onchange = () => { $('quality-label').hidden = audioFormats.includes($('format').value); };
$('download-button').onclick = async () => {
  if (!analyzed || singleQueuing) return; singleQueuing=true; $('download-button').disabled = true;
  try {
    await ensureDownloadLocation();
    await api('/api/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:analyzed.url,format:$('format').value,quality:$('quality').value,allow_repeat:repeatAllowed()})});
    notice(locationSettings?.direct_enabled ? 'Added to your collection. The file will save automatically in your selected folder.' : 'Added to your collection. Play or save it when processing is complete.'); await refresh();
  } catch(e) { notice(e.message,e.retryAfter); } finally { singleQueuing=false; if(typeof syncSingleDownload==='function')syncSingleDownload(); else $('download-button').disabled = false; }
};
$('bulk-format').onchange = () => { $('bulk-quality-label').hidden = audioFormats.includes($('bulk-format').value); };
$('bulk-form').onsubmit = async event => {
  event.preventDefault();
  const urls = $('bulk-urls').value.trim().split(/\s+/).filter(Boolean), box = $('bulk-message');
  box.hidden = false;
  if (!urls.length || urls.length > 50) { box.textContent = 'Paste between 1 and 50 video links, one per line.'; return; }
  $('bulk-button').disabled = true; box.textContent = 'Adding your videos to the queue…';
  try {
    await ensureDownloadLocation();
    const result = await api('/api/download/bulk',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({urls,format:$('bulk-format').value,quality:$('bulk-quality').value,allow_repeat:repeatAllowed(),youtube_concurrency:Number($('bulk-concurrency').value)})});
    box.textContent = result.added+' videos added.'+(result.duplicates_removed?' '+result.duplicates_removed+' repeated links skipped.':'')+(result.submission_failures?' '+result.submission_failures+' jobs could not start; use Retry after restarting the app.':' Originals finish before optional preview encoding.');
    $('bulk-urls').value = ''; if(result.duplicates_removed)notice(result.added+' queued · '+result.duplicates_removed+' duplicates skipped.'); await refresh();
  } catch(e) { box.textContent = e.message; }
  finally { $('bulk-button').disabled = false; }
};
let playlistChecking = false, playlistCheckId = null;
let playlist = null, playlistVersion = 0, playlistController = null, playlistQueuing = false;
function playlistSelection() {
  const boxes = [...$('playlist-videos').querySelectorAll('input[type=checkbox]')];
  const eligible = boxes.filter(box => !box.disabled);
  const selected = eligible.filter(box => box.checked);
  $('playlist-selected').textContent = selected.length+' / '+eligible.length+' selectable videos · '+(boxes.length-eligible.length)+' disabled';
  $('playlist-select-all').checked = eligible.length > 0 && selected.length === eligible.length;
  $('playlist-select-all').indeterminate = selected.length > 0 && selected.length < eligible.length;
  $('playlist-queue').textContent = selected.length === eligible.length ? '↓ Queue all selectable videos' : '↓ Queue '+selected.length+' selected videos';
  $('playlist-queue').disabled = !selected.length || playlistQueuing || playlistChecking;
  return selected.map(box => box.value);
}
$('playlist-select-all').onchange = () => {
  $('playlist-videos').querySelectorAll('input[type=checkbox]').forEach(box => { box.checked = !box.disabled && $('playlist-select-all').checked; });
  playlistSelection();
};
$('playlist-format').onchange = () => { $('playlist-quality-label').hidden = audioFormats.includes($('playlist-format').value); };
$('playlist-url').oninput = () => {
  if (playlistQueuing || playlistChecking) return;
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
  event.preventDefault(); if (playlistQueuing || playlistChecking) return;
  const version = ++playlistVersion, box = $('playlist-message');
  playlistController?.abort(); const controller = new AbortController(); playlistController = controller; playlist = null;
  let loadId = null, finished = false;
  $('playlist-cancel').hidden = false;
  $('playlist-result').hidden = true; $('playlist-load').disabled = true; box.hidden = false;
  box.textContent = 'Loading your playlist… Video downloads continue independently.';
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
      const elapsed = state.started ? ' · '+duration(Math.max(0,Date.now()/1000-state.started))+' elapsed' : '';
      box.textContent = state.status === 'queued' ? 'Playlist analysis queued. Video downloads continue independently.' : state.phase === 'waiting_metadata' ? 'Another YouTube analysis is running. Waiting for a metadata slot'+elapsed : state.entries > 0 ? 'Reading playlist · '+state.entries+' entries found'+elapsed : 'Loading playlist from YouTube'+elapsed+'. Video downloads continue independently.';
      await playlistPause(controller.signal);
    }
    playlist = result; $('playlist-check-status').textContent = ''; $('playlist-title').textContent = result.title;
    $('playlist-summary').textContent = result.videos.length+' selectable entries'+(result.unavailable?' · '+result.unavailable+' unavailable/live entries skipped':'')+(result.unlisted?' · '+result.unlisted+' entries not listed by YouTube (may be hidden or unavailable)':'')+(result.duplicates_removed?' · '+result.duplicates_removed+' duplicates skipped':'')+'.';
    renderPlaylistRows(false); $('playlist-result').hidden = false;
    playlistSelection(); box.textContent = 'Choose your format and queue the whole playlist, or select individual videos.';
  } catch(e) { if (version === playlistVersion && e.name !== 'AbortError') box.textContent = e.message; }
  finally {
    if (loadId && !finished) api('/api/playlist/loads/'+encodeURIComponent(loadId),{method:'DELETE'}).catch(() => {});
    if (version === playlistVersion) { $('playlist-load').disabled = false; $('playlist-cancel').hidden = true; playlistController = null; }
  }
};
$('playlist-queue').onclick = async () => {
  if (!playlist || playlistQueuing || playlistChecking) return;
  const video_ids = playlistSelection(); if (!video_ids.length) return;
  playlistQueuing = true; $('playlist-queue').disabled = true; $('playlist-load').disabled = true; $('playlist-url').disabled = true;
  $('playlist-message').hidden = false; $('playlist-message').textContent = 'Adding selected playlist videos…';
  try {
    await ensureDownloadLocation();
    const result = await api('/api/playlist/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:playlist.token,video_ids,format:$('playlist-format').value,quality:$('playlist-quality').value,allow_repeat:repeatAllowed(),youtube_concurrency:Number($('playlist-concurrency').value)})});
    $('playlist-message').textContent = result.added+' playlist videos added.'+(result.duplicates_removed?' '+result.duplicates_removed+' duplicates skipped.':'')+' Completed videos move to the Completed tab.'+(result.submission_failures?' '+result.submission_failures+' jobs need Retry after restarting the app.':'');
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
  $('player-open-folder').hidden = !locationSettings?.direct_enabled; $('player-open-folder').onclick = () => openJobFolder(j);
  $('player-save').textContent = locationSettings?.direct_enabled ? '↓ Save a copy' : '↓ Save to device';
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
const selectedJobs = new Set();
let recentFilter = 'pending', deletingSelection = false, pendingRemoval = [];
const selectionLists = {};
const removable = j => ['completed','failed','queued'].includes(j.status);
function matchesStatus(j, filter) {
  return filter === 'all' || (filter==='pending' && j.status!=='completed' && j.stage!=='duplicate_skipped') || (filter === 'downloading' ? ['queued','downloading','processing'].includes(j.status) : j.status === filter);
}
function eligiblePage(key) {
  const state = paginationState[key], start = (state.page-1)*state.size;
  return (selectionLists[key] || []).slice(start,start+state.size).filter(removable);
}
for (const [key,id] of [['recent','recent-selection'],['library','library-selection']]) {
  const bar = $(id);
  bar.innerHTML = '<span class="selection-count" aria-live="polite">0 selected</span><button type="button" class="select-page secondary">Select this page</button><button type="button" class="select-matching text-button">Select all matching</button><button type="button" class="clear-selection text-button">Clear</button><button type="button" class="remove-selected danger-button">Remove selected</button><small>Selection stays across pages and filters · Completed, failed or queued videos</small>';
  bar.querySelector('.select-page').onclick = () => {
    const list = eligiblePage(key), all = list.length && list.every(j => selectedJobs.has(j.id));
    for (const j of list) all ? selectedJobs.delete(j.id) : selectedJobs.add(j.id);
    syncSelection();
  };
  bar.querySelector('.select-matching').onclick = () => { (selectionLists[key] || []).filter(removable).forEach(j => selectedJobs.add(j.id)); syncSelection(); };
  bar.querySelector('.clear-selection').onclick = () => { selectedJobs.clear(); syncSelection(); };
  bar.querySelector('.remove-selected').onclick = () => {
    pendingRemoval = jobs.filter(j => selectedJobs.has(j.id) && removable(j)).map(j => j.id);
    if (!pendingRemoval.length) return;
    const entries = jobs.filter(j => pendingRemoval.includes(j.id));
    const owned = entries.filter(j => !j.output_dir).length;
    $('remove-summary').textContent = 'Remove '+pendingRemoval.length+' selected videos from your library?';
    $('remove-file-policy').textContent = (entries.length-owned ? (entries.length-owned)+' originals in your selected download folders will be kept. ' : '') + (owned ? 'Project-owned media and cache for '+owned+' entries will be deleted.' : 'Only history entries and preview caches will be removed.');
    $('remove-error').hidden = true;
    $('remove-confirm').textContent = 'Remove '+pendingRemoval.length+' videos';
    $('remove-dialog').showModal();
  };
}
function syncSelection() {
  for (const row of document.querySelectorAll('.job')) {
    const checkbox = row.querySelector('.card-select input');
    if (!checkbox) continue;
    checkbox.checked = selectedJobs.has(row.dataset.jobId);
    checkbox.disabled = deletingSelection || !['completed','failed','queued'].some(status => row.classList.contains(status));
    row.classList.toggle('selected',checkbox.checked);
  }
  for (const [key,id] of [['recent','recent-selection'],['library','library-selection']]) {
    const bar = $(id), page = eligiblePage(key), matches = (selectionLists[key] || []).filter(removable);
    bar.querySelector('.selection-count').textContent = selectedJobs.size+' selected';
    const all = page.length && page.every(j => selectedJobs.has(j.id));
    bar.querySelector('.select-page').textContent = all ? 'Deselect this page' : 'Select this page';
    bar.querySelector('.select-matching').textContent = 'Select all matching ('+matches.length+')';
    for (const button of bar.querySelectorAll('button')) button.disabled = deletingSelection;
    bar.querySelector('.select-page').disabled ||= !page.length;
    bar.querySelector('.select-matching').disabled ||= !matches.length;
    bar.querySelector('.remove-selected').disabled ||= !selectedJobs.size;
    bar.querySelector('.clear-selection').disabled ||= !selectedJobs.size;
  }
}
$('remove-cancel').onclick = () => $('remove-dialog').close();
$('remove-dialog').addEventListener('cancel', event => { if (deletingSelection) event.preventDefault(); });
$('remove-confirm').onclick = async () => {
  if (deletingSelection || !pendingRemoval.length) return;
  deletingSelection = true; syncSelection();
  $('remove-confirm').disabled = true; $('remove-cancel').disabled = true;
  $('remove-confirm').textContent = 'Removing…';
  try {
    const result = await api('/api/jobs/remove',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_ids:pendingRemoval}),timeout:120000});
    result.removed.forEach(id => selectedJobs.delete(id));
    if (result.removed.includes(playingId)) $('player-dialog').close();
    $('remove-dialog').close();
    notice(result.removed.length+' videos removed.'+(result.failed.length ? ' '+result.failed.length+' could not be removed: '+result.failed[0].detail : ' Lifetime download counters are unchanged.'));
    await refresh();
  } catch(e) { $('remove-error').textContent = e.message; $('remove-error').hidden = false; }
  finally {
    deletingSelection = false; $('remove-confirm').disabled = false; $('remove-cancel').disabled = false;
    $('remove-confirm').textContent = 'Remove '+pendingRemoval.length+' videos'; syncSelection();
  }
};
for (const button of $('collection-filters').querySelectorAll('button')) button.onclick = () => {
  recentFilter = button.dataset.filter; paginationState.recent.page = 1; renderAll();
};

function createCard(j) {
  const row = document.createElement('article'); row.className = 'job '+j.status;
  row.dataset.jobId = j.id; row.tabIndex = 0; row.setAttribute('aria-label',j.title+' · '+j.status+' · Focus for details');
  const cover = document.createElement('div'); cover.className = 'cover'; placeholder(cover,sourceName(j));
  const local = j.status === 'completed' && !audioFormats.includes(j.format) ? endpoint(j,'thumbnail') : null;
  image(cover,j.thumbnail || local,'Thumbnail for '+j.title,local);
  const stages = {extracting:'Getting video',downloading:'Downloading',merging:'Merging audio + video',converting:'Converting format',resizing:'Resizing video',verifying:'Checking file'};
  const badge = document.createElement('span');
  badge.className = 'status-badge'+(j.status === 'completed'?' downloaded-before':j.status === 'failed'?' failed':'');
  badge.textContent = j.status === 'completed' ? '✓ Downloaded' : {failed:'Needs attention',queued:j.stage === 'retry_wait' ? 'Recovery queued' : j.stage === 'source_wait' ? 'YouTube cooldown' : 'Queued',processing:stages[j.stage] || 'Finishing file',downloading:(stages[j.stage] || 'Downloading')+' · '+j.progress+'%'}[j.status] || j.status;
  if (j.status !== 'completed' && j.previous_downloads > 0) badge.textContent += ' · Downloaded before';
  cover.append(badge);
  if (j.status === 'completed') {
    const ribbon = document.createElement('span'); ribbon.className = 'complete-ribbon'; ribbon.textContent = '✓ DOWNLOADED · READY TO PLAY'; cover.append(ribbon);
  }
  const choice = document.createElement('label'); choice.className = 'card-select';
  choice.title = removable(j) ? 'Select video for removal' : 'This download is running; wait for it to finish';
  const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.checked = selectedJobs.has(j.id); checkbox.disabled = !removable(j) || deletingSelection;
  checkbox.setAttribute('aria-label','Select '+j.title);
  checkbox.onchange = () => { checkbox.checked ? selectedJobs.add(j.id) : selectedJobs.delete(j.id); syncSelection(); };
  choice.onclick = event => event.stopPropagation(); choice.append(checkbox); row.append(choice);
  if (j.duration) { const time = document.createElement('span'); time.className = 'cover-duration'; time.textContent = duration(j.duration); cover.append(time); }
  const toggle = document.createElement('button'); toggle.className = 'details-toggle'; toggle.textContent = 'ⓘ'; toggle.setAttribute('aria-label','Show details for '+j.title);
  toggle.onclick = () => { row.classList.toggle('details-open'); toggle.setAttribute('aria-expanded',String(row.classList.contains('details-open'))); }; cover.append(toggle);
  cover.onclick = event => { if (event.target.tagName === 'IMG' || event.target.closest('.cover-placeholder')) row.classList.toggle('details-open'); };
  row.onkeydown = event => { if (event.target !== row) return; if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); row.classList.toggle('details-open'); } if (event.key === 'Escape') { row.classList.remove('details-open'); row.blur(); } };
  const detail = document.createElement('div'); detail.className = 'job-details';
  const title = document.createElement('div'); title.className = 'job-title'; title.textContent = j.title; title.title = j.title;
  const meta = document.createElement('div'); meta.className = 'job-meta'; meta.textContent = sourceName(j)+' · '+j.format.toUpperCase()+' · '+(audioFormats.includes(j.format) ? 'Audio' : (j.actual_height || j.quality)+'p')+(j.size?' · '+(j.size/1024/1024).toFixed(1)+' MB':'')+(j.duration?' · '+duration(j.duration):''); meta.title = meta.textContent;
  const status = document.createElement('div'); status.className = 'status'+(j.status === 'failed'?' failed':'');
  status.textContent = j.error || (j.status === 'completed' ? j.preview_status === 'processing'?'Preview encoding '+(j.preview_progress || 0)+'%':j.preview_status === 'queued'?'Original ready · Preview queued':j.preview_status === 'failed'?'Original ready · Preview can be retried':'Ready to play & save' : j.status === 'queued'?(j.stage === 'retry_wait' ? 'Automatic recovery '+(j.auto_attempts || 1)+' · After current queue' : j.stage === 'source_wait' ? 'YouTube cooldown · Queue kept; resumes after the source wait' : 'Waiting for a download slot'):(stages[j.stage] || 'Downloading')+(j.stage_started?' · '+duration(Math.max(0,Date.now()/1000-j.stage_started))+' elapsed':''));
  status.title = (j.next_action || status.textContent)+(j.playlist_title?' · '+j.playlist_title+' #'+j.playlist_position:'');
  detail.append(title,meta,status);
  if (['queued','downloading','processing'].includes(j.status)) { const progress = document.createElement('progress'); progress.max = 100; progress.value = j.progress; progress.setAttribute('aria-label','Download progress'); row.append(progress); }
  const actions = document.createElement('div'); actions.className = 'job-actions';
  if (j.status === 'completed') {
    const play = document.createElement('button'); play.className = 'play-button'; play.append(icon('play'),document.createTextNode(' Play')); play.onclick = () => playJob(j); actions.append(play);
    if (locationSettings?.direct_enabled) { const folder = document.createElement('button'); folder.className = 'open-folder-button'; folder.textContent = 'Folder'; folder.setAttribute('aria-label','Open folder for '+j.title); folder.onclick = () => openJobFolder(j); actions.append(folder); }
    else { const save = document.createElement('a'); save.href = endpoint(j,'file'); save.textContent = '↓ Save'; save.setAttribute('aria-label','Save to device '+j.title); actions.append(save); }
    const again = document.createElement('button'); again.className = 'redownload-button'; again.textContent = '↻ Again'; again.setAttribute('aria-label','Download again '+j.title);
    again.onclick = async () => { if(!await confirmRepeat(j.title))return; again.disabled = true; try { await ensureDownloadLocation(); await api(endpoint(j,'redownload'),{method:'POST'}); notice('A fresh download was added. Your existing file is still available.'); await refresh(); } catch(e) { notice(e.message,e.retryAfter); view('home'); } finally { again.disabled = false; } }; actions.append(again);
  }
  if (j.status === 'failed') { const retry = document.createElement('button'); retry.className = 'retry-button'; retry.textContent = j.recoverable?'↻ Resume':'↻ Retry'; retry.onclick = async () => { retry.disabled = true; try { await ensureDownloadLocation(); await api(endpoint(j,'retry'),{method:'POST'}); await refresh(); } catch(e) { notice(e.message,e.retryAfter); view('home'); } finally { retry.disabled = false; } }; actions.append(retry); }
  if (['failed','completed'].includes(j.status)) { const remove = document.createElement('button'); remove.className = 'remove-button'; remove.textContent = '×'; remove.setAttribute('aria-label','Remove '+j.title); remove.title = j.output_dir ? 'Remove from library; keep the saved file' : 'Remove video and history'; remove.onclick = async () => { try { await api('/api/jobs/'+encodeURIComponent(j.id),{method:'DELETE'}); if (playingId === j.id) $('player-dialog').close(); await refresh(); } catch(e) { notice(e.message); view('home'); } }; actions.append(remove); }
  if (j.status === 'queued' && j.stage === 'retry_wait') row.classList.add('retry-wait');
  row.append(cover,detail,actions); return row;
}
function render(target,list) {
  const signature = JSON.stringify([list,locationSettings?.direct_enabled,target==='recent-jobs'?recentFilter:$('status-filter').value]); if (rendered.get(target) === signature) return; rendered.set(target,signature);
  const container = $(target), cache = cardCaches.get(target) || new Map(); cardCaches.set(target,cache);
  if (!list.length) {
    cache.clear();const queue=target==='recent-jobs'?recentFilter==='pending':$('status-filter').value==='pending';
    container.innerHTML=queue?'<div class="empty"><span class="empty-icon">✓</span><h3>Your work queue is clear.</h3><p>Finished videos are safe in Completed. Add more links when you are ready.</p></div>':'<div class="empty"><span class="empty-icon">▻</span><h3>No matching videos here.</h3><p>Change the search or filter, or add a new video link.</p></div>';
    if(queue){const button=document.createElement('button');button.className='secondary';button.textContent='Open Completed';button.onclick=()=>view('completed');container.firstChild.append(button);}return;
  }
  container.querySelector('.empty')?.remove();
  const ids = new Set(list.map(j => j.id));
  for (const [id,cached] of cache) if (!ids.has(id)) { cached.node.remove(); cache.delete(id); }
  list.forEach((j,index) => {
    const signature = JSON.stringify([j,locationSettings?.direct_enabled]), cached = cache.get(j.id);
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
  for (const value of [12,24,48,100]) size.add(new Option(String(value),value)); size.value = String(state.size);
  size.onchange = () => { state.size = Number(size.value); state.page = 1; renderAll(); }; label.append(size);
  nav.append(summary,buttons,label);
}
function renderAll() {
  const available = new Set(jobs.filter(removable).map(j => j.id));
  for (const id of selectedJobs) if (!available.has(id)) selectedJobs.delete(id);
  const recent = jobs.filter(j => matchesStatus(j,recentFilter));
  selectionLists.recent = recent;
  paginate('recent',recent,'recent-jobs','recent-pagination');
  for (const button of $('collection-filters').querySelectorAll('button')) {
    button.setAttribute('aria-pressed',String(recentFilter === button.dataset.filter));
    button.querySelector('b').textContent = jobs.filter(j => matchesStatus(j,button.dataset.filter)).length;
  }
  const term = $('search').value.trim().toLowerCase(), filter = $('status-filter').value;
  const filtered = jobs.filter(j => ($('source-filter').value==='all' || sourceName(j)===$('source-filter').value) && (j.title+' '+j.url+' '+sourceName(j)).toLowerCase().includes(term) && matchesStatus(j,filter));
  const sort = $('sort-order').value;
  filtered.sort((a,b) => sort === 'oldest' ? a.created-b.created : sort === 'title' ? a.title.localeCompare(b.title) : sort === 'completed' ? Number(b.status==='completed')-Number(a.status==='completed') || b.created-a.created : b.created-a.created);
  selectionLists.library = filtered;
  paginate('library',filtered,'all-jobs','library-pagination');
  syncSelection();
  for (const id of ['total-count','recent-count','nav-count']) $(id).textContent = jobs.length;
  $('recent-count').textContent=recent.length;
  if (typeof syncWorkspaceTabs === 'function') syncWorkspaceTabs();
  if (typeof renderQueueOverview === 'function') renderQueueOverview();
  $('ready-count').textContent = jobs.filter(j => j.status === 'completed').length; $('active-count').textContent = jobs.filter(j => ['queued','downloading','processing'].includes(j.status)).length;
}
function renderStats(stats) {
  if(typeof syncSourceStatistics==='function')syncSourceStatistics(stats);
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
      try {
        const [items,h,stats,destination] = await Promise.all([
          api('/api/jobs',{timeout:10000}), api('/api/health',{timeout:10000}),
          api('/api/stats',{timeout:10000}).catch(() => null),
          api('/api/download-location',{timeout:10000}).catch(() => null)
        ]);
        if (destination) applyDownloadLocation(destination);
        else {
          $('location-path').textContent = h.version !== UI_VERSION ? 'Restart to apply the update' : 'Download settings unavailable';
          $('location-help').textContent = 'Stop the server with Ctrl+C, launch the updated app, then refresh with Ctrl+F5.';
          $('location-help').classList.add('location-warning');
        }
        jobs = items; renderAll(); renderStats(stats);
        if (typeof syncDiscoveryJobs === 'function') syncDiscoveryJobs();
        if (typeof refreshWorkspace === 'function') refreshWorkspace();
        if (typeof refreshSources === 'function')refreshSources();
        if (typeof renderSystemChecks === 'function') renderSystemChecks(h);
        const status = h.version !== UI_VERSION ? 'Restart to apply update'
          : !h.ffmpeg ? 'FFmpeg missing' : h.ffprobe === false ? 'FFprobe missing'
          : !h.youtube_ejs ? 'YouTube dependency missing' : !h.js_runtimes?.length ? 'JavaScript runtime missing'
          : h.youtube_cooldown_seconds > 0 ? 'YouTube wait '+Math.ceil(h.youtube_cooldown_seconds/60)+'m' : 'Engine online';
        $('health').textContent = status;
        $('health').dataset.state = status === 'Engine online' ? 'ready' : 'warning';
        $('health').title = h.version !== UI_VERSION ? 'Stop the previous server with Ctrl+C. Launch the updated project, then refresh with Ctrl+F5.' : status;
      } catch(e) {
        $('health').textContent = 'Connection problem'; $('health').title = e.message; $('health').dataset.state = 'error';
        if (!locationSettings) $('location-path').textContent = 'Waiting for the local server';
      }
    } while (refreshAgain);
  })().finally(() => { refreshing = null; });
  return refreshing;
}
$('search').oninput = () => { paginationState.library.page = 1; renderAll(); }; $('status-filter').onchange = () => { paginationState.library.page = 1; renderAll(); };
$('source-filter').onchange=()=>{paginationState.library.page=1;renderAll();};
$('sort-order').onchange = () => { paginationState.library.page = 1; renderAll(); };
$('theme').onclick = () => { document.body.classList.toggle('dark'); try { localStorage.setItem('sv-theme',document.body.classList.contains('dark') ? 'dark' : 'light'); } catch {} };
try { if (localStorage.getItem('sv-theme') === 'dark') document.body.classList.add('dark'); } catch {}
refresh(); setInterval(refresh,2000);
