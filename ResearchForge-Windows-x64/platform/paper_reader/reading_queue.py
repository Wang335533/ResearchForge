"""Durable reading shortlist with owned source copies; adding never calls a model."""
import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path

from flask import redirect, jsonify, request, send_file

if __package__:
    from . import storage, library, projects
    from .configuration import DATA
else:
    import storage, library, projects
    from configuration import DATA

FILES = DATA / 'reading_queue_files'
LOCK = threading.RLock()
STATUSES = ('unread', 'reading', 'read', 'removed')


def initialize():
    FILES.mkdir(parents=True, exist_ok=True, mode=0o700)
    with storage.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS reading_queue (id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL, library_uid TEXT UNIQUE, payload TEXT NOT NULL)')
        # The unique columns are scoped to a project ("<project>|<sha256>"), so one paper can wait in two projects.
        for item_id, fingerprint, uid, payload in db.execute("SELECT id,fingerprint,library_uid,payload FROM reading_queue WHERE fingerprint NOT LIKE '%|%'").fetchall():
            project = projects.of(json.loads(payload))
            db.execute('UPDATE reading_queue SET fingerprint=?, library_uid=? WHERE id=?',
                       (project + '|' + fingerprint, project + '|' + uid if uid else None, item_id))


def key(project, value):
    return project + '|' + value if value else None


def get(item_id):
    with storage.connect() as db:
        row = db.execute('SELECT payload FROM reading_queue WHERE id=?', (item_id,)).fetchone()
    if not row:
        raise ValueError('未找到这篇待读论文，请刷新列表。')
    return json.loads(row[0])


def save(item):
    item['updated'] = time.time()
    with storage.connect() as db:
        db.execute('UPDATE reading_queue SET payload=? WHERE id=?', (json.dumps(item, ensure_ascii=False), item['id']))
    return item


def public(item):
    return dict({k:v for k,v in item.items() if k not in ('stored_name', 'fingerprint')}, has_source=bool(item.get('stored_name')))


def source(item):
    name = item.get('stored_name', '')
    if not name:
        raise ValueError('这篇待读论文目前只有书目信息，请先补充全文，再载入精读。')
    if not re.fullmatch(r'[a-f0-9]{32}\.(pdf|md|markdown|txt)', name):
        raise ValueError('原文件记录无效。')
    path = (FILES / name).resolve()
    if not path.is_relative_to(FILES.resolve()) or not path.is_file():
        raise ValueError('待读原文件暂不可用，请恢复数据目录中的原文件。')
    return path


def split_authors(value):
    """The library keeps authors as one '; '-joined string; the queue edits them one per line."""
    if isinstance(value, list):
        return [str(a).strip()[:200] for a in value if str(a).strip()][:100]
    return [a.strip()[:200] for a in re.split(r'[;；]', str(value or '')) if a.strip()][:100]


def find_remote(document_id, project):
    with storage.connect() as db:
        row = db.execute('SELECT payload FROM reading_queue WHERE fingerprint=?', (key(project, 'online:' + document_id),)).fetchone()
    return json.loads(row[0]) if row else None


def add_metadata(record, project=projects.DEFAULT):
    """A reference is explicitly source-less; never synthesize a paper from its abstract."""
    with LOCK, storage.connect() as db:
        existing = find_remote(record['id'], project)
        if existing:
            if existing['status'] == 'removed':
                existing['status'] = 'unread'
                save(existing)
            return existing, False
        now = time.time()
        item = dict(id=uuid.uuid4().hex, fingerprint='online:' + record['id'], library_uid=None,
                    title=record['title'], authors=record['authors'], year=record['year'], journal=record['journal'],
                    notes='', status='unread', priority=False, created=now, updated=now, filename='', stored_name='',
                    bytes=0, source_kind='online', document_id=None, project_id=project,
                    remote=dict(source='literature_api', document_id=record['id'], url=record['url'], doi=record['doi'], abstract=record['abstract'][:60000]))
        db.execute('INSERT INTO reading_queue VALUES (?,?,?,?)',
                   (item['id'], key(project, item['fingerprint']), None, json.dumps(item, ensure_ascii=False)))
        return item, True


