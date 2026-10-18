"""Full-text library: bibliographic search plus local file location. No model requests.

With the literature website API configured, search runs online (the API is the
metadata authority) and every record is located in the local file index by DOI,
attachment file name and title. Without the API, or when it is unreachable, the
local index (bibliographic fields from the abstract library, file locator and folder
layout) is searched directly.
"""
import html
import json
import os
import re
import sqlite3
import subprocess
import time
import unicodedata
from contextlib import contextmanager
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import unquote

if __package__:
    from . import literature_api
else:
    import literature_api

LIBRARY_ROOT = Path(os.environ.get('PAPERLENS_LIBRARY_ROOT', Path(__file__).resolve().parents[2] / 'user_data/papers'))
LIBRARY_ROOTS = tuple(Path(p).resolve() for p in json.loads(os.environ.get('PAPERLENS_LIBRARY_ROOTS', '[]'))) or (LIBRARY_ROOT.resolve(),)
INDEX_PATH = Path(os.environ.get('PAPERLENS_LIBRARY_INDEX', Path(__file__).resolve().parents[2] / 'user_data/fulltext-index.sqlite3'))
PAGE_SIZE = 20
SCOPES = ('all', 'title', 'author', 'doi')
FIELDS = ('paper_uid', 'path', 'title', 'title_norm', 'authors', 'journal', 'year', 'volume', 'issue', 'pages',
          'language', 'abstract', 'doi', 'mtime_ns', 'excluded')
UNUSABLE_ASSETS = ('quarantined_conflict', 'deleted_duplicate')


@contextmanager
def connect():
    if not INDEX_PATH.is_file():
        raise ValueError('还没有全文索引，请按使用指南建立后再搜索。')
    db = sqlite3.connect(INDEX_PATH.resolve().as_uri() + '?mode=ro', uri=True, timeout=4)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    deadline = time.monotonic() + 8
    db.set_progress_handler(lambda: time.monotonic() > deadline, 10000)
    try:
        yield db
    except sqlite3.OperationalError as exc:
        if 'interrupt' in str(exc).lower():
            raise ValueError('这个词匹配范围过大，请增加关键词，或切换为“仅题名”后重试。') from exc
        raise ValueError('论文索引暂时不可读，可能正在更新，请稍后刷新。') from exc
    finally:
        db.close()


def select_list(db, alias='p'):
    """Column list that also reads indexes built before authors/volume/pages were stored."""
    have = {r[1] for r in db.execute('PRAGMA table_info(papers)')}
    return ','.join(f'{alias}.{c}' if c in have else f"'' AS {c}" for c in FIELDS)


def status():
    online = literature_api.configured()
    if not any(root.is_dir() for root in LIBRARY_ROOTS) or not INDEX_PATH.is_file():
        return dict(ready=False, count=0, online=online, root=str(LIBRARY_ROOT),
                    message='还没有全文库。可以直接上传论文，或按使用指南建立全文索引。')
    with connect() as db:
        meta = dict(db.execute('SELECT key,value FROM index_meta').fetchall())
        stats = json.loads(meta.get('stats') or '{}')
        if meta.get('available_count', '').isdigit():
            count = int(meta['available_count'])
        elif meta.get('schema') == '2' and all(type(stats.get(k)) is int for k in ('sample_copies', 'non_literature')):
            # Older schema-2 indexes already recorded the two disjoint exclusion
            # counts. COUNT(*) uses a small covering index; WHERE excluded=0
            # would scan every paper's potentially large abstract on each poll.
            count = db.execute('SELECT COUNT(*) FROM papers').fetchone()[0] - stats['sample_copies'] - stats['non_literature']
        else:
            count = db.execute('SELECT COUNT(*) FROM papers WHERE excluded=0').fetchone()[0]
    return dict(ready=count > 0, count=count, online=online, root=str(LIBRARY_ROOT), roots=[str(p) for p in LIBRARY_ROOTS],
                indexed_at=meta.get('indexed_at', ''), metadata_only=meta.get('metadata_only') == '1',
                has_years=meta.get('has_years', '1') == '1', with_authors=stats.get('with_authors'),
                with_abstract=stats.get('with_abstract'), schema=meta.get('schema', '1'),
                message=('在文献网站检索书目，再到本机全文库找到对应原文。' if online else '在本机全文索引中检索题名、作者、期刊与摘要。')
                if count else '全文目录内还没有可用文件，请更新索引。')


