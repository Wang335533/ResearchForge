import json
import sqlite3
import threading
import time

if __package__:
    from .configuration import DATA
else:
    from configuration import DATA

DB = DATA / 'papers.sqlite3'
DB_LOCK = threading.RLock()


def connect():
    db = sqlite3.connect(DB, timeout=20)
    db.execute('PRAGMA journal_mode=WAL')
    return db


def initialize():
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL)')
    DB.chmod(0o600)
    for j in all_jobs():
        if j['status'] in ('queued', 'running', 'stopping'):
            update(j['id'], status='interrupted', message='上次运行中断。已保存进度；可手动继续，不会自动调用 API。')


def all_jobs():
    with connect() as db:
        return [json.loads(row[0]) for row in db.execute('SELECT payload FROM jobs ORDER BY created DESC')]


def get(job_id):
    with connect() as db:
        row = db.execute('SELECT payload FROM jobs WHERE id=?', (job_id,)).fetchone()
    if not row:
        raise KeyError('未找到这份分析记录。')
    return json.loads(row[0])


def create(job):
    with DB_LOCK, connect() as db:
        db.execute('INSERT INTO jobs VALUES (?,?,?)', (job['id'], job['created'], json.dumps(job, ensure_ascii=False)))


def update(job_id, **changes):
    with DB_LOCK:
        job = get(job_id)
        job.update(changes, updated=time.time())
        with connect() as db:
            db.execute('UPDATE jobs SET payload=? WHERE id=?', (json.dumps(job, ensure_ascii=False), job_id))
        return job
