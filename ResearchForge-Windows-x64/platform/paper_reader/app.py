import copy
import base64
from functools import lru_cache
import hashlib
import html
import json
import logging
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import bleach
import markdown
from flask import Flask, jsonify, request, send_file
from werkzeug.exceptions import HTTPException

if __package__:
    from . import storage, library, memory, memory_jobs, research_bridge, prompt_library, report_workflow
    from .configuration import DATA, ROOT, LOCK, atomic_json, public_provider, public_settings, redact, save_settings, settings, validate_provider
    from .documents import extract_document, split_text
    from .llm import GenerationError, Stopped, complete
    from .prompts import DEFAULT_PROMPTS
else:
    import storage
    import library
    import memory
    import memory_jobs
    import research_bridge
    import prompt_library
    import report_workflow
    from configuration import DATA, ROOT, LOCK, atomic_json, public_provider, public_settings, redact, save_settings, settings, validate_provider
    from documents import extract_document, split_text
    from llm import GenerationError, Stopped, complete
    from prompts import DEFAULT_PROMPTS

app = Flask(__name__, static_folder=str(ROOT / 'static'))
app.config['MAX_CONTENT_LENGTH'] = 40 * 1024 * 1024
DOCS = DATA / 'documents'
DOCS.mkdir(exist_ok=True, mode=0o700)
storage.initialize()
memory.initialize()
research_bridge.initialize()
POOL = ThreadPoolExecutor(max_workers=2)
ACTIVE = set()
STOPS = {}
RUN_LOCK = threading.RLock()
TAGS = set(bleach.sanitizer.ALLOWED_TAGS) | {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'br', 'hr', 'pre', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'span', 'div', 'sup', 'sub'}


def render_report(text):
    rendered = markdown.markdown(text, extensions=['tables', 'fenced_code', 'sane_lists', 'pymdownx.arithmatex'],
                                 extension_configs={'pymdownx.arithmatex': {'generic': True}})
    return bleach.clean(rendered, tags=TAGS, attributes={'a': ['href', 'title'], 'th': ['align'], 'td': ['align'],
                        'span': ['class'], 'div': ['class']}, protocols=['http', 'https', 'mailto'], strip=True)


@lru_cache(maxsize=1)
def embedded_math_assets():
    assets = ROOT / 'static/vendor/katex'
    css = (assets / 'katex.min.css').read_text()
    def embed(match):
        font = assets / match.group(1)
        return 'url(data:font/' + font.suffix[1:] + ';base64,' + base64.b64encode(font.read_bytes()).decode() + ')'
    css = re.sub(r'url\((fonts/[^)]+)\)', embed, css)
    js = (assets / 'katex.min.js').read_text() + '\n' + (assets / 'auto-render.min.js').read_text()
    js += '\nrenderMathInElement(document.querySelector(".report"),{throwOnError:false,trust:false,strict:"ignore"});'
    return '<style>' + css + '</style>', '<script>' + js.replace('</script', '<\\/script') + '</script>'


def valid_id(value):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', str(value)):
        raise ValueError('无效的记录标识。')
    return value


def get_doc(doc_id):
    path = DOCS / (valid_id(doc_id) + '.json')
    if not path.exists():
        raise KeyError('未找到上传文档，请重新上传。')
    return json.loads(path.read_text())


def provider_by_id(provider_id):
    p = next((p for p in settings()['providers'] if p['id'] == provider_id), None)
    if not p:
        raise ValueError('所选 API 配置不存在，请重新选择。')
    return p


@app.before_request
def local_only():
    if urlsplit('http://' + request.host).hostname not in ('127.0.0.1', 'localhost', '::1'):
        return jsonify(error='仅允许从本机访问。'), 403
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        if request.headers.get('X-PaperLens') != '1':
            return jsonify(error='请求来源无效，请刷新页面后重试。'), 403
        origin = request.headers.get('Origin')
        if origin and origin != request.host_url.rstrip('/'):
            return jsonify(error='不允许跨站修改。'), 403


