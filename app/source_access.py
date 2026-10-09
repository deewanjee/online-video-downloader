"""Respect YouTube rate limits and keep optional desktop session cookies local."""
import math
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse


class SourceRateLimited(RuntimeError):
    def __init__(self, seconds):
        self.retry_after = max(1, math.ceil(seconds))
        super().__init__(f'YouTube is limiting requests (HTTP 429). Wait {self.retry_after} seconds before retrying. Authentication may still be required afterwards.')


class SourceBusy(RuntimeError):
    retry_after = 5


class YoutubeAccess:
    def __init__(self):
        self.slot = threading.Lock()
        self.state = threading.Lock()
        self.until = 0

    def remaining(self):
        with self.state:
            return max(0, self.until - time.monotonic())

    def limited(self):
        with self.state:
            self.until = max(self.until, time.monotonic() + 600)

    @contextmanager
    def request(self, download):
        if self.remaining():
            raise SourceRateLimited(self.remaining())
        if not self.slot.acquire(blocking=download):
            raise SourceBusy('A YouTube request is already running. Wait for it to finish before analyzing another link.')
        try:
            if self.remaining():
                raise SourceRateLimited(self.remaining())
            yield
        finally:
            self.slot.release()


youtube = YoutubeAccess()


def is_rate_limit(message):
    return bool(re.search(r'(?:http(?:\s+error)?|status(?:\s+code)?)[\s:=]+429\b|too many requests', str(message), re.I))


class SourceLogger:
    def debug(self, message):
        if not message.startswith('[debug] '):
            print(message, file=sys.stderr)

    def warning(self, message):
        print('WARNING: ' + message, file=sys.stderr)
        if is_rate_limit(message):
            youtube.limited()
            raise SourceRateLimited(youtube.remaining())

    def error(self, message):
        print('ERROR: ' + message, file=sys.stderr)
        if is_rate_limit(message):
            youtube.limited()
            raise SourceRateLimited(youtube.remaining())


def youtube_url(url):
    host = urlparse(url).hostname or ''
    return any(host == domain or host.endswith('.' + domain) for domain in ('youtube.com', 'youtu.be'))


def authenticated_settings(settings):
    cookie_file = os.environ.get('STREAMVAULT_COOKIE_FILE')
    if not cookie_file:
        return settings
    if os.environ.get('STREAMVAULT_PUBLIC', '').lower() in ('1', 'true', 'yes'):
        raise RuntimeError('Session cookies are available only in local desktop mode.')
    if not Path(cookie_file).expanduser().is_file():
        raise RuntimeError('The configured session cookie file is missing. Check STREAMVAULT_COOKIE_FILE locally.')
    return settings | {'cookiefile': str(Path(cookie_file).expanduser())}
