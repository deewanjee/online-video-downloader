"""Persistent download metadata. Media remains in per-job directories."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def connect(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(directory / 'history.sqlite3', timeout=10)
    try:
        connection.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        yield connection
        connection.commit()
    finally:
        connection.close()


def save(directory, job):
    with connect(directory) as connection:
        connection.execute('INSERT OR REPLACE INTO jobs VALUES (?, ?)', (job['id'], json.dumps(job)))


def delete(directory, job_id):
    with connect(directory) as connection:
        connection.execute('DELETE FROM jobs WHERE id = ?', (job_id,))


def restore(directory):
    with connect(directory) as connection:
        records = [json.loads(row[0]) for row in connection.execute('SELECT payload FROM jobs')]
        jobs = {job['id']: job for job in records}
        for job in jobs.values():
            if job['status'] in ('queued', 'downloading', 'processing'):
                job.update(status='failed', progress=0, speed=None, eta=None,
                           error='Interrupted by a server restart. Use Retry to download again.')
                connection.execute('UPDATE jobs SET payload = ? WHERE id = ?', (json.dumps(job), job['id']))
    return jobs
