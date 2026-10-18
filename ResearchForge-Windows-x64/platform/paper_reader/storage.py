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
            update(j['id'], status='interrupted', message='上次运行被中断，进度已保存，可以手动继续')


def all_jobs():
    with connect() as db:
        return [json.loads(row[0]) for row in db.execute('SELECT payload FROM jobs ORDER BY created DESC')]


SUMMARY_FIELDS = ('id', 'title', 'created', 'updated', 'status', 'stage', 'message', 'progress', 'model', 'filename', 'project_id', 'archived')
# Readings from before research projects existed belong to the default project.
PROJECT = "coalesce(json_extract(payload,'$.project_id'),'default')"


def summaries(archived=False, project=''):
    """History rows without report bodies or evidence, so frequent polling stays cheap. project '' = all."""
    columns = ','.join(f"json_extract(payload,'$.{field}')" for field in SUMMARY_FIELDS)
    with connect() as db:
        rows = db.execute(f"SELECT {columns} FROM jobs WHERE coalesce(json_extract(payload,'$.archived'),0)=? AND (?='' OR {PROJECT}=?) ORDER BY created DESC",
                          (1 if archived else 0, project, project)).fetchall()
    return [dict(zip(SUMMARY_FIELDS, row), archived=bool(row[-1]), project_id=row[-2] or 'default') for row in rows]


def archived_count(project=''):
    with connect() as db:
        return db.execute(f"SELECT count(*) FROM jobs WHERE coalesce(json_extract(payload,'$.archived'),0)=1 AND (?='' OR {PROJECT}=?)",
                          (project, project)).fetchone()[0]


def get(job_id):
    with connect() as db:
        row = db.execute('SELECT payload FROM jobs WHERE id=?', (job_id,)).fetchone()
    if not row:
        raise KeyError('未找到这份分析记录。')
    return json.loads(row[0])


def create(job):
    with DB_LOCK, connect() as db:
        db.execute('INSERT INTO jobs VALUES (?,?,?)', (job['id'], job['created'], json.dumps(job, ensure_ascii=False)))


def create_once(job):
    """An uncertain POST can be retried without queuing a second paid analysis."""
    with DB_LOCK, connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT payload FROM jobs WHERE id=?', (job['id'],)).fetchone()
        if row:
            existing = json.loads(row[0])
            if existing.get('request_signature') != job.get('request_signature'):
                raise ValueError('同一个提交编号不能用于不同精读内容，请新建精读。')
            return existing, False
        db.execute('INSERT INTO jobs VALUES (?,?,?)', (job['id'], job['created'], json.dumps(job, ensure_ascii=False)))
        return job, True


def request_stop(job_id):
    with DB_LOCK:
        job = get(job_id)
        if job['status'] in ('queued', 'running', 'stopping'):
            return update(job_id, status='stopping', message='正在停止，已返回的内容会保留')
        return job


def update(job_id, **changes):
    with DB_LOCK:
        job = get(job_id)
        job.update(changes, updated=time.time())
        with connect() as db:
            db.execute('UPDATE jobs SET payload=? WHERE id=?', (json.dumps(job, ensure_ascii=False), job_id))
        return job
