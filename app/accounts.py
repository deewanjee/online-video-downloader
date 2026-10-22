"""Bounded, cancellable TikTok account listing and verified-selection imports."""
import math
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
from typing import Annotated
from urllib.parse import urlparse
from fastapi import HTTPException
from pydantic import BaseModel, Field
from app import availability, source_access, storage, recovery

BATCH=1000
PROVIDERS={'youtube':'YouTube','tiktok':'TikTok','instagram':'Instagram','facebook':'Facebook','x':'X / Twitter','dailymotion':'Dailymotion','threads':'Threads'}


def profile_url(value):
    value=value.strip()
    if '://' in value:
        try:
            parsed=urlparse(value)
            if parsed.scheme!='https' or parsed.hostname not in ('www.tiktok.com','tiktok.com') or parsed.username or parsed.password or parsed.port not in (None,443):raise ValueError()
            match=re.fullmatch(r'/@([A-Za-z0-9_.]{1,24})/?',parsed.path)
            if not match:raise ValueError()
            value=match[1]
        except ValueError:raise HTTPException(400,'Enter a TikTok @username or an HTTPS tiktok.com/@username profile link.') from None
    value=value.removeprefix('@')
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.]{0,23}',value) or value.endswith('.'):
        raise HTTPException(400,'Use the account username (up to 24 letters, numbers, dots or underscores), not a video link or display name.')
    return 'https://www.tiktok.com/@'+value


def cover(value):
    if not isinstance(value,str):return None
    try:
        parsed=urlparse(value);host=(parsed.hostname or '').lower()
        domains=('tiktokcdn.com','tiktokcdn-us.com','tiktokcdn-eu.com','tiktokv.com','byteoversea.com','ibytedtos.com','muscdn.com')
        if parsed.scheme=='https' and not parsed.username and not parsed.password and parsed.port in (None,443) and any(host==d or host.endswith('.'+d) for d in domains):return value
    except ValueError:pass
    return None


def video_username(info):
    # yt-dlp uploader_id is normally a numeric user ID, NOT the @username.
    value=info.get('uploader_url')
    if isinstance(value,str):
        try:return profile_url(value).rsplit('@',1)[-1]
        except HTTPException:pass
    value=info.get('uploader_id')
    if isinstance(value,str) and not value.isdigit():
        try:return profile_url(value).rsplit('@',1)[-1]
        except HTTPException:pass
    return None


def profile_details(data,expected):
    user=data.get('user') if isinstance(data,dict) else None
    if not isinstance(user,dict):return None
    username=user.get('uniqueId')
    if not isinstance(username,str) or username.casefold()!=expected.casefold():return None
    try:url=profile_url(username)
    except HTTPException:return None
    stats=data.get('statsV2') or data.get('stats') or {}
    if not isinstance(stats,dict):stats={}
    result={'username':username,'profile_url':url,'identity_verified':True,'checked_at':time.time(),
            'display_name':str(user.get('nickname') or '')[:200],'bio':str(user.get('signature') or '')[:2000],
            'avatar':cover(user.get('avatarLarger')) or cover(user.get('avatarMedium')),
            'user_id':str(user.get('id') or '')[:100],'account_id':str(user.get('secUid') or '')[:150],
            'verified':user.get('verified') if isinstance(user.get('verified'),bool) else None,
            'private':user.get('privateAccount') if isinstance(user.get('privateAccount'),bool) else None}
    for key,field in [('followers','followerCount'),('following','followingCount'),('likes','heartCount'),('video_count','videoCount')]:
        value=stats.get(field)
        if isinstance(value,str) and value.isdigit() and len(value)<20:value=int(value)
        result[key]=value if isinstance(value,int) and not isinstance(value,bool) and 0<=value<10**19 else None
    return result


def public_video_url(value,expected=None):
    """Accept only supported TikTok public post/share URLs, never arbitrary URLs."""
    try:
        parsed=urlparse(value.strip())
        if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in (None,443):raise ValueError()
        match=re.fullmatch(r'/@([A-Za-z0-9_.]{1,24})/video/([0-9]{6,25})/?',parsed.path)
        if parsed.hostname in ('www.tiktok.com','tiktok.com') and match:
            if expected and match[1].casefold()!=expected.casefold():raise HTTPException(400,'Use a public video from the same username as the account.')
            return 'https://www.tiktok.com/@'+match[1]+'/video/'+match[2]
        if ((parsed.hostname in ('vm.tiktok.com','vt.tiktok.com') and re.fullmatch(r'/[A-Za-z0-9_-]{3,100}/?',parsed.path)) or
            (parsed.hostname in ('www.tiktok.com','tiktok.com') and re.fullmatch(r'/t/[A-Za-z0-9_-]{3,100}/?',parsed.path))):
            return 'https://'+parsed.hostname+parsed.path
    except ValueError:pass
    raise HTTPException(400,'Enter a TikTok @username, profile URL, direct public video URL or supported vt.tiktok.com / vm.tiktok.com share link.')