@app.after_request
def response_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.errorhandler(Exception)
def error_response(exc):
    if isinstance(exc, HTTPException):
        messages = {413: '文件超过 40 MB，请压缩或拆分后上传。', 404: '未找到所请求的内容。'}
        return jsonify(error=messages.get(exc.code, exc.description)), exc.code
    if isinstance(exc, (ValueError, KeyError, GenerationError)):
        return jsonify(error=redact(str(exc).strip("'"))), 400
    app.logger.error('Application error: %s', type(exc).__name__)
    return jsonify(error='处理失败。已保留已有记录，请重试或查看本地运行日志。'), 500


@app.get('/')
def index():
    return workspace_page('index.html')


def workspace_page(filename, active='reading'):
    page = (ROOT / 'static' / filename).read_text()
    prefix = request.script_root
    page = page.replace('<head>', '<head><meta name="app-base" content="' + html.escape(prefix, quote=True) + '">')
    page = page.replace('="/static/', '="' + prefix + '/static/')
    if prefix:
        page = page.replace('PaperLens · 论文精读工作台', 'ResearchForge · 论文精读')
        page = page.replace('<span class="brand-mark">P<span>l</span></span><span>PaperLens<small>论文精读工作台</small></span>',
                            '<span class="brand-mark">R<span>f</span></span><span>ResearchForge<small>研究工作台</small></span>')
        page = page.replace('支持任意兼容 Chat Completions 的接口。', '与研究构想共用地址与密钥；模型与额度仅用于精读。')
        page = page.replace('API 设置</button>', '共享 API 设置</button>')
    def link(href, label, icon, current):
        return ('<a href="' + href + '"' + (' aria-current="page"' if current else '') + '>' +
                ('<i class="nav-pill" aria-hidden="true"></i>' if current else '') +
                '<svg class="icon" aria-hidden="true"><use href="/ui/icons.svg#i-' + icon + '"/></svg><span>' + label + '</span></a>')
    nav = '<nav class="workspace-modules" aria-label="工作台功能">' + (link('/', '研究构想', 'lightbulb', False) if prefix else '')
    nav += link(prefix + '/', '论文精读', 'book-open-text', active == 'reading')
    nav += link(prefix + '/memory', '文献地图', 'waypoints', active == 'memory')
    nav += link(prefix + '/prompt-library', '提示词库', 'library-big', active == 'prompts') + '</nav>'
    page = page.replace('<!-- workspace-navigation -->', nav)
    if not prefix:
        # Running alone there is no workspace guide page to link to.
        page = page.replace('<a class="rf-utility-link" href="/setup">', '<a class="rf-utility-link" hidden href="/setup">')
    return page


prompt_library.install(app, workspace_page)
SHARED_UI = ROOT.parent / 'shared_ui'
SHARED_UI_TYPES = {'.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.svg': 'image/svg+xml'}


@app.get('/ui/<path:name>')
def shared_ui(name):
    # Used only when the reader runs on its own; mounted pages load /ui from the workspace.
    path = (SHARED_UI / name).resolve()
    mimetype = SHARED_UI_TYPES.get(path.suffix.lower())
    if not mimetype or not path.is_file() or not path.is_relative_to(SHARED_UI.resolve()):
        return jsonify(error='未找到界面资源。'), 404
    response = send_file(path, mimetype=mimetype)
    response.headers['Cache-Control'] = 'no-cache'
    return response


@app.get('/api/health')
def health():
    return jsonify(app='paperlens', version='1.6.0', integrated=bool(request.script_root))


@app.get('/memory')
def memory_index():
    return workspace_page('memory.html','memory')


@app.get('/api/memory')
def memory_overview():
    papers=[]
    for p in memory.all_papers():
        brief={k:p.get(k) for k in ('id','title','authors','year','journal','created','updated','version_count','classification_status','classification_error','reviewed','tags')}
        brief['locations']=[{k:x[k] for k in ('field','topic','question')} for x in p['locations']]
        brief['research_question']=p['research_question'][:300];brief['contribution']=p['contribution'][:300]
        papers.append(brief)
    with memory_jobs.LOCK:active=len(memory_jobs.ACTIVE)
    return jsonify(papers=papers,edges=[e for e in memory.edges() if e['status']!='rejected'],active=active)


