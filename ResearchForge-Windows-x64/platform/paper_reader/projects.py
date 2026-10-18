"""Research projects (课题): the top level that keeps separate lines of research apart.

Every reading (job), literature card, reading-queue item and, through its card, every variable record
belongs to exactly one project. Cards are identified within a project, so the same paper read for two
projects keeps two independent cards, maps and networks. Records written before projects existed belong
to the default project. A project may carry the research description that new readings compare with.
"""
import json
import time
import uuid

from flask import jsonify, request

if __package__:
    from . import storage
else:
    import storage

DEFAULT = 'default'
DEFAULT_NAME = '默认课题'


def of(record):
    return (record or {}).get('project_id') or DEFAULT


def initialize():
    with storage.DB_LOCK, storage.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        if not db.execute('SELECT 1 FROM projects WHERE id=?', (DEFAULT,)).fetchone():
            now = time.time()
            put(db, dict(id=DEFAULT, name=DEFAULT_NAME, description='', archived=False, created=now, updated=now))


def put(db, project):
    db.execute('INSERT OR REPLACE INTO projects VALUES (?,?)', (project['id'], json.dumps(project, ensure_ascii=False)))


def get(pid, db=None):
    if db is None:
        with storage.connect() as con:
            return get(pid, con)
    row = db.execute('SELECT payload FROM projects WHERE id=?', (pid,)).fetchone()
    if not row:
        raise ValueError('未找到这个课题，请刷新后重试。')
    return json.loads(row[0])


def resolve(value, allow_all=True):
    """'' means every project (only where allowed); anything else must be an existing project id."""
    value = str(value or '').strip()
    if not value or value == 'all':
        if allow_all:
            return ''
        value = DEFAULT
    get(value)
    return value


def counts(db):
    def per(table, where=''):
        sql = f"SELECT coalesce(json_extract(payload,'$.project_id'),'{DEFAULT}'),count(*) FROM {table} {where} GROUP BY 1"
        return dict(db.execute(sql).fetchall())
    queue = per('reading_queue', "WHERE json_extract(payload,'$.status') NOT IN ('read','removed')") if \
        db.execute("SELECT 1 FROM sqlite_master WHERE name='reading_queue'").fetchone() else {}
    return dict(papers=per('memory_papers'), readings=per('jobs', "WHERE coalesce(json_extract(payload,'$.archived'),0)=0"), queue=queue)


def all_projects(include_archived=True):
    with storage.connect() as db:
        rows = [json.loads(r[0]) for r in db.execute('SELECT payload FROM projects')]
        tally = counts(db)
    for p in rows:
        p['counts'] = {k: v.get(p['id'], 0) for k, v in tally.items()}
    rows.sort(key=lambda p: (p['archived'], p['id'] != DEFAULT, -p.get('updated', 0)))
    return [p for p in rows if include_archived or not p['archived']]


def validate(data, partial=False):
    if not isinstance(data, dict):
        raise ValueError('请提交有效的课题信息。')
    out = {}
    if 'name' in data or not partial:
        name = data.get('name')
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            raise ValueError('课题名称需要 1–80 个字符。')
        out['name'] = name.strip()
    if 'description' in data:
        if not isinstance(data['description'], str) or len(data['description']) > 20000:
            raise ValueError('研究说明最多 20,000 字符。')
        out['description'] = data['description'].strip()
    if 'archived' in data:
        if type(data['archived']) is not bool:
            raise ValueError('归档状态无效。')
        out['archived'] = data['archived']
    return out


def create(data):
    fields = validate(data)
    now = time.time()
    project = dict(id=uuid.uuid4().hex, description='', archived=False, created=now, updated=now, **fields)
    with storage.DB_LOCK, storage.connect() as db:
        if any(json.loads(r[0])['name'] == project['name'] for r in db.execute('SELECT payload FROM projects')):
            raise ValueError('已有同名课题，请换一个名称。')
        put(db, project)
    return project


def update(pid, data):
    fields = validate(data, partial=True)
    with storage.DB_LOCK, storage.connect() as db:
        project = get(pid, db)
        if pid == DEFAULT and fields.get('archived'):
            raise ValueError('默认课题不能归档；可以重命名。')
        if 'name' in fields and any(json.loads(r[0])['name'] == fields['name'] and json.loads(r[0])['id'] != pid
                                    for r in db.execute('SELECT payload FROM projects')):
            raise ValueError('已有同名课题，请换一个名称。')
        project.update(fields, updated=time.time())
        put(db, project)
    return project


def delete(pid):
    """Only an empty project can be deleted; content must be moved or deleted first."""
    if pid == DEFAULT:
        raise ValueError('默认课题不能删除。')
    with storage.DB_LOCK, storage.connect() as db:
        get(pid, db)
        tally = counts(db)
        if any(v.get(pid) for v in tally.values()) or db.execute(
                f"SELECT 1 FROM jobs WHERE json_extract(payload,'$.project_id')=?", (pid,)).fetchone():
            raise ValueError('这个课题里还有精读记录、文献卡片或待读论文；请先移到其他课题，或改为归档。')
        db.execute('DELETE FROM projects WHERE id=?', (pid,))


def install(app):
    initialize()

    @app.get('/api/projects')
    def projects_list():
        return jsonify(projects=all_projects(), default=DEFAULT)

    @app.post('/api/projects')
    def projects_create():
        return jsonify(project=create(request.get_json())), 201

    @app.post('/api/projects/<pid>')
    def projects_update(pid):
        return jsonify(project=update(pid, request.get_json()))

    @app.delete('/api/projects/<pid>')
    def projects_delete(pid):
        delete(pid)
        return jsonify(ok=True)
