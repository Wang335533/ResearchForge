"""Read-only search over the existing paper library index. No model requests."""
import html
import os
import re
import sqlite3
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path

LIBRARY_ROOT = Path(os.environ.get('PAPERLENS_LIBRARY_ROOT', Path(__file__).resolve().parents[2] / 'user_data/papers'))
INDEX_PATH = Path(os.environ.get('PAPERLENS_LIBRARY_INDEX', Path(__file__).resolve().parents[2] / 'user_data/fulltext-index.sqlite3'))
PAGE_SIZE = 20


@contextmanager
def connect():
    if not INDEX_PATH.is_file():
        raise ValueError('未找到本地论文索引。请先按配置指南导入自己的全文目录并构建索引。')
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


def status():
    if not LIBRARY_ROOT.is_dir() or not INDEX_PATH.is_file():
        return dict(ready=False, count=0, root=str(LIBRARY_ROOT), message='论文库目录或现有索引暂不可用。')
    with connect() as db:
        count = db.execute('SELECT COUNT(*) FROM papers WHERE excluded=0').fetchone()[0]
        meta = dict(db.execute('SELECT key,value FROM index_meta').fetchall())
    return dict(ready=True, count=count, root=str(LIBRARY_ROOT), indexed_at=meta.get('indexed_at', ''),
                message='使用当前分享版自己的索引；检索与预览不调用 API。')


def normalized(text):
    return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', text).casefold())


def plain(text):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]*>', ' ', text or ''))).strip()


def public_row(row):
    r = dict(row)
    path = Path(r['path'])
    abstract = plain(r.get('abstract'))
    return dict(uid=r['paper_uid'], title=plain(r['title']), year=r.get('year'), journal=plain(r.get('journal')),
                language=r['language'], filename=path.name, path=str(path), abstract=abstract,
                excerpt=abstract[:250] + ('…' if len(abstract) > 250 else ''), doi=r.get('doi') or '')


def parse_terms(query):
    query = unicodedata.normalize('NFKC', query).strip()
    if not 2 <= len(query) <= 400:
        raise ValueError('请输入 2–400 个字符的题名或关键词。')
    terms = [a or b.strip('"') for a, b in re.findall(r'"([^\"]+)"|(\S+)', query)]
    terms = list(dict.fromkeys(t for t in terms if normalized(t)))
    if not terms or len(terms) > 30:
        raise ValueError('请输入有效题名或关键词，最多 30 个检索词。双引号可用于完整短语。')
    return query, terms


def search(query, scope='all', language='', year_from='', year_to='', page=1):
    query, terms = parse_terms(query)
    if scope not in ('all', 'title') or language not in ('', 'zh', 'en'):
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
    filters, params = ['p.excluded=0'], []
    if language:
        filters.append('p.language=?'); params.append(language)
    if yfrom:
        filters.append('p.year>=?'); params.append(yfrom)
    if yto:
        filters.append('p.year<=?'); params.append(yto)
    long_terms = [t for t in terms if len(t) >= 3]
    short_terms = [t for t in terms if len(t) < 3]
    columns = 'p.paper_uid,p.path,p.title,p.journal,p.year,p.language,p.abstract,p.doi'
    start = time.monotonic()
    with connect() as db:
        if not long_terms:
            # The existing trigram FTS cannot match two-character Chinese words.
            # Its small covering title index makes this fallback fast without
            # copying the corpus or scanning the large abstracts/reference rows.
            conditions = ' AND '.join('title_norm LIKE ?' for _ in terms)
            needle_params = ['%' + normalized(t) + '%' for t in terms]
            sql = ('WITH hits AS MATERIALIZED (SELECT id FROM papers INDEXED BY idx_papers_title_norm WHERE ' + conditions +
                   ' ORDER BY (title_norm=?) DESC,instr(title_norm,?),length(title_norm),id) '
                   'SELECT ' + columns + ' FROM hits CROSS JOIN papers p ON p.id=hits.id WHERE ' + ' AND '.join(filters) + ' LIMIT ? OFFSET ?')
            query_params = needle_params + [normalized(query), normalized(terms[0])] + params
            strategy = '短词按题名匹配'
            note = '当前检索词少于 3 个字符，已使用题名索引。要搜索摘要与关键词，可输入更具体的长词，例如“技术创新”“政府采购”。' if scope == 'all' else ''
        else:
            field = 'title' if scope == 'title' else '{title abstract keywords}'
            expression = field + ': (' + ' AND '.join('"' + t.replace('"', '""') + '"' for t in long_terms) + ')'
            source = "coalesce(p.title,'')" if scope == 'title' else "coalesce(p.title,'') || ' ' || coalesce(p.abstract,'') || ' ' || coalesce(p.keywords,'')"
            for t in short_terms:
                filters.append('instr(lower(' + source + '),?)>0'); params.append(t.lower())
            sql = ('SELECT ' + columns + ' FROM papers_fts CROSS JOIN papers p ON p.id=papers_fts.rowid '
                   'WHERE papers_fts MATCH ? AND ' + ' AND '.join(filters) +
                   ' ORDER BY (instr(p.title_norm,?)>0) DESC,bm25(papers_fts,12.0,3.0,0.0,5.0),p.id LIMIT ? OFFSET ?')
            query_params = [expression] + params + [normalized(query)]
            strategy = '题名相关度优先' if scope == 'title' else '题名、摘要与关键词匹配'
            note = ''
        rows = db.execute(sql, query_params + [PAGE_SIZE + 1, (page - 1) * PAGE_SIZE]).fetchall()
    return dict(results=[public_row(r) for r in rows[:PAGE_SIZE]], page=page, has_more=len(rows)>PAGE_SIZE and page<100,
                strategy=strategy, note=note, elapsed_ms=round((time.monotonic()-start)*1000), query=query)


def paper(uid):
    if not isinstance(uid, str) or len(uid) > 250:
        raise ValueError('无效的论文标识。')
    with connect() as db:
        row = db.execute('SELECT paper_uid,path,title,journal,year,language,abstract,doi,mtime_ns FROM papers WHERE paper_uid=? AND excluded=0', (uid,)).fetchone()
    if not row:
        raise ValueError('这篇论文已不在当前索引中，请刷新后重新搜索。')
    root = LIBRARY_ROOT.resolve()
    source = Path(row['path']).resolve()
    if not source.is_relative_to(root):
        raise ValueError('索引记录的文件不在授权的本地论文库目录内，无法读取。')
    if source.suffix.lower() not in ('.md', '.markdown', '.txt', '.pdf'):
        raise ValueError('该文件格式暂不支持。')
    if not source.is_file():
        raise ValueError('索引中的原文件已移动或不存在，请更新论文库索引后再试。')
    if source.stat().st_size > 40 * 1024 * 1024:
        raise ValueError('这篇论文超过 40 MB，请拆分后上传。')
    result = public_row(row)
    result['path'] = str(source)
    result['changed_since_index'] = source.stat().st_mtime_ns != row['mtime_ns']
    return result
