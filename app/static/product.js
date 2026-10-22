let recoveryState = null, recoveryLoading = null, recoverySignature = '', recoveryVisible = 50;
function renderQueueOverview() {
  const active = jobs.filter(j => ['downloading','processing'].includes(j.status));
  const waiting = jobs.filter(j => j.status === 'queued' && j.stage !== 'retry_wait');
  const retrying = jobs.filter(j => j.status === 'queued' && j.stage === 'retry_wait');
  $('queue-running').textContent = active.length;
  $('queue-waiting').textContent = waiting.length;
  $('queue-recovering').textContent = retrying.length;
  const box = $('queue-items');
  const list = [...active,...retrying,...waiting].slice(0,3);
  const signature = JSON.stringify(list);
  if (box.dataset.signature === signature) return;
  box.dataset.signature = signature; box.replaceChildren();
  if (!list.length) {
    const empty = document.createElement('div'); empty.className = 'queue-empty';
    const mark = document.createElement('span'); mark.textContent = '✓';
    const title = document.createElement('strong'); title.textContent = 'You’re all caught up';
    const text = document.createElement('p'); text.textContent = 'New downloads and recovery progress will appear here.';
    empty.append(mark,title,text); box.append(empty); return;
  }
  for (const job of list) {
    const row = document.createElement('button'); row.className = 'queue-item';
    const title = document.createElement('strong'); title.textContent = job.title;
    const status = document.createElement('span');
    status.textContent = job.stage === 'retry_wait' ? 'Recovery after queue · Attempt '+job.auto_attempts : job.stage === 'source_wait' ? 'Waiting for source cooldown' : job.status === 'queued' ? 'Waiting for a download slot' : job.status === 'processing' ? 'Finishing your file' : 'Downloading · '+job.progress+'%';
    row.append(title,status);
    if (job.status==='downloading') { const meter = document.createElement('progress'); meter.max=100; meter.value=job.progress; meter.setAttribute('aria-label','Download progress');row.append(meter); }
    row.onclick = () => { view('downloads');$('search').value=job.title;paginationState.library.page=1;renderAll(); };
    box.append(row);
  }
}
$('open-recovery').onclick = () => view('recovery');
$('show-attention').onclick = () => { view('downloads');$('status-filter').value='failed';$('search').value='';paginationState.library.page=1;renderAll(); };
function renderRecovery(data) {
  recoveryState = data;
  $('nav-recovery').textContent = data.waiting+data.needs_action;
  $('recovery-enable').checked = data.enabled;
  $('recovery-enabled-label').textContent = data.enabled ? 'Enabled' : 'Paused';
  $('recovery-held').textContent = data.waiting;
  $('recovery-action').textContent = data.needs_action;
  $('recovery-next').textContent = data.next_retry_at ? new Date(data.next_retry_at*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : '—';
  const signature = JSON.stringify([data.items,recoveryVisible]);
  if (signature===recoverySignature) return;
  recoverySignature=signature; const box=$('recovery-list');box.replaceChildren();
  if (!data.items.length) {
    const empty=document.createElement('div');empty.className='empty';
    const heading=document.createElement('h3');heading.textContent='Everything is in good shape.';
    const description=document.createElement('p');description.textContent='Temporary failures and clear next steps will appear here when needed.';
    empty.append(heading,description);box.append(empty);return;
  }
  for (const item of data.items.slice(0,recoveryVisible)) {
    const row=document.createElement('article');row.className='recovery-entry';
    const badge=document.createElement('span');badge.className='recovery-entry-badge '+(item.status==='queued'?'scheduled':'action');badge.textContent=item.status==='queued'?'Scheduled':'Needs action';
    const content=document.createElement('div');content.className='recovery-entry-content';
    const title=document.createElement('h3');title.textContent=item.title;
    const reason=document.createElement('p');reason.textContent=item.reason || 'Waiting for the shared source cooldown.';
    const action=document.createElement('small');action.textContent=item.action;
    content.append(title,reason,action);
    const meta=document.createElement('div');meta.className='recovery-entry-meta';
    const attempt=document.createElement('span');attempt.textContent='Automatic attempts: '+item.attempts;
    const when=document.createElement('small');when.textContent=item.status==='queued' ? 'After queue · '+(item.retry_at ? new Date(item.retry_at*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : 'Source wait') : 'Check the next step';meta.append(attempt,when);
    const help=document.createElement('a');help.href='/help/troubleshooting';help.textContent='Get help';help.className='secondary';
    row.append(badge,content,meta,help);box.append(row);
  }
  if (data.items.length > recoveryVisible) {
    const more=document.createElement('button');more.className='recovery-more secondary';
    more.textContent='Showing '+recoveryVisible+' of '+data.items.length+' · Show 50 more';
    more.onclick=()=>{recoveryVisible+=50;renderRecovery(data);};box.append(more);
  }
}
async function loadRecovery() {
  if (recoveryLoading) return recoveryLoading;
  recoveryLoading = (async () => {
    try {renderRecovery(await api('/api/recovery',{timeout:10000}));$('recovery-message').hidden=true;}
    catch(e) {$('recovery-message').textContent=e.message;$('recovery-message').hidden=false;}
  })().finally(() => {recoveryLoading=null;});
  return recoveryLoading;
}
$('recovery-enable').onchange=async () => {
  const input=$('recovery-enable');input.disabled=true;
  try {renderRecovery(await api('/api/recovery/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:input.checked})}));$('recovery-message').hidden=true;await refresh();}
  catch(e) {input.checked=recoveryState?.enabled ?? true;$('recovery-message').textContent=e.message;$('recovery-message').hidden=false;}
  finally {input.disabled=false;}
};
function renderSystemChecks(health) {
  const checks=[['App version',health.version,health.version===UI_VERSION],['Download extractor',health.yt_dlp_version || 'Check installation',!!health.yt_dlp_version],['FFmpeg',health.ffmpeg?'Installed':'Missing',health.ffmpeg],['FFprobe',health.ffprobe?'Installed':'Missing',health.ffprobe],['JavaScript runtime',(health.js_runtimes || []).join(', ') || 'Missing',!!health.js_runtimes?.length],['YouTube helper scripts',health.youtube_ejs?'Available':'Missing',health.youtube_ejs],['Download mode',health.capabilities?.direct_save?'Direct laptop saving':'Server + browser saving',true]];
  const box=$('checks-list');box.replaceChildren();
  for (const [name,value,ok] of checks) {
    const row=document.createElement('div');row.className='check-row';const label=document.createElement('span');label.textContent=name;const result=document.createElement('strong');result.textContent=(ok?'✓ ':'! ')+value;result.className=ok?'check-ok':'check-warning';row.append(label,result);box.append(row);
  }
}
$('system-checks').onclick=()=>$('checks-dialog').showModal();$('close-checks').onclick=()=>$('checks-dialog').close();
$('workspace-search').oninput=event=>{view('downloads');$('search').value=event.target.value;paginationState.library.page=1;renderAll();};
$('paste-link').onclick=async()=>{
  try {if(!navigator.clipboard?.readText)throw Error();$('url').value=(await navigator.clipboard.readText()).trim();$('url').dispatchEvent(new Event('input'));$('url').focus();}
  catch {notice('Click the link field and press Ctrl+V (Windows) or Command+V (Mac) to paste your copied link.');$('url').focus();}
};
document.addEventListener('keydown',event=>{
  if(event.key!=='/' || /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) || document.querySelector('dialog[open]'))return;
  event.preventDefault();$('workspace-search').focus();
});
try {$('welcome-guide').hidden=localStorage.getItem('aaasoft-welcome-dismissed')==='1';}catch {$('welcome-guide').hidden=false;}
$('dismiss-welcome').onclick=()=>{$('welcome-guide').hidden=true;try{localStorage.setItem('aaasoft-welcome-dismissed','1');}catch{}};
loadRecovery();setInterval(loadRecovery,10000);renderAll();
