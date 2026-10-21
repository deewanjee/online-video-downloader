"""Persistent download metadata. Media remains in per-job directories."""
import json
import sqlite3
import hashlib
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode


PLATFORMS = ('YouTube', 'Instagram', 'TikTok', 'Facebook', 'X / Twitter', 'Dailymotion', 'Threads', 'Other')


def platform_name(url):
    host = (urlparse(url).hostname or '').lower()
    families = {'YouTube': ('youtube.com', 'youtu.be'), 'Instagram': ('instagram.com',), 'TikTok': ('tiktok.com',),
                'Facebook': ('facebook.com', 'fb.watch'), 'X / Twitter': ('x.com', 'twitter.com'),
                'Dailymotion': ('dailymotion.com', 'dai.ly'), 'Threads': ('threads.com', 'threads.net')}
    return next((name for name, domains in families.items() if any(host == d or host.endswith('.' + d) for d in domains)), 'Other')


def video_key(url):
    parsed = urlparse(url)
    platform = platform_name(url)
    path = parsed.path.strip('/')
    query = parse_qs(parsed.query)
    identity = None
    if platform == 'YouTube':
        identity = (query.get('v') or [None])[0]
        if not identity and ((parsed.hostname or '').endswith('youtu.be') or path.startswith(('shorts/', 'embed/', 'live/'))):
            identity = path.split('/')[-1]
    elif platform == 'X / Twitter' and '/status/' in '/' + path:
        identity = path.split('/status/', 1)[-1].split('/')[0]
    elif platform == 'Facebook':
        identity = (query.get('v') or [None])[0]
        if '/videos/' in '/' + path:
            identity = path.split('videos/', 1)[-1].split('/')[0]
    elif platform == 'Dailymotion':
        identity = path.split('/')[-1].split('_')[0]
    # Query parameters on social post URLs are typically share/tracking data.
    meaningful = {k: v for k, v in query.items() if k in ('v', 'id', 'story_fbid')}
    canonical = identity or (path + '?' + urlencode(meaningful, doseq=True))
    return hashlib.sha256((platform + ':' + canonical).encode()).hexdigest()


def record_completion(connection, job):
    if job.get('status') == 'completed':
        url = job.get('url', '')
        event_id = job['id'] + (':' + job['completion_id'] if job.get('completion_id') else '')
        connection.execute('INSERT OR IGNORE INTO download_events VALUES (?, ?, ?, ?)',
                           (event_id, platform_name(url), video_key(url) if url else job['id'], job.get('completed_at') or job.get('created') or time.time()))


@contextmanager
def connect(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(directory / 'history.sqlite3', timeout=10)
    try:
        connection.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        connection.execute('CREATE TABLE IF NOT EXISTS download_events (event_id TEXT PRIMARY KEY, platform TEXT NOT NULL, video_key TEXT NOT NULL, completed_at REAL NOT NULL)')
        connection.execute('CREATE INDEX IF NOT EXISTS events_video_key ON download_events(video_key)')
        connection.execute('CREATE INDEX IF NOT EXISTS events_platform ON download_events(platform)')
        yield connection
        connection.commit()
    finally:
        connection.close()


def save(directory, job):
    with connect(directory) as connection:
        connection.execute('INSERT OR REPLACE INTO jobs VALUES (?, ?)', (job['id'], json.dumps(job)))
        record_completion(connection, job)


def save_many(directory, jobs):
    records = [(job['id'], json.dumps(job)) for job in jobs]
    with connect(directory) as connection:
        connection.executemany('INSERT OR REPLACE INTO jobs VALUES (?, ?)', records)
        for job in jobs:
            record_completion(connection, job)


def delete(directory, job_id):
    with connect(directory) as connection:
        connection.execute('DELETE FROM jobs WHERE id = ?', (job_id,))


def restore(directory):
    with connect(directory) as connection:
        records = [json.loads(row[0]) for row in connection.execute('SELECT payload FROM jobs')]
        jobs = {job['id']: job for job in records}
        for job in jobs.values():
            record_completion(connection, job)
            if job['status'] in ('queued', 'downloading', 'processing'):
                job['recoverable'] = job['status'] == 'processing'
                job.update(status='failed', progress=0, speed=None, eta=None,
                           error='Interrupted by a server restart. Use Retry to download again.')
                connection.execute('UPDATE jobs SET payload = ? WHERE id = ?', (json.dumps(job), job['id']))
            if job.get('preview_status') in ('queued', 'processing'):
                job.update(preview_status='not_requested', preview_available=False, preview_progress=0)
                connection.execute('UPDATE jobs SET payload = ? WHERE id = ?', (json.dumps(job), job['id']))
    return jobs


def statistics(directory):
    with connect(directory) as connection:
        counts = dict(connection.execute('SELECT platform, COUNT(*) FROM download_events GROUP BY platform'))
    return {'total': sum(counts.values()), 'platforms': [{'name': name, 'downloads': counts.get(name, 0)} for name in PLATFORMS]}


def video_counts(directory):
    with connect(directory) as connection:
        return dict(connection.execute('SELECT video_key, COUNT(*) FROM download_events GROUP BY video_key'))


def downloaded_count(directory, url):
    with connect(directory) as connection:
        return connection.execute('SELECT COUNT(*) FROM download_events WHERE video_key = ?', (video_key(url),)).fetchone()[0]