def account_visibility(profile,error=None):
    value=profile.get('private') if isinstance(profile,dict) and profile.get('identity_verified') else None
    text=str(error or '').lower()
    if value is True:return {'status':'private','label':'Private account · source confirmed'}
    if value is False:return {'status':'public','label':'Public account · source confirmed'}
    if 'account is private' in text and not any(word in text for word in ('likely','either','may be','or all')):
        return {'status':'private','label':'Private account · source confirmed'}
    return {'status':'unknown','label':'Account privacy · Not verified'}


def source_issue(error):
    text=str(error).lower()
    if any(word in text for word in ('certificate verify failed','certificate_verify_failed')):return 'certificate'
    if any(word in text for word in ('failed to parse json','expecting value','invalid json')):return 'invalid_response'
    if source_access.is_rate_limit(text):return 'rate_limit'
    if 'ip address is blocked' in text:return 'ip_block'
    if re.search(r'(?:http(?: error)?|status)[ :=]+403\b',text):return 'access_denied'
    if account_visibility(None,error)['status']=='private':return 'private_account'
    if any(word in text for word in ('secondary user id','secuid','captcha','challenge','not a bot','verification')):return 'lookup_unavailable'
    code=recovery.classify(error)['code']
    return code if code in ('network','proxy','access','removed','restricted') else 'source_error'


def video_visibility(info=None,error=None):
    value=(info or {}).get('availability');text=str(error or '').lower()
    if value=='private' or (any(word in text for word in ('private video','video is private')) and not any(word in text for word in ('likely','either','may be','or all'))):
        return {'visibility':'private','visibility_label':'Private video · source confirmed'}
    if value in ('needs_auth','premium_only','subscriber_only') or any(word in text for word in ('login required','sign in','sign-in','age verification')):
        return {'visibility':'restricted','visibility_label':'Access restricted · privacy not verified'}
    if value=='public':return {'visibility':'public','visibility_label':'Public video · source confirmed'}
    if error is None and any(isinstance(f,dict) and f.get('vcodec') not in (None,'none') for f in (info or {}).get('formats',[]) or []):
        return {'visibility':'accessible','visibility_label':'Accessible · checked; privacy not provided'}
    return {'visibility':'unknown','visibility_label':'Video privacy · Not verified'}


def video_state(info=None,error=None):
    state=availability.checked(info,error)
    visibility=video_visibility(info,error)
    if visibility['visibility']=='private':state.update(status='private',status_label='Private video',selectable=False)
    elif visibility['visibility']=='restricted':state.update(status='restricted',status_label='Access restricted',selectable=False)
    elif error is not None and state['status']=='private':state.update(status='unknown',status_label='Not verified',selectable=False)
    if state['status']=='available' and not any(isinstance(f,dict) and f.get('vcodec') not in (None,'none') for f in (info or {}).get('formats',[]) or []):
        state={'status':'unsupported','status_label':'No video stream','selectable':False,'detail':'This post does not expose a playable video stream.'}
    return state|visibility


def account_row(entry,username,position):
    entry=entry if isinstance(entry,dict) else {}
    id=str(entry.get('id') or '')
    valid=bool(re.fullmatch(r'[0-9]{6,25}',id))
    state=video_state(entry)
    if not valid:state={'status':'invalid','status_label':'Unavailable entry','selectable':False}
    # Full formats prove a usable listing now; flat public metadata does not.
    state['selectable']=state['status']=='available'
    state['checked_at']=time.time() if state['status']!='unknown' else None
    duration=entry.get('duration')
    if not isinstance(duration,(int,float)) or isinstance(duration,bool) or not math.isfinite(duration) or duration<0:duration=None
    image=cover(entry.get('thumbnail')) or next((image for t in reversed(entry.get('thumbnails') or []) if isinstance(t,dict) and (image:=cover(t.get('url')))),None)
    return {'id':id if valid else 'unavailable-'+str(position),'url':'https://www.tiktok.com/@'+username+'/video/'+id if valid else None,
            'title':str(entry.get('title') or entry.get('description') or 'TikTok video')[:500],'position':position,'thumbnail':image,'duration':duration,
            'platform':'TikTok','provider':'tiktok','kind':'videos',**state}


