const discoveryPending = new Set();
let discoveryResults = new Map(), discoverySelected = new Set(), discoverySearch = null;
let discoveryController = null, discoveryVersion = 0, discoveryPage = 1, discoveryMore = false, discoveryBusy = false;
function discoverySelection() {
  const index=discoveryJobIndex();
  const ready=[...discoverySelected].map(url=>discoveryResults.get(url)).filter(Boolean).filter(row=>{const state=discoveryState(row,index);return !state.active && !state.pending && (repeatAllowed() || !state.count);}).length;
  $('discovery-selected').textContent=discoverySelected.size+' selected'+(ready<discoverySelected.size?' · '+(discoverySelected.size-ready)+' already in progress':'');
  $('discovery-queue').disabled=!ready || discoveryBusy;
  for(const checkbox of $('discovery-results').querySelectorAll('input[type=checkbox]')) checkbox.checked=discoverySelected.has(checkbox.value);
}
function discoveryReset() {
  discoveryVersion++;discoveryController?.abort();discoveryController=null;
  discoverySelected.clear();discoveryResults.clear();discoverySearch=null;
  if(typeof syncDiscoveryFilters==='function')discoveryFilter='new';
  $('discovery-download-controls').hidden=true;$('discovery-pagination').replaceChildren();
  $('discovery-results').replaceChildren();$('discovery-message').hidden=true;
  $('discovery-submit').disabled=false;$('discovery-cancel').hidden=true;
  if(typeof syncDiscoveryFilters==='function')syncDiscoveryFilters();
}
$('discovery-provider').onchange=()=>{if($('discovery-provider').value==='dailymotion')$('discovery-kind').value='videos';$('discovery-kind').disabled=$('discovery-provider').value==='dailymotion';discoveryReset();};
$('discovery-kind').onchange=discoveryReset;
$('discovery-query').oninput=discoveryReset;
$('discovery-format').onchange=()=>{$('discovery-quality-label').hidden=audioFormats.includes($('discovery-format').value);};
$('discovery-clear').onclick=()=>{discoverySelected.clear();discoverySelection();};
$('discovery-cancel').onclick=()=>{discoveryVersion++;discoveryController?.abort();$('discovery-submit').disabled=false;$('discovery-cancel').hidden=true;$('discovery-message').textContent='Search cancelled. The current server request may finish in the background. Queued downloads continue.';};
function searchMessage(text) {$('discovery-message').textContent=text;$('discovery-message').hidden=!text;}
function discoveryKey(row) { return row.source_key || row.url; }
function discoveryJobIndex() {
  const index=new Map();
  for(const job of jobs) {
    const key=discoveryKey(job);
    if(!index.has(key))index.set(key,[]);
    index.get(key).push(job);
  }
  for(const list of index.values())list.sort((a,b)=>(b.created || 0)-(a.created || 0));
  return index;
}
function discoveryState(row,index=discoveryJobIndex()) {
  const matches=index.get(discoveryKey(row)) || [];
  const active=matches.find(job=>['queued','downloading','processing'].includes(job.status));
  const saved=matches.find(job=>job.status==='completed' && !audioFormats.includes(job.format)) || matches.find(job=>job.status==='completed');
  const latest=matches[0];
  const count=Math.max(row.previous_downloads || 0,...matches.map(job=>job.previous_downloads || 0),saved?1:0);
  row.previous_downloads=count;
  const pending=discoveryPending.has(discoveryKey(row));
  const current=active || latest;
  const stages={extracting:'Getting video',downloading:'Downloading',merging:'Merging audio + video',converting:'Converting format',resizing:'Resizing video',verifying:'Checking file'};
  let kind='idle',label=row.platform,detail='Preview before downloading',progress=0;
  if(active) {
    kind=active.status;progress=Math.max(0,Math.min(100,Number(active.progress) || 0));
    label=active.status==='queued'?(active.stage==='source_wait'?'Source cooldown':active.stage==='retry_wait'?'Recovery queued':'Queued'):active.status==='processing'?(stages[active.stage] || 'Finishing file'):(stages[active.stage] || 'Downloading')+' · '+Math.floor(progress)+'%';
    detail=active.status==='queued'?(active.stage==='source_wait'?'Waiting for the source cooldown; your queue is kept.':active.stage==='retry_wait'?'Automatic recovery will retry after the current queue.':'Waiting for a download slot.'):'Your download continues while you browse.';
  } else if(pending){kind='adding';label='Adding to queue…';detail='Confirming destination and adding your download.';}
  else if(latest?.status==='failed'){kind='failed';label='Needs attention';detail=latest.error || 'The download failed. Retry when the source is available.';}
  else if(saved){kind='completed';label='✓ Downloaded';detail='Saved and ready to play'+(saved.format?' · '+saved.format.toUpperCase():'');}
  else if(count){kind='history';label='✓ Downloaded before';detail='Earlier download recorded; preview or download again.';}
  return {active,saved,current,pending,kind,label,detail,progress,count};
}
function syncDiscoveryJobs() {
  const index=discoveryJobIndex();
  for(const card of $('discovery-results').querySelectorAll('.discovery-card')) {
    const controls=card.discoveryControls;if(!controls)continue;
    const {row,badge,progress,detail,download,preview,coverPlay}=controls;
    const state=discoveryState(row,index);
    card.dataset.state=state.kind;
    badge.textContent=state.label;badge.title=state.label;
    detail.textContent=state.detail;detail.title=state.detail;
    progress.hidden=!state.active;
    if(state.active){progress.value=state.progress;progress.setAttribute('aria-valuetext',state.label);}
    download.disabled=discoveryBusy || !!state.active || state.pending || (!!state.count && !repeatAllowed());
    download.textContent=state.active?({queued:'Queued',downloading:'Downloading…',processing:'Finishing…'}[state.active.status]):state.pending?'Adding…':state.count && !repeatAllowed()?'Already downloaded':state.kind==='failed'?'Retry download':state.count?'Download again':'Download';
    preview.textContent=state.saved?'Play saved':'Preview';
    coverPlay.setAttribute('aria-label',(state.saved?'Play saved video: ':'Preview video: ')+row.title);
    coverPlay.title=state.saved?'Play saved video':'Preview on '+row.platform;
  }
  discoverySelection();syncSourcePreview(index);
  if(typeof syncDiscoveryFilters==='function')syncDiscoveryFilters();
}
async function queueDiscovered(rows) {
  if(discoveryBusy || !rows.length)return;
  rows=rows.filter(row=>{const state=discoveryState(row);return !state.active && !state.pending && (repeatAllowed() || !state.count);});
  if(!rows.length){searchMessage('These videos are already queued or downloaded. Open Completed to play saved files; enable Allow repeat downloads only for another copy.');return;}
  discoveryBusy=true;rows.forEach(row=>discoveryPending.add(discoveryKey(row)));discoverySelection();syncDiscoveryJobs();
  try {
    await ensureDownloadLocation();
    const result=await api('/api/download/bulk',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({urls:rows.map(row=>row.url),format:$('discovery-format').value,quality:$('discovery-quality').value,youtube_concurrency:1,allow_repeat:repeatAllowed()})});
    // Show returned jobs immediately, then reconcile with the shared two-second poll.
    const incoming=result.jobs || [],ids=new Set(incoming.map(job=>job.id));
    jobs=[...incoming,...jobs.filter(job=>!ids.has(job.id))];
    // Older servers omit source keys on submission; the canonical search URL still matches.
    for(const job of incoming){const row=rows.find(row=>row.url===job.url);if(row && !job.source_key)job.source_key=discoveryKey(row);}
    rows.forEach(row=>discoverySelected.delete(row.url));syncDiscoveryJobs();
    searchMessage(result.added+(result.added===1?' video':' videos')+' added to your download queue.'+(result.duplicates_removed?' '+result.duplicates_removed+' duplicates skipped.':'')+' Live progress appears on the thumbnails below.');await refresh();
  }catch(e){searchMessage(e.message);}finally{rows.forEach(row=>discoveryPending.delete(discoveryKey(row)));discoveryBusy=false;discoverySelection();syncDiscoveryJobs();}
}
$('discovery-queue').onclick=()=>queueDiscovered([...discoverySelected].map(url=>discoveryResults.get(url)).filter(Boolean));
function renderDiscovery(rows) {
  const box=$('discovery-results');box.replaceChildren();
  if(!rows.length){const empty=document.createElement('div');empty.className='empty';empty.textContent='No matching results. Try a different title, topic or platform.';box.append(empty);return;}
  for(const row of rows) {
    const card=document.createElement('article');card.className='discovery-card';card.dataset.sourceId=row.id;card.dataset.resultKind=row.kind;
    const cover=document.createElement('div');cover.className='discovery-cover';placeholder(cover,row.platform);image(cover,row.thumbnail,'Thumbnail for '+row.title);
    if(row.kind==='videos') {
      const label=document.createElement('label');label.className='card-select';
      const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.value=row.url;checkbox.setAttribute('aria-label','Select '+row.title);checkbox.checked=discoverySelected.has(row.url);
      checkbox.onchange=()=>{if(checkbox.checked && discoverySelected.size>=50){checkbox.checked=false;searchMessage('Select up to 50 videos per batch.');return;}checkbox.checked?discoverySelected.add(row.url):discoverySelected.delete(row.url);discoverySelection();};label.append(checkbox);cover.append(label);
    }
    const badge=document.createElement('span');badge.className='discovery-type';badge.textContent=row.kind==='playlists'?'PLAYLIST'+(row.video_count?' · '+row.video_count+' videos':''):row.platform;cover.append(badge);
    if(row.duration){const length=document.createElement('span');length.className='cover-duration';length.textContent=duration(row.duration);cover.append(length);}
    const content=document.createElement('div');content.className='discovery-card-content';
    const title=document.createElement('h2');title.textContent=row.title;title.title=row.title;
    const author=document.createElement('p');author.textContent=row.author;
    const actions=document.createElement('div');actions.className='discovery-actions';
    const primary=document.createElement('button');primary.className='primary';primary.textContent=row.kind==='playlists'?'Review playlist':'Download';
    primary.onclick=async()=>{
      if(row.kind==='playlists') {
        if(playlistQueuing || playlistChecking){searchMessage('Finish or cancel the current playlist operation before opening another playlist.');return;}
        view('home');$('playlist-panel').open=true;$('playlist-url').value=row.url;$('playlist-url').dispatchEvent(new Event('input'));$('playlist-form').requestSubmit();$('playlist-panel').scrollIntoView({behavior:'smooth',block:'start'});
      } else await queueDiscovered([row]);
    };
    actions.append(primary);
    content.append(title,author);
    if(row.kind==='videos') {
      const preview=document.createElement('button');preview.className='secondary discovery-preview';preview.textContent='Preview';preview.onclick=()=>playDiscovered(row);actions.append(preview);
      const coverPlay=document.createElement('button');coverPlay.className='discovery-cover-play';coverPlay.append(icon('play'));coverPlay.onclick=()=>playDiscovered(row);cover.append(coverPlay);
      const choose=document.createElement('button');choose.className='text-button';choose.textContent='Choose format';
      choose.onclick=()=>{view('home');$('url').value=row.url;$('url').dispatchEvent(new Event('input'));$('analyze-form').requestSubmit();};actions.append(choose);
      const progress=document.createElement('progress');progress.max=100;progress.value=0;progress.hidden=true;progress.className='discovery-progress';progress.setAttribute('aria-label','Download progress for '+row.title);cover.append(progress);
      const detail=document.createElement('p');detail.className='discovery-job-detail';content.append(detail);
      card.discoveryControls={row,badge,progress,detail,download:primary,preview,coverPlay};
    }
    content.append(actions);card.append(cover,content);box.append(card);
  }
  syncDiscoveryJobs();
}
let sourcePreviewRow=null;
function discoveryEmbedURL(row) {
  if(row.provider==='youtube' && /^[A-Za-z0-9_-]{11}$/.test(row.id))return 'https://www.youtube-nocookie.com/embed/'+row.id+'?autoplay=1&playsinline=1&rel=0&origin='+encodeURIComponent(location.origin);
  if(row.provider==='dailymotion' && /^[A-Za-z0-9]{3,30}$/.test(row.id))return 'https://www.dailymotion.com/embed/video/'+row.id+'?autoplay=1';
  if(row.provider==='tiktok' && /^[0-9]{6,25}$/.test(row.id))return 'https://www.tiktok.com/player/v1/'+row.id+'?autoplay=1';
  return null;
}
function stopSourcePreview() {
  $('source-preview-frame').replaceChildren();sourcePreviewRow=null;
}
function syncSourcePreview(index=discoveryJobIndex()) {
  if(!sourcePreviewRow)return;
  const state=discoveryState(sourcePreviewRow,index);
  $('source-preview-saved').hidden=!state.saved;
  $('source-preview-download').hidden=!!sourcePreviewRow.account_preview;
  const status=state.kind==='idle'?'':state.label+' · '+state.detail;
  if($('source-preview-download-status').textContent!==status)$('source-preview-download-status').textContent=status;
  $('source-preview-download').disabled=discoveryBusy || !!state.active || state.pending || (!!state.count && !repeatAllowed());
  $('source-preview-download').textContent=state.active?state.label:state.pending?'Adding…':state.count && !repeatAllowed()?'Already downloaded':state.count?'Download again':'Download this video';
}
function playDiscovered(row) {
  const state=discoveryState(row);
  if(state.saved){$('source-preview-dialog').close();playJob(state.saved);return;}
  const url=discoveryEmbedURL(row);
  if(!url){searchMessage('Source preview is unavailable for this result. Download the video to play the saved file.');return;}
  $('player-dialog').close();stopSourcePreview();sourcePreviewRow=row;
  $('source-preview-title').textContent=row.title;
  $('source-preview-note').textContent='Preview provided by '+row.platform+'. Playback depends on the source allowing embedded video. '+(row.account_preview?'Close this preview and select checked videos in the account list to download.':'If it is blocked, download the video and use Play saved when ready.');
  const frame=document.createElement('iframe');frame.title='Source preview: '+row.title;frame.src=url;
  frame.allow='autoplay; encrypted-media; fullscreen; picture-in-picture';frame.allowFullscreen=true;frame.referrerPolicy='strict-origin-when-cross-origin';
  frame.onerror=()=>{$('source-preview-note').textContent='The source preview could not load. Download the video and use Play saved when ready.';};
  $('source-preview-frame').append(frame);syncSourcePreview();
  if(!$('source-preview-dialog').open)$('source-preview-dialog').showModal();
}
$('source-preview-close').onclick=()=>$('source-preview-dialog').close();
$('source-preview-dialog').onclose=stopSourcePreview;
$('source-preview-dialog').onclick=event=>{if(event.target!==$('source-preview-dialog'))return;const rect=event.target.getBoundingClientRect();if(event.clientX<rect.left || event.clientX>rect.right || event.clientY<rect.top || event.clientY>rect.bottom)event.target.close();};
$('source-preview-download').onclick=()=>{if(sourcePreviewRow)queueDiscovered([sourcePreviewRow]);};
$('source-preview-saved').onclick=()=>{if(sourcePreviewRow)playDiscovered(sourcePreviewRow);};
async function runDiscovery(page=1) {
  const version=++discoveryVersion;discoveryController?.abort();discoveryController=new AbortController();
  if(page===1){discoverySelected.clear();discoveryResults.clear();discoverySearch={query:$('discovery-query').value.trim(),provider:$('discovery-provider').value,kind:$('discovery-kind').value};}
  const query={...discoverySearch,page};
  $('discovery-submit').disabled=true;$('discovery-cancel').hidden=false;searchMessage('Searching public '+(query.kind==='playlists'?'playlists':'videos')+'…');
  try {
    const result=await api('/api/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(query),signal:discoveryController.signal,timeout:120000});
    if(version!==discoveryVersion)return;
    discoveryPage=page;discoveryMore=result.has_more;result.results.forEach(row=>discoveryResults.set(row.url,row));
    renderDiscovery(result.results);$('discovery-download-controls').hidden=query.kind!=='videos' || !result.results.length;
    searchMessage(result.results.length+' results · '+(query.provider==='youtube'?'YouTube':'Dailymotion')+' · Page '+page+'. Search results do not guarantee source download access.');
    const nav=$('discovery-pagination');nav.replaceChildren();
    for(const [label,target,disabled] of [['Previous',page-1,page===1],['Next',page+1,!discoveryMore]]){const button=document.createElement('button');button.className='secondary';button.textContent=label;button.disabled=disabled;button.onclick=()=>runDiscovery(target);nav.append(button);}
    discoverySelection();
  }catch(e){if(version===discoveryVersion && e.name!=='AbortError')searchMessage(e.message);}
  finally{if(version===discoveryVersion){$('discovery-submit').disabled=false;$('discovery-cancel').hidden=true;discoveryController=null;}}
}
$('discovery-form').onsubmit=event=>{event.preventDefault();runDiscovery();};

function renderPlaylistRows(preserve=true) {
  const before=new Set([...$('playlist-videos').querySelectorAll('input:checked')].map(input=>input.value));
  const rows=playlist.entries || playlist.videos;
  const selectable=rows.filter(row=>row.selectable!==false && !row.already_queued && (repeatAllowed() || !row.previous_downloads)).length;
  $('playlist-summary').textContent=selectable+' selectable entries · '+(rows.length-selectable)+' excluded · '+rows.filter(row=>row.previous_downloads>0).length+' already downloaded'+(playlist.unlisted?' · '+playlist.unlisted+' entries not listed by YouTube':'')+'.';
  const fragment=document.createDocumentFragment();
  for(const video of rows) {
    const row=document.createElement('label');row.className='playlist-video availability-'+(video.status || 'unknown');
    const input=document.createElement('input');input.type='checkbox';input.value=video.id;input.disabled=video.selectable===false || video.already_queued || (video.previous_downloads>0 && !repeatAllowed());
    input.checked=!input.disabled && (!preserve || before.has(video.id));input.onchange=playlistSelection;
    const title=document.createElement('span');title.textContent=video.position+'. '+video.title;
    const status=document.createElement('small');status.className='playlist-availability';status.textContent=(video.previous_downloads?'✓ Already downloaded · ':'')+(video.already_queued?'In queue · ':'')+(video.status_label || 'Not verified');status.title=video.detail || 'Flat listing information; choose Check selected for a live access check.';
    row.append(input,title,status);fragment.append(row);
  }
  $('playlist-videos').replaceChildren(fragment);playlistSelection();
}
$('playlist-check').onclick=async()=>{
  if(!playlist || playlistChecking || playlistQueuing)return;
  const selected=new Set(playlistSelection());
  const candidates=playlist.videos.filter(row=>selected.has(row.id) && !row.check_attempted && row.status!=='available').slice(0,20);
  if(!candidates.length){$('playlist-check-status').textContent='Select unchecked videos to verify. Private/removed entries cannot be selected.';return;}
  playlistChecking=true;$('playlist-check').disabled=true;$('playlist-queue').disabled=true;$('playlist-load').disabled=true;$('playlist-url').disabled=true;$('playlist-check-cancel').hidden=false;$('playlist-check-cancel').disabled=false;
  try {
    const started=await api('/api/playlist/checks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:playlist.token,video_ids:candidates.map(row=>row.id)})});playlistCheckId=started.id;
    let last=-1;
    while(true){
      const state=await api('/api/playlist/checks/'+encodeURIComponent(playlistCheckId));
      if(state.checked!==last){for(const update of state.updates)for(const row of [...playlist.videos,...(playlist.entries || [])])if(row.id===update.id && row.status!=='duplicate')Object.assign(row,update);renderPlaylistRows(true);last=state.checked;}
      $('playlist-check-status').textContent='Checked '+state.checked+' / '+state.total+' · '+(state.status==='queued'?'Waiting for a metadata worker':state.status==='running'?'Checking source access':state.status==='ready'?'Complete — repeat to check the next selected videos':state.detail || state.status);
      if(['ready','failed','cancelled'].includes(state.status))break;
      await new Promise(resolve=>setTimeout(resolve,1000));
    }
  }catch(e){$('playlist-check-status').textContent=e.message;}
  finally{playlistChecking=false;playlistCheckId=null;$('playlist-check').disabled=false;$('playlist-load').disabled=false;$('playlist-url').disabled=false;$('playlist-check-cancel').hidden=true;playlistSelection();}
};
$('playlist-check-cancel').onclick=async()=>{if(!playlistCheckId)return;$('playlist-check-cancel').disabled=true;try{await api('/api/playlist/checks/'+encodeURIComponent(playlistCheckId),{method:'DELETE'});$('playlist-check-status').textContent='Cancelling after the current metadata request…';}catch(e){$('playlist-check-status').textContent=e.message;$('playlist-check-cancel').disabled=false;}};
