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


class SourceCancelled(RuntimeError):
    pass


class TransferSlots:
    def __init__(self):
        self.condition = threading.Condition()
        self.limit = 1
        self.active = 0
        self.waiters = []

    def set_limit(self, limit):
        with self.condition:
            self.limit = limit
            self.condition.notify_all()

    def acquire(self, blocking=True, cancel=None):
        with self.condition:
            ticket = object()
            if not blocking and (self.active >= self.limit or self.waiters):
                return False
            self.waiters.append(ticket)
            try:
                while self.active >= self.limit or self.waiters[0] is not ticket:
                    if cancel is not None and cancel.is_set():
                        raise SourceCancelled('Playlist loading cancelled.')
                    self.condition.wait(.2 if cancel is not None else None)
                if cancel is not None and cancel.is_set():
                    raise SourceCancelled('Playlist loading cancelled.')
                self.active += 1
                return True
            finally:
                self.waiters.remove(ticket)
                self.condition.notify_all()

    def release(self):
        with self.condition:
            self.active -= 1
            self.condition.notify_all()


class YoutubeAccess:
    def __init__(self):
        self.slot = TransferSlots()
        self.state = threading.Lock()
        self.until = 0

    def remaining(self):
        with self.state:
            return max(0, self.until - time.monotonic())

    def limited(self):
        with self.state:
            self.until = max(self.until, time.monotonic() + 600)

    @contextmanager
    def request(self, download, wait=False, cancel=None):
        if self.remaining():
            raise SourceRateLimited(self.remaining())
        if not self.slot.acquire(blocking=download or wait, cancel=cancel):
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


def is_bot_verification(message):
    message = str(message).lower().replace('\u2019', "'")
    return "confirm you're not a bot" in message or 'confirm you are not a bot' in message


class SourceLogger:
    def __init__(self):
        self.saw_rate_limit = False
        self.terminal_rate_limit = False
        self.saw_error = False

    def debug(self, message):
        if not message.startswith('[debug] '):
            print(message, file=sys.stderr)

    def warning(self, message):
        print('WARNING: ' + message, file=sys.stderr)
        if is_rate_limit(message):
            # A webpage warning can be recovered by the extractor's native fallback.
            # Never raise from a logging callback or mark a successful request blocked.
            self.saw_rate_limit = True

    def error(self, message):
        print('ERROR: ' + message, file=sys.stderr)
        self.saw_error = True
        if is_rate_limit(message):
            self.saw_rate_limit = True
            self.terminal_rate_limit = True


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
