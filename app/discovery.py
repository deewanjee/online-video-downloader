"""Bounded public source search. Returned download links are rebuilt from validated IDs."""
import math
import re
from itertools import islice
from urllib.parse import urlencode, urlparse
import requests

PAGE_SIZE = 12
MAX_PAGE = 5


class SearchFailure(RuntimeError):
    pass


def thumbnail(value, provider):
    if not isinstance(value,str):
        return None
    domains=('ytimg.com','googleusercontent.com') if provider=='youtube' else ('dailymotion.com','dmcdn.net')
    try:
        parsed=urlparse(value)
        host=(parsed.hostname or '').lower()
        valid=parsed.scheme=='https' and not parsed.username and not parsed.password and parsed.port in (None,443)
    except ValueError:
        return None
    return value if valid and any(host==domain or host.endswith('.'+domain) for domain in domains) else None


def number(value):
    return value if isinstance(value,(float,int)) and not isinstance(value,bool) and math.isfinite(value) and value>=0 else None


def youtube(query,kind,page,extract):
    # YoutubeSearchURLIE reads the supported search filter and extracts flat entries.
    url='https://www.youtube.com/results?'+urlencode({'search_query':query,'sp':'EgIQAw==' if kind=='playlists' else 'EgIQAQ=='})
    start=(page-1)*PAGE_SIZE+1
    info=extract(url,False,{'noplaylist':False,'extract_flat':'in_playlist','lazy_playlist':True,
                           'playliststart':start,'playlistend':start+PAGE_SIZE,'ignoreerrors':False})
    entries=list(islice((info or {}).get('entries') or [],PAGE_SIZE+1))
    results=[];seen=set()
    for entry in entries[:PAGE_SIZE]:
        if not isinstance(entry,dict) or entry.get('availability') in ('private','needs_auth','premium_only','subscriber_only') or entry.get('is_live') or entry.get('live_status') in ('is_live','is_upcoming','post_live'):
            continue
        id=entry.get('id')
        if not isinstance(id,str):continue
        if kind=='playlists':
            if not re.fullmatch(r'[A-Za-z0-9_-]{10,100}',id) or not id.startswith(('PL','UU','OLAK5uy_')):continue
            link='https://www.youtube.com/playlist?list='+id
        else:
            if not re.fullmatch(r'[A-Za-z0-9_-]{11}',id):continue
            link='https://www.youtube.com/watch?v='+id
        if id in seen:continue
        seen.add(id)
        cover=thumbnail(entry.get('thumbnail'),'youtube') or next((valid for item in reversed(entry.get('thumbnails') or []) if isinstance(item,dict) and (valid:=thumbnail(item.get('url'),'youtube'))),None)
        if kind=='videos' and not cover:cover='https://i.ytimg.com/vi/'+id+'/hqdefault.jpg'
        results.append({'id':id,'url':link,'kind':kind,'provider':'youtube','platform':'YouTube',
                        'title':str(entry.get('title') or 'Untitled result')[:500],
                        'author':str(entry.get('uploader') or entry.get('channel') or 'YouTube creator')[:200],
                        'thumbnail':cover,'duration':number(entry.get('duration')),
                        'video_count':number(entry.get('playlist_count') or entry.get('video_count'))})
    return {'results':results,'has_more':len(entries)>PAGE_SIZE and page<MAX_PAGE}


def dailymotion(query,page):
    params={'search':query,'fields':'id,title,thumbnail_360_url,duration,owner.screenname','limit':PAGE_SIZE,'page':page}
    try:
        with requests.get('https://api.dailymotion.com/videos',params=params,timeout=(5,20),allow_redirects=False,stream=True,
                          headers={'User-Agent':'AAASoft Video Studio'}) as response:
            if response.status_code!=200:
                raise SearchFailure('Dailymotion search is unavailable (HTTP '+str(response.status_code)+'). Check your connection and try again later.')
            chunks=[];size=0
            for chunk in response.iter_content(65536):
                size+=len(chunk)
                if size>2*1024*1024:raise SearchFailure('Dailymotion returned too much search data. Try a more specific search.')
                chunks.append(chunk)
            import json
            data=json.loads(b''.join(chunks))
    except SearchFailure:raise
    except (requests.RequestException,ValueError):
        raise SearchFailure('Dailymotion search could not be reached. Check the server connection and try again.') from None
    if not isinstance(data,dict) or not isinstance(data.get('list'),list):
        raise SearchFailure('Dailymotion returned an unreadable search response. Try again later.')
    results=[];seen=set()
    for entry in data['list'][:PAGE_SIZE]:
        id=entry.get('id') if isinstance(entry,dict) else None
        if not isinstance(id,str) or not re.fullmatch(r'[A-Za-z0-9]{3,30}',id) or id in seen:continue
        seen.add(id)
        results.append({'id':id,'url':'https://www.dailymotion.com/video/'+id,'kind':'videos','provider':'dailymotion','platform':'Dailymotion',
                        'title':str(entry.get('title') or 'Dailymotion video')[:500],
                        'author':str(entry.get('owner.screenname') or 'Dailymotion creator')[:200],
                        'thumbnail':thumbnail(entry.get('thumbnail_360_url'),'dailymotion') or 'https://www.dailymotion.com/thumbnail/video/'+id,
                        'duration':number(entry.get('duration')),'video_count':None})
    return {'results':results,'has_more':data.get('has_more') is True and page<MAX_PAGE}
