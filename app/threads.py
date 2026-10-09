"""Extract videos exposed in public Threads post pages, without account cookies."""
import json
from html.parser import HTMLParser
from urllib.parse import urlsplit

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import ExtractorError, int_or_none


class PostData(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.active = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.active = dict(attrs).get('type') == 'application/json'
            self.parts = []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.active:
            self.scripts.append(''.join(self.parts))
            self.active = False


def dictionaries(value):
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            yield item
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def public_media_url(url):
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        return (parsed.scheme == 'https' and not parsed.username and not parsed.password
                and parsed.port in (None, 443)
                and any(host == domain or host.endswith('.' + domain)
                        for domain in ('cdninstagram.com', 'fbcdn.net')))
    except ValueError:
        return False


class ThreadsIE(InfoExtractor):
    IE_NAME = 'threads'
    _VALID_URL = r'https://(?:www\.)?threads\.(?:com|net)/(?:@[^/?#]+/post/(?P<id>[\w-]+)|share/(?P<share>[\w-]+))(?:[/?#]|$)'

    def _real_extract(self, url):
        match = self._match_valid_url(url)
        video_id = match.group('id') or match.group('share')
        # Threads exposes public server-rendered post data to crawler requests.
        # No login, private endpoint, account cookie or token is used.
        webpage, response = self._download_webpage_handle(url, video_id, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
        })
        final = self._match_valid_url(response.url)
        if not final or not final.group('id'):
            raise ExtractorError('Threads share link did not resolve to a public post.', expected=True)
        video_id = final.group('id')
        parser = PostData()
        parser.feed(webpage)
        post = None
        for script in parser.scripts:
            try:
                data = json.loads(script)
            except ValueError:
                continue
            post = next((item for item in dictionaries(data)
                         if item.get('code') == video_id and 'video_versions' in item), None)
            if post:
                break
        if not post:
            raise ExtractorError('Threads public video data is unavailable. The post may require login, be removed, or its page format may have changed.', expected=True)
        # Stay inside this exact post: never download videos from recommendations.
        candidates = [post]
        candidates.extend(post.get('carousel_media') or [])
        quoted = ((post.get('text_post_app_info') or {}).get('share_info') or {}).get('quoted_attachment_post')
        if isinstance(quoted, dict):
            candidates.append(quoted)
            candidates.extend(quoted.get('carousel_media') or [])
        videos = [item for item in candidates if isinstance(item, dict) and item.get('video_versions')]
        if not videos:
            raise ExtractorError('This Threads post has no publicly available video.', expected=True)
        if len(videos) > 1:
            raise ExtractorError('This Threads post contains multiple videos. Choose a post containing a single video.', expected=True)
        media = videos[0]
        formats = []
        for index, version in enumerate(media['video_versions']):
            if not isinstance(version, dict) or not public_media_url(version.get('url')):
                continue
            formats.append({
                'format_id': f'threads-{index}', 'url': version['url'], 'ext': 'mp4',
                'vcodec': 'unknown', 'acodec': 'none' if media.get('has_audio') is False else 'unknown',
            })
        if not formats:
            raise ExtractorError('Threads public video files are unavailable.', expected=True)
        caption = (post.get('caption') or {}).get('text') or (media.get('caption') or {}).get('text')
        user = post.get('user') or {}
        images = (media.get('image_versions2') or {}).get('candidates') or []
        thumbnails = [item for item in images if isinstance(item, dict) and public_media_url(item.get('url'))]
        thumbnail = max(thumbnails, key=lambda item: int_or_none(item.get('width')) or 0)['url'] if thumbnails else None
        return {
            'id': video_id, 'title': (caption or f'Threads video {video_id}')[:240],
            'description': caption, 'uploader': user.get('username'),
            'timestamp': int_or_none(post.get('taken_at')),
            'thumbnail': thumbnail,
            'webpage_url': response.url.split('?')[0], 'formats': formats,
        }
