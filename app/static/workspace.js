/* Completed archive, duplicate policy and retained workspace activity. */
let discoveryFilter='new',activityPage=1,activityVersion=0,activityController=null,activityLast=0;
function repeatAllowed(){return $('allow-repeat').checked;}
let repeatResolve=null;
function confirmRepeat(title){
  if(repeatResolve)return Promise.resolve(false);
  $('repeat-detail').textContent='You have already downloaded “'+title+'”. Choose another copy only if you need it.';
  $('repeat-dialog').showModal();return new Promise(resolve=>{repeatResolve=resolve;});
}
$('repeat-cancel').onclick=()=>$('repeat-dialog').close();
$('repeat-confirm').onclick=()=>{const resolve=repeatResolve;repeatResolve=null;$('repeat-dialog').close();resolve?.(true);};
$('repeat-dialog').onclose=()=>{repeatResolve?.(false);repeatResolve=null;};
$('allow-repeat').onchange=()=>{
  const allowed=repeatAllowed();
  $('duplicate-policy-label').textContent=allowed?'Repeat downloads allowed for this session':'Duplicate protection is on';
  $('allow-repeat').closest('.duplicate-policy').classList.toggle('repeat-enabled',allowed);
  syncDiscoveryJobs();syncSingleDownload();
  if(playlist)renderPlaylistRows(true);
};
function syncWorkspaceTabs(){
  const filter=$('status-filter').value;
  for(const button of document.querySelectorAll('[data-library-tab]')){
    button.setAttribute('aria-pressed',String(button.dataset.libraryTab===filter));
    button.querySelector('b').textContent=jobs.filter(job=>matchesStatus(job,button.dataset.libraryTab)).length;
  }
  $('nav-completed-count').textContent=jobs.filter(job=>job.status==='completed').length;
  if(!$('downloads').hidden){
    $('downloads').querySelector('.page-heading h1').textContent=filter==='completed'?'Your completed videos.':filter==='pending'?'Your download work queue.':'Your video library.';
    $('page-label').textContent=filter==='completed'?'Completed library':filter==='pending'?'Work queue':'Video library';
    for(const button of document.querySelectorAll('.nav')){
      const active=button.dataset.view===(filter==='completed'?'completed':'downloads');
      button.classList.toggle('active',active);button.setAttribute('aria-current',active?'page':'false');
    }
  }
}
for(const button of document.querySelectorAll('[data-library-tab]'))button.onclick=()=>view(button.dataset.libraryTab==='completed'?'completed':'downloads',button.dataset.libraryTab);
for(const button of document.querySelectorAll('[data-discovery-tab]'))button.onclick=()=>{discoveryFilter=button.dataset.discoveryTab;syncDiscoveryFilters();};
$('discovery-open-completed').onclick=()=>view('completed');
function syncDiscoveryFilters(){
  let total=0,done=0,visible=0;
  const playlists=$('discovery-kind').value==='playlists';
  if(playlists)discoveryFilter='all';
  const index=discoveryJobIndex();
  for(const card of $('discovery-results').querySelectorAll('.discovery-card')){
    const row=card.discoveryControls?.row;
    const downloaded=!!row && discoveryState(row,index).count>0;
    total++;if(downloaded)done++;
    card.hidden=card.dataset.resultKind==='playlists'?false:discoveryFilter==='new'?downloaded:discoveryFilter==='downloaded'?!downloaded:false;
    if(!card.hidden)visible++;
  }
  for(const button of document.querySelectorAll('[data-discovery-tab]')){
    const tab=button.dataset.discoveryTab;button.hidden=playlists && tab!=='all';
    button.setAttribute('aria-pressed',String(tab===discoveryFilter));
    button.querySelector('b').textContent=tab==='new'?total-done:tab==='downloaded'?done:total;
  }
  $('discovery-filter-empty').hidden=!total || !!visible;
  $('discovery-filter-empty').textContent=discoveryFilter==='downloaded'?'No downloaded videos on this search page yet. Open Completed for your full saved library.':'These results are already downloaded. Choose Downloaded above to preview them, or open Completed for saved files.';
}
function syncSingleDownload(){
  if(!analyzed)return;
  const state=discoveryState(analyzed);
  $('download-button').disabled=singleQueuing || !!state.active || (state.count>0 && !repeatAllowed());
  $('download-button').textContent=state.active?'Already in queue':state.count && !repeatAllowed()?'Already downloaded':state.count?'Download another copy':'Add to queue';
}
function refreshWorkspace(){
  syncSingleDownload();
  syncWorkspaceTabs();
  if(playlist && !playlistChecking && !playlistQueuing){
    const index=discoveryJobIndex();let changed=false;
    for(const row of [...playlist.videos,...(playlist.entries || [])]){
      if(!row.url)continue;
      const matches=index.get(row.source_key || row.url) || [];
      const count=Math.max(row.previous_downloads || 0,...matches.map(job=>job.previous_downloads || 0),matches.some(job=>job.status==='completed')?1:0);
      const active=matches.some(job=>['queued','downloading','processing'].includes(job.status));
      if(count!==row.previous_downloads || active!==row.already_queued){row.previous_downloads=count;row.already_queued=active;changed=true;}
    }
    if(changed)renderPlaylistRows(true);
  }
  if(!$('activity').hidden && Date.now()-activityLast>5000 && !activityController)loadActivity(false);
}
$('activity-form').onsubmit=event=>{event.preventDefault();activityPage=1;loadActivity();};
async function loadActivity(force=true){
  if(activityController && !force)return;
  activityController?.abort();const controller=new AbortController();activityController=controller;
  const version=++activityVersion;
  try{
    const params=new URLSearchParams({page:activityPage,size:25,q:$('activity-search').value,event:$('activity-event').value});
    const result=await api('/api/activity?'+params,{signal:controller.signal});
    if(version!==activityVersion)return;
    const signature=JSON.stringify(result);const body=$('activity-rows');
    if(body.dataset.signature!==signature){
      body.dataset.signature=signature;body.replaceChildren();
      for(const item of result.items){
        const tr=document.createElement('tr');
        const when=document.createElement('td');when.textContent=new Date(item.created*1000).toLocaleString();
        const event=document.createElement('td');const badge=document.createElement('span');badge.className='activity-event';badge.dataset.event=item.event;badge.textContent=item.event.replaceAll('_',' ');event.append(badge);
        const video=document.createElement('td');const title=document.createElement('strong');title.textContent=item.title;video.append(title);
        const link=document.createElement('a');link.textContent=item.url;try{const url=new URL(item.url);if(['https:','http:'].includes(url.protocol)){link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';}}catch{}
        video.append(link);
        const copy=document.createElement('button');copy.className='text-button';copy.textContent='Copy link';copy.onclick=async()=>{try{await navigator.clipboard.writeText(item.url);copy.textContent='Copied ✓';}catch{$('activity-status').textContent='Clipboard unavailable. Select and copy the source link in the log.';}};video.append(copy);
        const detail=document.createElement('td');detail.textContent=[item.format?.toUpperCase(),item.quality?item.quality+'p':'',item.detail].filter(Boolean).join(' · ');
        tr.append(when,event,video,detail);body.append(tr);
      }
      if(!result.items.length){const tr=document.createElement('tr');const td=document.createElement('td');td.colSpan=4;td.textContent='No activity matches. New downloads and duplicate skips appear here automatically.';tr.append(td);body.append(tr);}
      const nav=$('activity-pagination');nav.replaceChildren();const pages=Math.max(1,Math.ceil(result.total/result.size));
      const summary=document.createElement('span');summary.textContent='Page '+result.page+' / '+pages+' · '+result.total+' events';nav.append(summary);
      for(const [label,page,disabled] of [['Previous',activityPage-1,activityPage===1],['Next',activityPage+1,activityPage>=pages]]){const button=document.createElement('button');button.className='secondary';button.textContent=label;button.disabled=disabled;button.onclick=()=>{activityPage=page;loadActivity();};nav.append(button);}
    }
    $('activity-status').textContent='Activity retained on this server · Updated '+new Date().toLocaleTimeString();activityLast=Date.now();
  }catch(error){if(version===activityVersion && error.name!=='AbortError')$('activity-status').textContent=error.message;}
  finally{if(version===activityVersion)activityController=null;}
}
syncWorkspaceTabs();syncDiscoveryFilters();