class AccountRequest(BaseModel):
    account:str=Field(min_length=1,max_length=300)
    offset:int=Field(default=0,ge=0,le=100000)
    seed_url:str|None=Field(default=None,max_length=2048)


class AccountCheckRequest(BaseModel):
    all_entries:bool=False


class AccountQueueRequest(BaseModel):
    video_ids:list[Annotated[str,Field(pattern=r'^[0-9]{6,25}$')]]=Field(min_length=1,max_length=BATCH)
    format:str='mp4'
    quality:str='1080'
    allow_repeat:bool=False
    request_id:Annotated[str,Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')]|None=None


class AccountManager:
    def __init__(self,main):
        self.main=main;self.lock=threading.RLock();self.tasks={};self.previews={};self.until=0
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='tiktok-account')

    def stop(self):
        with self.lock:
            for task in self.tasks.values():task['cancel'].set()
        self.pool.shutdown(wait=False,cancel_futures=True)

    def clean(self):
        now=time.monotonic()
        for key,item in list(self.tasks.items()):
            if item['status'] in ('ready','failed','cancelled') and now-item['created']>1800:self.tasks.pop(key)
        for key,item in list(self.previews.items()):
            if not item['busy'] and now-item['created']>1800:self.previews.pop(key)

    def start(self,url=None,offset=0,token=None,all_entries=False,seed_url=None):
        with self.lock:
            self.clean()
            if self.until>time.monotonic():raise HTTPException(429,'TikTok account requests are paused after rate/access blocking. Wait before loading or checking again.',headers={'Retry-After':str(math.ceil(self.until-time.monotonic()))})
            if any(item['status'] in ('queued','running','cancelling') for item in self.tasks.values()):raise HTTPException(409,'A TikTok account load/check is already running. Finish or cancel it first.')
            if len(self.tasks)>=10:self.tasks.pop(next(iter(self.tasks)))
            if token:
                cache=self.preview(token)
                cache['busy']=True
            id=uuid.uuid4().hex
            self.tasks[id]={'id':id,'status':'queued','phase':'waiting','checked':0,'total':0,'entries':0,'created':time.monotonic(),'cancel':threading.Event(),'token':token}
        try:self.pool.submit(self.run,id,url,offset,token,all_entries,seed_url)
        except RuntimeError:
            with self.lock:
                self.tasks.pop(id,None)
                if token:cache['busy']=False
            raise HTTPException(503,'TikTok account worker unavailable. Restart the app.')
        return {'id':id,'status':'queued'}

    def preview(self,token):
        item=self.previews.get(token)
        if not item or (not item['busy'] and time.monotonic()-item['created']>1800):raise HTTPException(410,'TikTok account preview expired. Load the account again.')
        return item

    def fail_message(self,error):
        text=str(error).lower()
        if source_issue(error)=='invalid_response':return 'TikTok returned an empty or invalid JSON response from its account-list API. The video may be accessible while account enumeration is unavailable; this does not establish whether the account is private.'
        if 'ip address is blocked' in text:return 'TikTok is blocking access from this network/IP. Account/video availability could not be verified. Try again later.'
        if '403' in text and 'proxy' not in text:return 'TikTok rejected account access (HTTP 403). Its public account listing may be blocked even when individual video links work.'
        if any(x in text for x in ('account is private',"account is either private",'login required','log in','sign in')):return 'TikTok requires authorized access to this account/video. Try a public account; private videos cannot be listed here.'
        if any(x in text for x in ('captcha','challenge','verify','secondary user id','secuid','impersonat','not a bot')):return 'TikTok could not provide a public account listing or requires browser verification. Paste a public video link in the same input to identify its account automatically. TikTok may still block account listing; individual video downloads can be tried separately.'
        if 'does not have any videos' in text:return 'TikTok reports no posted videos for this account.'
        message=self.main.download_error(error)
        return 'TikTok did not provide usable account/video metadata. Privacy and availability could not be verified. Open the source link and check access; this is not proof that the account is private.' if message.startswith('Download failed.') else message

    def run(self,id,url,offset,token,all_entries,seed_url=None):
        task=self.tasks[id];event=task['cancel'];cache=None;terminal='ready';detail=None
        manager=self
        issue_code=None;failure=None
        username=url.rsplit('@',1)[-1] if url else None
        def receive_profile(data):
            profile=profile_details(data,username)
            if profile:
                with self.lock:task['profile']=profile
        with self.lock:
            task['profile']={'username':username,'profile_url':url,'identity_verified':False} if url else None
        def checkpoint():
            if event.is_set():raise source_access.SourceCancelled()
            if time.monotonic()-task['created']>1800:raise HTTPException(408,'Account checks reached the 30-minute time limit. Checked entries remain reviewable; recheck unverified entries later.')
        class Logger(source_access.SourceLogger):
            def debug(self,message):
                checkpoint()
                super().debug(message)
            def warning(self,message):
                checkpoint()
                super().warning(message)
        try:
            with self.lock:task.update(status='running',phase='listing' if not token else 'checking')
            if event.is_set():raise source_access.SourceCancelled()
            if not token:
                logger=Logger()
                settings=self.main.options()|{'logger':logger,'noplaylist':False,'extract_flat':'in_playlist','lazy_playlist':True,
                    '_aaasoft_profile':receive_profile,'check_formats':False,'playliststart':offset+1,'playlistend':offset+BATCH+1,'ignoreerrors':False,'socket_timeout':20,'retries':1,'extractor_retries':1,'sleep_interval_requests':1}
                entries=[]
                extract_url=url
                with self.main.video_downloader(settings) as downloader:
                    with self.lock:task['stage']='video_lookup' if seed_url else 'profile_lookup'
                    if seed_url:
                        seed=downloader.extract_info(seed_url,download=False) or {}
                        with self.lock:task['video_visibility']=video_visibility(seed)
                        actual=video_username(seed)
                        if not actual or (username and actual.casefold()!=username.casefold()):raise HTTPException(422,'The public video belongs to @'+actual+' rather than @'+username+'. Use a video from the same account.' if actual else 'TikTok did not expose a verifiable username for this video. The numeric user ID alone cannot confirm the account.')
                        if not username:
                            username=actual;url=profile_url(actual)
                        with self.lock:task['account_url']=url
                        receive_profile({'user':{'uniqueId':actual,'nickname':seed.get('uploader'),'id':seed.get('uploader_id'),'secUid':seed.get('channel_id')}})
                        secondary=seed.get('channel_id')
                        if not isinstance(secondary,str) or not re.fullmatch(r'MS4wLjABAAAA[A-Za-z0-9_-]{64}',secondary):raise HTTPException(422,'The public video did not provide a supported account ID. Account listing is unavailable from this source response.')
                        extract_url='tiktokuser:'+secondary
                    # Keep the challenge/session cookie jar from the video request.
                    # Successful seed metadata must not poison the subsequent listing.
                    logger.saw_error=False
                    with self.lock:task['stage']='account_listing' if seed_url else 'profile_lookup'
                    info=downloader.extract_info(extract_url,download=False)
                    if not info or info.get('_type')!='playlist':raise HTTPException(422,'TikTok did not return an account video list. Use a public account username.')
                    with self.lock:task['stage']='account_listing'
                    for entry in islice(info.get('entries') or [],BATCH+1):
                        if event.is_set():raise source_access.SourceCancelled()
                        entries.append(entry)
                        with self.lock:task['entries']=len(entries)
                    if logger.saw_error:raise HTTPException(422,'TikTok did not finish listing this account. No incomplete list was queued. Try again later.')
                username=url.rsplit('@',1)[-1]
                rows=[];seen=set()
                for position,entry in enumerate(entries[:BATCH],offset+1):
                    row=account_row(entry,username,position)
                    if row['id'] in seen:continue
                    seen.add(row['id']);rows.append(row)
                token=uuid.uuid4().hex
                with self.lock:
                    self.clean()
                    if len(self.previews)>=10:self.previews.pop(next(iter(self.previews)))
                    cache={'created':time.monotonic(),'busy':True,'submissions':{},'url':url,'username':username,'title':str(info.get('title') or '@'+username)[:200],
                           'profile':task.get('profile'),'rows':rows,'offset':offset,'has_more':len(entries)>BATCH}
                    self.previews[token]=cache;task.update(token=token,total=len(rows),phase='checking',stage='video_checks')
            else:
                with self.lock:cache=self.preview(token);task['total']=len(cache['rows'])
            for row in cache['rows']:
                checkpoint()
                if row['url'] and (all_entries or row['status']=='unknown'):
                    try:
                        info=self.main.extract_video(row['url'],False,self.main.options()|{'logger':Logger(),'socket_timeout':20,'retries':1,'extractor_retries':1,'sleep_interval_requests':1})
                        state=video_state(info)
                        state['selectable']=state['status']=='available'
                        with self.lock:
                            row.update(state,checked_at=time.time())
                            if info:
                                row['thumbnail']=cover(info.get('thumbnail')) or row['thumbnail']
                    except source_access.SourceCancelled:raise
                    except Exception as exc:
                        issue=self.main.recovery.classify(exc)
                        if issue['code'] in ('rate_limit','network','proxy','storage') or any(x in str(exc).lower() for x in ('captcha','challenge','secondary user id','not a bot','ip address is blocked','http error 403')):raise
                        state=video_state(error=exc);state['selectable']=state['status']=='available'
                        with self.lock:row.update(state,checked_at=time.time())
                with self.lock:task['checked']+=1
            if not cache['rows']:detail='No public video entries were returned. The source may hide posts; this is not proof that the account has no videos.'
        except source_access.SourceCancelled:terminal='cancelled';detail='Stopped. Checked entries remain reviewable; unverified entries cannot be selected.'
        except Exception as exc:
            issue_code=source_issue(exc);failure=exc
            with self.lock:task['failed_stage']=task.get('stage');task['error_type']=type(exc).__name__
            terminal='cancelled' if event.is_set() else 'failed';detail='Stopped after the current source request.' if event.is_set() else exc.detail if isinstance(exc,HTTPException) else self.fail_message(exc)
            if source_access.is_rate_limit(str(exc)) or 'ip address is blocked' in str(exc).lower():
                with self.lock:self.until=time.monotonic()+600
        finally:
            with self.lock:
                if cache:cache['busy']=False;cache['created']=time.monotonic()
                task.update(status=terminal,detail=detail,issue_code=issue_code,account_visibility=account_visibility(task.get('profile'),failure))

    def annotated(self,cache):
        counts=storage.video_counts(self.main.DATA)
        with self.main.lock:
            active={storage.video_key(j.get('url','')) for j in self.main.jobs.values() if j['status'] in ('queued','downloading','processing')}
        rows=[]
        for original in cache['rows']:
            row=original.copy();key=storage.video_key(row['url']) if row['url'] else ''
            row.update(source_key=key,previous_downloads=storage.history_count(counts,row['url']) if row['url'] else 0,already_queued=key in active)
            rows.append(row)
        return rows

    def filtered(self,cache,q='',status='all'):
        rows=self.annotated(cache);counts={'all':len(rows),'available':0,'downloaded':0,'unavailable':0,'unknown':0,'public':0,'private':0,'restricted':0,'accessible':0,'privacy_unknown':0}
        for row in rows:
            counts['privacy_unknown']+=row.get('visibility','unknown')=='unknown'
            if row.get('visibility') in ('public','private','restricted','accessible'):counts[row['visibility']]+=1
            counts['available']+=row['status']=='available'
            counts['downloaded']+=row['previous_downloads']>0
            counts['unknown']+=row['status']=='unknown'
            counts['unavailable']+=row['status'] not in ('available','unknown')
        def matches(row):
            return (not q or q.casefold() in (row['title']+' '+row['id']).casefold()) and (status=='all' or status=='downloaded' and row['previous_downloads']>0 or status=='available' and row['status']=='available' or status in ('public','private','restricted') and row.get('visibility')==status or status=='unknown' and row['status']=='unknown' or status=='unavailable' and row['status'] not in ('unknown','available'))
        return [row for row in rows if matches(row)],counts


