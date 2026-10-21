"""Owner-selected local download destinations; never a remote browser filesystem API."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


class DestinationError(RuntimeError):
    pass


def enabled():
    return os.environ.get('STREAMVAULT_DESKTOP') == '1' and os.environ.get('STREAMVAULT_PUBLIC', '').lower() not in ('1', 'true', 'yes')


def selected(data):
    try:
        value = json.loads((data / 'download-location.json').read_text(encoding='utf-8')).get('path')
        return value if isinstance(value, str) and value else None
    except (OSError, ValueError, AttributeError):
        return None


def configure(data, value):
    try:
        path = Path(value).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise DestinationError('Choose an existing absolute folder on this computer.')
        path = path.resolve()
        if path.is_relative_to(data.resolve()):
            raise DestinationError('Choose a folder outside the app data directory so library cleanup cannot remove your saved videos.')
        with tempfile.TemporaryFile(dir=path):
            pass
        data.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=data, delete=False) as handle:
                temporary = Path(handle.name)
                json.dump({'path': str(path)}, handle)
            temporary.replace(data / 'download-location.json')
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        return str(path)
    except (OSError, ValueError):
        raise DestinationError('This folder is unavailable or not writable. Choose another folder.') from None


def snapshot(data):
    if not enabled():
        return {}
    path = selected(data)
    if not path:
        raise DestinationError('Choose your download folder before adding videos.')
    if not Path(path).is_dir():
        raise DestinationError('Your download folder is unavailable. Reconnect the drive or choose another folder.')
    return {'output_dir': path}


def work_folder(data, job):
    if not job.get('output_dir'):
        return data / job['id']
    root = Path(job['output_dir']).resolve()
    if not root.is_dir():
        raise DestinationError('Your download folder is unavailable. Reconnect the drive and retry.')
    folder = root / '.aaasoft-work' / job['id']
    if not folder.resolve().is_relative_to(root):
        raise DestinationError('The temporary download folder points outside the selected folder. Choose another folder.')
    return folder


def picker(initial=None):
    result = subprocess.run([sys.executable, '-m', 'app.folder_picker', initial or ''], capture_output=True, text=True,
                            encoding='utf-8', timeout=120, cwd=Path(__file__).parent.parent)
    try:
        value = json.loads(result.stdout)
        if result.returncode or value.get('error'):
            raise ValueError()
        return value.get('path')
    except (ValueError, AttributeError):
        raise DestinationError('The folder dialog is unavailable. Enter a folder path below instead.') from None


def open_folder(path):
    if sys.platform == 'win32':
        os.startfile(str(path))
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'):
        subprocess.Popen(['xdg-open', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        raise DestinationError('A desktop file manager is unavailable. Open the displayed folder path manually.')