@app.get('/api/memory/papers/<pid>')
def memory_detail(pid):
    p=memory.get(valid_id(pid))
    return jsonify(paper=p,versions=[{k:v.get(k) for k in ('id','created','model','filename','document_id')} for v in memory.versions(pid)],edges=memory.edges(pid))


@app.get('/api/memory/versions/<job_id>')
def memory_version(job_id):
    pid,v=memory.version(valid_id(job_id))
    return jsonify(paper_id=pid,version={k:v.get(k) for k in ('id','created','model','research','analysis_basis','filename','document_id','report','source_research')},html=render_report(v['report']))


@app.post('/api/memory/papers/<pid>')
def edit_memory(pid):
    data=request.get_json()
    if not isinstance(data,dict):raise ValueError('请提交有效的文献卡片。')
    return jsonify(paper=memory.change(valid_id(pid),data,expected_revision=data.get('revision')))


@app.post('/api/memory/papers/<pid>/classify')
def classify_memory(pid):
    memory.get(valid_id(pid))
    return jsonify(queued=memory_jobs.schedule(pid,explicit=True)),202


@app.post('/api/memory/edges')
def edit_memory_edge():
    return jsonify(id=memory.save_edge(request.get_json()))


@app.post('/api/memory/taxonomy')
def edit_memory_taxonomy():
    data=request.get_json()
    if not isinstance(data,dict):raise ValueError('分类格式无效。')
    return jsonify(updated=memory.rename_category(data))


@app.get('/api/memory/backup')
def backup_memory():
    return send_file(memory.export_bundle(),mimetype='application/zip',as_attachment=True,
                     download_name=time.strftime('文献记忆备份_%Y%m%d_%H%M%S.zip'))


@app.post('/api/memory/restore')
def restore_memory():
    request.max_content_length=256*1024*1024
    upload=request.files.get('file')
    if not upload:raise ValueError('请选择文献记忆 ZIP 备份。')
    with memory_jobs.LOCK:
        if memory_jobs.ACTIVE:raise ValueError('有文献正在自动定位，请完成后再恢复备份。')
        with storage.DB_LOCK:
            if memory.all_papers():
                folder=DATA/'memory_backups';folder.mkdir(exist_ok=True,mode=0o700)
                path=folder/('before_restore_'+uuid.uuid4().hex+'.zip');path.write_bytes(memory.export_bundle().getvalue());path.chmod(0o600)
            return jsonify(added=memory.restore_bundle(upload.read()))


@app.get('/api/library/status')
def library_status():
    return jsonify(library.status())


@app.get('/api/library/search')
def library_search():
    return jsonify(library.search(request.args.get('q', ''), scope=request.args.get('scope', 'all'),
                                  language=request.args.get('language', ''), year_from=request.args.get('year_from', ''),
                                  year_to=request.args.get('year_to', ''), page=request.args.get('page', '1')))


@app.get('/api/library/papers/<path:uid>')
def library_paper(uid):
    record = library.paper(uid)
    result = extract_document(Path(record['path']), record['filename'])
    return jsonify(paper=record, preview=result['text'][:60000], chars=result['chars'], pages=result.get('pages'),
                   preview_limited=len(result['text'])>60000, warnings=result['warnings'])


@app.post('/api/library/import')
def library_import():
    data = request.get_json()
    if not isinstance(data, dict):
        raise ValueError('请选择要载入的论文。')
    record = library.paper(data.get('uid'))
    doc=import_library_record(record)
    return jsonify({**{k:v for k,v in doc.items() if k!='text'},'preview':doc['text'][:12000]})


