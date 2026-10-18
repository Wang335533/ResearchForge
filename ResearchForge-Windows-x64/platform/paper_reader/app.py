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
import chatgpt_oauth
import chatgpt_models as model_catalog
import markdown
from flask import Flask, jsonify, redirect, request, send_file
from werkzeug.exceptions import HTTPException

if __package__:
    from . import storage, library, memory, memory_jobs, research_bridge, prompt_library, report_workflow
    from . import reading_catalog, reading_knowledge, reading_queue, reading_sync, projects
    from .configuration import DATA, ROOT, LOCK, atomic_json, public_provider, public_settings, provider_ready, redact, save_settings, settings, validate_provider
    from .documents import extract_document, split_text
    from .llm import GenerationError, OutputLimitError, Stopped, complete
    from .prompts import DEFAULT_PROMPTS
else:
    import storage
    import library
    import memory
    import memory_jobs
    import research_bridge
    import prompt_library
    import report_workflow
    import reading_catalog
    import reading_knowledge
    import reading_queue
    import reading_sync
    import projects
    from configuration import DATA, ROOT, LOCK, atomic_json, public_provider, public_settings, provider_ready, redact, save_settings, settings, validate_provider
    from documents import extract_document, split_text
    from llm import GenerationError, OutputLimitError, Stopped, complete
    from prompts import DEFAULT_PROMPTS

app = Flask(__name__, static_folder=str(ROOT / 'static'))
app.config['MAX_CONTENT_LENGTH'] = 40 * 1024 * 1024
DOCS = DATA / 'documents'
DOCS.mkdir(exist_ok=True, mode=0o700)
storage.initialize()
projects.initialize()
memory.initialize()
research_bridge.initialize()
POOL = ThreadPoolExecutor(max_workers=2)
ACTIVE = set()
STOPS = {}
RUN_LOCK = threading.RLock()
TAGS = set(bleach.sanitizer.ALLOWED_TAGS) | {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'br', 'hr', 'pre', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'span', 'div', 'sup', 'sub'}


TABLE_RULE = re.compile(r'^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$')


def isolate_tables(text):
    """Python-Markdown only parses a table set off by blank lines; model output often runs one into a paragraph."""
    lines, out, fenced = text.split('\n'), [], False
    for i, line in enumerate(lines):
        if line.lstrip().startswith(('```', '~~~')):
            fenced = not fenced
        row = not fenced and line.lstrip().startswith('|')
        previous = out[-1] if out else ''
        if row and previous.strip() and not previous.lstrip().startswith('|') and i + 1 < len(lines) and '|' in lines[i + 1] and TABLE_RULE.match(lines[i + 1]):
            out.append('')
        elif not fenced and not row and line.strip() and previous.lstrip().startswith('|'):
            out.append('')
        out.append(line)
    return '\n'.join(out)


def wrap_tables(html_text):
    """Each table scrolls inside its own frame; the column count lets wide tables keep readable columns."""
    def wrap(match):
        columns = min(10, len(re.findall(r'<th[\s>]', match[0].split('</tr>', 1)[0])))
        return '<div class="table-wrap cols-' + str(columns) + '">' + match[0] + '</div>'
    return re.sub(r'<table>.*?</table>', wrap, html_text, flags=re.S)


def render_report(text):
    rendered = markdown.markdown(isolate_tables(text), extensions=['tables', 'fenced_code', 'sane_lists', 'pymdownx.arithmatex'],
                                 extension_configs={'pymdownx.arithmatex': {'generic': True},
                                                    'tables': {'use_align_attribute': True}})  # style= would be stripped
    return bleach.clean(wrap_tables(rendered), tags=TAGS, attributes={'a': ['href', 'title'], 'th': ['align'], 'td': ['align'],
                        'span': ['class'], 'div': ['class']}, protocols=['http', 'https', 'mailto'], strip=True)


@lru_cache(maxsize=1)
def embedded_math_assets():
    assets = ROOT / 'static/vendor/katex'
    css = (assets / 'katex.min.css').read_text(encoding='utf-8')
    def embed(match):
        font = assets / match.group(1)
        return 'url(data:font/' + font.suffix[1:] + ';base64,' + base64.b64encode(font.read_bytes()).decode() + ')'
    css = re.sub(r'url\((fonts/[^)]+)\)', embed, css)
    js = (assets / 'katex.min.js').read_text(encoding='utf-8') + '\n' + (assets / 'auto-render.min.js').read_text(encoding='utf-8')
    js += '\nrenderMathInElement(document.querySelector(".report"),{throwOnError:false,trust:false,strict:"ignore"});'
    return '<style>' + css + '</style>', '<script>' + js.replace('</script', '<\\/script') + '</script>'


def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', value):
        raise ValueError('无效的记录标识。')
    return value


def get_doc(doc_id):
    path = DOCS / (valid_id(doc_id) + '.json')
    if not path.exists():
        raise KeyError('未找到上传文档，请重新上传。')
    return json.loads(path.read_text(encoding='utf-8'))


def provider_by_id(provider_id):
    p = next((p for p in settings()['providers'] if p['id'] == provider_id), None)
    if not p:
        raise ValueError('所选 API 配置不存在，请重新选择。')
    return p


