"""Public Instagram profile reader; source permissions and login requirements apply.

The deprecated yt-dlp InstagramUser extractor is marked broken. This adapter uses
Instagram's web profile and feed responses, in one verified browser session.
"""
import re
from urllib.parse import urlparse
from fastapi import HTTPException
from yt_dlp.extractor.instagram import InstagramBaseIE
from yt_dlp.utils import ExtractorError


def profile_url(value):
    value=value.strip()
    if '://' in value:
        try:
            parsed=urlparse(value)
            if parsed.scheme!='https' or parsed.hostname not in ('instagram.com','www.instagram.com') or parsed.username or parsed.password or parsed.port not in (None,443):raise ValueError()
            match=re.fullmatch(r'/([A-Za-z0-9_.]{1,30})/?',parsed.path)
            if not match:raise ValueError()
            value=match[1]
        except ValueError:raise HTTPException(400,'Enter an Instagram @username or HTTPS instagram.com/username profile URL.') from None
    value=value.removeprefix('@')
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.]{0,29}',value) or value.endswith('.') or '..' in value or value.lower() in ('p','reel','reels','tv','stories','accounts','explore','direct'):
        raise HTTPException(400,'Enter the Instagram username, not a display name, post or reels URL.')
    return 'https://www.instagram.com/'+value+'/'


def cover(value):
    if not isinstance(value,str):return None
    try:
        parsed=urlparse(value);host=(parsed.hostname or '').lower()
        if parsed.scheme=='https' and not parsed.username and not parsed.password and parsed.port in (None,443) and any(host==d or host.endswith('.'+d) for d in ('cdninstagram.com','fbcdn.net')):return value
    except ValueError:pass
    return None


class InstagramProfileIE(InstagramBaseIE):
    _VALID_URL=r'https://(?:www\.)?instagram\.com/(?P<id>[A-Za-z0-9_.]{1,30})/?(?:\?[^#]*)?$'
    IE_NAME='instagram:public-profile'

    @classmethod
    def suitable(cls,url):
        try:profile_url(url)
        except HTTPException:return False
        return super().suitable(url)

    def _json(self,path,username,query=None):
        headers=self._api_headers|{'Referer':profile_url(username)}
        csrf=self._get_cookies('https://www.instagram.com/').get('csrftoken')
        if csrf:headers['X-CSRFToken']=csrf.value
        data=self._download_json('https://www.instagram.com/api/v1/'+path,username,
            query=query,headers=headers,
            impersonate=True,require_impersonation=True)
        if not isinstance(data,dict):raise ExtractorError('Instagram returned invalid JSON metadata.',expected=True)
        if data.get('require_login') or data.get('message') in ('login_required','challenge_required','checkpoint_required'):
            raise ExtractorError('Instagram requires login or browser verification for this account listing.',expected=True)
        if data.get('status')=='fail':raise ExtractorError('Instagram rejected the account listing request. Try again later.',expected=True)
        return data

    def _entries(self,username,user):
        cursor=None;seen=set()
        # Feed cursors are never mixed with GraphQL timeline cursors.
        for _ in range(3400):
            query={'count':30}
            if cursor:query['max_id']=cursor
            data=self._json('feed/user/'+str(user['id'])+'/',username,query)
            items=data.get('items')
            if not isinstance(items,list):raise ExtractorError('Instagram did not return a usable account video list. Login may be required.',expected=True)
            for item in items:
                if not isinstance(item,dict):continue
                code=item.get('code')
                if not isinstance(code,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',code):continue
                caption=item.get('caption') or {}
                caption=caption.get('text') if isinstance(caption,dict) else caption
                candidates=(item.get('image_versions2') or {}).get('candidates') or []
                yield {'id':code,'title':str(caption or 'Instagram post')[:500],
                    'thumbnail':next((cover(x.get('url')) for x in candidates if isinstance(x,dict) and cover(x.get('url'))),None),
                    'duration':item.get('video_duration'),'media_type':item.get('media_type'),
                    'availability':'private' if item.get('is_private') is True else None}
            if not data.get('more_available'):return
            next_cursor=data.get('next_max_id')
            if not isinstance(next_cursor,str) or not next_cursor or next_cursor in seen:raise ExtractorError('Instagram repeated or omitted its pagination cursor. The account listing could not be completed.',expected=True)
            seen.add(next_cursor);cursor=next_cursor
        raise ExtractorError('Instagram account listing reached its page limit.',expected=True)

    def _real_extract(self,url):
        username=self._match_id(url)
        data=self._json('users/web_profile_info/',username,{'username':username})
        user=(data.get('data') or {}).get('user')
        if not isinstance(user,dict) or str(user.get('username') or '').casefold()!=username.casefold() or not re.fullmatch(r'[0-9]{1,30}',str(user.get('id') or '')):
            raise ExtractorError('Instagram did not confirm the requested account identity. The account may be unavailable or require login.',expected=True)
        callback=self.get_param('_aaasoft_profile')
        if callback:callback(user)
        if user.get('is_private') is True:raise ExtractorError('Instagram account is private.',expected=True)
        return self.playlist_result(self._entries(username,user),username,user.get('full_name') or username)


def session_settings(settings):
    """Optional owner-provided local session; never accepted over the HTTP API."""
    import os
    from pathlib import Path
    value=os.environ.get('AAASOFT_INSTAGRAM_COOKIE_FILE')
    if not value:return settings
    if os.environ.get('STREAMVAULT_PUBLIC','').lower() in ('1','true','yes'):
        raise RuntimeError('Instagram session cookies are available only in local desktop mode.')
    path=Path(value).expanduser()
    if not path.is_file():raise RuntimeError('The local Instagram session cookie file is missing. Check AAASOFT_INSTAGRAM_COOKIE_FILE locally.')
    return settings|{'cookiefile':str(path)}
