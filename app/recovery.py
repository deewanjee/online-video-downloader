"""Classify recoverable failures without exposing extractor diagnostics or credentials."""
import re

MAX_RETRIES = 3
DELAYS = (30, 120, 300)


def classify(error):
    text = str(error).lower().replace('unavailable or has been removed', 'unavailable')
    rules = [
        ('rate_limit', True, ('http 429','http error 429','too many requests','youtube is limiting'), 'Wait for the source cooldown. Recovery will retry after the current queue.'),
        ('access', False, ('private video','requires sign-in','sign in','login required','age verification','bot verification','confirm your age','confirm you'), 'Open the source link and check your authorized access. See Troubleshooting.'),
        ('restricted', False, ('drm','region','country','geo-restricted'), 'Check source availability and permissions. Automatic recovery cannot remove source restrictions.'),
        ('removed', False, ('has been removed','has been deleted','http error 404'), 'Check the source link. Removed videos need an available replacement link.'),
        ('storage', False, ('disk space','not writable','folder is unavailable','permissions','cookie file','certificate'), 'Check the selected folder, free disk space or local setup, then retry manually.'),
        ('proxy', False, ('unable to connect to proxy','tunnel connection failed'), 'Check the proxy or firewall on the computer running the app. This connection failure does not establish whether the source video is available.'),
        ('interrupted', True, ('interrupted by a server restart',), 'Recovery can resume a verified merged file or retry after other queued videos.'),
        ('network', True, ('timed out','timeout','connection reset','connection aborted','temporarily unavailable','temporary failure','unable to download webpage'), 'Check the connection. Recovery retries temporary failures after the current queue.'),
        ('unavailable', True, ('video unavailable','video is unavailable','unavailable or has been removed'), 'The source reports this video unavailable. One recovery attempt will recheck it after the queue; check the source link if it persists.'),
    ]
    for code, retryable, phrases, action in rules:
        if any(phrase in text for phrase in phrases):
            return {'code':code,'retryable':retryable,'limit':1 if code=='unavailable' else MAX_RETRIES,'action':action}
    if re.search(r'(?:http(?: error)?|status)[\s:=]+50[0234]\b',text):
        return {'code':'network','retryable':True,'limit':MAX_RETRIES,'action':'The source server has a temporary error. Recovery will retry after the current queue.'}
    return {'code':'other','retryable':False,'limit':MAX_RETRIES,'action':'Check the video link, format and local setup in Troubleshooting, then retry manually.'}


def youtube_fallback(error):
    text = str(error).lower()
    return 'video unavailable' in text and not any(token in text for token in (
        '429','too many requests','private','removed','deleted','copyright','country','region','age','sign in','drm','bot'))