@app.before_request
def local_only():
    if urlsplit('http://' + request.host).hostname not in ('127.0.0.1', 'localhost', '::1'):
        return jsonify(error='仅允许从本机访问。'), 403
    if urlsplit('http://' + request.host).hostname == 'localhost' and request.headers.get('Sec-Fetch-Mode') == 'navigate':
        # Windows resolves "localhost" to IPv6 first and stalls on the IPv4-only service; use 127.0.0.1.
        return redirect(request.url.replace('://localhost', '://127.0.0.1', 1), 302)
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        if request.headers.get('X-PaperLens') != '1':
            return jsonify(error='请求来源无效，请刷新页面后重试。'), 403
        origin = request.headers.get('Origin')
        if origin and origin != request.host_url.rstrip('/'):
            return jsonify(error='不允许跨站修改。'), 403


def static_cache_policy(name):
    """Fonts and images are used from the browser cache at once and re-checked in the background, so a page
    switch never flashes fallback text or pops in artwork; styles and scripts are re-checked first, so an
    updated package shows at once."""
    suffix = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    if suffix in ('woff2', 'woff', 'ttf', 'webp', 'png', 'jpg', 'jpeg', 'gif'):
        return 'public, max-age=604800, stale-while-revalidate=2592000'
    if suffix in ('svg', 'ico'):
        return 'public, max-age=0, stale-while-revalidate=604800'
    return 'no-cache'


@app.after_request
def response_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    elif request.path.startswith(('/static/', '/ui/')):
        response.headers['Cache-Control'] = static_cache_policy(request.path)
    return response


@app.errorhandler(Exception)
def error_response(exc):
    if isinstance(exc, HTTPException):
        messages = {413: '文件超过 40 MB，请压缩或拆分后上传。', 404: '未找到所请求的内容。'}
        return jsonify(error=messages.get(exc.code, exc.description)), exc.code
    if isinstance(exc, (ValueError, KeyError, GenerationError, chatgpt_oauth.OAuthError)):
        return jsonify(error=redact(str(exc).strip("'"))), 400
    app.logger.error('Application error: %s', type(exc).__name__)
    return jsonify(error='处理失败。已保留已有记录，请重试或查看本地运行日志。'), 500


@app.get('/')
def index():
    return workspace_page('index.html')


# Page templates carry [[token]] placeholders; the mounted workspace and the standalone
# reader fill them differently instead of rewriting finished sentences.
PAGE_TOKENS = {
    True: dict(product='ResearchForge', product_sub='研究工作台', api_label='共享 API 设置',
               api_hint='与研究构想共用地址与密钥；模型与额度仅用于精读。', guide_hidden=''),
    False: dict(product='PaperLens', product_sub='论文精读工作台', api_label='API 设置',
                api_hint='支持 ChatGPT 套餐授权与兼容 Chat Completions 接口。', guide_hidden=' hidden'),
}
MODULE_LINKS = (('research', '研究构想', 'lightbulb'), ('reading', '论文精读', 'book-open-text'),
                ('memory', '文献地图', 'waypoints'), ('knowledge', '变量与术语', 'ruler'),
                ('prompts', '提示词库', 'library-big'), ('sync', '组会填报', 'table-2'))


def workspace_nav(prefix, active):
    targets = {'research': '/?new=1', 'reading': prefix + '/', 'memory': prefix + '/memory', 'knowledge': prefix + '/knowledge',
               'prompts': prefix + '/prompt-library', 'sync': prefix + '/reading-sync'}
    links = []
    for key, label, icon in MODULE_LINKS:
        if key == 'research' and not prefix:
            continue
        current = key == active
        links.append('<a href="' + html.escape(targets[key], quote=True) + '"' + (' aria-current="page"' if current else '') + '>' +
                     ('<i class="nav-pill" aria-hidden="true"></i>' if current else '') +
                     '<svg class="icon" aria-hidden="true"><use href="/ui/icons.svg#i-' + icon + '"/></svg><span>' + label + '</span></a>')
    return '<nav class="workspace-modules" aria-label="工作台功能">' + ''.join(links) + '</nav>'


def workspace_page(filename, active='reading'):
    page = (ROOT / 'static' / filename).read_text(encoding='utf-8')
    prefix = request.script_root
    page = page.replace('<head>', '<head><meta name="app-base" content="' + html.escape(prefix, quote=True) + '">', 1)
    page = page.replace('="/static/', '="' + prefix + '/static/')
    for key, value in PAGE_TOKENS[bool(prefix)].items():
        page = page.replace('[[' + key + ']]', html.escape(value, quote=False))
    # Pages organised by research project reserve the switcher's place in the markup, so the sidebar
    # never shifts when shell.js fills it in.
    slot = PROJECT_SLOT if active in ('reading', 'memory', 'knowledge') else ''
    return page.replace('<!-- workspace-navigation -->', slot + workspace_nav(prefix, active))


PROJECT_SLOT = ('<div class="rf-project" data-rf-project-slot><button type="button" class="rf-project-current" aria-haspopup="menu" '
                'aria-expanded="false" title="切换研究课题"><span class="rf-project-mark" aria-hidden="true"><svg class="icon"><use href="/ui/icons.svg#i-layers"/></svg></span>'
                '<span class="rf-project-text"><small>当前课题</small><strong data-rf-project-name>&nbsp;</strong><em data-rf-project-meta>&nbsp;</em></span>'
                '<svg class="icon" aria-hidden="true"><use href="/ui/icons.svg#i-chevron-down"/></svg></button><div class="rf-project-menu" role="menu" hidden></div></div>')


