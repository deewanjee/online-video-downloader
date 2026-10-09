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
const rendered = new Map();
function view(name) {
  for (const n of ['home','downloads','platforms']) $(n).hidden = n !== name;
  document.querySelectorAll('.nav').forEach(el => { el.classList.toggle('active',el.dataset.view === name); el.setAttribute('aria-current', el.dataset.view === name ? 'page' : 'false'); });
  $('page-label').textContent = {home:'Overview',downloads:'My library',platforms:'Platforms'}[name];
}
document.querySelectorAll('.nav').forEach(el => el.onclick = () => view(el.dataset.view));
$('view-all').onclick = () => view('downloads'); $('see-platforms').onclick = () => view('platforms');
function notice(message) { $('notice').hidden = !message; $('notice').textContent = message; }
async function api(path,options) {
  const response = await fetch(path,options); const data = await response.json();
  if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Request could not be completed.');
  return data;
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
  $('video-result').hidden = true; notice(''); $('analyze-button').disabled = true; $('analyze-button').textContent = 'Finding…';
  try {
    analyzed = await api('/api/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:$('url').value.trim()})});
    $('video-title').textContent = analyzed.title; $('video-platform').textContent = analyzed.platform;
    $('video-meta').textContent = analyzed.author+(analyzed.duration ? ' · '+duration(analyzed.duration) : '');
    $('analysis-cover').replaceChildren(); placeholder($('analysis-cover'), 'Your next good find'); image($('analysis-cover'),analyzed.thumbnail,'Thumbnail for '+analyzed.title);
    $('quality').replaceChildren();
    const qualities = [2160,1440,1080,720,480,360].filter(h => !analyzed.qualities.length || h <= Math.max(...analyzed.qualities));
    for (const q of qualities.length ? qualities : [360]) $('quality').add(new Option(q+'p'+(q === 2160 ? ' · 4K' : q === 1080 ? ' · Full HD' : ''),q));
    $('quality').value = qualities.includes(1080) ? '1080' : String(qualities[0] || 360); $('video-result').hidden = false;
  } catch(e) { notice(e.message); }
  finally { busy = false; $('analyze-button').disabled = false; analyzeLabel(); }
};
$('url').oninput = () => { analyzed = null; $('video-result').hidden = true; };
$('format').onchange = () => { $('quality-label').hidden = audioFormats.includes($('format').value); };
$('download-button').onclick = async () => {
  if (!analyzed) return; $('download-button').disabled = true;
  try {
    await api('/api/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:analyzed.url,format:$('format').value,quality:$('quality').value})});
    notice('Added to your collection. Play or save it when processing is complete.'); await refresh();
  } catch(e) { notice(e.message); } finally { $('download-button').disabled = false; }
};
function stopPlayer() {
  for (const id of ['video-player','audio-player']) { const media = $(id); media.pause(); media.removeAttribute('src'); media.load(); }
  playingId = null;
}
function playJob(j) {
  stopPlayer(); playingId = j.id; $('player-title').textContent = j.title; $('player-save').href = endpoint(j,'file'); $('player-error').hidden = true;
  const audio = audioFormats.includes(j.format); $('video-player').hidden = audio; $('audio-player').hidden = !audio;
  const player = $(audio ? 'audio-player' : 'video-player');
  if (!audio) player.poster = endpoint(j,'thumbnail');
  $('player-dialog').showModal(); player.src = endpoint(j,'stream'); player.load();
  player.play().catch(() => { /* Native controls remain available if autoplay is blocked. */ });
}
for (const id of ['video-player','audio-player']) $(id).onerror = () => {
  if (!playingId) return; $('player-error').hidden = false;
  $('player-error').textContent = 'This preview could not be played in your browser. Use Save original to play the downloaded file on your device.';
};
$('close-player').onclick = () => $('player-dialog').close(); $('player-dialog').onclose = stopPlayer;
$('player-dialog').onclick = event => { if (event.target !== $('player-dialog')) return; const rect = $('player-dialog').getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) $('player-dialog').close(); };
function render(target,list) {
  const signature = JSON.stringify(list); if (rendered.get(target) === signature) return; rendered.set(target,signature);
  const container = $(target); container.replaceChildren();
  if (!list.length) { container.innerHTML = '<div class="empty"><span class="empty-icon">✳</span><h3>Your collection starts with a link.</h3><p>Find something worth keeping, then drop its link above.</p></div>'; return; }
  for (const j of list) {
    const row = document.createElement('article'); row.className = 'job';
    const cover = document.createElement('div'); cover.className = 'cover'; placeholder(cover,sourceName(j));
    image(cover,j.status === 'completed' && !audioFormats.includes(j.format) ? endpoint(j,'thumbnail') : j.thumbnail,'Thumbnail for '+j.title);
    const badge = document.createElement('span'); badge.className = 'status-badge'+(j.status === 'failed' ? ' failed' : ''); badge.textContent = {completed:'In your vault',failed:'Needs attention',queued:'Queued',processing:'Preparing preview',downloading:'Downloading · '+j.progress+'%'}[j.status] || j.status; cover.append(badge);
    if (j.duration) { const time = document.createElement('span'); time.className = 'cover-duration'; time.textContent = duration(j.duration); cover.append(time); }
    const playable = j.status === 'completed' && j.preview_available !== false;
    if (playable) { const p = document.createElement('button'); p.className = 'cover-play'; p.append(icon('play')); p.setAttribute('aria-label','Play '+j.title); p.onclick = () => playJob(j); cover.append(p); }
    const detail = document.createElement('div'); detail.className = 'job-details';
    const platform = document.createElement('div'); platform.className = 'job-platform'; platform.textContent = sourceName(j);
    const title = document.createElement('div'); title.className = 'job-title'; title.textContent = j.title; title.title = j.title;
    const meta = document.createElement('div'); meta.className = 'job-meta'; meta.textContent = j.format.toUpperCase()+' · '+(audioFormats.includes(j.format) ? 'Audio' : (j.actual_height || j.quality)+'p')+(j.size ? ' · '+(j.size/1024/1024).toFixed(1)+' MB' : '')+(j.has_audio === false ? ' · No audio track' : '');
    const status = document.createElement('div'); status.className = 'status'+(j.status === 'failed' ? ' failed' : ''); status.textContent = j.error || (j.status === 'completed' ? j.preview_available === false ? 'Preview unavailable · Save the original' : 'Ready for a replay' : j.status === 'processing' ? 'Finishing your file and browser preview…' : 'We’re bringing this one home.');
    detail.append(platform,title,meta,status);
    if (['queued','downloading','processing'].includes(j.status)) { const progress = document.createElement('progress'); progress.max = 100; progress.value = j.progress; progress.setAttribute('aria-label','Download progress'); detail.append(progress); }
    const actions = document.createElement('div'); actions.className = 'job-actions';
    if (playable) { const p = document.createElement('button'); p.className = 'play-button'; p.append(icon('play'),document.createTextNode(' Play')); p.onclick = () => playJob(j); actions.append(p); }
    if (j.status === 'completed') { const a = document.createElement('a'); a.href = endpoint(j,'file'); a.textContent = '↓ Save original'; actions.append(a); }
    if (j.status === 'failed') { const retry = document.createElement('button'); retry.className = 'retry-button'; retry.textContent = '↻ Try again'; retry.onclick = async () => { retry.disabled = true; try { await api('/api/jobs/'+encodeURIComponent(j.id)+'/retry',{method:'POST'}); await refresh(); } catch(e) { notice(e.message); view('home'); } finally { retry.disabled = false; } }; actions.append(retry); }
    if (['failed','completed'].includes(j.status)) { const remove = document.createElement('button'); remove.className = 'remove-button'; remove.textContent = '×'; remove.setAttribute('aria-label','Remove '+j.title); remove.onclick = async () => { try { await api('/api/jobs/'+encodeURIComponent(j.id),{method:'DELETE'}); if (playingId === j.id) $('player-dialog').close(); await refresh(); } catch(e) { notice(e.message); view('home'); } }; actions.append(remove); }
    row.append(cover,detail,actions); container.append(row);
  }
}
function renderAll() {
  render('recent-jobs',jobs.slice(0,6));
  const term = $('search').value.toLowerCase(), filter = $('status-filter').value;
  render('all-jobs',jobs.filter(j => (j.title+' '+j.url).toLowerCase().includes(term) && (filter === 'all' || filter === 'downloading' && ['queued','downloading','processing'].includes(j.status) || j.status === filter)));
  for (const id of ['total-count','recent-count','nav-count']) $(id).textContent = jobs.length;
  $('ready-count').textContent = jobs.filter(j => j.status === 'completed').length; $('active-count').textContent = jobs.filter(j => ['queued','downloading','processing'].includes(j.status)).length;
}
async function refresh() {
  try { jobs = await api('/api/jobs'); renderAll(); const h = await api('/api/health'); $('health').textContent = !h.ffmpeg ? '● FFmpeg missing' : !h.youtube_ejs ? '● YouTube dependency missing' : !h.js_runtimes?.length ? '● JavaScript runtime missing' : '● Engine online'; }
  catch(e) { $('health').textContent = '● Engine offline'; }
}
$('search').oninput = renderAll; $('status-filter').onchange = renderAll;
$('theme').onclick = () => { document.body.classList.toggle('dark'); try { localStorage.setItem('sv-theme',document.body.classList.contains('dark') ? 'dark' : 'light'); } catch {} };
try { if (localStorage.getItem('sv-theme') === 'dark') document.body.classList.add('dark'); } catch {}
refresh(); setInterval(refresh,2000);
