"""Offline launcher check; install only when required modules/extractor pins need updating."""
import importlib.util
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path


def ready(lock_file=None):
    try:
        from packaging.version import Version
        if not all(importlib.util.find_spec(name) for name in ('fastapi','uvicorn','yt_dlp','yt_dlp_ejs','curl_cffi')):
            return False
        pins = dict(line.strip().split('==',1) for line in (lock_file or Path(__file__).parent.parent/'requirements.lock').read_text().splitlines() if '==' in line)
        return all(Version(version(name)) >= Version(pins[name]) for name in ('yt-dlp','yt-dlp-ejs','certifi'))
    except (ImportError, OSError, KeyError, ValueError, PackageNotFoundError):
        return False


if __name__ == '__main__':
    raise SystemExit(0 if ready() else 1)
