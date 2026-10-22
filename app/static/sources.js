/* Dedicated platform panels. TikTok profile imports use verified server previews. */
const sourceNames={youtube:'YouTube',tiktok:'TikTok',instagram:'Instagram',facebook:'Facebook',x:'X / Twitter',dailymotion:'Dailymotion',threads:'Threads'};
let accountProfileSignature='';
let sourceCurrent='tiktok';
let accountTask=null,accountToken=null,accountPage=1,accountOffset=0,accountBusy=false,accountLoading=false,accountSubmitted=false;
let accountTotal=0,accountMore=false,accountRows=[],accountSelected=new Set(),accountVersion=0,accountRequest=null,accountPoll=null,accountLast=0,accountPollErrors=0,accountUnknown=0,accountSubmission=null;
function sourceMessage(panel,text){panel.querySelector('.source-message').textContent=text;}
function showSource(provider){
  sourceCurrent=provider;
  for(const panel of $('source-panels').children)panel.hidden=panel.dataset.source!==provider;
  for(const button of $('source-tabs').children)button.setAttribute('aria-pressed',String(button.dataset.source===provider));
  if(provider==='tiktok' && accountToken)fetchAccountPage();
}
for(const [provider,name] of Object.entries(sourceNames)){
  const tab=document.createElement('button');tab.type='button';tab.dataset.source=provider;tab.textContent=name;tab.onclick=()=>showSource(provider);$('source-tabs').append(tab);
  const panel=document.createElement('section');panel.className='source-panel';panel.dataset.source=provider;
  const heading=document.createElement('div');heading.className='source-heading';
  const title=document.createElement('h2');title.textContent=name+' workspace';
  const counter=document.createElement('span');counter.className='source-lifetime';counter.dataset.sourceTotal=provider;counter.textContent='0 lifetime downloads';heading.append(title,counter);panel.append(heading);
  if(provider==='tiktok'){
    const tools=document.createElement('div');tools.className='account-tools';
    // Static markup only; source-supplied metadata is inserted with textContent below.
    tools.innerHTML='<div class="account-intro"><div><h3>Browse a TikTok account</h3><p>Enter a username or public profile link. Videos are listed and checked sequentially before you queue a selection.</p></div><a href="/help/tiktok-accounts">Account import guide ↗</a></div><form id="account-form"><label for="account-name">TikTok username / profile URL</label><div class="account-input"><input id="account-name" required maxlength="300" placeholder="@username or https://www.tiktok.com/@username" autocomplete="off"><button class="primary" id="account-load">Load &amp; check account</button><button class="secondary" type="button" id="account-cancel" hidden>Stop</button><button class="secondary" type="button" id="account-resume" hidden>Retry status</button></div><details class="account-seed"><summary>Optional: assist account lookup with a public video</summary><label for="account-seed">Public video or TikTok share link from this same account</label><input id="account-seed" type="url" maxlength="2048" placeholder="https://www.tiktok.com/@username/video/…"><p>Accepts direct video URLs and vt.tiktok.com / vm.tiktok.com share links. The source must confirm the same account; network blocks may still prevent lookup.</p></details></form><p id="account-status" role="status">Public videos only. Private posts, source limits and verification may prevent a complete listing.</p><section id="account-profile" class="account-profile" aria-label="TikTok account details" hidden></section><progress id="account-progress" max="100" value="0" hidden aria-label="TikTok availability check progress"></progress><section id="account-result" hidden><div class="account-list-heading"><div><h3 id="account-title"></h3><p id="account-counts"></p></div><button id="account-recheck" class="secondary">Check unverified again</button></div><form id="account-filter-form" class="account-filters"><input id="account-search" type="search" maxlength="200" placeholder="Search captions or video IDs…" aria-label="Search account videos"><select id="account-filter" aria-label="Filter account videos"><option value="all">All listed videos</option><option value="available">Available · checked</option><option value="downloaded">Already downloaded</option><option value="unavailable">Unavailable / private</option><option value="unknown">Not verified</option></select><button class="secondary">Apply filter</button></form><div class="account-selection"><strong id="account-selected">0 selected</strong><button id="account-select-page" class="secondary">Select available on page</button><button id="account-select-all" class="secondary">Select all available matches</button><button id="account-clear" class="text-button">Clear selection</button><label>Format<select id="account-format"><option>mp4</option><option>webm</option><option>mkv</option><option>mp3</option><option>m4a</option><option>wav</option></select></label><label>Maximum quality<select id="account-quality"><option value="1080">1080p</option><option value="720">720p</option><option value="480">480p</option><option value="360">360p</option></select></label><button id="account-queue" class="primary" disabled>Download selected</button></div><p class="account-hint">Selections stay across pages. Only checked, available videos can be selected. Previous downloads are protected unless you enable Allow repeat downloads.</p><div id="account-videos" class="account-grid"></div><nav id="account-pagination" class="pagination" aria-label="TikTok account pages"></nav><div class="account-batches"><button id="account-previous-batch" class="secondary" disabled>Previous profile batch</button><span id="account-batch-label"></span><button id="account-next-batch" class="secondary" disabled>Load next 1,000 posts</button></div><p class="account-hint">Profiles are read in batches of up to 1,000 source-listed posts. Use Next for larger accounts. TikTok may omit or restrict posts; an empty response does not prove the account has no videos.</p></section>';
    panel.append(tools);
  }
  const single=document.createElement('div');single.className='source-single';
  const label=document.createElement('h3');label.textContent='Download one '+name+' video';
  const note=document.createElement('p');note.textContent='Paste a public video/post link from '+name+'. Duplicate protection, folder settings and the activity log apply.';
  const form=document.createElement('form');form.className='source-single-form';
  const url=document.createElement('input');url.type='url';url.required=true;url.maxLength=2048;url.placeholder=name+' video link';url.setAttribute('aria-label',name+' video URL');
  const analyze=document.createElement('button');analyze.className='primary';analyze.textContent='Find video';form.append(url,analyze);
  const message=document.createElement('p');message.className='source-message';message.setAttribute('role','status');
  const result=document.createElement('div');result.className='source-single-result';result.hidden=true;
  const mediaTitle=document.createElement('strong');const format=document.createElement('select');format.setAttribute('aria-label',name+' output format');for(const value of ['mp4','webm','mkv','mp3','m4a','wav'])format.add(new Option(value,value));
  const quality=document.createElement('select');quality.setAttribute('aria-label',name+' maximum quality');for(const value of ['1080','720','480','360'])quality.add(new Option(value+'p',value));
  const queue=document.createElement('button');queue.type='button';queue.className='primary';queue.textContent='Add to queue';result.append(mediaTitle,format,quality,queue);
  let analyzed=null,version=0,controller=null;
  url.oninput=()=>{version++;controller?.abort();analyzed=null;result.hidden=true;analyze.disabled=false;message.textContent='';};
  form.onsubmit=async event=>{event.preventDefault();const current=++version;controller?.abort();controller=new AbortController();analyze.disabled=true;sourceMessage(panel,'Checking '+name+' video…');
    try{const info=await api('/api/platform/'+provider+'/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:url.value.trim()}),signal:controller.signal});if(current!==version)return;analyzed=info;mediaTitle.textContent=info.title;result.hidden=false;sourceMessage(panel,info.previous_downloads?'Already downloaded. Use Completed or explicitly allow another copy.':'Choose format and quality, then add to queue.');}
    catch(error){if(current===version && error.name!=='AbortError')sourceMessage(panel,error.message);}finally{if(current===version)analyze.disabled=false;}};
  queue.onclick=async()=>{if(!analyzed)return;queue.disabled=true;try{await ensureDownloadLocation();await api('/api/platform/'+provider+'/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:analyzed.url,format:format.value,quality:quality.value,allow_repeat:repeatAllowed()})});sourceMessage(panel,'Added to Work queue. Completed videos move to Completed.');await refresh();}catch(error){sourceMessage(panel,error.message);}finally{queue.disabled=false;}};
  single.append(label,note,form,message,result);
  if(provider==='youtube' || provider==='dailymotion'){
    const search=document.createElement('button');search.className='secondary';search.textContent='Search '+name+' inside the portal';search.onclick=()=>{view('discover');$('discovery-provider').value=provider;$('discovery-provider').dispatchEvent(new Event('change'));$('discovery-query').focus();};single.append(search);
  }
  if(provider==='youtube'){
    const playlist=document.createElement('button');playlist.className='secondary';playlist.textContent='Open YouTube playlist tools';playlist.onclick=()=>{view('home');$('playlist-panel').open=true;$('playlist-panel').scrollIntoView({behavior:'smooth'});};single.append(playlist);
  }
  const history=document.createElement('button');history.className='text-button';history.textContent='View '+name+' completed videos';history.onclick=()=>{$('search').value='';$('source-filter').value=name;view('completed');};single.append(history);
  panel.append(single);$('source-panels').append(panel);
}
showSource('tiktok');
function syncSourceStatistics(stats){for(const counter of document.querySelectorAll('[data-source-total]')){const name=sourceNames[counter.dataset.sourceTotal];const value=stats?.platforms.find(item=>item.name===name)?.downloads || 0;counter.textContent=value+(value===1?' lifetime download':' lifetime downloads');}}
function showAccountProfile(profile){
  if(!profile)return;
  const signature=JSON.stringify(profile);if(signature===accountProfileSignature)return;accountProfileSignature=signature;
  const panel=$('account-profile');panel.hidden=false;panel.replaceChildren();
  const heading=document.createElement('div');heading.className='profile-heading';
  if(profile.avatar){const avatar=document.createElement('img');avatar.src=profile.avatar;avatar.alt='Account avatar';avatar.loading='lazy';avatar.referrerPolicy='no-referrer';avatar.onerror=()=>avatar.remove();heading.append(avatar);}
  const title=document.createElement('h3');title.textContent=profile.display_name || '@'+profile.username;
  const label=document.createElement('p');label.textContent=profile.identity_verified?'Public account identity confirmed':'Requested account · identity not yet confirmed';
  const intro=document.createElement('div');intro.append(title,label);heading.append(intro);panel.append(heading);
  const fields=document.createElement('dl');const details=[['Username','@'+profile.username],['Profile URL',profile.profile_url],['User ID',profile.user_id],['Account ID',profile.account_id],['Bio',profile.bio],['Followers',profile.followers],['Following',profile.following],['Likes',profile.likes],['Source video count',profile.video_count],['Verified account',typeof profile.verified==='boolean'?(profile.verified?'Yes':'No'):null],['Private account',typeof profile.private==='boolean'?(profile.private?'Yes':'No'):null]];
  const copy=async(button,value)=>{try{await navigator.clipboard.writeText(String(value));button.textContent='Copied';}catch{button.textContent='Select the text to copy';}};
  for(const [name,value] of details){const row=document.createElement('div');const term=document.createElement('dt');term.textContent=name;const desc=document.createElement('dd');const text=document.createElement('span');text.textContent=value===null || value===undefined || value===''?'Not provided':String(value);desc.append(text);if(value!==null && value!==undefined && value!==''){const button=document.createElement('button');button.type='button';button.className='text-button';button.textContent='Copy';button.setAttribute('aria-label','Copy '+name);button.onclick=()=>copy(button,value);desc.append(button);}row.append(term,desc);fields.append(row);}panel.append(fields);
  const actions=document.createElement('div');actions.className='profile-actions';const all=document.createElement('button');all.type='button';all.className='secondary';all.textContent='Copy account details';all.onclick=()=>copy(all,details.filter(([,value])=>value!==null && value!==undefined && value!=='').map(([name,value])=>name+': '+value).join('\n'));actions.append(all);
  const link=document.createElement('a');link.textContent='Open profile ↗';link.href=profile.profile_url;link.target='_blank';link.rel='noopener noreferrer';actions.append(link);panel.append(actions);
  const note=document.createElement('p');note.className='account-hint';note.textContent=profile.identity_verified?'Available account details are used automatically; no ID copying is needed to load videos. Counts describe source metadata, not guaranteed downloadable videos.':'Only the requested username and profile link are known. TikTok has not confirmed public account details; missing values are not zero.';panel.append(note);
}
function accountEligible(row){const state=discoveryState(row);return !state.active && (repeatAllowed() || !state.count) && row.status==='available' && row.selectable && !row.already_queued && (repeatAllowed() || !row.previous_downloads);}
function accountSelection(){
  $('account-selected').textContent=accountSelected.size+' selected';$('account-queue').disabled=!accountToken || !accountSelected.size || accountBusy || accountLoading || accountSubmitted;
  $('account-queue').textContent=accountSubmitted?'Selection submitted':'Download selected';
  for(const box of $('account-videos').querySelectorAll('input')){const row=accountRows.find(row=>row.id===box.value);box.checked=accountSelected.has(box.value);box.disabled=accountBusy || accountSubmitted || !row || !accountEligible(row);}
  $('account-recheck').disabled=accountBusy || accountLoading || accountSubmitted || !accountUnknown;
  $('account-next-batch').disabled=accountBusy || accountLoading || !accountMore || accountOffset>=100000;
  $('account-previous-batch').disabled=accountBusy || accountLoading || accountOffset===0;
}
function accountParams(){return new URLSearchParams({page:accountPage,size:30,q:$('account-search').value,status:$('account-filter').value});}
async function fetchAccountPage(){
  if(!accountToken)return;
  const version=++accountVersion;accountRequest?.abort();const controller=new AbortController();accountRequest=controller;
  try{const data=await api('/api/tiktok/previews/'+encodeURIComponent(accountToken)+'?'+accountParams(),{signal:controller.signal});if(version!==accountVersion)return;
    showAccountProfile(data.profile);accountRows=data.rows;accountUnknown=data.counts.unknown;accountTotal=data.total;accountMore=data.has_more;accountSubmitted=data.submitted;accountOffset=data.offset;accountLoading=data.busy;
    $('account-title').textContent='@'+data.account+' · '+data.title;
    $('account-counts').textContent=data.counts.all+' listed · '+data.counts.available+' available · '+data.counts.downloaded+' downloaded · '+data.counts.unavailable+' unavailable · '+data.counts.unknown+' not verified';
    $('account-result').hidden=false;$('account-batch-label').textContent=data.counts.all?'Source posts '+(data.offset+1)+'–'+(data.offset+data.counts.all):'No posts listed in this batch';
    const grid=$('account-videos');const focused=grid.contains(document.activeElement)?document.activeElement.value:null;const signature=JSON.stringify([data.rows,repeatAllowed(),accountSubmitted]);
    if(grid.dataset.signature!==signature){grid.dataset.signature=signature;grid.replaceChildren();
      for(const row of data.rows){
        if(!accountEligible(row))accountSelected.delete(row.id);
        const card=document.createElement('article');card.className='account-card';card.dataset.videoId=row.id;card.dataset.status=row.previous_downloads?'downloaded':row.status;
        const poster=document.createElement('div');poster.className='account-cover';placeholder(poster,'TikTok');image(poster,row.thumbnail,'Thumbnail for '+row.title);
        const badge=document.createElement('span');badge.className='account-badge';badge.textContent=row.previous_downloads?'✓ Already downloaded':row.already_queued?'In queue':row.status_label;poster.append(badge);
        const choice=document.createElement('label');choice.className='card-select';const box=document.createElement('input');box.type='checkbox';box.value=row.id;box.disabled=!accountEligible(row) || accountSubmitted;box.checked=accountSelected.has(row.id);box.setAttribute('aria-label','Select '+row.title);box.onchange=()=>{box.checked?accountSelected.add(row.id):accountSelected.delete(row.id);accountSelection();};choice.append(box);poster.append(choice);
        if(row.url){const play=document.createElement('button');play.className='account-preview';play.append(icon('play'));play.title='Preview '+row.title;play.setAttribute('aria-label','Preview '+row.title);play.onclick=()=>playDiscovered({...row,account_preview:true});poster.append(play);}
        const progress=document.createElement('progress');progress.className='account-download-progress';progress.max=100;progress.value=0;progress.hidden=true;progress.setAttribute('aria-label','Download progress for '+row.title);poster.append(progress);
        const title=document.createElement('p');title.textContent=row.title;title.title=row.title;
        const info=document.createElement('small');info.textContent=(row.duration?duration(row.duration)+' · ':'')+row.id;card.append(poster,title,info);grid.append(card);
      }
      if(!data.rows.length){const empty=document.createElement('p');empty.className='empty';empty.textContent='No entries match this filter. A source may hide or restrict posts; try another filter or account.';grid.append(empty);}
    }
    if(focused){const control=[...grid.querySelectorAll('input')].find(box=>box.value===focused && !box.disabled);control?.focus({preventScroll:true});}
    const pages=Math.max(1,Math.ceil(data.total/30));const nav=$('account-pagination');const navKey=[accountPage,pages,data.total].join(':');
    if(nav.dataset.signature!==navKey){nav.dataset.signature=navKey;nav.replaceChildren();const summary=document.createElement('span');summary.textContent='Page '+accountPage+' / '+pages+' · '+data.total+' matching videos';nav.append(summary);for(const [label,page,disabled] of [['Previous',accountPage-1,accountPage===1],['Next',accountPage+1,accountPage>=pages]]){const button=document.createElement('button');button.className='secondary';button.textContent=label;button.disabled=disabled;button.onclick=()=>{accountPage=page;fetchAccountPage();};nav.append(button);}}
    accountSelection();syncAccountDownloads();accountLast=Date.now();
  }catch(error){if(version===accountVersion && error.name!=='AbortError'){$('account-status').textContent=error.message;if(error.message.includes('preview expired')){accountToken=null;accountSelected.clear();$('account-result').hidden=true;accountSelection();}}}
  finally{if(version===accountVersion)accountRequest=null;}
}
async function pollAccountTask(id){
  try{const task=await api('/api/tiktok/tasks/'+encodeURIComponent(id));if(accountTask!==id)return;accountPollErrors=0;showAccountProfile(task.profile);$('account-resume').hidden=true;
    accountLoading=['queued','running','cancelling'].includes(task.status);$('account-progress').hidden=!accountLoading;
    $('account-progress').value=task.total?task.checked/task.total*100:0;
    $('account-status').textContent=task.status==='queued'?'Waiting for the account worker…':task.phase==='listing' && accountLoading?'Reading TikTok profile · '+task.entries+' entries found…':(task.phase==='listing' && !accountLoading && task.status==='failed'?'Account listing failed':(accountLoading?'Checking source access · ':'Checks '+(task.status==='ready'?'complete':task.status)+' · ')+task.checked+' / '+task.total)+(task.detail?' · '+task.detail:'');
    if(task.token){accountToken=task.token;await fetchAccountPage();}
    $('account-load').disabled=accountLoading;$('account-name').disabled=accountLoading;$('account-cancel').hidden=!accountLoading;
    if(accountLoading)accountPoll=setTimeout(()=>pollAccountTask(id),2000);else{accountTask=null;accountSelection();}
  }catch(error){$('account-status').textContent=error.message;accountPollErrors++;if(error.message.includes('expired')){accountTask=null;accountLoading=false;$('account-load').disabled=false;$('account-name').disabled=false;$('account-cancel').hidden=true;accountSelection();}else if(accountPollErrors<3){accountPoll=setTimeout(()=>{if(accountTask===id)pollAccountTask(id);},4000);}else{$('account-status').textContent+=' Check the connection, then choose Retry status.';$('account-resume').hidden=false;}}
}
async function loadAccount(offset=0){
  if(accountBusy || accountLoading)return;
  if(accountSelected.size && !window.confirm('Changing the profile batch clears the current selection. Continue?'))return;
  accountProfileSignature='';$('account-profile').hidden=true;accountVersion++;accountRequest?.abort();accountRequest=null;accountBusy=true;accountPollErrors=0;accountSubmission=null;accountSelected.clear();accountToken=null;accountPage=1;accountSubmitted=false;$('account-result').hidden=true;$('account-status').textContent='Starting account listing…';$('account-load').disabled=true;
  try{const result=await api('/api/tiktok/loads',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({account:$('account-name').value,offset,seed_url:$('account-seed').value.trim() || null})});accountTask=result.id;accountLoading=true;await pollAccountTask(result.id);}
  catch(error){$('account-status').textContent=error.message;$('account-load').disabled=false;accountLoading=false;}
  finally{accountBusy=false;accountSelection();}
}
$('account-name').oninput=()=>{if(accountLoading)return;accountProfileSignature='';$('account-profile').hidden=true;accountVersion++;accountRequest?.abort();accountRequest=null;accountToken=null;accountSelected.clear();accountSubmitted=false;$('account-result').hidden=true;$('account-status').textContent='Load and check this account to review its videos.';accountSelection();};
$('account-form').onsubmit=event=>{event.preventDefault();loadAccount();};
$('account-resume').onclick=()=>{if(accountTask){accountPollErrors=0;pollAccountTask(accountTask);}};
$('account-cancel').onclick=async()=>{if(!accountTask)return;try{await api('/api/tiktok/tasks/'+encodeURIComponent(accountTask),{method:'DELETE'});$('account-status').textContent='Stopping after the current source request…';}catch(error){$('account-status').textContent=error.message;}};
$('account-filter-form').onsubmit=event=>{event.preventDefault();accountPage=1;fetchAccountPage();};
$('account-filter').onchange=()=>{accountPage=1;fetchAccountPage();};
$('account-clear').onclick=()=>{accountSelected.clear();accountSelection();};
$('account-select-page').onclick=()=>{for(const row of accountRows)if(accountEligible(row))accountSelected.add(row.id);accountSelection();};
$('account-select-all').onclick=async()=>{if(!accountToken)return;try{const params=accountParams();params.set('allow_repeat',String(repeatAllowed()));const token=accountToken;const result=await api('/api/tiktok/previews/'+encodeURIComponent(token)+'/selection?'+params);if(accountToken!==token)return;result.ids.forEach(id=>accountSelected.add(id));accountSelection();}catch(error){$('account-status').textContent=error.message;}};
$('account-recheck').onclick=async()=>{if(!accountToken || accountLoading || accountBusy)return;accountBusy=true;accountSelection();try{const result=await api('/api/tiktok/previews/'+encodeURIComponent(accountToken)+'/checks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({all_entries:false})});accountTask=result.id;accountLoading=true;pollAccountTask(result.id);}catch(error){$('account-status').textContent=error.message;}finally{accountBusy=false;accountSelection();}};
$('account-format').onchange=()=>{$('account-quality').closest('label').hidden=audioFormats.includes($('account-format').value);};
$('account-next-batch').onclick=()=>loadAccount(accountOffset+1000);$('account-previous-batch').onclick=()=>loadAccount(Math.max(0,accountOffset-1000));
$('account-queue').onclick=async()=>{if(!accountToken || !accountSelected.size || accountBusy || accountLoading)return;accountBusy=true;accountSelection();try{await ensureDownloadLocation();const payload={video_ids:[...accountSelected].sort(),format:$('account-format').value,quality:$('account-quality').value,allow_repeat:repeatAllowed()};const signature=JSON.stringify(payload);if(!accountSubmission || accountSubmission.signature!==signature)accountSubmission={signature,id:globalThis.crypto?.randomUUID?.() || Date.now().toString(36)+'-'+Math.random().toString(36).slice(2)};payload.request_id=accountSubmission.id;const result=await api('/api/tiktok/previews/'+encodeURIComponent(accountToken)+'/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});accountSubmission=null;accountSelected.clear();accountSubmitted=true;$('account-status').textContent=result.added+(result.added===1?' video':' videos')+' queued · '+result.duplicates_removed+' duplicates skipped. Follow Work queue and Completed.';await refresh();await fetchAccountPage();}catch(error){$('account-status').textContent=error.message;}finally{accountBusy=false;accountSelection();}};
function syncAccountDownloads(){
  const index=discoveryJobIndex();
  for(const card of $('account-videos').querySelectorAll('.account-card')){const row=accountRows.find(row=>row.id===card.dataset.videoId);if(!row)continue;const state=discoveryState(row,index);const badge=card.querySelector('.account-badge');badge.textContent=state.active?state.label:state.count?'✓ Already downloaded':row.status_label;badge.title=row.status_label+(row.checked_at?' · Checked '+new Date(row.checked_at*1000).toLocaleString():'');card.dataset.status=state.active?state.kind:state.count?'downloaded':row.status;const progress=card.querySelector('progress');if(progress){progress.hidden=!state.active;progress.value=state.progress;}const play=card.querySelector('.account-preview');if(play)play.title=(state.saved?'Play saved: ':'Preview: ')+row.title;}
}
function refreshSources(){syncAccountDownloads();if(!$('sources').hidden && sourceCurrent==='tiktok' && accountToken && !accountTask && !accountRequest && Date.now()-accountLast>2500)fetchAccountPage();}
$('allow-repeat').addEventListener('change',()=>{if(accountToken)fetchAccountPage();});