prompt_library.install(app, workspace_page)
reading_sync.install(app, workspace_page)
reading_knowledge.install(app, workspace_page, render_report)
projects.install(app)
SHARED_UI = ROOT.parent / 'shared_ui'
SHARED_UI_TYPES = {'.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.svg': 'image/svg+xml',
                   '.woff2': 'font/woff2', '.webp': 'image/webp', '.png': 'image/png', '.json': 'application/json; charset=utf-8'}


@app.get('/ui/<path:name>')
def shared_ui(name):
    # Used only when the reader runs on its own; mounted pages load /ui from the workspace.
    path = (SHARED_UI / name).resolve()
    mimetype = SHARED_UI_TYPES.get(path.suffix.lower())
    if not mimetype or not path.is_file() or not path.is_relative_to(SHARED_UI.resolve()):
        return jsonify(error='未找到界面资源。'), 404
    return send_file(path, mimetype=mimetype, conditional=True)  # caching: response_headers


@app.get('/api/health')
def health():
    return jsonify(app='paperlens', version='1.6.0', integrated=bool(request.script_root))


@app.get('/api/overview')
def overview():
    return jsonify(workspace_overview())


def workspace_overview():
    """Counts for the workspace landing views; reads summaries only, never report bodies."""
    with storage.connect() as db:
        statuses = [r[0] for r in db.execute("SELECT json_extract(payload,'$.status') FROM jobs WHERE coalesce(json_extract(payload,'$.archived'),0)=0")]
        cards = [(json.loads(r[0] or '[]'), r[1]) for r in db.execute(
            "SELECT json_extract(payload,'$.locations'),json_extract(payload,'$.reviewed') FROM memory_papers")]
        confirmed = db.execute("SELECT count(*) FROM memory_edges WHERE json_extract(payload,'$.status')='confirmed'").fetchone()[0]
    fields = {l.get('field') for locations, _ in cards for l in locations if l.get('field') and l.get('field') != '待分类'}
    stats = prompt_library.catalog({'view': 'all'})['stats']
    config = settings()
    default = next((p for p in config['providers'] if p['id'] == config.get('default_provider')), None)
    with memory_jobs.LOCK:
        classifying = len(memory_jobs.ACTIVE)
    return dict(
        reading=dict(total=len(statuses), active=sum(s in ('queued', 'running', 'stopping') for s in statuses),
                     completed=statuses.count('completed')),
        memory=dict(papers=len(cards), fields=len(fields), confirmed_edges=confirmed,
                    unreviewed=sum(not reviewed for _, reviewed in cards), classifying=classifying),
        prompts=dict(total=stats['total'], favorites=stats['favorites']),
        provider=dict(name=default['name'] if default else '', ready=bool(default and provider_ready(default))))


@app.get('/memory')
def memory_index():
    return workspace_page('memory.html','memory')


@app.get('/api/memory')
def memory_overview():
    project=projects.resolve(request.args.get('project'))
    papers=[];cards=memory.all_papers(project);cited=memory.cited_overview(cards)
    for p in cards:
        brief={k:p.get(k) for k in ('id','title','authors','year','journal','created','updated','version_count','classification_status','classification_error','reviewed','tags')}
        brief['project_id']=projects.of(p)
        brief['locations']=[{k:x[k] for k in ('field','topic','question')} for x in p['locations']]
        brief['research_question']=memory.report_digest.preview(p['research_question']);brief['contribution']=memory.report_digest.preview(p['contribution'])
        brief['cited']=cited[p['id']][:40]
        papers.append(brief)
    with memory_jobs.LOCK:active=len(memory_jobs.ACTIVE)
    shown={p['id'] for p in papers}
    return jsonify(papers=papers,edges=[e for e in memory.edges() if e['status']!='rejected' and e['source'] in shown and e['target'] in shown],active=active)


@app.post('/api/memory/papers/<pid>/project')
def move_memory_paper(pid):
    data=request.get_json()
    target=projects.resolve(data.get('project_id') if isinstance(data,dict) else None,allow_all=False)
    return jsonify(moved=memory.move_paper(valid_id(pid),target))


@app.get('/api/memory/papers/<pid>')
def memory_detail(pid):
    p=memory.get(valid_id(pid))
    p['cited']=memory.cited_overview(memory.all_papers(),full=True).get(pid,[])
    # Card text is Markdown (tables, formulas, lists); render it the same way as the report.
    p['html']={k:render_report(p[k]) for k in (*memory.TEXT_FIELDS,'bibliography_evidence') if p.get(k)}
    return jsonify(paper=p,versions=[{k:v.get(k) for k in ('id','created','model','filename','document_id')} for v in memory.versions(pid)],edges=memory.edges(pid))


@app.get('/api/memory/versions/<job_id>')
def memory_version(job_id):
    pid,v=memory.version(valid_id(job_id))
    return jsonify(paper_id=pid,version={k:v.get(k) for k in ('id','created','model','research','analysis_basis','filename','document_id','report','source_research')},
                   html=render_report(report_workflow.presentation_report(v['report'])),audit_html=render_report(v['report']))


@app.post('/api/memory/papers/<pid>')
def edit_memory(pid):
    data=request.get_json()
    if not isinstance(data,dict):raise ValueError('请提交有效的文献卡片。')
    return jsonify(paper=memory.change(valid_id(pid),data,expected_revision=data.get('revision')))