def import_library_record(record,origin=None):
    original = Path(record['path'])
    doc_id = uuid.uuid4().hex
    source = DOCS / (doc_id + original.suffix.lower())
    try:
        shutil.copyfile(original, source)
        source.chmod(0o600)
        result = extract_document(source, original.name)
        doc = dict(id=doc_id, filename=original.name, suffix=original.suffix.lower(), created=time.time(),
                   source_library={'uid': record['uid'], 'path': record['path'], 'title': record['title']}, **result)
        if origin:doc['source_research']=origin
        atomic_json(DOCS / (doc_id + '.json'), doc)
    except Exception:
        source.unlink(missing_ok=True)
        raise
    return doc


def bridge_task(task_id):
    loader=app.config.get('RESEARCH_TASK_LOADER')
    if not loader:raise ValueError('请从统一研究工作台打开研究构想与精读连接。')
    try:return loader(str(uuid.UUID(str(task_id))))
    except Exception as exc:raise ValueError('原研究任务暂不可用，请返回研究构想刷新。') from exc


@app.get('/api/bridge/references')
def bridge_references():
    task=bridge_task(request.args.get('task_id'))
    return jsonify(research_bridge.group(task,request.args.get('idea_id'),request.args.get('kind')))


@app.post('/api/bridge/import')
def bridge_import():
    d=request.get_json()
    if not isinstance(d,dict):raise ValueError('请选择研究中的参考文献。')
    task=bridge_task(d.get('task_id'))
    doc,created=research_bridge.import_reference(task,d.get('idea_id'),d.get('kind'),d.get('index'),d.get('uid'),d.get('confirmed') is True,import_library_record,get_doc,DOCS)
    return jsonify(document_id=doc['id'],created=created,reading_url=request.script_root+'/?document='+doc['id']+'#new')


@app.get('/api/settings')
def read_settings():
    return jsonify(public_settings())


@app.post('/api/providers')
def save_provider():
    data = request.get_json()
    if not isinstance(data, dict):
        raise ValueError('配置格式无效。')
    with LOCK:
        config = settings()
        pid = valid_id(data.get('id') or uuid.uuid4().hex)
        old = next((p for p in config['providers'] if p['id'] == pid), {})
        p = {k: data.get(k, old.get(k)) for k in ('name', 'endpoint', 'model', 'thinking', 'max_tokens', 'context_chars', 'direct')}
        p.update(id=pid, api_key=(data.get('api_key') or old.get('api_key', '')).strip())
        if data.get('clear_key'):
            p['api_key'] = ''
        validate_provider(p)
        config['providers'] = [p if q['id'] == pid else q for q in config['providers']]
        if not old:
            config['providers'].append(p)
        if data.get('is_default'):
            config['default_provider'] = pid
        save_settings(config)
    return jsonify(provider=public_provider(p))


@app.post('/api/providers/<provider_id>/test')
def test_provider(provider_id):
    p = provider_by_id(provider_id)
    # A small, explicitly user-triggered connectivity check.
    p['thinking'] = 'disabled' if p['thinking'] != 'auto' else 'auto'
    start = time.monotonic()
    _, usage = complete(p, 'You are a connection test. Reply only OK.', 'Reply OK.', max_tokens=256)
    return jsonify(ok=True, seconds=round(time.monotonic() - start, 1), usage=usage)


@app.post('/api/prompts')
def save_prompts():
    data = request.get_json()
    with LOCK:
        config = settings()
        for key in DEFAULT_PROMPTS:
            value = data.get(key)
            if not isinstance(value, str) or not 30 <= len(value.strip()) <= 80000:
                raise ValueError('提示词需要 30–80,000 个字符。')
        config['prompts'] = {k: data[k].strip() for k in DEFAULT_PROMPTS}
        save_settings(config)
    return jsonify(ok=True)


@app.get('/api/prompts/defaults')
def prompt_defaults():
    return jsonify(DEFAULT_PROMPTS)