def authorized_source(path):
    source = Path(path).resolve()
    return any(source.is_relative_to(root.resolve()) for root in LIBRARY_ROOTS)


def normalized(text):
    return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', text).casefold())


def doi_key(value):
    value = unquote(str(value or '')).strip().casefold()
    value = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', value).rstrip(' .;,')
    return value if re.fullmatch(r'10\.\d{4,9}/\S+', value) else ''


def plain(text):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]*>', ' ', text or ''))).strip()


def excerpt(text, size=260):
    text = plain(text)
    return text[:size] + ('…' if len(text) > size else '')


def public_row(row):
    r = dict(row)
    path = Path(r['path'])
    abstract = plain(r.get('abstract'))
    return dict(uid=r['paper_uid'], title=plain(r['title']), authors=plain(r.get('authors')), year=r.get('year') or None,
                journal=plain(r.get('journal')), volume=r.get('volume') or '', issue=r.get('issue') or '', pages=r.get('pages') or '',
                language=r['language'], filename=path.name, path=str(path), abstract=abstract, excerpt=excerpt(abstract),
                doi=r.get('doi') or '', source='local', local=dict(status='found', uid=r['paper_uid'], filename=path.name))


def search_text(text):
    """Clean text pasted from web pages or PDFs (entities, %-escapes, invisible marks)."""
    text = unicodedata.normalize('NFKC', html.unescape(html.unescape(unquote(str(text)))))
    return ''.join(c for c in text if unicodedata.category(c) != 'Cf')


def main_title(query):
    """The part before a subtitle, when long enough to identify a paper on its own."""
    text = search_text(query).strip()
    value = re.split(r'[:：]|\s[—–-]\s', text, maxsplit=1)[0].strip(' "“”')
    return value if value != text and len(normalized(value)) >= 15 else ''