@app.post('/api/memory/papers/<pid>/classify')
def classify_memory(pid):
    memory.get(valid_id(pid))
    # Re-read the latest report first, so the card and citation links are current even if the model call fails.
    memory.refresh_digests(pid)
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
                                  year_to=request.args.get('year_to', ''), page=request.args.get('page', '1'),
                                  local_only=request.args.get('local_only', '1') != '0', source=request.args.get('source', 'auto'),
                                  fuzzy=request.args.get('fuzzy', '1') != '0'))


@app.post('/api/library/reveal')
def reveal_library_file():
    data = request.get_json()
    if not isinstance(data, dict):
        raise ValueError('请选择一篇本地论文。')
    return jsonify(library.reveal(data.get('uid')))


@app.get('/api/library/download')
def download_library_file():
    record = library.paper(request.args.get('uid'), reading=False)
    if not record.get('path'):
        raise ValueError('本机全文库里没有这篇论文的原文。')
    return send_file(record['path'], as_attachment=True, download_name=record['filename'],
                     mimetype='application/octet-stream', conditional=True)


@app.get('/api/library/papers/<path:uid>')
def library_paper(uid):
    record = library.paper(uid)
    if not record.get('path'):
        # A website record without a local file still shows its bibliographic details.
        return jsonify(paper=record, available=False, preview='', chars=0, preview_limited=False, warnings=[])
    result = extract_document(Path(record['path']), record['filename'])
    return jsonify(paper=record, available=True, preview=result['text'][:60000], chars=result['chars'], pages=result.get('pages'),
                   preview_limited=len(result['text'])>60000, warnings=result['warnings'])


@app.post('/api/library/import')
def library_import():
    data = request.get_json()
    if not isinstance(data, dict):
        raise ValueError('请选择要载入的论文。')
    record = library.paper(data.get('uid'))
    if not record.get('path'):
        raise ValueError('本机全文库里没有这篇论文的原文，无法载入。')
    doc=import_library_record(record)
    return jsonify({**{k:v for k,v in doc.items() if k!='text'},'preview':doc['text'][:12000]})


LIBRARY_FIELDS = ('authors', 'journal', 'year', 'volume', 'issue', 'pages', 'doi', 'document_id', 'source')


def import_library_record(record,origin=None):
    original = Path(record['path'])
    doc_id = uuid.uuid4().hex
    source = DOCS / (doc_id + original.suffix.lower())
    try:
        shutil.copyfile(original, source)
        source.chmod(0o600)
        result = extract_document(source, original.name)
        doc = dict(id=doc_id, filename=original.name, suffix=original.suffix.lower(), created=time.time(),
                   source_library={'uid': record['uid'], 'path': record['path'], 'title': record['title'],
                                   **{k: record[k] for k in LIBRARY_FIELDS if record.get(k)}}, **result)
        if origin:doc['source_research']=origin
        atomic_json(DOCS / (doc_id + '.json'), doc)
    except Exception:
        source.unlink(missing_ok=True)
        raise
    return doc


def import_queued_file(item, original):
    """Copy a reading-list file into the reader's documents, like an upload."""
    doc_id = uuid.uuid4().hex
    source = DOCS / (doc_id + original.suffix.lower())
    try:
        shutil.copyfile(original, source)
        source.chmod(0o600)
        result = extract_document(source, item['filename'])
        doc = dict(id=doc_id, filename=item['filename'], suffix=original.suffix.lower(), created=time.time(),
                   source_queue={'id': item['id'], 'title': item['title']}, **result)
        if item.get('library_uid'):
            doc['source_library'] = {'uid': item['library_uid'], 'title': item['title']}
        atomic_json(DOCS / (doc_id + '.json'), doc)
        return doc
    except Exception:
        source.unlink(missing_ok=True)
        raise


reading_catalog.install(app)
reading_queue.install(app, workspace_page, import_queued_file, get_doc)


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


@app.get('/api/chatgpt/status')
def chatgpt_status():
    return jsonify(chatgpt_oauth.status())


@app.post('/api/chatgpt/login')
def chatgpt_login():
    data = request.get_json(silent=True) or {}
    return jsonify(chatgpt_oauth.start_login(data.get('client_id'), data.get('reconsent') is True))


@app.post('/api/chatgpt/select')
def chatgpt_select():
    chatgpt_oauth.select_account((request.get_json(silent=True) or {}).get('client_id'))
    return jsonify(ok=True)


@app.post('/api/chatgpt/logout')
def chatgpt_logout():
    return jsonify(chatgpt_oauth.sign_out())


@app.get('/api/chatgpt/models')
def chatgpt_models():
    return jsonify(model_catalog.catalog(refresh=request.args.get('refresh') == '1'))


@app.post('/api/providers')
def save_provider():
    data = request.get_json()
    if not isinstance(data, dict):
        raise ValueError('配置格式无效。')
    with LOCK:
        config = settings()
        pid = valid_id(data.get('id') or uuid.uuid4().hex)
        old = next((p for p in config['providers'] if p['id'] == pid), {})
        if old.get('auth_type') == 'chatgpt_oauth':
            if data.get('is_default'):
                config['default_provider'] = pid
            save_settings(config)
            return jsonify(provider=public_provider(old))
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
    archived = request.args.get('archived') == '1'
    project = projects.resolve(request.args.get('project'))
    return jsonify(jobs=storage.summaries(archived=archived, project=project), archived_count=storage.archived_count(project),
                   memory_active=classifying)


