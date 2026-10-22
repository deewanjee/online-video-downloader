let discoveryResults = new Map(), discoverySelected = new Set(), discoverySearch = null;
let discoveryController = null, discoveryVersion = 0, discoveryPage = 1, discoveryMore = false, discoveryBusy = false;
function discoverySelection() {
  $('discovery-selected').textContent=discoverySelected.size+' selected';
  $('discovery-queue').disabled=!discoverySelected.size || discoveryBusy;
  for(const checkbox of $('discovery-results').querySelectorAll('input[type=checkbox]')) checkbox.checked=discoverySelected.has(checkbox.value);
}
function discoveryReset() {
  discoveryVersion++;discoveryController?.abort();discoveryController=null;
  discoverySelected.clear();discoveryResults.clear();discoverySearch=null;
  $('discovery-download-controls').hidden=true;$('discovery-pagination').replaceChildren();
  $('discovery-results').replaceChildren();$('discovery-message').hidden=true;
  $('discovery-submit').disabled=false;$('discovery-cancel').hidden=true;
}
$('discovery-provider').onchange=()=>{if($('discovery-provider').value==='dailymotion')$('discovery-kind').value='videos';$('discovery-kind').disabled=$('discovery-provider').value==='dailymotion';discoveryReset();};
$('discovery-kind').onchange=discoveryReset;
$('discovery-query').oninput=discoveryReset;
$('discovery-format').onchange=()=>{$('discovery-quality-label').hidden=audioFormats.includes($('discovery-format').value);};
$('discovery-clear').onclick=()=>{discoverySelected.clear();discoverySelection();};
$('discovery-cancel').onclick=()=>{discoveryVersion++;discoveryController?.abort();$('discovery-submit').disabled=false;$('discovery-cancel').hidden=true;$('discovery-message').textContent='Search cancelled. The current server request may finish in the background. Queued downloads continue.';};
function searchMessage(text) {$('discovery-message').textContent=text;$('discovery-message').hidden=!text;}
async function queueDiscovered(rows) {
  if(discoveryBusy || !rows.length)return;
  discoveryBusy=true;discoverySelection();
  try {
    await ensureDownloadLocation();
    const result=await api('/api/download/bulk',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({urls:rows.map(row=>row.url),format:$('discovery-format').value,quality:$('discovery-quality').value,youtube_concurrency:1})});
    rows.forEach(row=>discoverySelected.delete(row.url));
    searchMessage(result.added+(result.added===1?' video':' videos')+' added to your download queue. Follow progress in Dashboard or Video library.');await refresh();
  }catch(e){searchMessage(e.message);}finally{discoveryBusy=false;discoverySelection();}
}
$('discovery-queue').onclick=()=>queueDiscovered([...discoverySelected].map(url=>discoveryResults.get(url)).filter(Boolean));
function renderDiscovery(rows) {
  const box=$('discovery-results');box.replaceChildren();
  if(!rows.length){const empty=document.createElement('div');empty.className='empty';empty.textContent='No matching results. Try a different title, topic or platform.';box.append(empty);return;}
  for(const row of rows) {
    const card=document.createElement('article');card.className='discovery-card';
    const cover=document.createElement('div');cover.className='discovery-cover';placeholder(cover,row.platform);image(cover,row.thumbnail,'Thumbnail for '+row.title);
    if(row.kind==='videos') {
      const label=document.createElement('label');label.className='card-select';
      const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.value=row.url;checkbox.setAttribute('aria-label','Select '+row.title);checkbox.checked=discoverySelected.has(row.url);
      checkbox.onchange=()=>{if(checkbox.checked && discoverySelected.size>=50){checkbox.checked=false;searchMessage('Select up to 50 videos per batch.');return;}checkbox.checked?discoverySelected.add(row.url):discoverySelected.delete(row.url);discoverySelection();};label.append(checkbox);cover.append(label);
    }
    const badge=document.createElement('span');badge.className='discovery-type';badge.textContent=row.kind==='playlists'?'PLAYLIST'+(row.video_count?' · '+row.video_count+' videos':''):row.previous_downloads?'✓ Downloaded before':row.platform;cover.append(badge);
    if(row.duration){const length=document.createElement('span');length.className='cover-duration';length.textContent=duration(row.duration);cover.append(length);}
    const content=document.createElement('div');content.className='discovery-card-content';
    const title=document.createElement('h2');title.textContent=row.title;title.title=row.title;
    const author=document.createElement('p');author.textContent=row.author;
    const actions=document.createElement('div');actions.className='discovery-actions';
    const primary=document.createElement('button');primary.className='primary';primary.textContent=row.kind==='playlists'?'Review playlist':row.previous_downloads?'Download again':'Download';
    primary.onclick=async()=>{
      if(row.kind==='playlists') {
        if(playlistQueuing || playlistChecking){searchMessage('Finish or cancel the current playlist operation before opening another playlist.');return;}
        view('home');$('playlist-panel').open=true;$('playlist-url').value=row.url;$('playlist-url').dispatchEvent(new Event('input'));$('playlist-form').requestSubmit();$('playlist-panel').scrollIntoView({behavior:'smooth',block:'start'});
      } else {primary.disabled=true;await queueDiscovered([row]);primary.disabled=false;}
    };
    actions.append(primary);
    if(row.kind==='videos') {
      const choose=document.createElement('button');choose.className='text-button';choose.textContent='Choose format';
      choose.onclick=()=>{view('home');$('url').value=row.url;$('url').dispatchEvent(new Event('input'));$('analyze-form').requestSubmit();};actions.append(choose);
    }
    content.append(title,author,actions);card.append(cover,content);box.append(card);
  }
}
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
  const selectable=rows.filter(row=>row.selectable!==false).length;
  $('playlist-summary').textContent=selectable+' selectable entries · '+(rows.length-selectable)+' unavailable or duplicate entries excluded'+(playlist.unlisted?' · '+playlist.unlisted+' entries not listed by YouTube':'')+'.';
  const fragment=document.createDocumentFragment();
  for(const video of rows) {
    const row=document.createElement('label');row.className='playlist-video availability-'+(video.status || 'unknown');
    const input=document.createElement('input');input.type='checkbox';input.value=video.id;input.disabled=video.selectable===false;
    input.checked=!input.disabled && (!preserve || before.has(video.id));input.onchange=playlistSelection;
    const title=document.createElement('span');title.textContent=video.position+'. '+video.title;
    const status=document.createElement('small');status.className='playlist-availability';status.textContent=video.status_label || 'Not verified';status.title=video.detail || 'Flat listing information; choose Check selected for a live access check.';
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