def register(main):
    manager=AccountManager(main);app=main.app
    @app.post('/api/tiktok/loads',status_code=202)
    def load(req:AccountRequest):
        value=req.account.strip();seed=None
        try:url=profile_url(value)
        except HTTPException:
            seed=public_video_url(value);url=None
        if req.seed_url:
            if not url:raise HTTPException(400,'Paste just one public video link, or a username with an optional matching video.')
            seed=public_video_url(req.seed_url,url.rsplit('@',1)[-1])
        return manager.start(url,req.offset,seed_url=seed)

    @app.get('/api/tiktok/tasks/{id}')
    def task(id:str):
        with manager.lock:
            item=manager.tasks.get(id)
            if not item:raise HTTPException(404,'TikTok account task expired.')
            item['account_visibility']=account_visibility(item.get('profile')) if item['status'] in ('running','queued') else item.get('account_visibility',account_visibility(item.get('profile')))
            return {key:item[key] for key in ('id','status','phase','checked','total','entries','token','detail','profile','account_url','issue_code','stage','failed_stage','error_type','account_visibility','video_visibility') if key in item}

    @app.delete('/api/tiktok/tasks/{id}',status_code=204)
    def cancel(id:str):
        with manager.lock:
            item=manager.tasks.get(id)
            if item and item['status'] in ('queued','running','cancelling'):item['cancel'].set();item['status']='cancelling'

    @app.get('/api/tiktok/previews/{token}')
    def preview(token:str,page:int=1,size:int=30,q:str='',status:str='all'):
        if page<1 or page>100000 or size not in (30,60,100) or len(q)>200 or status not in ('all','available','downloaded','unavailable','unknown','public','private','restricted'):raise HTTPException(400,'Invalid TikTok list filter or page.')
        with manager.lock:
            cache=manager.preview(token);rows,counts=manager.filtered(cache,q,status)
            return {'account_visibility':account_visibility(cache.get('profile')),'profile':cache.get('profile'),'token':token,'title':cache['title'],'account':cache['username'],'offset':cache['offset'],'has_more':cache['has_more'],'busy':cache['busy'],'submitted':False,'submitted_batches':len(cache['submissions']),
                    'rows':rows[(page-1)*size:page*size],'total':len(rows),'counts':counts,'page':page,'size':size}

    @app.get('/api/tiktok/previews/{token}/selection')
    def selection(token:str,q:str='',status:str='all',allow_repeat:bool=False):
        if len(q)>200 or status not in ('all','available','downloaded','unavailable','unknown','public','private','restricted'):raise HTTPException(400,'Invalid TikTok selection filter.')
        with manager.lock:
            rows,_=manager.filtered(manager.preview(token),q,status)
            return {'ids':[r['id'] for r in rows if r['selectable'] and not r['already_queued'] and (allow_repeat or not r['previous_downloads'])]}

    @app.post('/api/tiktok/previews/{token}/checks',status_code=202)
    def checks(token:str,req:AccountCheckRequest):return manager.start(token=token,all_entries=req.all_entries)

    @app.post('/api/tiktok/previews/{token}/download',status_code=202)
    def queue(token:str,req:AccountQueueRequest):
        with manager.lock:
            cache=manager.preview(token)
            if cache['busy']:raise HTTPException(409,'TikTok availability checks are running. Finish or stop them before queueing.')
            signature=(tuple(sorted(set(req.video_ids))),req.format,req.quality,req.allow_repeat)
            key=req.request_id or signature
            previous=cache['submissions'].get(key)
            if previous:
                if previous[0]!=signature:raise HTTPException(409,'This request ID was used for a different selection. Submit a new request ID.')
                return previous[1]
            if len(cache['submissions'])>=100:raise HTTPException(429,'This preview has reached 100 selection submissions. Reload the account for a new preview.')
            ids=set(req.video_ids);rows=[row for row in cache['rows'] if row['id'] in ids]
            if len(rows)!=len(ids) or any(not row['selectable'] or row['status']!='available' for row in rows):raise HTTPException(400,'Choose checked, available videos from this account preview. Unverified/private/removed entries cannot be queued.')
            options=main.BulkDownloadRequest(urls=[row['url'] for row in rows[:50]],format=req.format,quality=req.quality,allow_repeat=req.allow_repeat)
            result=main.queue_batch([row['url'] for row in rows],options,main.MAX_PLAYLIST,{row['url']:{k:row[k] for k in ('title','thumbnail','duration','platform')} for row in rows})
            cache['submissions'][key]=(signature,result)
            return result

    @app.post('/api/platform/{provider}/analyze')
    def analyze_source(provider:str,req:main.VideoRequest):
        main.validate_url(req.url)
        if provider not in PROVIDERS or storage.platform_name(req.url)!=PROVIDERS[provider]:raise HTTPException(400,'Paste a video link from the selected platform.')
        return main.analyze(req)

    @app.post('/api/platform/{provider}/download',status_code=202)
    def download_source(provider:str,req:main.DownloadRequest):
        main.validate_url(req.url)
        if provider not in PROVIDERS or storage.platform_name(req.url)!=PROVIDERS[provider]:raise HTTPException(400,'Paste a video link from the selected platform.')
        return main.download(req)
    return manager