@app.post('/api/jobs/<job_id>/project')
def move_job(job_id):
    """Move a reading, with its literature card and every other reading of that card, to another project."""
    data = request.get_json()
    target = projects.resolve(data.get('project_id') if isinstance(data, dict) else None, allow_all=False)
    storage.get(valid_id(job_id))
    return jsonify(moved=memory.move_job(job_id, target))


@app.post('/api/jobs/<job_id>/archive')
def archive_job(job_id):
    """Hide a finished reading from the history (reversible); the literature map keeps its card."""
    data = request.get_json(silent=True) or {}
    if type(data.get('archived', True)) is not bool:
        raise ValueError('archived 需为 true 或 false。')
    with RUN_LOCK:
        job = storage.get(valid_id(job_id))
        if job['status'] in ('queued', 'running', 'stopping') or job_id in ACTIVE:
            raise ValueError('分析还在进行，请先停止再移出。')
        job = storage.update(job_id, archived=data.get('archived', True))
    return jsonify(job=brief(job) | {'archived': bool(job.get('archived'))})


@app.get('/api/jobs/<job_id>')
def read_job(job_id):
    j = storage.get(valid_id(job_id))
    public = {k: v for k, v in j.items() if k not in ('evidence', 'snapshot', 'report_input', 'report_cursor',
                                                    'report_partial', 'evidence_splits', 'previous_drafts')}
    # The reading copy hides line/page locators; the saved report keeps them for checking.
    raw = j.get('report') or j.get('draft', '')
    public['display_report'] = report_workflow.presentation_report(raw)
    public['html'] = render_report(public['display_report'])
    if j['status'] not in ('queued', 'running', 'stopping'):
        public['audit_html'] = render_report(raw)  # not re-rendered on every progress poll
    public['snapshot'] = {'provider': j['snapshot']['provider'], 'prompts': j['snapshot']['prompts']}
    return jsonify(public)