@app.post('/api/documents')
def upload_document():
    upload = request.files.get('file')
    if not upload or not upload.filename:
        raise ValueError('请选择要分析的论文。')
    filename = Path(upload.filename.replace('\\', '/')).name[:220]
    suffix = Path(filename).suffix.lower()
    if suffix not in ('.pdf', '.md', '.markdown', '.txt'):
        raise ValueError('支持 PDF、MD、Markdown 和 TXT 文件。')
    doc_id = uuid.uuid4().hex
    source = DOCS / (doc_id + suffix)
    try:
        upload.save(source)
        source.chmod(0o600)
        result = extract_document(source, filename)
        doc = dict(id=doc_id, filename=filename, suffix=suffix, created=time.time(), **result)
        atomic_json(DOCS / (doc_id + '.json'), doc)
    except Exception:
        source.unlink(missing_ok=True)
        raise
    return jsonify({**{k: v for k, v in doc.items() if k != 'text'}, 'preview': doc['text'][:12000]})


@app.get('/api/documents/<doc_id>')
def document(doc_id):
    d = get_doc(doc_id)
    return jsonify(d)


@app.get('/api/documents/<doc_id>/source')
def document_source(doc_id):
    d = get_doc(doc_id)
    return send_file(DOCS / (d['id'] + d['suffix']), as_attachment=True, download_name=d['filename'])


def brief(job):
    return {k: job.get(k) for k in ('id', 'title', 'created', 'updated', 'status', 'stage', 'message', 'progress', 'model', 'filename')}


@app.get('/api/jobs')
def list_jobs():
    with memory_jobs.LOCK:classifying=len(memory_jobs.ACTIVE)
    return jsonify(jobs=[brief(j) for j in storage.all_jobs()],memory_active=classifying)


@app.get('/api/jobs/<job_id>')
def read_job(job_id):
    j = storage.get(valid_id(job_id))
    public = {k: v for k, v in j.items() if k not in ('evidence', 'snapshot')}
    public['html'] = render_report(j.get('report') or j.get('draft', ''))
    public['snapshot'] = {'provider': j['snapshot']['provider'], 'prompts': j['snapshot']['prompts']}
    return jsonify(public)


@app.post('/api/jobs')
def create_job():
    data = request.get_json()
    doc = get_doc(data.get('document_id', ''))
    provider = provider_by_id(data.get('provider_id'))
    if not provider.get('api_key'):
        raise ValueError('请先在 API 设置中填写密钥。')
    research = str(data.get('research', '')).strip()
    if len(research) > 20000:
        raise ValueError('研究说明最多 20,000 字符。')
    jid = uuid.uuid4().hex
    job = dict(id=jid, document_id=doc['id'], title=doc.get('source_library', {}).get('title') or Path(doc['filename']).stem, filename=doc['filename'],
               created=time.time(), updated=time.time(), status='queued', stage='等待分析', progress=0,
               message='已进入分析队列。', model=provider['model'], research=research, report='', draft='',
               error='', warnings=doc['warnings'], checks=[], evidence={}, usage=[],
               snapshot={'provider': public_provider(provider), 'prompts': settings()['prompts']})
    if doc.get('source_research'):job['source_research']=copy.deepcopy(doc['source_research'])
    storage.create(job)
    schedule(jid, provider)
    return jsonify(id=jid), 202


@app.post('/api/jobs/<job_id>/stop')
def stop_job(job_id):
    j = storage.get(valid_id(job_id))
    if j['status'] in ('queued', 'running', 'stopping'):
        with RUN_LOCK:
            STOPS.setdefault(job_id, threading.Event()).set()
        storage.update(job_id, status='stopping', message='停止请求已发出；当前响应有新数据或超时后结束，已返回内容会保留。')
    return jsonify(ok=True)


@app.post('/api/jobs/<job_id>/resume')
def resume_job(job_id):
    j = storage.get(valid_id(job_id))
    if j['status'] not in ('failed', 'interrupted', 'stopped'):
        raise ValueError('只有失败、中断或停止的任务可以继续。')
    p = provider_by_id(j['snapshot']['provider']['id'])
    old = j['snapshot']['provider']
    if p['endpoint'] != old['endpoint'] or p['model'] != old['model']:
        raise ValueError('此 API 的地址或模型已更改。请使用“重新分析”创建新报告，保留原记录。')
    if not p.get('api_key'):
        raise ValueError('请先配置 API 密钥。')
    schedule(job_id, p)
    return jsonify(ok=True), 202


