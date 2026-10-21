"""Local-only launcher that opens the browser after the API is ready."""
import argparse
import json
import shutil
import socket
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from app import __version__


def server_info(port):
    # Loopback readiness should not pass through a user's system HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f'http://127.0.0.1:{port}/api/health', timeout=1) as response:
            info = json.load(response)
            return info if isinstance(info, dict) and info.get('application') == 'StreamVault' else None
    except urllib.error.HTTPError as exc:
        if exc.code == 401 and 'StreamVault' in exc.headers.get('WWW-Authenticate', ''):
            return {'application': 'StreamVault', 'authentication_required': True}
    except (OSError, ValueError):
        return None


def probe(port):
    return bool(server_info(port))


def main():
    parser = argparse.ArgumentParser(description='Launch StreamVault on this computer')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Port must be between 1 and 65535.')
    url = f'http://127.0.0.1:{args.port}'
    existing = server_info(args.port)
    if existing:
        if existing.get('authentication_required'):
            print('A protected StreamVault is running. If you updated project files, stop its server with Ctrl+C and launch again to load the update.')
        elif existing.get('version') != __version__:
            parser.error(f"StreamVault {existing.get('version') or '(unknown version)'} is still running; installed files are {__version__}. Stop the previous server with Ctrl+C, then launch again from this project folder. Refresh the browser with Ctrl+F5.")
        print('StreamVault is already running. Opening your dashboard.')
        webbrowser.open(url)
        return
    if not shutil.which('ffmpeg'):
        parser.error('FFmpeg was not found. Install it and reopen this window.')
    try:
        connection = socket.create_connection(('127.0.0.1', args.port), timeout=1)
    except OSError:
        pass
    else:
        connection.close()
        parser.error('Port is in use. Stop the previous server with Ctrl+C, or use --port 8001.')
    def open_when_ready():
        for _ in range(100):
            if probe(args.port):
                webbrowser.open(url)
                return
            time.sleep(0.2)
        print('The browser could not open automatically. Check the server output.')
    print(f'StreamVault is starting at {url}')
    print('Keep this window open. Press Ctrl+C to stop.')
    threading.Thread(target=open_when_ready, daemon=True).start()
    import uvicorn
    uvicorn.run('app.main:app', host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
