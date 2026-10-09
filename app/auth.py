"""Optional single-owner HTTP Basic authentication and same-origin mutation checks."""
import base64
import binascii
import hmac
import os
import time
from starlette.responses import JSONResponse


class AccessControl:
    def __init__(self, app):
        self.app = app
        self.failures = {}

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = dict(scope['headers'])
        password = os.environ.get('STREAMVAULT_PASSWORD', '')
        username = os.environ.get('STREAMVAULT_USERNAME', 'admin')
        public = os.environ.get('STREAMVAULT_PUBLIC', '').lower() in ('1', 'true', 'yes')
        if public and not password:
            return await JSONResponse({'detail': 'Public mode requires STREAMVAULT_PASSWORD.'}, status_code=503)(scope, receive, send)
        if password:
            client = (scope.get('client') or ('unknown',))[0]
            now = time.monotonic()
            self.failures = {key: value for key, value in self.failures.items() if now - value[0] < 60}
            attempts = self.failures.get(client, (now, 0))
            if attempts[1] >= 10:
                return await JSONResponse({'detail': 'Too many login attempts. Wait one minute.'}, status_code=429, headers={'Retry-After': '60'})(scope, receive, send)
            credentials = ('', '')
            try:
                scheme, encoded = headers.get(b'authorization', b'').decode('ascii').split(' ', 1)
                if scheme.lower() == 'basic':
                    decoded = base64.b64decode(encoded, validate=True).decode('utf-8')
                    credentials = tuple(decoded.split(':', 1))
            except (ValueError, UnicodeError, binascii.Error):
                pass
            valid = len(credentials) == 2 and hmac.compare_digest(credentials[0].encode(), username.encode()) and hmac.compare_digest(credentials[1].encode(), password.encode())
            if not valid:
                # An initial browser challenge is not a failed password attempt.
                if b'authorization' in headers:
                    if len(self.failures) < 1000 or client in self.failures:
                        self.failures[client] = (attempts[0], attempts[1] + 1)
                return await JSONResponse({'detail': 'Sign in to StreamVault.'}, status_code=401, headers={'WWW-Authenticate': 'Basic realm="StreamVault", charset="UTF-8"'})(scope, receive, send)
            self.failures.pop(client, None)
        if scope['method'] in ('POST', 'PUT', 'PATCH', 'DELETE') and b'origin' in headers:
            origin = headers[b'origin'].decode('latin-1')
            expected = scope['scheme'] + '://' + headers.get(b'host', b'').decode('latin-1')
            if origin != expected:
                return await JSONResponse({'detail': 'Cross-site requests are not allowed.'}, status_code=403)(scope, receive, send)
        return await self.app(scope, receive, send)
