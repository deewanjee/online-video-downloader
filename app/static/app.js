const $ = id => document.getElementById(id);
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
  for (const [name,icon,bg,color] of platforms) {
    const el = document.createElement('div'); el.className = 'platform';
    el.innerHTML = `<span class="platform-logo" style="background:${bg};color:${color}">${icon}</span><div><strong>${name}</strong><small>${name === 'Threads' ? 'Public posts & share links' : 'Public video links'}</small></div>`;
    if (name === 'YouTube') el.querySelector('.platform-logo').innerHTML = '<svg class="ui-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="m8 5 11 7-11 7z"/></svg>';
    $(id).append(el);
  }
}
let analyzed = null, jobs = [], busy = false, playingId = null;
let analysisVersion = 0, analysisController = null, refreshing = null, refreshAgain = false;
let noticeTimer = null;
let playerVersion = 0, previewTimer = null, previewController = null;
let originalFallback = null, originalTimer = null;
const rendered = new Map();
function view(name) {
  for (const n of ['home','downloads','platforms']) $(n).hidden = n !== name;
  document.querySelectorAll('.nav').forEach(el => { el.classList.toggle('active',el.dataset.view === name); el.setAttribute('aria-current', el.dataset.view === name ? 'page' : 'false'); });
  $('page-label').textContent = {home:'Overview',downloads:'My library',platforms:'Platforms'}[name];
}
document.querySelectorAll('.nav').forEach(el => el.onclick = () => view(el.dataset.view));
$('view-all').onclick = () => view('downloads'); $('see-platforms').onclick = () => view('platforms');
function notice(message,retryAfter = 0) {
  clearInterval(noticeTimer); noticeTimer = null;
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
    const body = await response.text(); let data;
    try { data = JSON.parse(body); } catch { throw Error('The server returned an unreadable response (HTTP '+response.status+'). Check the server connection and retry.'); }
    if (!response.ok) {
      const error = Error(typeof data.detail === 'string' ? data.detail : 'Request could not be completed (HTTP '+response.status+').');
      const seconds = Number(response.headers.get('Retry-After'));
      if (Number.isFinite(seconds) && seconds > 0) error.retryAfter = seconds;
      throw error;
    }
    return data;
  } catch(e) {
    if (e.name === 'AbortError' && !callerSignal?.aborted) throw Error('The request timed out. Check the server window before retrying; a download may still be running.');
    if (e instanceof TypeError) throw Error('Cannot reach StreamVault. Check that the server window is open.');
    throw e;
  } finally { clearTimeout(timer); callerSignal?.removeEventListener('abort',cancel); }
}
function duration(s) { return Math.floor(s/60)+':'+String(Math.floor(s%60)).padStart(2,'0'); }
function sourceName(j) {
  try { return j.platform || new URL(j.url).hostname.replace(/^www\./,''); } catch { return 'Your collection'; }
}
function endpoint(j,kind) { return '/api/jobs/'+encodeURIComponent(j.id)+'/'+kind; }
function image(parent,url,label) {
  if (!url) return;
  try { const parsed = new URL(url,location.origin); if (!['https:','http:'].includes(parsed.protocol) || parsed.username || parsed.password || (parsed.origin !== location.origin && parsed.protocol !== 'https:')) return; } catch { return; }
  const img = document.createElement('img'); img.alt = label; img.loading = 'lazy'; img.referrerPolicy = 'no-referrer';
  img.onerror = () => { img.hidden = true; }; img.src = url; parent.append(img);
}
function placeholder(parent,label) {
  const box = document.createElement('div'); box.className = 'cover-placeholder';
  const text = document.createElement('span'); text.textContent = label; box.append(text); parent.append(box);
}
$('analyze-form').onsubmit = async event => {
  event.preventDefault(); if (busy) return; busy = true; analyzed = null;
  const version = ++analysisVersion, url = $('url').value.trim();
  analysisController = new AbortController();
  $('video-result').hidden = true; notice(''); $('analyze-button').disabled = true; $('analyze-button').textContent = 'Finding…';
  try {
    const result = await api('/api/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url}),signal:analysisController.signal});
    if (version !== analysisVersion || $('url').value.trim() !== url) return;
    analyzed = result;
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
function render(target,list) {
  const signature = JSON.stringify(list); if (rendered.get(target) === signature) return; rendered.set(target,signature);
  const container = $(target); container.replaceChildren();
  if (!list.length) { container.innerHTML = '<div class="empty"><span class="empty-icon">✳</span><h3>Your collection starts with a link.</h3><p>Find something worth keeping, then drop its link above.</p></div>'; return; }
  for (const j of list) {
    const row = document.createElement('article'); row.className = 'job '+j.status;
    const cover = document.createElement('div'); cover.className = 'cover'; placeholder(cover,sourceName(j));
    image(cover,j.status === 'completed' && !audioFormats.includes(j.format) ? endpoint(j,'thumbnail') : j.thumbnail,'Thumbnail for '+j.title);
    const stages = {extracting:'Getting video',downloading:'Downloading',merging:'Merging audio + video',converting:'Converting format',resizing:'Resizing video',verifying:'Checking file'};
    const badge = document.createElement('span'); badge.className = 'status-badge'+(j.status === 'failed' ? ' failed' : ''); badge.textContent = {completed:'Downloaded',failed:'Needs attention',queued:'Queued',processing:stages[j.stage] || 'Finishing file',downloading:(stages[j.stage] || 'Downloading')+' · '+j.progress+'%'}[j.status] || j.status; cover.append(badge);
    if (j.duration) { const time = document.createElement('span'); time.className = 'cover-duration'; time.textContent = duration(j.duration); cover.append(time); }
    const playable = j.status === 'completed';
    if (playable) { const p = document.createElement('button'); p.className = 'cover-play'; p.append(icon('play')); p.setAttribute('aria-label','Play '+j.title); p.onclick = () => playJob(j); cover.append(p); }
    const detail = document.createElement('div'); detail.className = 'job-details';
    const platform = document.createElement('div'); platform.className = 'job-platform'; platform.textContent = sourceName(j);
    const title = document.createElement('div'); title.className = 'job-title'; title.textContent = j.title; title.title = j.title;
    const meta = document.createElement('div'); meta.className = 'job-meta'; meta.textContent = j.format.toUpperCase()+' · '+(audioFormats.includes(j.format) ? 'Audio' : (j.actual_height || j.quality)+'p')+(j.size ? ' · '+(j.size/1024/1024).toFixed(1)+' MB' : '')+(j.has_audio === false ? ' · No audio track' : '');
    const previewPending = j.status === 'completed' && j.preview_status !== 'ready';
    const status = document.createElement('div'); status.className = 'status'+(j.status === 'failed' ? ' failed' : '')+(previewPending?' preview-note':'');
    status.textContent = j.error || (j.status === 'completed' ? j.preview_status === 'processing' ? 'Original ready · Preview encoding '+(j.preview_progress || 0)+'%' : j.preview_status === 'queued' ? 'Original ready · Preview queued separately' : j.preview_status === 'failed' ? 'Original ready · Preview failed; press Play to retry' : j.preview_available ? 'Ready for a replay' : 'Original ready · Play prepares a browser preview' : j.status === 'queued' ? 'Waiting for a download slot' : (stages[j.stage] || 'Downloading your video')+(j.stage_started?' · '+duration(Math.max(0,Date.now()/1000-j.stage_started))+' elapsed':''));
    detail.append(platform,title,meta,status);
    if (['queued','downloading','processing'].includes(j.status)) { const progress = document.createElement('progress'); progress.max = 100; progress.value = j.progress; progress.setAttribute('aria-label','Download progress'); detail.append(progress); }
    const actions = document.createElement('div'); actions.className = 'job-actions';
    if (playable) { const p = document.createElement('button'); p.className = 'play-button'; p.append(icon('play'),document.createTextNode(j.preview_status === 'failed'?' Retry preview':' Play')); p.onclick = () => playJob(j); actions.append(p); }
    if (j.status === 'completed') { const a = document.createElement('a'); a.href = endpoint(j,'file'); a.textContent = '↓ Save original'; actions.append(a); }
    if (j.status === 'failed') { const retry = document.createElement('button'); retry.className = 'retry-button'; retry.textContent = j.recoverable ? '↻ Resume / Retry' : '↻ Try again'; retry.onclick = async () => { retry.disabled = true; try { await api('/api/jobs/'+encodeURIComponent(j.id)+'/retry',{method:'POST'}); await refresh(); } catch(e) { notice(e.message,e.retryAfter); view('home'); } finally { retry.disabled = false; } }; actions.append(retry); }
    if (['failed','completed'].includes(j.status)) { const remove = document.createElement('button'); remove.className = 'remove-button'; remove.textContent = '×'; remove.setAttribute('aria-label','Remove '+j.title); remove.onclick = async () => { try { await api('/api/jobs/'+encodeURIComponent(j.id),{method:'DELETE'}); if (playingId === j.id) $('player-dialog').close(); await refresh(); } catch(e) { notice(e.message); view('home'); } }; actions.append(remove); }
    row.append(cover,detail,actions); container.append(row);
  }
}
function renderAll() {
  render('recent-jobs',jobs.slice(0,12));
  const term = $('search').value.toLowerCase(), filter = $('status-filter').value;
  render('all-jobs',jobs.filter(j => (j.title+' '+j.url).toLowerCase().includes(term) && (filter === 'all' || filter === 'downloading' && ['queued','downloading','processing'].includes(j.status) || j.status === filter)));
  for (const id of ['total-count','recent-count','nav-count']) $(id).textContent = jobs.length;
  $('ready-count').textContent = jobs.filter(j => j.status === 'completed').length; $('active-count').textContent = jobs.filter(j => ['queued','downloading','processing'].includes(j.status)).length;
}
function refresh() {
  refreshAgain = true;
  if (refreshing) return refreshing;
  refreshing = (async () => {
    do {
      refreshAgain = false;
      try { jobs = await api('/api/jobs',{timeout:10000}); renderAll(); const h = await api('/api/health',{timeout:10000}); $('health').textContent = !h.ffmpeg ? '● FFmpeg missing' : h.ffprobe === false ? '● FFprobe missing' : !h.youtube_ejs ? '● YouTube dependency missing' : !h.js_runtimes?.length ? '● JavaScript runtime missing' : h.youtube_cooldown_seconds > 0 ? '● YouTube wait '+Math.ceil(h.youtube_cooldown_seconds/60)+'m' : '● Engine online'; }
      catch(e) { $('health').textContent = '● Connection problem'; $('health').title = e.message; }
    } while (refreshAgain);
  })().finally(() => { refreshing = null; });
  return refreshing;
}
$('search').oninput = renderAll; $('status-filter').onchange = renderAll;
$('theme').onclick = () => { document.body.classList.toggle('dark'); try { localStorage.setItem('sv-theme',document.body.classList.contains('dark') ? 'dark' : 'light'); } catch {} };
try { if (localStorage.getItem('sv-theme') === 'dark') document.body.classList.add('dark'); } catch {}
refresh(); setInterval(refresh,2000);