@app.post('/api/jobs')
def create_job():
    data = request.get_json()
    if not isinstance(data, dict):
        raise ValueError('请提交有效的精读请求。')
    doc = get_doc(data.get('document_id', ''))
    provider = provider_by_id(data.get('provider_id'))
    if provider.get('auth_type') == 'chatgpt_oauth':
        defaults = model_catalog.catalog()['defaults']
        provider.update(model_catalog.validate_selection(data.get('model', defaults['model']),
                        data.get('reasoning_effort', defaults['reasoning_effort'])))
    if not provider_ready(provider):
        raise ValueError('请先在共享 API 设置中完成登录授权或配置密钥。')
    research = str(data.get('research', '')).strip()
    if len(research) > 20000:
        raise ValueError('研究说明最多 20,000 字符。')
    project = projects.resolve(data.get('project_id'), allow_all=False)
    try:
        jid = uuid.UUID(data['request_id']).hex if 'request_id' in data else uuid.uuid4().hex
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError('提交编号格式无效，请刷新页面后重试。') from exc
    title = doc.get('source_queue', {}).get('title') or doc.get('source_library', {}).get('title') or Path(doc['filename']).stem
    job = dict(id=jid, document_id=doc['id'], title=title, filename=doc['filename'],
               created=time.time(), updated=time.time(), status='queued', stage='等待分析', progress=0,
               message='排队中', model=provider['model'], research=research, project_id=project, report='', draft='',
               error='', warnings=doc['warnings'], checks=[], evidence={}, usage=[],
               snapshot={'provider': public_provider(provider), 'prompts': settings()['prompts']})
    if doc.get('source_research'):job['source_research']=copy.deepcopy(doc['source_research'])
    signature = dict(document_id=doc['id'], research=research, project_id=project, provider_id=provider['id'],
                     model=provider['model'], reasoning_effort=provider.get('reasoning_effort'))
    job['request_signature'] = hashlib.sha256(json.dumps(signature, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with RUN_LOCK:
        _, created = storage.create_once(job)
        if created:
            schedule(jid, provider)
    return jsonify(id=jid, created=created), 202 if created else 200


@app.post('/api/jobs/<job_id>/stop')
def stop_job(job_id):
    valid_id(job_id)
    with RUN_LOCK:
        j = storage.request_stop(job_id)
        if j['status'] == 'stopping':
            STOPS.setdefault(job_id, threading.Event()).set()
    return jsonify(ok=True)


@app.post('/api/jobs/<job_id>/resume')
def resume_job(job_id):
    j = storage.get(valid_id(job_id))
    if j['status'] not in ('failed', 'interrupted', 'stopped'):
        raise ValueError('只有失败、中断或停止的任务可以继续。')
    p = provider_by_id(j['snapshot']['provider']['id'])
    old = j['snapshot']['provider']
    if p.get('auth_type') == 'chatgpt_oauth':
        p.update(model_catalog.validate_selection(old['model'], old.get('reasoning_effort')))
    if p['endpoint'] != old['endpoint'] or p['model'] != old['model'] or p.get('reasoning_effort') != old.get('reasoning_effort'):
        raise ValueError('接口设置已变化，无法接着原来的继续。请点“重新分析”生成新报告（原记录保留）。')
    if not provider_ready(p):
        raise ValueError('请先完成登录授权或配置 API 密钥。')
    schedule(job_id, p)
    return jsonify(ok=True), 202


def schedule(jid, provider):
    with RUN_LOCK:
        if jid in ACTIVE:
            raise ValueError('该分析仍在运行，请等待结束。')
        ACTIVE.add(jid)
        STOPS[jid] = threading.Event()
        storage.update(jid, status='queued', error='', message='即将开始，已完成的证据和小节不会重做')
        POOL.submit(run_job, jid, copy.deepcopy(provider))


def call_for_job(jid, p, system, user, final=False, token_limit=None, prefix=''):
    if STOPS[jid].is_set():
        raise Stopped('已停止')
    if len(system) + len(user) > p['context_chars']:
        raise GenerationError('正文超出单次输入上限（没有截断）。请在 API 设置里调高“单次输入字符预算”，或拆分文档后再试。')
    observed, outcome = {}, 'failed'
    def update(content, reasoning, usage):
        observed.update(usage or {})
        values = dict(generated_chars=len(content), reasoning_chars=reasoning)
        if final:
            values['draft'] = prefix + content
            values['report_partial'] = content
        else:
            values['chunk_partial'] = content
        storage.update(jid, **values)
    try:
        result, usage = complete(p, system, user, update, STOPS[jid].is_set, max_tokens=token_limit)
        observed.update(usage or {})
        outcome = 'completed'
        return result
    except OutputLimitError as exc:
        observed.update(exc.usage)
        outcome = 'length'
        raise
    except Stopped:
        outcome = 'stopped'
        raise
    finally:
        # Every request is recorded, including cut-off and failed ones, so usage is never undercounted.
        j = storage.get(jid)
        storage.update(jid, usage=j['usage'] + [{**observed, '_status': outcome, '_stage': j.get('stage', ''),
                                                 '_usage_reported': bool(observed)}])


def extract_evidence(jid, provider, system, chunk, evidence, splits, depth=0):
    """Cache each finished piece; a confirmed over-long piece is halved, never re-sent whole."""
    if STOPS[jid].is_set():
        raise Stopped()
    digest = hashlib.sha256((system + chunk).encode()).hexdigest()
    if digest in evidence:
        return evidence[digest]
    if digest not in splits:
        try:
            note = call_for_job(jid, provider, system, '<论文片段>\n' + chunk + '\n</论文片段>',
                                token_limit=min(provider['max_tokens'], 48000))
            evidence[digest] = note
            storage.update(jid, evidence=evidence)
            return note
        except OutputLimitError:
            if depth >= 3 or len(chunk) < 3000:
                raise GenerationError('这一小段的证据提取仍达到输出上限，进度已保存。请在 API 设置提高输出额度后点“继续”。')
            splits[digest] = max(1500, len(chunk) // 2)
            storage.update(jid, evidence_splits=splits, message='这一段证据太长，已自动拆小；此前提取的证据都保留')
    notes = [extract_evidence(jid, provider, system, child, evidence, splits, depth + 1)
             for child in split_text(chunk, splits[digest])]
    result = '\n\n'.join(notes)
    evidence[digest] = result
    storage.update(jid, evidence=evidence)
    return result


def fit_evidence(jid, provider, material, budget, evidence):
    """Bound the final input without silently dropping any source segment."""
    compressed = False
    system = ('你在合并论文的派生证据笔记，不是在阅读原文。压缩重复表述，保留来源段号、页码、'
              '变量定义、研究设计、关键结果及数值、限制和不确定性。不得新增事实、引用或因果结论。'
              '冲突和缺失必须保留。输出中文结构化证据笔记，严格遵守用户给出的字符目标。')
    for level in range(1, 4):
        if len(material) <= budget:
            return material, compressed
        parts = split_text(material, min(budget, provider['context_chars'] - len(system) - 1000))
        if len(parts) > 40:
            raise GenerationError('证据笔记需超过 40 批合并，已保留笔记并停止新增请求。请拆分论文后分析。')
        target = max(500, budget // (2 * len(parts)))
        merged = []
        for i, part in enumerate(parts, 1):
            if STOPS[jid].is_set():
                raise Stopped()
            user = f'本批 {i}/{len(parts)}，目标不超过 {target} 字符；保留笔记原有来源定位。\n\n' + part
            digest = hashlib.sha256(('merge-v1' + system + user).encode()).hexdigest()
            storage.update(jid, stage=f'合并长文证据 {level}.{i}/{len(parts)}', progress=68,
                           message='证据笔记较多，正在压缩合并（原始笔记都已保存）')
            if digest not in evidence:
                evidence[digest] = call_for_job(jid, provider, system, user,
                                               token_limit=min(provider['max_tokens'], 16000))
                storage.update(jid, evidence=evidence)
            merged.append(evidence[digest])
        reduced = ('分析依据：全文分段证据经过分层压缩，并非完整原文；可能损失细节，'
                   '请在报告中说明限制，重要数值与判断须回查原文。\n' + '\n\n'.join(merged))
        if len(reduced) >= len(material):
            raise GenerationError('模型未能压缩长文证据，已保留全部笔记。请拆分论文后分析，避免反复请求。')
        material, compressed = reduced, True
    if len(material) > budget:
        raise GenerationError('三轮合并后证据仍超过综合预算，已保留全部笔记。请拆分论文后分析。')
    return material, compressed


def prepare_report_input(jid, provider, doc, prompts):
    """Build the report request once; a resumed job reuses it so finished sections stay valid."""
    j = storage.get(jid)
    if j.get('report_input'):
        return j['report_input']
    prefix = ('文件：' + doc['filename'] + '\n提取说明：' + '；'.join(doc['warnings']) +
              '\n用户研究说明：\n' + (j['research'] or '未提供') + '\n')
    prior_memory=memory.reading_context(doc,projects.of(j))
    if prior_memory:
        context=json.dumps(prior_memory,ensure_ascii=False)
        if len(context)<provider['context_chars']//5:
            prefix+='\n<已读文献记忆>\n'+context+'\n</已读文献记忆>\n这些是以往精读的派生笔记，不是当前论文原文，可能有误。仅在相关时辅助文献定位；引用时注明“已读库笔记”与题名作者年份，不能借此补全当前论文的证据、断言引用关系或声称已检索全部文献。\n'
            storage.update(jid,memory_context_ids=[p['id'] for p in prior_memory])
    # Room for the continuation scope and protocol that a resumed section request adds.
    budget = provider['context_chars'] - len(prompts['report']) - len(prefix) - 4000
    if budget < 2000:
        raise GenerationError('提示词与研究说明已占满输入预算。请缩短提示词或提高 API 输入预算。')
    segmented = len(doc['text']) > budget
    compressed = False
    if not segmented:
        material = '分析依据：上传论文的全部提取文本，图表覆盖受上述提取限制。\n\n' + doc['text']
    else:
        extract_prompt = prompts['extract']
        if '【分段笔记的取舍】' not in extract_prompt:
            extract_prompt += '\n\n' + report_workflow.EVIDENCE_GUIDANCE
        # Smaller pieces keep each note faithful; the 80-piece cap bounds paid requests.
        chunk_budget = min(18000, budget, provider['context_chars'] - len(extract_prompt) - 2000)
        if chunk_budget < 2000:
            raise GenerationError('证据提示词过长，无法容纳正文，请调整提示词或输入预算。')
        chunks = split_text(doc['text'], chunk_budget)
        if len(chunks) > 80:
            raise GenerationError('全文需要超过 80 个证据分段。请拆分文档后分别分析。')
        evidence = j['evidence']
        splits = j.get('evidence_splits', {})
        notes = []
        for i, chunk in enumerate(chunks, 1):
            if STOPS[jid].is_set():
                raise Stopped()
            storage.update(jid, stage=f'精读分段 {i}/{len(chunks)}', progress=10 + int(55 * (i-1)/len(chunks)),
                           message='全文较长，正在逐段提取证据；每段完成即保存', analysis_basis='全文分段证据汇总',
                           chunk_partial='', generated_chars=0, reasoning_chars=0)
            note = extract_evidence(jid, provider, extract_prompt, chunk, evidence, splits)
            notes.append(f'\n--- 第 {i}/{len(chunks)} 段已核对文本的证据笔记 ---\n' + note)
        material = '分析依据：全文共分为 ' + str(len(chunks)) + ' 段逐段提取证据，以下为证据笔记；最终综合未直接重读完整原文，须在报告中明确此限制。\n' + '\n'.join(notes)
        material, compressed = fit_evidence(jid, provider, material, budget, evidence)
    analysis_input = prefix + '\n<论文材料>\n' + material + '\n</论文材料>'
    storage.update(jid, report_input=analysis_input, workflow_version='section-resume-v1',
                   analysis_basis=('全文分段证据汇总（分层压缩）' if compressed else '全文分段证据汇总') if segmented else '全部提取文本')
    return analysis_input


def write_report(jid, provider, prompt, user):
    """Write the report; when output is cut off, keep the finished sections and continue the rest."""
    expected = report_workflow.outline(prompt)
    if not expected:
        # Free-form custom prompts keep their own structure and are written in one request.
        storage.update(jid, stage='撰写精读报告', progress=72, message='正在撰写完整报告',
                       draft='', generated_chars=0, reasoning_chars=0, report_partial='')
        return call_for_job(jid, provider, prompt, user, final=True)
    job = storage.get(jid)
    cursor = job.get('report_cursor')
    if cursor and job.get('report_partial'):
        # Recover sections streamed before a crash between a response and its checkpoint.
        try:
            accepted, count = report_workflow.completed_prefix(job['report_partial'], expected[cursor['count']:], False)
            if count:
                cursor.update(done=cursor['done'] + ('\n\n' if cursor['done'] else '') + accepted, count=cursor['count'] + count)
        except ValueError:
            pass
        storage.update(jid, report_cursor=cursor, report_partial='')
    if not cursor:
        done, count = '', 0
        if job.get('draft'):
            # Jobs from before sectioned writing have no cursor: keep their draft and reuse finished sections.
            storage.update(jid, previous_drafts=job.get('previous_drafts', []) + [job['draft']])
            try:
                done, count = report_workflow.completed_prefix(job['draft'], expected, False)
            except ValueError:
                pass
        cursor = dict(done=done, count=count, group_size=len(expected) if not count else 9)
        storage.update(jid, report_cursor=cursor, report_partial='')
    for attempt in range(4):
        if STOPS[jid].is_set():
            raise Stopped()
        remaining = expected[cursor['count']:]
        if not remaining:
            return cursor['done']
        scope = remaining[:cursor['group_size']]
        continuing = cursor['count'] > 0 or len(scope) < len(expected) or attempt > 0
        request_text = user + (report_workflow.continuation_scope(scope, cursor['done']) if continuing else '')
        system = prompt + ('\n\n【程序交付协议】本轮是续写任务，以用户消息中“本次分段交付范围”为唯一输出目录；'
                           '模板中的完整目录用于说明最终报告结构，本轮不重写目录范围之外的章节。' if continuing else '')
        prefix = cursor['done'] + ('\n\n' if cursor['done'] else '')
        storage.update(jid, stage='续写未完成的小节' if continuing else '撰写精读报告',
                       progress=72 + int(25 * cursor['count'] / len(expected)),
                       message=('已完成 ' + str(cursor['count']) + '/' + str(len(expected)) + ' 节，从“' + scope[0].lstrip('# ') + '”接着写'
                                if continuing else '正在撰写完整报告；超出输出上限时会保存写完的小节并自动接着写'),
                       generated_chars=0, reasoning_chars=0, report_partial='')
        limited = False
        try:
            text = call_for_job(jid, provider, system, request_text, final=True, prefix=prefix)
        except OutputLimitError as exc:
            text, limited = exc.content, True
        except (GenerationError, Stopped):
            # After a network failure or stop, a manual resume only rewrites the unfinished section.
            partial = storage.get(jid).get('report_partial', '')
            try:
                accepted, count = report_workflow.completed_prefix(partial, scope, False)
                if count:
                    cursor.update(done=prefix + accepted, count=cursor['count'] + count)
                    storage.update(jid, report_cursor=cursor, report_partial='')
            except ValueError:
                pass
            raise
        storage.update(jid, draft=prefix + text)
        try:
            accepted, count = report_workflow.completed_prefix(text, scope, not limited)
        except ValueError as exc:
            raise GenerationError(str(exc)) from exc
        if count:
            cursor.update(done=prefix + accepted, count=cursor['count'] + count)
        if limited:
            # The next request asks for a smaller, explicit set of sections instead of replaying the report.
            minimum = 2 if cursor['count'] == 0 else 1
            cursor['group_size'] = max(minimum, min(9, (len(scope) - count + 1) // 2))
            if not count and len(scope) <= minimum:
                storage.update(jid, report_cursor=cursor)
                raise GenerationError('已经缩小到单个小节，仍在写完前达到输出上限。此前的小节已保存；请提高输出额度或调整思考模式后继续。')
        elif not count:
            raise GenerationError('本次没有得到符合目录的完整小节，草稿已保留。请检查提示词后继续。')
        storage.update(jid, report_cursor=cursor, report_partial='')
        if cursor['count'] == len(expected):
            return cursor['done']
    raise GenerationError('本轮已连续写了 4 次，仍有小节未完成。写完的小节都已保存，点“继续”可接着写，不必整篇重来。')


def run_job(jid, provider):
    try:
        if STOPS[jid].is_set():
            raise Stopped()
        j = storage.get(jid)
        doc = get_doc(j['document_id'])
        prompts = j['snapshot']['prompts']
        storage.update(jid, status='running', stage='读取全文', progress=8, message='正在读取全文与已保存的进度')
        user = prepare_report_input(jid, provider, doc, prompts)
        if STOPS[jid].is_set():
            raise Stopped()
        result = write_report(jid, provider, prompts['report'], user)
        result = report_workflow.clean_presentation(result)
        checks = report_workflow.checks(result, expanded='## 15.' in prompts['report'], template=prompts['report'])
        storage.update(jid, status='completed', stage='分析完成', progress=100, report=result, draft='', checks=checks,
                       message='报告已完成，重要结论请对照原文核对', error='', chunk_partial='', report_partial='')
        try:
            pid,new=memory.remember(storage.get(jid),doc)
            if new:memory_jobs.schedule(pid,provider)
            try:reading_knowledge.collect(jid)
            except Exception:app.logger.warning('Measurement notebook update deferred; completed report preserved.')
        except Exception:
            # A classification/storage failure must never discard a completed report.
            storage.update(jid,message='报告已保存；加入文献地图未成功，下次启动会自动重试')
        try:
            reading_sync.notify_completed(jid)
        except Exception:
            app.logger.warning('Group-meeting sheet queue unavailable; completed report preserved.')
    except Stopped:
        storage.update(jid, status='stopped', stage='已停止', message='已停止。证据和写完的小节都已保存，继续时只写剩下的部分')
    except Exception as exc:
        storage.update(jid, status='failed', stage='分析未完成', error=redact(exc), message='没有完成。证据、写完的小节和草稿都已保存，可以继续')
    finally:
        with RUN_LOCK:
            ACTIVE.discard(jid)
            STOPS.pop(jid, None)


@app.get('/api/jobs/<job_id>/export/<kind>')
def export_job(job_id, kind):
    j = storage.get(valid_id(job_id))
    return export_report(j,kind)


@app.get('/api/memory/versions/<job_id>/export/<kind>')
def export_memory_version(job_id,kind):
    pid,v=memory.version(valid_id(job_id))
    return export_report({**v,'status':'completed','title':memory.get(pid)['title']},kind)


def export_report(j,kind):
    # Exports are the reading copy; the saved report keeps the source locators for checking.
    text = report_workflow.presentation_report(j.get('report') or j.get('draft') or '')
    if not text:
        raise ValueError('报告还没有可导出的正文。')
    if j['status'] != 'completed':
        text = '> 注意：这是一份未完成的分析草稿。\n\n' + text
    from io import BytesIO
    if kind == 'md':
        content, mime, ext = text, 'text/markdown; charset=utf-8', '.md'
    elif kind == 'html':
        css = (ROOT / 'static/report.css').read_text(encoding='utf-8')
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
    reading_sync.start()
    # Prefer the previous address after an update so an open tab can be refreshed.
    port = 0
    try:
        last_url = json.loads((DATA / 'server.json').read_text(encoding='utf-8'))['url']
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