def attach_file(item_id, stream, filename):
    filename = Path(filename.replace('\\', '/')).name[:220]
    suffix = Path(filename).suffix.lower()
    if suffix not in ('.pdf', '.md', '.markdown', '.txt'):
        raise ValueError('支持 PDF、MD、Markdown 和 TXT 文件。')
    target = FILES / (uuid.uuid4().hex + suffix)
    try:
        with target.open('xb') as output:
            target.chmod(0o600)
            size = 0
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > 40 * 1024 * 1024:
                    raise ValueError('每篇论文最多 40 MB。')
                output.write(chunk)
        if not size:
            raise ValueError('文件为空，请重新选择。')
        with LOCK:
            item = get(item_id)
            if item.get('stored_name'):
                target.unlink()
                return item
            item.update(filename=filename, stored_name=target.name, bytes=size)
            return save(item)
    except Exception:
        target.unlink(missing_ok=True)
        raise


def validate(data):
    if not isinstance(data, dict):
        raise ValueError('请提交有效的论文信息。')
    out = {}
    for key, maximum in [('title', 600), ('notes', 10000), ('journal', 300)]:
        if key in data:
            value = data[key]
            if not isinstance(value, str) or len(value) > maximum or (key == 'title' and not value.strip()):
                raise ValueError('题名不能为空；题名最多 600 字符，备注最多 10,000 字符。')
            out[key] = value.strip()
    if 'authors' in data:
        value = data['authors']
        if not isinstance(value, list) or len(value)>100 or any(not isinstance(a,str) or len(a)>200 for a in value):
            raise ValueError('作者请逐行填写，每位最多 200 字符。')
        out['authors'] = list(dict.fromkeys(a.strip() for a in value if a.strip()))
    if 'year' in data:
        value = data['year']
        if value not in (None, '') and not re.fullmatch(r'(18|19|20)\d{2}|2100', str(value)):
            raise ValueError('年份只填写 1800–2100 之间的四位数字；未知请留空。')
        out['year'] = int(value) if value not in (None, '') else None
    if 'status' in data:
        if data['status'] not in STATUSES:
            raise ValueError('无效的阅读状态。')
        out['status'] = data['status']
    if 'priority' in data:
        if type(data['priority']) is not bool:
            raise ValueError('优先阅读状态无效。')
        out['priority'] = data['priority']
    return out


def add(stream, filename, metadata=None, library_uid=None, project=projects.DEFAULT):
    filename = Path(filename.replace('\\', '/')).name[:220]
    suffix = Path(filename).suffix.lower()
    if suffix not in ('.pdf', '.md', '.markdown', '.txt'):
        raise ValueError('支持 PDF、MD、Markdown 和 TXT 文件。')
    item_id = uuid.uuid4().hex
    target = FILES / (item_id + suffix)
    digest, size = hashlib.sha256(), 0
    try:
        with target.open('xb') as output:
            target.chmod(0o600)
            while chunk := stream.read(1024*1024):
                size += len(chunk)
                if size > 40*1024*1024:
                    raise ValueError('每篇论文最多 40 MB。')
                digest.update(chunk)
                output.write(chunk)
        if not size:
            raise ValueError('文件为空，请重新选择。')
        fingerprint = digest.hexdigest()
        with LOCK, storage.connect() as db:
            # Stable source identity as well as content identity protect double clicks,
            # browser retries and uploading the same paper with a different filename.
            row = db.execute('SELECT payload FROM reading_queue WHERE fingerprint=? OR library_uid=?', (key(project, fingerprint), key(project, library_uid))).fetchone()
            if row:
                item = json.loads(row[0])
                if item['status'] == 'removed':
                    item['status'] = 'unread'
                    save(item)
                target.unlink()
                return item, False
            now = time.time()
            item = dict(id=item_id, title=Path(filename).stem, authors=[], year=None, journal='', notes='',
                        status='unread', priority=False, created=now, updated=now, filename=filename,
                        stored_name=target.name, fingerprint=fingerprint, bytes=size,
                        library_uid=library_uid, source_kind='library' if library_uid else 'upload', document_id=None, project_id=project)
            item.update(validate(metadata or {}))
            db.execute('INSERT INTO reading_queue VALUES (?,?,?,?)', (item_id, key(project, fingerprint), key(project, library_uid), json.dumps(item,ensure_ascii=False)))
        return item, True
    except Exception:
        target.unlink(missing_ok=True)
        raise