def schedule(jid, provider):
    with RUN_LOCK:
        if jid in ACTIVE:
            raise ValueError('该分析仍在运行，请等待结束。')
        ACTIVE.add(jid)
        STOPS[jid] = threading.Event()
        storage.update(jid, status='queued', error='', message='等待开始；已完成的分段证据将复用。')
        POOL.submit(run_job, jid, copy.deepcopy(provider))


def call_for_job(jid, p, system, user, final=False, token_limit=None):
    if len(system) + len(user) > p['context_chars']:
        raise GenerationError('完整输入超过当前字符预算，未截断正文。请提高 API 设置中的输入预算后继续，或拆分文档。')
    def update(content, reasoning, usage):
        values = dict(generated_chars=len(content), reasoning_chars=reasoning)
        if final:
            values['draft'] = content
        else:
            values['chunk_partial'] = content
        storage.update(jid, **values)
    result, usage = complete(p, system, user, update, STOPS[jid].is_set, max_tokens=token_limit)
    j = storage.get(jid)
    storage.update(jid, usage=j['usage'] + [usage])
    return result


def run_job(jid, provider):
    try:
        if STOPS[jid].is_set():
            raise Stopped()
        j = storage.get(jid)
        doc = get_doc(j['document_id'])
        prompts = j['snapshot']['prompts']
        storage.update(jid, status='running', stage='读取全文', progress=8, message='正在准备带有原文定位的论文材料。')
        prefix = ('文件：' + doc['filename'] + '\n提取说明：' + '；'.join(doc['warnings']) +
                  '\n用户研究说明：\n' + (j['research'] or '未提供') + '\n')
        prior_memory=memory.reading_context(doc)
        if prior_memory:
            context=json.dumps(prior_memory,ensure_ascii=False)
            if len(context)<provider['context_chars']//5:
                prefix+='\n<已读文献记忆>\n'+context+'\n</已读文献记忆>\n这些是以往精读的派生笔记，不是当前论文原文，可能有误。仅在相关时辅助文献定位；引用时注明“已读库笔记”与题名作者年份，不能借此补全当前论文的证据、断言引用关系或声称已检索全部文献。\n'
                storage.update(jid,memory_context_ids=[p['id'] for p in prior_memory])
        budget = provider['context_chars'] - len(prompts['report']) - len(prefix) - 4000
        if budget < 2000:
            raise GenerationError('提示词与研究说明已占满输入预算。请缩短提示词或提高 API 输入预算。')
        segmented = len(doc['text']) > budget
        if not segmented:
            material = '分析依据：上传论文的全部提取文本，图表覆盖受上述提取限制。\n\n' + doc['text']
        else:
            chunk_budget = min(budget, provider['context_chars'] - len(prompts['extract']) - 2000)
            if chunk_budget < 2000:
                raise GenerationError('证据提示词过长，无法容纳正文，请调整提示词或输入预算。')
            chunks = split_text(doc['text'], chunk_budget)
            if len(chunks) > 40:
                raise GenerationError('全文需要超过 40 个分段。请提高单次输入预算或拆分文档。')
            evidence = j['evidence']
            notes = []
            for i, chunk in enumerate(chunks, 1):
                if STOPS[jid].is_set():
                    raise Stopped()
                digest = hashlib.sha256((prompts['extract'] + chunk).encode()).hexdigest()
                storage.update(jid, stage=f'精读分段 {i}/{len(chunks)}', progress=10 + int(55 * (i-1)/len(chunks)),
                               message='全文较长，正在逐段保留带有来源定位的证据。', analysis_basis='全文分段证据汇总',
                               chunk_partial='', generated_chars=0, reasoning_chars=0)
                if digest not in evidence:
                    evidence[digest] = call_for_job(jid, provider, prompts['extract'], f'第 {i}/{len(chunks)} 段：\n' + chunk,
                                                   token_limit=min(provider['max_tokens'], 16000))
                    storage.update(jid, evidence=evidence)
                notes.append(f'\n--- 第 {i}/{len(chunks)} 段已核对文本的证据笔记 ---\n' + evidence[digest])
            material = '分析依据：全文共分为 ' + str(len(chunks)) + ' 段逐段提取证据，以下为证据笔记；最终综合未直接重读完整原文，须在报告中明确此限制。\n' + '\n'.join(notes)
        if STOPS[jid].is_set():
            raise Stopped()
        storage.update(jid, stage='撰写精读报告', progress=72, message='正在一次生成完整报告：APA 引用、理论与假说、详细分析、生活故事和研究对比。',
                       draft='', generated_chars=0, reasoning_chars=0,
                       analysis_basis='全文分段证据汇总' if segmented else '全部提取文本')
        result = call_for_job(jid, provider, prompts['report'], prefix + '\n<论文材料>\n' + material + '\n</论文材料>', final=True)
        result = report_workflow.clean_presentation(result)
        checks = report_workflow.checks(result, expanded='## 15.' in prompts['report'])
        storage.update(jid, status='completed', stage='分析完成', progress=100, report=result, draft='', checks=checks,
                       message='报告已保存。重要判断请结合原文核对。', error='', chunk_partial='')
        try:
            pid,new=memory.remember(storage.get(jid),doc)
            if new:memory_jobs.schedule(pid,provider)
        except Exception:
            # A classification/storage failure must never discard a completed report.
            storage.update(jid,message='报告已保存，文献地图入库待恢复；下次启动会重新检查入库。')
    except Stopped:
        storage.update(jid, status='stopped', stage='已停止', message='已保存分段证据和已返回的报告内容。继续会重新请求尚未完成的步骤。')
    except Exception as exc:
        storage.update(jid, status='failed', stage='分析未完成', error=redact(exc), message='已保留已完成证据和部分正文；没有自动重复请求。')
    finally:
        with RUN_LOCK:
            ACTIVE.discard(jid)