def similar_titles(db, query, filters, params, exclude, limit):
    """Candidates for a mistyped title: rows sharing most anchor pieces, ranked by similarity.

    Scans only the small title index (no abstracts, no in-memory copy of the library), and is
    shown separately from exact matches. The score is string similarity, not confidence.
    """
    key = normalized(query)
    if len(key) < 8:
        return []
    size = 3 if re.search(r'[一-鿿]', key) else 5
    count = max(3, min(10, len(key) // size))
    step = len(key) / count
    anchors = list(dict.fromkeys(key[int(i * step):int(i * step) + size] for i in range(count)))
    hits = '+'.join('(instr(title_norm,?)>0)' for _ in anchors)
    rows = db.execute(f'SELECT id,title_norm FROM papers INDEXED BY idx_papers_title_norm WHERE {hits}>=? ORDER BY {hits} DESC LIMIT 400',
                      anchors + [max(2, (len(anchors) + 1) // 2)] + anchors).fetchall()
    threshold = .86 if len(key) < 16 else .8
    scored = []
    for pid, title in rows:
        if pid in exclude or not title:
            continue
        score = SequenceMatcher(None, key, title, autojunk=False).ratio()
        if score >= threshold:
            scored.append((score, pid))
    scored.sort(key=lambda x: (-x[0], x[1]))
    if not scored:
        return []
    chosen = scored[:limit]
    found = {r['id']: r for r in db.execute('SELECT p.id,' + select_list(db) + ' FROM papers p WHERE p.id IN (' + ','.join('?' * len(chosen)) +
                                             ') AND ' + ' AND '.join(filters), [pid for _, pid in chosen] + params)}
    return [(found[pid], round(score * 100)) for score, pid in chosen if pid in found]


def parse_terms(query):
    query = search_text(query).strip()
    if not 2 <= len(query) <= 400:
        raise ValueError('请输入 2–400 个字符的题名或关键词。')
    terms = [a or b.strip('"') for a, b in re.findall(r'"([^\"]+)"|(\S+)', query)]
    terms = list(dict.fromkeys(t for t in terms if normalized(t)))
    if not terms or len(terms) > 30:
        raise ValueError('请输入有效题名或关键词，最多 30 个检索词。双引号可用于完整短语。')
    return query, terms


def search(query, scope='all', language='', year_from='', year_to='', page=1, local_only=True, source='auto', fuzzy=True):
    query, terms = parse_terms(query)
    if scope == 'all' and doi_key(query):
        scope = 'doi'  # a pasted DOI needs no scope change
    if scope not in SCOPES or language not in ('', 'zh', 'en') or source not in ('auto', 'online', 'local'):
        raise ValueError('无效的检索范围或语言。')
    try:
        page = int(page)
        yfrom = int(year_from) if year_from else None
        yto = int(year_to) if year_to else None
    except (ValueError, TypeError) as exc:
        raise ValueError('页码与年份需要填写整数。') from exc
    if not 1 <= page <= 100:
        raise ValueError('最多查看前 100 页，请缩小检索范围。')
    if any(y is not None and not 1800 <= y <= 2100 for y in (yfrom, yto)) or (yfrom and yto and yfrom > yto):
        raise ValueError('请填写有效的年份范围。')
    start = time.monotonic()
    fallback = ''
    if source != 'local' and literature_api.configured():
        try:
            return online_search(query, scope, language, yfrom, yto, page, local_only, start)
        except literature_api.ApiError as exc:
            if source == 'online':
                raise ValueError(str(exc)) from exc
            fallback = f'在线书目暂不可用（{exc}），已改用本机索引。'
    result = local_search(query, terms, scope, language, yfrom, yto, page, start, fuzzy)
    result['note'] = ' '.join(filter(None, [fallback, result['note']]))
    return result


# ---------------------------------------------------------------- online: website records, local location
def online_search(query, scope, language, yfrom, yto, page, local_only, start):
    found = literature_api.search(query, scope, language, yfrom, yto, page, PAGE_SIZE, has_pdf=local_only)
    try:
        with connect() as db:
            located = [locate(db, item['doi'], item['title'], ()) for item in found['items']]
    except ValueError:
        located = [[] for _ in found['items']]
    results = [online_row(item, candidates) for item, candidates in zip(found['items'], located)]
    note = ''
    if local_only and not found['pdf_filtered']:
        note = '按题名、作者或 DOI 检索时无法只列有全文的记录，结果已标出本机是否有原文。'
    return dict(results=results, page=page, has_more=page < found['pages'] and page < 100, total=found['total'],
                strategy='文献网站书目' + (' · 仅含有全文的记录' if found['pdf_filtered'] else ''), note=note,
                elapsed_ms=round((time.monotonic() - start) * 1000), query=query, source='online')


def online_row(item, candidates):
    best = candidates[0] if candidates else None
    local = dict(status='found' if best else 'missing', uid=best['uid'] if best else '', filename=best['filename'] if best else '',
                 reasons=best['reasons'] if best else [], candidates=len(candidates))
    return dict(uid='api:' + item['document_id'], document_id=item['document_id'], title=item['title'], authors=item['authors'],
                journal=item['journal'], year=item['year'], volume=item['volume'], issue=item['issue'], pages=item['pages'],
                doi=item['doi'], url=item['url'], language=item['language'], document_type=item['document_type'],
                abstract=item['abstract'], excerpt=excerpt(item['abstract']), keywords=item['keywords'],
                filename=local['filename'], source='online', local=local)


def locate(db, doi, title, stems):
    """Index rows that may hold this record's full text, most reliable first."""
    wanted_doi, wanted_title = doi_key(doi), normalized(title or '')
    stems = [s for s in dict.fromkeys(stems) if s]
    clauses, params = [], []
    if wanted_doi:
        clauses.append('doi=?'); params.append(wanted_doi)
    if len(wanted_title) >= 6:
        clauses.append('title_norm=?'); params.append(wanted_title)
    have = {r[1] for r in db.execute('PRAGMA table_info(papers)')}
    if stems and 'stem_key' in have:
        clauses.append('stem_key IN (' + ','.join('?' * len(stems)) + ')'); params += stems
    if not clauses:
        return []
    rows = db.execute('SELECT ' + select_list(db, 'papers') + (', stem_key' if 'stem_key' in have else '') +
                      ' FROM papers WHERE excluded=0 AND (' + ' OR '.join(clauses) + ') LIMIT 30', params).fetchall()
    candidates = []
    for row in rows:
        r = dict(row)
        row_doi = doi_key(r.get('doi'))
        reasons = []
        if wanted_doi and row_doi == wanted_doi:
            reasons.append('DOI 一致')
        if stems and r.get('stem_key') in stems:
            reasons.append('附件文件名一致')
        if wanted_title and r['title_norm'] == wanted_title:
            reasons.append('题名一致')
        conflict = bool(wanted_doi and row_doi and row_doi != wanted_doi)
        score = 4 * ('DOI 一致' in reasons) + 3 * ('附件文件名一致' in reasons) + 2 * ('题名一致' in reasons) - 6 * conflict
        if score <= 0:
            continue
        path = Path(r['path'])
        candidates.append(dict(uid=r['paper_uid'], path=str(path), filename=path.name, reasons=reasons, score=score,
                               conflict=conflict, row=r))
    candidates.sort(key=lambda c: (-c['score'], c['path']))
    # The same file name reached through two folders is one paper; keep the best-ranked copy.
    unique, seen = [], set()
    for c in candidates:
        if c['filename'].casefold() not in seen:
            seen.add(c['filename'].casefold()); unique.append(c)
    return unique


def usable(path, reading=True):
    source = Path(path).resolve()
    if not authorized_source(source):
        return '索引记录的文件不在授权的本地论文库目录内，无法读取。'
    if source.suffix.lower() not in ('.md', '.markdown', '.txt', '.pdf'):
        return '该文件格式暂不支持。'
    if not source.is_file():
        return '索引中的原文件已移动或不存在，请更新论文库索引后再试。'
    if reading and source.stat().st_size > 40 * 1024 * 1024:
        return '这篇论文超过 40 MB，请拆分后上传。'
    return ''


def online_paper(document_id):
    try:
        doc = literature_api.document(document_id)
    except literature_api.ApiError as exc:
        raise ValueError(str(exc)) from exc
    stems = [normalized(Path(a['filename']).stem) for a in doc['assets'] if a['status'] not in UNUSABLE_ASSETS]
    try:
        with connect() as db:
            candidates = locate(db, doc['doi'], doc['title'], stems)
    except ValueError:
        candidates = []
    best = next((c for c in candidates if not usable(c['path'])), None)
    record = dict(uid='api:' + doc['document_id'], document_id=doc['document_id'], title=doc['title'], authors=doc['authors'],
                  journal=doc['journal'], year=doc['year'], volume=doc['volume'], issue=doc['issue'], pages=doc['pages'],
                  doi=doc['doi'], url=doc['url'], language=doc['language'], document_type=doc['document_type'],
                  abstract=doc['abstract'], excerpt=excerpt(doc['abstract']), keywords=doc['keywords'], source='online',
                  path=None, filename='', changed_since_index=False)
    if best:
        source = Path(best['path']).resolve()
        record.update(path=str(source), filename=source.name, file_uid=best['uid'],
                      changed_since_index=source.stat().st_mtime_ns != best['row'].get('mtime_ns'))
        extra = f'；另有 {len(candidates) - 1} 个候选文件' if len(candidates) > 1 else ''
        record['identity_note'] = '本机原文按' + '、'.join(best['reasons']) + '匹配' + extra + '。载入前请在原文中核对题名、作者与年份。'
        record['local'] = dict(status='found', uid=best['uid'], filename=source.name, reasons=best['reasons'], candidates=len(candidates))
    else:
        record['identity_note'] = ('找到候选文件，但无法读取：' + usable(candidates[0]['path'])) if candidates else \
            '文献网站有这篇论文的记录，但本机全文库里没有找到对应文件。'
        record['local'] = dict(status='missing', uid='', filename='', reasons=[], candidates=len(candidates))
    return record


# ---------------------------------------------------------------- local index search
def local_search(query, terms, scope, language, yfrom, yto, page, start, fuzzy=True):
    filters, params = ['p.excluded=0'], []
    if language:
        filters.append('p.language=?'); params.append(language)
    if yfrom:
        filters.append('p.year>=?'); params.append(yfrom)
    if yto:
        filters.append('p.year<=?'); params.append(yto)
    long_terms = [t for t in terms if len(t) >= 3]
    short_terms = [t for t in terms if len(t) < 3]
    with connect() as db:
        meta = dict(db.execute('SELECT key,value FROM index_meta'))
        modern = meta.get('schema') == '2'
        columns = select_list(db)
        if scope == 'author' and not modern:
            raise ValueError('当前全文索引没有作者信息，请先更新全文索引。')
        if scope == 'doi':
            key = doi_key(query)
            if not key:
                raise ValueError('请输入完整 DOI，例如 10.1111/j.1540-6261.2010.01624.x。')
            sql = ('SELECT ' + columns + ' FROM papers p INDEXED BY idx_papers_doi WHERE p.doi=? AND ' + ' AND '.join(filters) + ' LIMIT ? OFFSET ?')
            query_params = [key] + params
            strategy, note = 'DOI 精确匹配', ''
        elif scope == 'author' and not long_terms:
            # Two-character Chinese names are below the trigram size; scan the short author column.
            sql = ('SELECT ' + columns + ' FROM papers p WHERE ' + ' AND '.join(['instr(p.authors,?)>0'] * len(terms) + filters) +
                   ' ORDER BY p.year DESC,p.id LIMIT ? OFFSET ?')
            query_params = list(terms) + params
            strategy, note = '按作者匹配', ''
        elif not long_terms:
            # Trigram FTS cannot match two-character Chinese words; the small
            # covering title index keeps this fallback fast.
            conditions = ' AND '.join('title_norm LIKE ?' for _ in terms)
            needle_params = ['%' + normalized(t) + '%' for t in terms]
            sql = ('WITH hits AS MATERIALIZED (SELECT id FROM papers INDEXED BY idx_papers_title_norm WHERE ' + conditions +
                   ' ORDER BY (title_norm=?) DESC,instr(title_norm,?),length(title_norm),id) '
                   'SELECT ' + columns + ' FROM hits CROSS JOIN papers p ON p.id=hits.id WHERE ' + ' AND '.join(filters) + ' LIMIT ? OFFSET ?')
            query_params = needle_params + [normalized(query), normalized(terms[0])] + params
            strategy = '短词按题名匹配'
            note = '两个字的词只匹配题名，输入更具体的词可同时检索摘要与作者。' if scope != 'title' else ''
        else:
            if modern:
                field = {'title': 'title', 'author': 'authors'}.get(scope, '{title authors journal abstract keywords}')
                source = {'title': "coalesce(p.title,'')", 'author': "coalesce(p.authors,'')"}.get(
                    scope, "coalesce(p.title,'')||' '||coalesce(p.authors,'')||' '||coalesce(p.journal,'')||' '||coalesce(p.abstract,'')||' '||coalesce(p.keywords,'')")
                rank = 'bm25(papers_fts,12.0,6.0,2.0,3.0,4.0)'
            else:
                field = 'title' if scope == 'title' else '{title abstract keywords}'
                source = "coalesce(p.title,'')" if scope == 'title' else "coalesce(p.title,'')||' '||coalesce(p.abstract,'')||' '||coalesce(p.keywords,'')"
                rank = 'bm25(papers_fts,12.0,3.0,0.0,5.0)'
            expression = field + ': (' + ' AND '.join('"' + t.replace('"', '""') + '"' for t in long_terms) + ')'
            for t in short_terms:
                filters.append('instr(lower(' + source + '),?)>0'); params.append(t.lower())
            sql = ('SELECT ' + columns + ' FROM papers_fts CROSS JOIN papers p ON p.id=papers_fts.rowid '
                   'WHERE papers_fts MATCH ? AND ' + ' AND '.join(filters) +
                   ' ORDER BY (instr(p.title_norm,?)>0) DESC,' + rank + ',p.id LIMIT ? OFFSET ?')
            query_params = [expression] + params + [normalized(query)]
            strategy = {'title': '题名相关度优先', 'author': '按作者匹配'}.get(scope, '题名、作者、期刊与摘要匹配')
            note = ''
        rows = db.execute(sql, query_params + [PAGE_SIZE + 1, (page - 1) * PAGE_SIZE]).fetchall()
        labelled = [(row, None) for row in rows[:PAGE_SIZE]]
        if page == 1 and scope in ('all', 'title'):
            labelled, extra_note = supplement(db, query, labelled, filters, params, fuzzy)
            note = ' '.join(filter(None, [note, extra_note]))
    key_query = normalized(query)
    results, seen = [], set()
    for row, label in labelled:
        key = (row['title_norm'], row['year'])
        if key in seen:
            continue  # the same paper filed under two folders
        seen.add(key)
        if label is None:
            title = row['title_norm'] or ''
            label = (('doi', 'DOI 一致') if scope == 'doi' else ('author', '作者匹配') if scope == 'author' else
                     ('exact', '题名一致') if title == key_query else ('title', '题名匹配') if key_query and key_query in title else
                     ('keyword', '关键词匹配'))
        results.append({**public_row(row), 'match_kind': label[0], 'match_label': label[1]})
    return dict(results=results, page=page, has_more=len(rows) > PAGE_SIZE and page < 100, strategy=strategy, note=note,
                elapsed_ms=round((time.monotonic() - start) * 1000), query=query, source='local')


def supplement(db, query, labelled, filters, params, fuzzy):
    """When a pasted title is not found as typed: its main title, a file name, then similar titles."""
    key = normalized(query)
    if any(row['title_norm'] == key for row, _ in labelled) or len(key) < 8:
        return labelled, ''
    columns = 'p.id,' + select_list(db)
    where = ' AND '.join(filters)
    known = {row['paper_uid'] for row, _ in labelled}
    extra = []
    def add(rows, label):
        for row in rows:
            if row['paper_uid'] not in known and len(labelled) + len(extra) < PAGE_SIZE:
                known.add(row['paper_uid'])
                extra.append((row, label))
    main = main_title(query)
    if main:
        # A published title may be indexed under its earlier, shorter working-paper title.
        add(db.execute('SELECT ' + columns + ' FROM papers p INDEXED BY idx_papers_title_norm WHERE p.title_norm=? AND ' + where + ' LIMIT 10',
                       [normalized(main)] + params), ('title_variant', '主标题一致 · 版本待核对'))
    if 'stem_key' in {r[1] for r in db.execute('PRAGMA table_info(papers)')}:
        # A damaged extracted heading should not hide a descriptive file name.
        add(db.execute('SELECT ' + columns + ' FROM papers p INDEXED BY idx_papers_stem WHERE p.stem_key=? AND ' + where + ' LIMIT 10',
                       [key] + params), ('filename', '文件名一致 · 请核对原文'))
    note = ''
    if fuzzy and len(labelled) + len(extra) < PAGE_SIZE:
        ids = {r['id'] for r in db.execute('SELECT id FROM papers WHERE paper_uid IN (' + ','.join('?' * len(known)) + ')', list(known))} if known else set()
        similar = similar_titles(db, query, filters, params, ids, PAGE_SIZE - len(labelled) - len(extra))
        for row, score in similar:
            add([row], ('similar', f'相似题名 {score}%'))
        if similar:
            note = '没有完全一致的题名，下方列出相似题名，请核对作者与年份。'
    return labelled + extra, note


def paper(uid, reading=True):
    """An indexed paper with its readable local path; `reading=False` allows files over 40 MB (download, reveal)."""
    if not isinstance(uid, str) or len(uid) > 250:
        raise ValueError('无效的论文标识。')
    if uid.startswith('api:'):
        return online_paper(uid[4:])
    with connect() as db:
        row = db.execute('SELECT ' + select_list(db) + ' FROM papers p WHERE p.paper_uid=? AND p.excluded=0', (uid,)).fetchone()
        requires_preview = dict(db.execute('SELECT key,value FROM index_meta')).get('identity_verification') == 'preview'
    if not row:
        raise ValueError('这篇论文已不在当前索引中，请刷新后重新搜索。')
    problem = usable(row['path'], reading)
    if problem:
        raise ValueError(problem)
    source = Path(row['path']).resolve()
    result = public_row(row)
    result['path'] = str(source)
    result['changed_since_index'] = source.stat().st_mtime_ns != row['mtime_ns']
    result['identity_note'] = '书目信息来自本机索引（摘要库、文件定位记录与目录结构）；请展开原文核对题名、作者、年份及 DOI。' if requires_preview else ''
    return result


def reveal(uid):
    """Select the original file in Explorer so it can be copied or shared; never opens the file."""
    record = paper(uid, reading=False)
    if not record.get('path'):
        raise ValueError('本机全文库里没有这篇论文的原文。')
    source = str(Path(record['path']).resolve())
    if os.name == 'nt':
        command = ['explorer.exe', '/select,', source]
    else:
        command = ['xdg-open', str(Path(source).parent)]
    try:
        # Explorer returns 1 even when it succeeds, so only a launch failure is an error.
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise ValueError('无法打开文件管理器，可以改用“复制路径”或“下载原文件”。') from exc
    return dict(ok=True, message='已在资源管理器中选中原文件。', path=source)
