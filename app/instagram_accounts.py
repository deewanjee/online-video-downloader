"""Instagram-specific validation with shared bounded previews and queue protection."""
import re
import time
from typing import Annotated
from pydantic import Field
from app import accounts
from app.instagram_profile import cover, profile_url, session_settings


class InstagramQueueRequest(accounts.AccountQueueRequest):
    video_ids:list[Annotated[str,Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')]]=Field(min_length=1,max_length=accounts.BATCH)


def profile_details(data,expected):
    if not isinstance(data,dict) or str(data.get('username') or '').casefold()!=expected.casefold():return None
    def count(value):return value if isinstance(value,int) and not isinstance(value,bool) and 0<=value<10**19 else None
    def edge(key):
        value=data.get(key)
        return count(value.get('count')) if isinstance(value,dict) else None
    return {'username':data['username'],'profile_url':profile_url(data['username']),'identity_verified':True,'checked_at':time.time(),
        'display_name':str(data.get('full_name') or '')[:200],'bio':str(data.get('biography') or '')[:2000],
        'avatar':cover(data.get('profile_pic_url_hd')) or cover(data.get('profile_pic_url')),
        'user_id':str(data.get('id') or '')[:100],'account_id':None,
        'private':data.get('is_private') if isinstance(data.get('is_private'),bool) else None,
        'verified':data.get('is_verified') if isinstance(data.get('is_verified'),bool) else None,
        'followers':edge('edge_followed_by'),'following':edge('edge_follow'),'video_count':edge('edge_owner_to_timeline_media'),'likes':None}


def account_row(entry,username,position):
    entry=entry if isinstance(entry,dict) else {};code=str(entry.get('id') or '')
    valid=bool(re.fullmatch(r'[A-Za-z0-9_-]{1,64}',code))
    state=accounts.video_state(entry)
    media_type=entry.get('media_type')
    if media_type in (1,8):
        state.update(status='unsupported',status_label='Photo post' if media_type==1 else 'Mixed / carousel post',selectable=False,
            detail='This account importer supports individual video posts and reels. Photo and carousel posts are not queued as a single video.')
    if not valid:state.update(status='invalid',status_label='Unavailable entry',selectable=False)
    state['selectable']=state['status']=='available'
    duration=entry.get('duration')
    if not isinstance(duration,(int,float)) or isinstance(duration,bool) or not 0<=duration<10**8:duration=None
    return {'id':code if valid else 'unavailable-'+str(position),'url':'https://www.instagram.com/p/'+code+'/' if valid else None,
        'title':str(entry.get('title') or 'Instagram video')[:500],'thumbnail':cover(entry.get('thumbnail')),'duration':duration,
        'position':position,'platform':'Instagram','provider':'instagram','kind':'videos',
        **state,'checked_at':time.time() if state['status']!='unknown' else None}


class InstagramManager(accounts.AccountManager):
    provider='instagram'
    label='Instagram'
    details=staticmethod(profile_details)
    row=staticmethod(account_row)
    cover=staticmethod(cover)

    def settings(self):return session_settings(self.main.options())

    def username(self,url):return url.rstrip('/').rsplit('/',1)[-1]

    def fail_message(self,error):
        issue=accounts.source_issue(error)
        if 'session cookie' in str(error).lower():return self.main.download_error(error)
        if issue=='authentication':return 'Instagram requires an authorized login session for this request (HTTP 401). See the Instagram account guide for optional local session setup. This does not mean the account is private.'
        if issue=='private_account':return 'Instagram confirms this account is private. Public account import cannot list its private posts.'
        if issue in ('rate_limit','access_denied','ip_block'):return 'Instagram paused or rejected account listing. Wait before retrying; do not repeatedly submit the same account.'
        if issue in ('network','proxy','certificate','transport_unavailable'):return self.main.download_error(error)
        return 'Instagram did not provide the account list. Login, browser verification or source availability may be required. Account privacy is unknown unless explicitly confirmed above.'