def listing(query='', status='active', page=1, project=''):
    if status not in ('active', 'all') + STATUSES:
        raise ValueError('无效的列表筛选。')
    if len(query)>400:
        raise ValueError('搜索内容最多 400 字符。')
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        raise ValueError('无效的页码。')
    with storage.connect() as db:
        items = [i for i in (json.loads(r[0]) for r in db.execute('SELECT payload FROM reading_queue')) if not project or projects.of(i) == project]
        # Recover status even if the process exited after saving a completed report.
        completed = {r[0]:r[1] for r in db.execute("SELECT json_extract(payload,'$.document_id'),id FROM jobs WHERE json_extract(payload,'$.status')='completed' ORDER BY created")}
    with LOCK:
        for item in items:
            job_id = completed.get(item.get('document_id'))
            if job_id and item.get('job_id') != job_id:
                current = get(item['id'])
                current['job_id'] = job_id
                if current['status'] != 'removed':
                    current['status'] = 'read'
                item.update(save(current))
    counts = {s:sum(i['status']==s for i in items) for s in STATUSES}
    terms = [library.normalized(t) for t in query.split() if library.normalized(t)]
    def matches(i):
        if status=='active' and i['status'] in ('read', 'removed'):return False
        if status=='all' and i['status']=='removed':return False
        if status in STATUSES and i['status']!=status:return False
        text = library.normalized(' '.join([i['title'], *i['authors'], str(i['year'] or ''), i['notes'], i['journal']]))
        return all(t in text for t in terms)
    selected = sorted((i for i in items if matches(i)), key=lambda i:(not i['priority'], -i['created'], i['id']))
    start = (page-1)*30
    return dict(items=[public(i) for i in selected[start:start+30]], total=len(selected), page=page, has_more=len(selected)>start+30, counts=counts)


def install(app, workspace_page, import_saved, get_doc):
    initialize()

    @app.get('/reading-queue')
    def queue_page():
        return redirect(request.script_root + '/?view=queue')

    @app.get('/api/reading-queue')
    def queue_list():
        return jsonify(listing(request.args.get('q',''), request.args.get('status','active'), request.args.get('page',1),
                               projects.resolve(request.args.get('project'))))

    @app.post('/api/reading-queue')
    def queue_add():
        data = request.get_json()
        if not isinstance(data,dict):raise ValueError('请选择要加入的论文。')
        project = projects.resolve(data.get('project_id'), allow_all=False)
        record = library.paper(data.get('uid'))
        if not record.get('path'):
            # A website record without a local file is saved as a reference to attach later.
            item, created = add_metadata(dict(id=record['document_id'], title=record['title'], authors=split_authors(record['authors']),
                                              year=record.get('year'), journal=record.get('journal', ''), url=record.get('url', ''),
                                              doi=record.get('doi', ''), abstract=record.get('abstract', '')), project)
            return jsonify(item=public(item), created=created), 201 if created else 200
        metadata = dict(title=record['title'], authors=split_authors(record['authors']), year=record.get('year'), journal=record.get('journal', ''))
        with Path(record['path']).open('rb') as stream:
            item, created = add(stream, record['filename'], metadata, record.get('file_uid') or record['uid'], project)
        return jsonify(item=public(item), created=created), 201 if created else 200

    @app.post('/api/reading-queue/upload')
    def queue_upload():
        upload = request.files.get('file')
        if not upload or not upload.filename:raise ValueError('请选择要保存的论文。')
        item, created = add(upload.stream, upload.filename, project=projects.resolve(request.form.get('project_id'), allow_all=False))
        return jsonify(item=public(item), created=created), 201 if created else 200

    @app.get('/api/reading-queue/<item_id>')
    def queue_detail(item_id):
        return jsonify(public(get(item_id)))

    @app.patch('/api/reading-queue/<item_id>')
    def queue_edit(item_id):
        changes = validate(request.get_json())
        with LOCK:
            item = get(item_id)
            item.update(changes)
            return jsonify(public(save(item)))

    @app.get('/api/reading-queue/<item_id>/source')
    def queue_source(item_id):
        item = get(item_id)
        return send_file(source(item), as_attachment=True, download_name=item['filename'])

    @app.post('/api/reading-queue/<item_id>/source')
    def queue_attach(item_id):
        get(item_id)
        upload = request.files.get('file')
        if not upload or not upload.filename:
            raise ValueError('请选择要补充的原文件。')
        return jsonify(public(attach_file(item_id, upload.stream, upload.filename)))

    @app.post('/api/reading-queue/<item_id>/read')
    def queue_read(item_id):
        with LOCK:
            item = get(item_id)
            doc = None
            if item.get('document_id'):
                try:doc = get_doc(item['document_id'])
                except (ValueError, KeyError):pass
            if doc is None:
                doc = import_saved(item, source(item))
                item['document_id'] = doc['id']
            if item['status'] != 'read':item['status'] = 'reading'
            save(item)
        return jsonify(document_id=doc['id'], item=public(item))
