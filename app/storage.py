"""Persistent download metadata. Media remains in per-job directories."""
import json
import sqlite3
import hashlib
import re
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
    elif platform == 'TikTok' and re.search(r'/video/[0-9]{6,25}/?$',parsed.path):
        identity=path.split('/video/',1)[-1].strip('/')
    elif platform == 'Instagram' and re.fullmatch(r'(?:p|reel|tv)/[A-Za-z0-9_-]{1,64}',path):
        identity=path.split('/')[1]
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


def video_keys(url):
    key=video_key(url)
    if platform_name(url)=='Instagram':
        parsed=urlparse(url);path=parsed.path.strip('/')
        if re.fullmatch(r'(?:p|reel|tv)/[A-Za-z0-9_-]{1,64}',path):
            code=path.split('/')[1]
            return tuple(dict.fromkeys([key]+[hashlib.sha256(('Instagram:'+kind+'/'+code+'?').encode()).hexdigest() for kind in ('p','reel','tv')]))
        return (key,)
    if platform_name(url)!='TikTok':return (key,)
    parsed=urlparse(url);query=parse_qs(parsed.query)
    meaningful={k:v for k,v in query.items() if k in ('v','id','story_fbid')}
    legacy=hashlib.sha256(('TikTok:'+parsed.path.strip('/')+'?'+urlencode(meaningful,doseq=True)).encode()).hexdigest()
    return tuple(dict.fromkeys((key,legacy)))


def history_count(counts,url):
    return sum(counts.get(key,0) for key in video_keys(url))


def record_completion(connection, job):
    if job.get('status') == 'completed':
        url = job.get('url', '')
        event_id = job['id'] + (':' + job['completion_id'] if job.get('completion_id') else '')
        connection.execute('INSERT OR IGNORE INTO download_events VALUES (?, ?, ?, ?)',
                           (event_id, platform_name(url), video_key(url) if url else job['id'], job.get('completed_at') or job.get('created') or time.time()))
        # Repair earlier URL-based identities when retained source metadata is available.
        if url:connection.execute('UPDATE download_events SET video_key=? WHERE event_id=?',(video_key(url),event_id))


@contextmanager
def connect(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(directory / 'history.sqlite3', timeout=10)
    try:
        connection.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        connection.execute('CREATE TABLE IF NOT EXISTS download_events (event_id TEXT PRIMARY KEY, platform TEXT NOT NULL, video_key TEXT NOT NULL, completed_at REAL NOT NULL)')
        connection.execute('CREATE INDEX IF NOT EXISTS events_video_key ON download_events(video_key)')
        connection.execute('CREATE INDEX IF NOT EXISTS events_platform ON download_events(platform)')
        has_log=connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='activity_log'").fetchone()
        connection.execute('CREATE TABLE IF NOT EXISTS activity_log (id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL NOT NULL, event TEXT NOT NULL, job_id TEXT, url TEXT NOT NULL, title TEXT NOT NULL, format TEXT, quality TEXT, detail TEXT)')
        connection.execute('CREATE INDEX IF NOT EXISTS activity_created ON activity_log(created)')
        if not has_log:
            for record in connection.execute('SELECT payload FROM jobs').fetchall():
                job=json.loads(record[0])
                log_row(connection,job,'imported','Imported existing history; earlier deleted links cannot be reconstructed.')
        yield connection
        connection.commit()
    finally:
        connection.close()


def save(directory, job):
    with connect(directory) as connection:
        old=connection.execute('SELECT payload FROM jobs WHERE id=?',(job['id'],)).fetchone()
        record_state(connection,job,json.loads(old[0]).get('status') if old else None)
        connection.execute('INSERT OR REPLACE INTO jobs VALUES (?, ?)', (job['id'], json.dumps(job)))
        record_completion(connection, job)


def save_many(directory, jobs, audit_rows=()):
    records = [(job['id'], json.dumps(job)) for job in jobs]
    with connect(directory) as connection:
        for job in jobs:
            old=connection.execute('SELECT payload FROM jobs WHERE id=?',(job['id'],)).fetchone()
            record_state(connection,job,json.loads(old[0]).get('status') if old else None)
        connection.executemany('INSERT OR REPLACE INTO jobs VALUES (?, ?)', records)
        for job in jobs:
            record_completion(connection, job)
        for job,event,detail in audit_rows:log_row(connection,job,event,detail)


def delete(directory, job_id):
    with connect(directory) as connection:
        record=connection.execute('SELECT payload FROM jobs WHERE id=?',(job_id,)).fetchone()
        if record:log_row(connection,json.loads(record[0]),'removed','Removed from library; activity and lifetime counts retained.')
        connection.execute('DELETE FROM jobs WHERE id = ?', (job_id,))


def restore(directory):
    with connect(directory) as connection:
        records = [json.loads(row[0]) for row in connection.execute('SELECT payload FROM jobs')]
        jobs = {job['id']: job for job in records}
        for job in jobs.values():
            record_completion(connection, job)
            if job['status'] in ('queued', 'downloading', 'processing'):
                old_status=job['status']
                job['recoverable'] = job['status'] == 'processing'
                job.update(status='failed', progress=0, speed=None, eta=None,
                           error='Interrupted by a server restart. Use Retry to download again.')
                record_state(connection,job,old_status)
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
        keys=video_keys(url)
        return connection.execute('SELECT COUNT(*) FROM download_events WHERE video_key IN ('+','.join('?' for _ in keys)+')',keys).fetchone()[0]


def log_row(connection,job,event,detail=''):
    connection.execute('INSERT INTO activity_log(created,event,job_id,url,title,format,quality,detail) VALUES (?,?,?,?,?,?,?,?)',
                       (time.time(),event,job.get('id'),job.get('url',''),str(job.get('title') or 'Video'),job.get('format'),job.get('quality'),detail or job.get('error') or job.get('stage') or ''))


def record_state(connection,job,previous):
    if previous!=job.get('status'):
        log_row(connection,job,job.get('status','updated'))


def audit_many(directory,rows):
    with connect(directory) as connection:
        for job,event,detail in rows:log_row(connection,job,event,detail)


def activity(directory,page=1,size=50,query='',event=''):
    where=[];args=[]
    if query:
        where.append('(url LIKE ? OR title LIKE ? OR detail LIKE ?)')
        args.extend(['%'+query+'%']*3)
    if event:where.append('event=?');args.append(event)
    clause=' WHERE '+' AND '.join(where) if where else ''
    with connect(directory) as connection:
        connection.row_factory=sqlite3.Row
        total=connection.execute('SELECT COUNT(*) FROM activity_log'+clause,args).fetchone()[0]
        rows=connection.execute('SELECT * FROM activity_log'+clause+' ORDER BY id DESC LIMIT ? OFFSET ?',args+[size,(page-1)*size]).fetchall()
    return {'items':[dict(row) for row in rows],'total':total,'page':page,'size':size}


def export_activity(directory):
    # Append-only IDs give a stable export cutoff without a long-running read lock.
    with connect(directory) as connection:
        cutoff=connection.execute('SELECT COALESCE(MAX(id),0) FROM activity_log').fetchone()[0]
    after=0
    while True:
        with connect(directory) as connection:
            connection.row_factory=sqlite3.Row
            rows=connection.execute('SELECT * FROM activity_log WHERE id>? AND id<=? ORDER BY id LIMIT 500',(after,cutoff)).fetchall()
        if not rows:return
        from datetime import datetime,timezone
        for row in rows:yield dict(row)|{'created_at':datetime.fromtimestamp(row['created'],timezone.utc).isoformat()}
        after=rows[-1]['id']
