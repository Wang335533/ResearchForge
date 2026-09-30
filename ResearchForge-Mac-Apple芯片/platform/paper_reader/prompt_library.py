"""A durable, local prompt collection, independent of live generation prompts."""
import base64
import hashlib
import io
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from flask import jsonify, request, send_file
from werkzeug.exceptions import Conflict

if __package__:
    from .configuration import DATA
else:
    from configuration import DATA

DB = DATA / 'prompt_library.sqlite3'
SOURCE_ROOT = Path(os.environ.get('PAPERLENS_PROMPT_SOURCE', Path(__file__).resolve().parents[2] / 'user_data/prompt-import'))
LOCK = threading.RLock()
EXTENSIONS = {'.txt', '.md', '.markdown', '.json'}
MAX_BYTES = 2 * 1024 * 1024
MAX_CHARS = 300000
EDIT_FIELDS = ('title', 'content', 'category', 'tags', 'notes', 'favorite', 'archived')


def connect():
    db = sqlite3.connect(DB, timeout=20)
    db.execute('PRAGMA journal_mode=WAL')
    return db


def initialize():
    with connect() as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS prompts(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS revisions(id TEXT PRIMARY KEY,prompt_id TEXT NOT NULL,created REAL NOT NULL,payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS revisions_prompt ON revisions(prompt_id,created);
        CREATE TABLE IF NOT EXISTS receipts(hash TEXT PRIMARY KEY,prompt_id TEXT NOT NULL);''')
    DB.chmod(0o600)


def clean(data):
    if not isinstance(data, dict):
        raise ValueError('提示词格式无效。')
    result = {}
    for key, limit, default in [('title', 240, ''), ('content', MAX_CHARS, ''), ('category', 240, '未分类'), ('notes', 5000, '')]:
        value = data.get(key, default)
        if not isinstance(value, str) or len(value) > limit or '\x00' in value:
            raise ValueError(f'{key} 内容无效或超过长度限制。')
        result[key] = value if key == 'content' else value.strip()
    result['category'] = result['category'] or '未分类'
    if result['category'] == '.':
        result['category'] = '未分类'
    if not result['title'] or not result['content'].strip():
        raise ValueError('请填写标题和提示词正文。')
    tags = data.get('tags', [])
    if not isinstance(tags, list) or len(tags) > 30 or any(not isinstance(t, str) or len(t) > 60 for t in tags):
        raise ValueError('最多 30 个标签，每个不超过 60 字。')
    result['tags'] = list(dict.fromkeys(t.strip() for t in tags if t.strip()))
    for key in ('favorite', 'archived'):
        if type(data.get(key, False)) is not bool:
            raise ValueError('收藏或回收站状态无效。')
        result[key] = data.get(key, False)
    return result


def get(pid, db=None):
    if db is None:
        with connect() as conn:
            return get(pid, conn)
    row = db.execute('SELECT payload FROM prompts WHERE id=?', (pid,)).fetchone()
    if not row:
        raise ValueError('未找到这份提示词。')
    return json.loads(row[0])


def save(db, record, action):
    record = dict(record, updated=time.time(), revision=uuid.uuid4().hex, action=action)
    payload = json.dumps(record, ensure_ascii=False)
    db.execute('INSERT OR REPLACE INTO prompts VALUES (?,?)', (record['id'], payload))
    db.execute('INSERT INTO revisions VALUES (?,?,?,?)', (record['revision'], record['id'], record['updated'], payload))
    return record


def create(db, data, **source):
    return save(db, dict(clean(data), id=uuid.uuid4().hex, created=time.time(), **source), '首次保存')


def update(pid, data):
    with LOCK, connect() as db:
        old = get(pid, db)
        if not isinstance(data, dict) or data.get('revision') != old['revision']:
            raise Conflict('这份提示词已在其他页面更新，请重新打开后再编辑。当前输入仍保留。')
        return save(db, {**old, **clean(data)}, '编辑保存')


def catalog(args):
    q = str(args.get('q', '')).strip()[:500]
    category = str(args.get('category', ''))[:240]
    view = args.get('view', 'all')
    if view not in ('all', 'favorites', 'trash'):
        raise ValueError('筛选方式无效。')
    try:
        page = max(1, int(args.get('page', 1)))
    except (TypeError, ValueError):
        raise ValueError('页码无效。')
    clauses = ["json_extract(payload,'$.archived')=?"]
    params = [int(view == 'trash')]
    if q:
        clauses.append("instr(lower(json_extract(payload,'$.title')||' '||json_extract(payload,'$.content')||' '||json_extract(payload,'$.tags')||' '||json_extract(payload,'$.notes')),lower(?))>0")
        params.append(q)
    if category:
        clauses.append("json_extract(payload,'$.category')=?")
        params.append(category)
    if view == 'favorites':
        clauses.append("json_extract(payload,'$.favorite')=1")
    where = ' AND '.join(clauses)
    with connect() as db:
        count = db.execute('SELECT count(*) FROM prompts WHERE '+where, params).fetchone()[0]
        records = [json.loads(r[0]) for r in db.execute("SELECT payload FROM prompts WHERE "+where+" ORDER BY json_extract(payload,'$.updated') DESC,id LIMIT 40 OFFSET ?", params+[(page-1)*40])]
        stats = dict(total=0, favorites=0, trash=0)
        categories = {}
        for cat, fav, archived in db.execute("SELECT json_extract(payload,'$.category'),json_extract(payload,'$.favorite'),json_extract(payload,'$.archived') FROM prompts"):
            stats['trash' if archived else 'total'] += 1
            if not archived:
                stats['favorites'] += bool(fav)
                categories[cat] = categories.get(cat, 0)+1
    items = [{k: r[k] for k in ('id', 'title', 'category', 'tags', 'favorite', 'archived', 'updated', 'revision')} | {'excerpt': r['content'][:170], 'chars': len(r['content'])} for r in records]
    return dict(items=items, total=count, page=page, has_more=page*40<count, stats=stats, categories=[dict(name=k, count=v) for k, v in sorted(categories.items())])


def decode(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError('单个提示词文件最大 2 MB。')
    for encoding in (['utf-16'] if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else ['utf-8-sig', 'gb18030']):
        try:
            text = raw.decode(encoding)
            if '\x00' in text:
                raise ValueError('无法读取二进制文件，请使用文本提示词。')
            return text
        except UnicodeDecodeError:
            continue
    raise ValueError('文件编码无法识别，请保存为 UTF-8 文本。')


def parse_file(name, raw, category='未分类'):
    if Path(name).suffix.lower() not in EXTENSIONS:
        raise ValueError('支持 TXT、Markdown 和 JSON 提示词。')
    text = decode(raw)
    content, notes, title = text, '', Path(name).stem
    if Path(name).suffix.lower() == '.json':
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            raise ValueError('JSON 格式无效，请检查后再导入。')
        if isinstance(value, dict) and value.get('format') == 'researchforge-prompt-library':
            raise ValueError('这是提示词库备份，请使用“恢复备份”。')
        if isinstance(value, dict) and isinstance(value.get('content'), str):
            content = value['content']
            if isinstance(value.get('name'), str):
                title = value['name'] or title
            if isinstance(value.get('description'), str):
                notes = value['description']
    return clean(dict(title=title, content=content, notes=notes, category=category))


def source_file(relative):
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError('文件位置无效。')
    root = SOURCE_ROOT.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in EXTENSIONS:
        raise ValueError('文件不在桌面提示词目录内，或已不可用。')
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('单个提示词文件最大 2 MB。')
    raw = path.read_bytes()
    return path, raw


def scan():
    if not SOURCE_ROOT.is_dir():
        raise ValueError('桌面提示词文件夹不存在；可以新建提示词或上传文件。')
    result, errors = [], []
    with connect() as db:
        known = {r[0] for r in db.execute('SELECT hash FROM receipts')}
        paths = {json.loads(r[0]).get('source_path') for r in db.execute('SELECT payload FROM prompts')}
    for path in sorted(SOURCE_ROOT.rglob('*')):
        if path.suffix.lower() not in EXTENSIONS or not path.is_file():
            continue
        if len(result)+len(errors) >= 500:
            errors.append(dict(path='…', error='一次最多显示 500 个文件，请分批整理。'))
            break
        relative = path.relative_to(SOURCE_ROOT).as_posix()
        try:
            full, raw = source_file(relative)
            data = parse_file(path.name, raw, str(Path(relative).parent).replace('/', ' / '))
            digest = hashlib.sha256(raw).hexdigest()
            result.append(dict(path=relative, title=data['title'], category=data['category'], bytes=len(raw), hash=digest,
                               status='imported' if digest in known else 'updated' if str(full) in paths else 'new'))
        except (ValueError, OSError) as exc:
            errors.append(dict(path=relative, error=str(exc)))
    return dict(root=str(SOURCE_ROOT), files=result, errors=errors)


def import_records(records):
    added, skipped = [], 0
    with LOCK, connect() as db:
        for name, raw, category, source_path in records:
            digest = hashlib.sha256(raw).hexdigest()
            if db.execute('SELECT 1 FROM receipts WHERE hash=?', (digest,)).fetchone():
                skipped += 1
                continue
            data = parse_file(name, raw, category)
            record = create(db, data, source_path=source_path, source_name=name, source_hash=digest,
                            source_bytes=base64.b64encode(raw).decode('ascii'))
            db.execute('INSERT INTO receipts VALUES (?,?)', (digest, record['id']))
            added.append(record['id'])
    return dict(added=len(added), skipped=skipped, ids=added)


def import_desktop(data):
    selected = data.get('files') if isinstance(data, dict) else None
    if not isinstance(selected, list) or not 1 <= len(selected) <= 500:
        raise ValueError('请选择 1–500 份提示词。')
    records = []
    for item in selected:
        if not isinstance(item, dict):
            raise ValueError('文件选择无效。')
        path, raw = source_file(item.get('path'))
        if hashlib.sha256(raw).hexdigest() != item.get('hash'):
            raise Conflict('来源文件已变化，请重新扫描后导入。')
        records.append((path.name, raw, str(Path(item['path']).parent).replace('/', ' / '), str(path)))
    if sum(len(r[1]) for r in records) > 40*1024*1024:
        raise ValueError('一次导入最大 40 MB，请分批选择。')
    return import_records(records)


def export_bundle():
    with connect() as db:
        entries = [json.loads(r[0]) for r in db.execute('SELECT payload FROM prompts ORDER BY id')]
        versions = [json.loads(r[0]) for r in db.execute('SELECT payload FROM revisions ORDER BY created,id')]
    return dict(format='researchforge-prompt-library', version=1, exported_at=time.time(), prompts=entries, revisions=versions)


def restore_bundle(data):
    if not isinstance(data, dict) or data.get('format') != 'researchforge-prompt-library' or data.get('version') != 1:
        raise ValueError('请选择本平台导出的提示词库 JSON 备份。')
    entries, versions = data.get('prompts'), data.get('revisions')
    if not isinstance(entries, list) or not isinstance(versions, list) or len(entries) > 10000 or len(versions) > 50000:
        raise ValueError('备份记录无效或过多。')
    ids = set()
    for record in entries+versions:
        clean(record)
        for key in ('id', 'revision'):
            if not isinstance(record.get(key), str) or not re.fullmatch('[a-f0-9]{32}', record[key]):
                raise ValueError('备份标识无效。')
        for key in ('created', 'updated'):
            if type(record.get(key)) not in (int, float) or not 0 <= record[key] <= 1e12:
                raise ValueError('备份时间无效。')
        for key, limit in (('source_path', 4096), ('source_name', 512), ('source_hash', 64), ('action', 240)):
            if key in record and (not isinstance(record[key], str) or len(record[key]) > limit):
                raise ValueError('备份来源信息无效。')
        if record.get('source_hash') and not record.get('source_bytes'):
            raise ValueError('备份缺少导入时的原始文件。')
        if record.get('source_bytes'):
            try:
                raw = base64.b64decode(record['source_bytes'], validate=True)
                if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != record.get('source_hash'):
                    raise ValueError()
            except Exception:
                raise ValueError('备份中的原始文件校验失败。')
    for r in entries:
        if r['id'] in ids:
            raise ValueError('备份含重复提示词标识。')
        ids.add(r['id'])
    if any(r['id'] not in ids for r in versions):
        raise ValueError('备份版本缺少所属提示词。')
    # Save an independent SQLite backup before any merge, including empty libraries.
    backup_dir = DB.parent / 'prompt_backups'
    backup_dir.mkdir(exist_ok=True, mode=0o700)
    backup = backup_dir / ('before-restore-'+uuid.uuid4().hex+'.sqlite3')
    with LOCK, connect() as db:
        with sqlite3.connect(backup) as target:
            db.backup(target)
        backup.chmod(0o600)
        added = 0
        for record in entries:
            payload = json.dumps(record, ensure_ascii=False)
            added += db.execute('INSERT OR IGNORE INTO prompts VALUES (?,?)', (record['id'], payload)).rowcount
            if record.get('source_hash'):
                db.execute('INSERT OR IGNORE INTO receipts VALUES (?,?)', (record['source_hash'], record['id']))
        for record in versions+entries:
            db.execute('INSERT OR IGNORE INTO revisions VALUES (?,?,?,?)', (record['revision'], record['id'], record['updated'], json.dumps(record, ensure_ascii=False)))
    return dict(added=added, retained=len(entries)-added)


def install(app, page):
    initialize()

    @app.get('/prompt-library')
    def prompt_library_page():
        return page('prompt-library.html', 'prompts')

    @app.get('/api/prompt-library')
    def prompt_catalog():
        return jsonify(catalog(request.args))

    @app.post('/api/prompt-library')
    def prompt_create():
        with LOCK, connect() as db:
            record = create(db, request.get_json())
        return jsonify(record), 201

    @app.get('/api/prompt-library/<pid>')
    def prompt_detail(pid):
        return jsonify(get(pid))

    @app.put('/api/prompt-library/<pid>')
    def prompt_update(pid):
        return jsonify(update(pid, request.get_json()))

    @app.get('/api/prompt-library/<pid>/versions')
    def prompt_versions(pid):
        get(pid)
        with connect() as db:
            rows = [json.loads(r[0]) for r in db.execute('SELECT payload FROM revisions WHERE prompt_id=? ORDER BY created DESC LIMIT 200', (pid,))]
        return jsonify(versions=[{k: r.get(k) for k in ('revision', 'title', 'updated', 'action')} for r in rows])

    @app.get('/api/prompt-library/<pid>/versions/<rid>')
    def prompt_version(pid, rid):
        with connect() as db:
            row = db.execute('SELECT payload FROM revisions WHERE prompt_id=? AND id=?', (pid, rid)).fetchone()
        if not row:
            raise ValueError('未找到此历史版本。')
        return jsonify(json.loads(row[0]))

    @app.post('/api/prompt-library/<pid>/restore-version')
    def prompt_restore_version(pid):
        data = request.get_json()
        with LOCK, connect() as db:
            old = get(pid, db)
            if not isinstance(data, dict) or data.get('revision') != old['revision']:
                raise Conflict('当前提示词已更新，请重新打开后恢复。')
            row = db.execute('SELECT payload FROM revisions WHERE prompt_id=? AND id=?', (pid, data.get('version'))).fetchone()
            if not row:
                raise ValueError('未找到此历史版本。')
            version = json.loads(row[0])
            record = save(db, {**old, **{k: version[k] for k in EDIT_FIELDS if k != 'archived'}}, '恢复历史版本')
        return jsonify(record)

    @app.get('/api/prompt-library/<pid>/download')
    def prompt_download(pid):
        record = get(pid)
        name = re.sub(r'[^\w\u4e00-\u9fff .-]', '_', record['title'])[:100] or 'prompt'
        return send_file(io.BytesIO(record['content'].encode()), mimetype='text/plain; charset=utf-8', as_attachment=True, download_name=name+'.txt')

    @app.get('/api/prompt-library/desktop')
    def prompt_desktop_scan():
        return jsonify(scan())

    @app.post('/api/prompt-library/desktop')
    def prompt_desktop_import():
        return jsonify(import_desktop(request.get_json()))

    @app.post('/api/prompt-library/upload')
    def prompt_upload():
        files = request.files.getlist('files')
        if not 1 <= len(files) <= 100:
            raise ValueError('请选择 1–100 个文件。')
        records = [(Path(f.filename or 'prompt.txt').name, f.read(MAX_BYTES+1), '文件导入', '') for f in files]
        return jsonify(import_records(records))

    @app.get('/api/prompt-library/backup')
    def prompt_backup():
        raw = json.dumps(export_bundle(), ensure_ascii=False, indent=2).encode()
        return send_file(io.BytesIO(raw), mimetype='application/json', as_attachment=True, download_name='提示词库备份_'+time.strftime('%Y%m%d_%H%M%S')+'.json')

    @app.post('/api/prompt-library/restore')
    def prompt_restore():
        file = request.files.get('file')
        if not file:
            raise ValueError('请选择备份文件。')
        try:
            data = json.loads(file.read())
        except (ValueError, UnicodeError):
            raise ValueError('备份不是有效的 JSON 文件。')
        return jsonify(restore_bundle(data))