@app.get('/api/jobs/<job_id>/export/<kind>')
def export_job(job_id, kind):
    j = storage.get(valid_id(job_id))
    return export_report(j,kind)


@app.get('/api/memory/versions/<job_id>/export/<kind>')
def export_memory_version(job_id,kind):
    pid,v=memory.version(valid_id(job_id))
    return export_report({**v,'status':'completed','title':memory.get(pid)['title']},kind)


def export_report(j,kind):
    text = j.get('report') or j.get('draft') or ''
    if not text:
        raise ValueError('报告还没有可导出的正文。')
    if j['status'] != 'completed':
        text = '> 注意：这是一份未完成的分析草稿。\n\n' + text
    from io import BytesIO
    if kind == 'md':
        content, mime, ext = text, 'text/markdown; charset=utf-8', '.md'
    elif kind == 'html':
        css = (ROOT / 'static/report.css').read_text()
        math_css, math_js = embedded_math_assets()
        content = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                   '<title>' + html.escape(j['title']) + '</title><style>' + css + '</style>' + math_css + '<body><article class="report standalone">' + render_report(text) + '</article>' + math_js + '</body></html>')
        mime, ext = 'text/html; charset=utf-8', '.html'
    else:
        raise ValueError('请选择 Markdown 或 HTML 格式。')
    return send_file(BytesIO(content.encode()), mimetype=mime, as_attachment=True, download_name=j['title'] + '_精读报告' + ext)


def serve():
    from werkzeug.serving import make_server
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    settings()
    memory_jobs.start()
    # Prefer the previous address after an update so an open tab can be refreshed.
    port = 0
    try:
        last_url = json.loads((DATA / 'server.json').read_text())['url']
        if urlsplit(last_url).hostname == '127.0.0.1':
            port = urlsplit(last_url).port or 0
    except (OSError, ValueError, KeyError):
        pass
    try:
        server = make_server('127.0.0.1', port, app, threaded=True)
    except (OSError, SystemExit):
        server = make_server('127.0.0.1', 0, app, threaded=True)
    atomic_json(DATA / 'server.json', dict(url=f'http://127.0.0.1:{server.server_port}', app='paperlens'))
    server.serve_forever()


if __name__ == '__main__':
    serve()
