#!/usr/bin/env python3
"""Local abstract-library retrieval. Python 3.10+; no Codex/skill dependency.

One implementation for CLI and local web UI. Source files are always read-only.
BM25 Chinese character n-grams + English stemming, independent corpus-wide
normalized embeddings, weighted RRF, optional reference expansion, and MMR.
Scores are retrieval signals, not calibrated relevance probabilities.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import hashlib
import heapq
import html
import io
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "1.1.0-researchforge"
SCHEMA = "researchforge-2"
DEFAULT_DIR = Path(__file__).resolve().parent / "data" / "library"
MODEL = "qwen3-embedding:0.6b"
BASE_URL = "http://127.0.0.1:11434"
QUERY_INSTRUCTION = (
    "Instruct: Retrieve academic papers relevant to the research question, "
    "including its mechanisms, constructs and empirical design.\nQuery: "
)
FIELDS = ("title", "authors", "abstract", "keywords", "year", "journal", "doi", "url", "language", "references")
ALIASES = {
    "title": ["title", "标题", "题名", "论文标题", "论文名称", "文献标题", "文章标题", "article title", "document title", "TI", "T1"],
    "authors": ["authors", "author", "作者", "作者姓名", "AU", "A1"],
    "abstract": ["abstract", "摘要", "内容摘要", "中文摘要", "英文摘要", "abstract text", "AB", "N2"],
    "keywords": ["keywords", "keyword", "关键词", "关键字", "author keywords", "DE", "KW"],
    "year": ["year", "年份", "年度", "发表年份", "出版年", "出版年份", "发表年", "发表时间", "publication year", "publish_time", "PY", "Y1"],
    "journal": ["journal", "期刊", "期刊名称", "刊名", "来源", "source title", "publication title", "SO", "JO", "JF", "T2"],
    "doi": ["doi", "DOI号", "digital object identifier", "DI", "DO"],
    "url": ["url", "link", "链接", "网址", "UR", "path", "文件路径"],
    "language": ["language", "语言", "语种", "LA"],
    "references": ["references", "reference", "参考文献", "cited references", "CR"],
}
STOP_EN = set("a an the and or of in to for on with by from as is are was were be been this that these those we our their its how why what whether using use study research paper evidence analysis results effect effects impact based can does do into through between under about at it not".split())
STOP_ZH = {"研究", "论文", "问题", "如何", "是否", "什么", "我们", "本文", "通过", "分析", "影响", "以及", "一个", "进行", "基于", "相关", "对于", "可以", "我的", "方向"}
NONARTICLES = ("contents", "tableofcontents", "forthcomingarticles", "forthcomingpapers", "authorindex", "subjectindex", "editorialboard", "frontmatter", "backmatter", "目录", "总目录", "编辑委员会", "征稿启事", "投稿须知")
LOCK_THREAD = threading.RLock()


class SearchError(Exception):
    pass


MAX_QUERY_CHARS = 12000


class QueryLimitError(SearchError):
    """An invalid query does not indicate an unavailable retrieval service."""
    pass


def np_module():
    try:
        import numpy as np
        return np
    except ImportError as exc:
        raise SearchError("向量功能需要 numpy：python -m pip install numpy") from exc


def dump(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False)


def digest(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def text(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    if isinstance(value, dict):
        if value.get("family") or value.get("given"):
            return " ".join(str(value[k]) for k in ("given", "family") if value.get(k))
        return str(value.get("name") or value.get("title") or "")
    if isinstance(value, (list, tuple)):
        return "; ".join(filter(None, (text(x) for x in value)))
    return unicodedata.normalize("NFKC", html.unescape(str(value))).strip()


def key(value):
    return "".join(c for c in text(value).casefold() if c.isalnum())


def doi_key(value):
    match = re.search(r"10\.\d{4,9}/[^\s<>\"]+", text(value), re.I)
    return match.group(0).lower().rstrip(".,;") if match else ""


def tokens(value, query=False):
    """Fixed tokenizer: do not depend on optional segmenter installation state."""
    out = []
    for part in re.findall(r"[\u3400-\u9fff]+|[a-zA-Z0-9]+(?:['-][a-zA-Z0-9]+)*", text(value).casefold()):
        if re.match(r"[\u3400-\u9fff]", part):
            for n in (2, 3):
                out.extend(part[i:i+n] for i in range(len(part)-n+1) if not query or part[i:i+n] not in STOP_ZH)
        elif len(part) > 1 and (not query or part not in STOP_EN):
            out.append(part)
    return out


def fts_text(value):
    return " ".join(tokens(value))


def now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


@contextlib.contextmanager
def locked(folder):
    """OS-released lock: crash-safe; serializes web threads and other processes."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with LOCK_THREAD, (folder / ".lock").open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                if not handle.read(1):
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SearchError("索引正在被另一进程使用；请等待当前建库/查询结束。") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def connect(folder, create=False):
    path = Path(folder) / "index.sqlite3"
    if not path.exists() and not create:
        raise SearchError("还没有索引，请先执行 index --input 摘要库文件。")
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    # Keep batch commits independent of long read-only corpus inspections.
    # FULL retains durable commits; WAL readers see their existing snapshot.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    if create:
        existing = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if not existing:
            con.executescript("""
            CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE papers(
              id INTEGER PRIMARY KEY, title TEXT NOT NULL, title_key TEXT NOT NULL,
              authors TEXT NOT NULL, author_key TEXT NOT NULL, abstract TEXT NOT NULL,
              keywords TEXT NOT NULL, year INTEGER, journal TEXT NOT NULL,
              doi TEXT NOT NULL, url TEXT NOT NULL, language TEXT NOT NULL,
              refs TEXT NOT NULL, text_hash TEXT NOT NULL, updated_at TEXT NOT NULL,
              ut TEXT NOT NULL DEFAULT '', document_types TEXT NOT NULL DEFAULT '');
            CREATE INDEX paper_doi ON papers(doi) WHERE doi<>'';
            CREATE UNIQUE INDEX paper_ut ON papers(ut) WHERE ut<>'';
            CREATE INDEX paper_title ON papers(title_key,year);
            CREATE TABLE sources(source TEXT,row_key TEXT,paper_id INTEGER REFERENCES papers(id),
              PRIMARY KEY(source,row_key));
            CREATE TABLE vectors(paper_id INTEGER PRIMARY KEY REFERENCES papers(id),
              text_hash TEXT NOT NULL, dimensions INTEGER NOT NULL, vector BLOB NOT NULL);
            CREATE TABLE edges(source_id INTEGER REFERENCES papers(id),target_id INTEGER REFERENCES papers(id),
              PRIMARY KEY(source_id,target_id));
            CREATE INDEX edge_target ON edges(target_id);
            CREATE TABLE reruns(id INTEGER PRIMARY KEY,source TEXT,created_at TEXT,audit TEXT);
            CREATE VIRTUAL TABLE search USING fts5(title,keywords,abstract,authors,journal,
              tokenize='porter unicode61 remove_diacritics 2');
            """)
            set_meta(con, "schema", SCHEMA)
            set_meta(con, "revision", "0")
            con.commit()
    try:
        if get_meta(con, "schema") != SCHEMA:
            con.close()
            raise SearchError("不是兼容的本工具索引；请指定新的 --index-dir。")
    except sqlite3.OperationalError as exc:
        con.close()
        raise SearchError("指定目录中存在其他数据库；请使用空目录作为 --index-dir。") from exc
    if create:
        # Coverage checks need IDs and hashes, not the large abstracts/vectors.
        con.execute("CREATE INDEX IF NOT EXISTS paper_vector_revision ON papers(id,text_hash)")
        con.execute("CREATE INDEX IF NOT EXISTS vector_text_revision ON vectors(paper_id,text_hash)")
        con.commit()
    return con


def get_meta(con, name, default=None):
    row = con.execute("SELECT value FROM meta WHERE key=?", (name,)).fetchone()
    return row[0] if row else default


def set_meta(con, name, value):
    con.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (name, str(value)))


def read_records(path, encoding="utf-8-sig", sheet=None, table="papers"):
    """Stream CSV, JSONL, XLSX, RIS, Parquet and SQLite; JSON is memory-loaded."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise SearchError(f"输入文件不存在：{path}")
    suffix = path.suffix.lower()
    if suffix in (".csv", ".tsv", ".txt"):
        with path.open(encoding=encoding, newline="") as handle:
            sample = handle.read(32768)
            handle.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
            except csv.Error:
                dialect = csv.excel_tab if suffix == ".tsv" else csv.excel
            reader = csv.DictReader(handle, dialect=dialect)
            headers = reader.fieldnames or []
            if not headers or any(not key(h) for h in headers) or len({key(h) for h in headers}) != len(headers):
                raise SearchError("CSV第一行须为不重复的有效列名；请移除空列名或同名列。")
            for n, row in enumerate(reader, 2):
                if None in row:
                    raise SearchError(f"CSV第{n}条记录比表头多列；摘要中的逗号/换行须使用CSV引号包围。")
                if any(text(v) for v in row.values()):
                    yield row
    elif suffix in (".jsonl", ".ndjson"):
        with path.open(encoding=encoding) as handle:
            for n, line in enumerate(handle, 1):
                if line.strip():
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise SearchError(f"JSONL第{n}行不是合法JSON：{exc.msg}") from exc
                    if not isinstance(row, dict):
                        raise SearchError(f"JSONL第{n}行必须是对象。")
                    yield row
    elif suffix == ".json":
        with path.open(encoding=encoding) as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            data = next((data[k] for k in ("papers", "records", "items", "data") if isinstance(data.get(k), list)), data)
        if isinstance(data, dict) and isinstance(data.get("message"), dict):
            data = data["message"].get("items", data)
        if not isinstance(data, list) or any(not isinstance(r, dict) for r in data):
            raise SearchError("JSON须为对象数组，或包含papers/records/items/data数组。大文件建议JSONL。")
        yield from data
    elif suffix == ".xlsx":
        try:
            import openpyxl
        except ImportError as exc:
            raise SearchError("读取Excel需要：python -m pip install openpyxl") from exc
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = book[sheet] if sheet else book.worksheets[0]
            rows = ws.iter_rows(values_only=True)
            headers = [text(x) for x in next(rows, ())]
            if not headers or any(not key(h) for h in headers) or len({key(h) for h in headers}) != len(headers):
                raise SearchError("Excel表头含重复/空列名，请整理表头或导出CSV。")
            for values in rows:
                if any(x is not None for x in values):
                    yield dict(zip(headers, values))
        finally:
            book.close()
    elif suffix == ".parquet":
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise SearchError("读取Parquet需要：python -m pip install pyarrow") from exc
        for batch in pq.ParquetFile(path).iter_batches(batch_size=2048):
            yield from batch.to_pylist()
    elif suffix == ".ris":
        with path.open(encoding=encoding) as handle:
            row, tag = {}, None
            for line in handle:
                m = re.match(r"^([A-Z0-9]{2})  - ?(.*)", line.rstrip())
                if m:
                    tag, val = m.groups()
                    if tag == "ER":
                        if row:
                            yield row
                        row, tag = {}, None
                    else:
                        row.setdefault(tag, []).append(val)
                elif line.strip() and tag:
                    row[tag][-1] += " " + line.strip()
            if row:
                yield row
    elif suffix in (".db", ".sqlite", ".sqlite3"):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
            raise SearchError("SQLite表名仅支持字母、数字、下划线。")
        source = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        source.row_factory = sqlite3.Row
        try:
            for row in source.execute(f'SELECT * FROM "{table}"'):
                yield dict(row)
        finally:
            source.close()
    else:
        raise SearchError("支持CSV/TSV、JSON/JSONL、XLSX、RIS、Parquet和SQLite；旧XLS请另存XLSX。")


def column_mapping(headers, explicit=None):
    normalized = {key(h): h for h in headers if h is not None}
    mapping = {}
    explicit = explicit or {}
    unknown = set(explicit) - set(FIELDS)
    if unknown:
        raise SearchError(f"未知标准字段：{sorted(unknown)}")
    for field in FIELDS:
        if field in explicit:
            if explicit[field] not in headers:
                raise SearchError(f"字段映射找不到源列：{field}={explicit[field]}")
            mapping[field] = explicit[field]
        else:
            for alias in ALIASES[field]:
                if key(alias) in normalized:
                    mapping[field] = normalized[key(alias)]
                    break
    if "title" not in mapping:
        raise SearchError("未找到标题列。用 --map title=你的列名 指定，或先运行 inspect。")
    return mapping


def normalize_record(row, mapping):
    p = {f: text(row.get(mapping.get(f))) for f in FIELDS}
    p["title"] = re.sub(r"<[^>]+>", " ", p["title"]).strip()
    p["abstract"] = re.sub(r"<[^>]+>", " ", p["abstract"]).strip()
    m = re.search(r"(?:18|19|20|21)\d{2}", p["year"])
    p["year"] = int(m.group()) if m else None
    p["doi"] = doi_key(p["doi"])
    p["title_key"] = key(p["title"])
    p["author_key"] = key(re.split(r"[;；|]", p["authors"])[0])
    if not p["language"]:
        p["language"] = "zh" if re.search(r"[\u3400-\u9fff]", p["title"]) else "en"
    refs = row.get(mapping.get("references"), [])
    if isinstance(refs, str):
        try:
            decoded = json.loads(refs)
            refs = decoded if isinstance(decoded, list) else re.split(r"[\n;；]+", refs)
        except (ValueError, TypeError):
            refs = re.split(r"[\n;；]+", refs)
    if not isinstance(refs, list):
        refs = []
    p["refs"] = json.dumps(refs, ensure_ascii=False)
    p.pop("references")
    return p


def document_text(p):
    return "\n".join([f"Title: {p['title']}", f"Keywords: {p['keywords']}", f"Abstract: {p['abstract']}"])


def upsert(con, p):
    # Repeat the partial-index predicate: without it SQLite may scan all papers
    # for every DOI, turning a large import into quadratic work.
    existing = con.execute("SELECT * FROM papers WHERE doi<>'' AND doi=?", (p["doi"],)).fetchone() if p["doi"] else None
    if existing is None and p["year"] and p["author_key"]:
        possible = con.execute("SELECT * FROM papers WHERE title_key=? AND year=? AND author_key=?", (p["title_key"], p["year"], p["author_key"])).fetchall()
        possible = [r for r in possible if not (r["doi"] and p["doi"] and r["doi"] != p["doi"])]
        if len(possible) == 1:
            existing = possible[0]
    if existing is None:
        # Missing year/author: only exact bibliographic content is merged.
        existing = con.execute("SELECT * FROM papers WHERE title_key=? AND authors=? AND abstract=? AND year IS ? AND doi=? LIMIT 1", (p["title_key"], p["authors"], p["abstract"], p["year"], p["doi"])).fetchone()
    before = dict(existing) if existing else None
    if before:
        for f in ("authors", "abstract", "keywords", "journal", "url", "language"):
            if len(p[f]) < len(before[f]):
                p[f] = before[f]
        for f in ("year", "doi"):
            p[f] = p[f] or before[f]
        if p["refs"] == "[]":
            p["refs"] = before["refs"]
        p["author_key"] = key(re.split(r"[;；|]", p["authors"])[0])
    p["text_hash"] = digest(document_text(p))
    fields = tuple(p)
    changed = not before or any(p[f] != before[f] for f in fields)
    if not changed:
        return before["id"], "unchanged"
    p["updated_at"] = now()
    if before:
        pid = before["id"]
        con.execute("UPDATE papers SET " + ",".join(f"{f}=?" for f in p) + " WHERE id=?", [*p.values(), pid])
        con.execute("DELETE FROM search WHERE rowid=?", (pid,))
        action = "enriched"
    else:
        cursor = con.execute("INSERT INTO papers(" + ",".join(p) + ") VALUES(" + ",".join("?" for _ in p) + ")", list(p.values()))
        pid, action = cursor.lastrowid, "added"
    con.execute("INSERT INTO search(rowid,title,keywords,abstract,authors,journal) VALUES(?,?,?,?,?,?)", [pid, *(fts_text(p[f]) for f in ("title", "keywords", "abstract", "authors", "journal"))])
    return pid, action


def import_library(folder, paths, explicit=None, encoding="utf-8-sig", sheet=None, table="papers", limit=0, skip_excluded=False):
    if limit < 0:
        raise SearchError("limit不能小于0。")
    started = time.monotonic()
    with locked(folder), contextlib.closing(connect(folder, True)) as con:
        audit = {"sources": [], "rows": 0, "added": 0, "enriched": 0, "unchanged": 0, "skipped_no_title": 0, "skipped_excluded": 0, "missing_abstract": 0, "skipped_examples": []}
        with con:
            for path in paths:
                path = Path(path).expanduser().resolve()
                if path == (Path(folder) / "index.sqlite3").resolve():
                    raise SearchError("不能将本工具自己的索引作为导入源。")
                mapping = None
                for n, row in enumerate(read_records(path, encoding, sheet, table), 1):
                    if limit and n > limit:
                        break
                    if mapping is None:
                        mapping = column_mapping(row, explicit)
                        audit["sources"].append({"path": str(path), "mapping": mapping})
                    audit["rows"] += 1
                    if skip_excluded and text(row.get("excluded")).casefold() in ("1", "true", "yes"):
                        audit["skipped_excluded"] += 1
                        continue
                    p = normalize_record(row, mapping)
                    if not p["title_key"]:
                        audit["skipped_no_title"] += 1
                        if len(audit["skipped_examples"]) < 10:
                            audit["skipped_examples"].append({"source": str(path), "row": n})
                        continue
                    audit["missing_abstract"] += int(not p["abstract"])
                    pid, action = upsert(con, p)
                    audit[action] += 1
                    # Provenance records are not deletions/synchronization instructions.
                    con.execute("INSERT OR REPLACE INTO sources VALUES(?,?,?)", (str(path), str(n), pid))
                    if n % 5000 == 0:
                        print(f"导入 {path.name}: {n:,}条", file=sys.stderr, flush=True)
                if mapping is None:
                    raise SearchError(f"文件没有记录：{path}")
            if audit["added"] or audit["enriched"]:
                set_meta(con, "revision", int(get_meta(con, "revision", "0")) + 1)
                set_meta(con, "edges_revision", "-1")
            set_meta(con, "indexed_at", now())
            audit["papers"] = con.execute("SELECT count(*) FROM papers").fetchone()[0]
            audit["seconds"] = round(time.monotonic()-started, 3)
            con.execute("INSERT INTO reruns(source,created_at,audit) VALUES(?,?,?)", (json.dumps([str(p) for p in paths]), now(), dump(audit)))
        return audit


def build_links(folder):
    """Only exact DOI or unambiguous normalized-title links; no invented edges."""
    with locked(folder), contextlib.closing(connect(folder)) as con:
        titles, dois = defaultdict(list), {}
        for r in con.execute("SELECT id,title_key,doi FROM papers"):
            titles[r["title_key"]].append(r["id"])
            if r["doi"]:
                dois[r["doi"]] = r["id"]
        counts = Counter()
        with con:
            con.execute("DELETE FROM edges")
            for r in con.execute("SELECT id,refs FROM papers WHERE refs<>'[]'"):
                for ref in json.loads(r["refs"]):
                    counts["references"] += 1
                    d = doi_key(ref.get("doi", "")) if isinstance(ref, dict) else doi_key(ref)
                    t = key(ref.get("title", "")) if isinstance(ref, dict) else key(ref)
                    target = dois.get(d)
                    if target is None and len(titles.get(t, [])) == 1:
                        target = titles[t][0]
                    if target and target != r["id"]:
                        con.execute("INSERT OR IGNORE INTO edges VALUES(?,?)", (r["id"], target))
                        counts["resolved_entries"] += 1
                    else:
                        counts["unresolved_or_self"] += 1
            set_meta(con, "edges_revision", get_meta(con, "revision"))
        counts["unique_edges"] = con.execute("SELECT count(*) FROM edges").fetchone()[0]
        return dict(counts)


def api(base, endpoint, payload=None, timeout=90):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    request = urllib.request.Request(base.rstrip("/")+endpoint, data=data, headers={"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read(1000).decode(errors="replace")
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise SearchError(f"模型服务HTTP {exc.code}: {detail}") from exc
        except (OSError, ValueError) as exc:
            if attempt == 2:
                raise SearchError(f"模型服务不可用：{exc}") from exc
        time.sleep(0.5 * 2**attempt)


class Ollama:
    def __init__(self, base=BASE_URL, model=MODEL, max_chars=16000, allow_remote=False):
        parsed = urllib.parse.urlparse(base)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise SearchError("模型地址须为无账号密码的HTTP(S)服务地址。")
        if parsed.hostname not in ("localhost", "127.0.0.1", "::1") and not allow_remote:
            raise SearchError("非本机模型会接收摘要/查询文本；确认后才可使用 --allow-remote。")
        self.base, self.model, self.max_chars = base, model, max_chars

    def identity(self):
        models = api(self.base, "/api/tags", timeout=8).get("models", [])
        match = next((m for m in models if m.get("name") == self.model or m.get("model") == self.model), None)
        if not match:
            raise SearchError(f"Ollama中没有模型 {self.model}；工具不会自动下载，请先安装或选择已有embedding模型。")
        return {"backend": "ollama", "model": self.model, "digest": match.get("digest", ""), "max_chars": self.max_chars, "text_version": 1, "query_instruction": QUERY_INSTRUCTION}

    def embed(self, values):
        np = np_module()
        result = api(self.base, "/api/embed", {"model": self.model, "input": values, "truncate": False, "keep_alive": "10m"})
        try:
            vectors = np.asarray(result["embeddings"], dtype=np.float32)
        except (KeyError, TypeError, ValueError) as exc:
            raise SearchError("Embedding服务没有返回有效向量。") from exc
        if vectors.ndim != 2 or vectors.shape[0] != len(values) or vectors.shape[1] == 0 or not np.isfinite(vectors).all():
            raise SearchError("Embedding向量数量/维度/数值无效。")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        if (norms <= 1e-12).any():
            raise SearchError("Embedding服务返回了零向量。")
        return vectors / norms


def vector_count(con):
    return con.execute("SELECT count(*) FROM vectors v JOIN papers p ON p.id=v.paper_id AND p.text_hash=v.text_hash").fetchone()[0]


def replace_file(source, target, attempts=40):
    """os.replace that waits out a brief concurrent reader, which Windows treats as a lock."""
    for attempt in range(attempts):
        try:
            return os.replace(source, target)
        except PermissionError:
            if os.name != 'nt' or attempt == attempts - 1:
                raise
            time.sleep(0.05)


def atomic_json(path, data):
    path = Path(path)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
    replace_file(temp, path)


def snapshot(con, folder):
    """Cache exact-search matrices; interrupted embeddings remain in SQLite."""
    np = np_module()
    n = vector_count(con)
    if not n:
        return
    dims = con.execute("SELECT DISTINCT v.dimensions FROM vectors v JOIN papers p ON p.id=v.paper_id AND p.text_hash=v.text_hash").fetchall()
    if len(dims) != 1:
        raise SearchError("缓存包含不一致的向量维度。请使用新的索引目录。")
    d = dims[0][0]
    folder = Path(folder)
    matrix_path, ids_path = folder / "vectors.npy.tmp", folder / "vector_ids.npy.tmp"
    matrix = np.lib.format.open_memmap(matrix_path, mode="w+", dtype="float32", shape=(n, d))
    ids = np.lib.format.open_memmap(ids_path, mode="w+", dtype="int64", shape=(n,))
    for i, r in enumerate(con.execute("SELECT p.id,v.vector FROM papers p JOIN vectors v ON p.id=v.paper_id AND p.text_hash=v.text_hash ORDER BY p.id")):
        ids[i] = r[0]
        matrix[i] = np.frombuffer(r[1], dtype=np.float32)
    matrix.flush()
    ids.flush()
    del matrix, ids
    replace_file(matrix_path, folder / "vectors.npy")
    replace_file(ids_path, folder / "vector_ids.npy")
    atomic_json(folder / "vectors.json", {"count": n, "dimensions": d, "revision": get_meta(con, "revision"), "profile": get_meta(con, "embedding_profile"), "created_at": now()})


def embed_library(folder, backend, batch_size=24, max_seconds=0, progress=None):
    np = np_module()
    if batch_size < 1 or max_seconds < 0 or not math.isfinite(max_seconds):
        raise SearchError("batch-size必须大于0，max-seconds须为非负有限数。")
    started = time.monotonic()
    with locked(folder), contextlib.closing(connect(folder)) as con:
        identity = backend.identity()
        profile = json.dumps(identity, sort_keys=True, ensure_ascii=False)
        saved = get_meta(con, "embedding_profile")
        if saved and saved != profile:
            raise SearchError("向量模型、版本或截断设置发生变化，不能混用。请为新模型指定另一个 --index-dir 并重新导入。")
        with con:
            set_meta(con, "embedding_profile", profile)
        completed, clipped = 0, 0
        total = con.execute("SELECT count(*) FROM papers").fetchone()[0]
        ready_before = vector_count(con)
        last_id = 0
        if progress:
            progress(phase="embedding", papers=total, vectors_ready=ready_before)
        while True:
            if max_seconds and time.monotonic()-started >= max_seconds:
                break
            rows = con.execute("SELECT p.* FROM papers p LEFT JOIN vectors v ON p.id=v.paper_id AND p.text_hash=v.text_hash WHERE p.id>? AND v.paper_id IS NULL ORDER BY p.id LIMIT ?", (last_id, batch_size)).fetchall()
            if not rows:
                break
            values = [document_text(r) for r in rows]
            if any(len(v) > backend.max_chars for v in values):
                raise SearchError(f"文献 {rows[0]['id']} 所在批次超过 embedding 字符预算；未截断，请提高预算后重新准备。")
            try:
                vectors = backend.embed(values)
                old = con.execute("SELECT dimensions FROM vectors LIMIT 1").fetchone()
                if old and old[0] != vectors.shape[1]:
                    raise SearchError("模型向量维度变化，停止以避免混用。")
            except Exception as exc:
                raise SearchError(f"向量构建停在文献ID {rows[0]['id']}；此前批次已保存，可重跑继续。{exc}") from exc
            with con:
                con.executemany("INSERT OR REPLACE INTO vectors VALUES(?,?,?,?)", [(r["id"], r["text_hash"], vectors.shape[1], v.astype(np.float32).tobytes()) for r, v in zip(rows, vectors)])
            completed += len(rows)
            last_id = rows[-1]["id"]
            if progress:
                progress(phase="embedding", papers=total, vectors_ready=ready_before+completed)
            else:
                print(f"本次已计算 {completed:,}篇向量", file=sys.stderr, flush=True)
        if progress:
            progress(phase="snapshot", papers=total, vectors_ready=vector_count(con))
        snapshot(con, folder)
        n = con.execute("SELECT count(*) FROM papers").fetchone()[0]
        ready = vector_count(con)
        return {"papers": n, "vectors_ready": ready, "complete": ready == n, "new_vectors": completed, "clipped_this_run": clipped, "max_chars": backend.max_chars, "seconds": round(time.monotonic()-started, 3), "note": "首次全量建库；中断后重跑同命令可续算。时间预算在批次之间检查。"}


def filters_sql(filters, prefix="p."):
    clauses, values = [], []
    if not filters.get("include_nonarticles", False):
        clauses.append(prefix+"title_key NOT IN ("+",".join("?" for _ in NONARTICLES)+")")
        values.extend(NONARTICLES)
        clauses.append(prefix+"title_key NOT LIKE 'indextovolume%'")
    for f, op in (("year_from", ">="), ("year_to", "<=")):
        if filters.get(f) is not None:
            clauses.append(prefix+"year"+op+"?")
            values.append(int(filters[f]))
    for f in ("journal", "authors", "language"):
        if filters.get(f):
            clauses.append(f"instr(lower({prefix}{f}),lower(?))>0")
            values.append(str(filters[f]))
    for exclusion in filters.get("exclude", []):
        clauses.append(f"instr(lower({prefix}title || ' ' || {prefix}abstract),lower(?))=0")
        values.append(str(exclusion))
    return (" AND "+" AND ".join(clauses) if clauses else ""), values


def make_queries(query):
    if isinstance(query, str):
        body = query.strip()
        if not body:
            raise SearchError("请输入研究方向或研究框架。")
        # Sentences/aspects get separate lanes; never invent bilingual terms.
        aspects = [x.strip() for x in re.split(r"[\n；;]+", body) if len(x.strip()) >= 4]
        queries = [(body, 1.0)]
        queries.extend((p, 0.45) for p in aspects[:4] if p != body)
    elif isinstance(query, dict):
        body = text(query.get("one_sentence_question") or query.get("query") or query.get("title") or query.get("summary"))
        if not body:
            raise SearchError("研究框架JSON至少需要query、title或one_sentence_question。")
        queries = [(body, 1.0)]
        for f in ("summary", "mechanisms", "outcomes", "theories", "identification", "keywords"):
            if query.get(f):
                queries.append((text(query[f]), 0.4))
        if not isinstance(query.get("queries", []), list):
            raise SearchError("queries须为查询对象数组。")
        for item in query.get("queries", [])[:10]:
            if isinstance(item, dict) and text(item.get("text")):
                weight = float(item.get("weight", 1.0))
                if not math.isfinite(weight) or weight <= 0:
                    raise SearchError("查询通道weight必须是正数。")
                queries.append((text(item["text"]), min(2.0, weight)))
    else:
        raise SearchError("研究问题须为文本或JSON对象。")
    seen, output = set(), []
    for q, w in queries:
        if len(q) > MAX_QUERY_CHARS:
            raise QueryLimitError(
                f"单条检索查询共 {len(q):,} 字符，超过 {MAX_QUERY_CHARS:,} 字符上限；"
                "原文未裁剪，检索未执行。请缩短查询后重试。")
        if q not in seen:
            output.append((q, w))
            seen.add(q)
    if not tokens(body, query=True):
        raise SearchError("查询没有有效词项；请输入至少两个汉字或有意义的英文术语。")
    return body, output[:12]


def lexical_search(con, queries, filters, pool):
    clause, params = filters_sql(filters)
    fused, matches, best = defaultdict(float), defaultdict(list), {}
    streams = {}
    for lane, (q, weight) in enumerate(queries):
        all_terms = list(dict.fromkeys(tokens(q, query=True)))
        # Chinese character n-grams and English terms have different document
        # frequencies. Rank each script separately, then fuse ranks fairly.
        groups = [[t for t in all_terms if bool(re.match(r"[\u3400-\u9fff]",t)) == chinese][:160] for chinese in (True,False)]
        groups = [g for g in groups if g]
        for terms in groups:
            stream_key = tuple(sorted(terms))
            stream = streams.setdefault(stream_key, {"weight":0.0,"lanes":set()})
            stream["weight"] += weight/len(groups)
            stream["lanes"].add(lane+1)
    for terms, stream in streams.items():
        expression = " OR ".join('"'+t.replace('"', '""')+'"' for t in terms)
        rows = con.execute("SELECT p.id,bm25(search,8.0,5.0,2.0,0.2,0.2) AS score FROM search JOIN papers p ON p.id=search.rowid WHERE search MATCH ?"+clause+" ORDER BY score,p.id LIMIT ?", [expression, *params, pool]).fetchall()
        for rank, r in enumerate(rows, 1):
            fused[r["id"]] += stream["weight"]/(60+rank)
            matches[r["id"]] = sorted(set(matches[r["id"]]) | stream["lanes"])
            best[r["id"]] = min(best.get(r["id"], math.inf), r["score"])
    return sorted(fused, key=lambda pid: (-fused[pid], pid))[:pool], matches, best


def dense_search(con, folder, queries, filters, pool, backend, allow_partial=False):
    np = np_module()
    saved = get_meta(con, "embedding_profile")
    if not saved:
        raise SearchError("尚未生成向量，请执行 embed。")
    if json.loads(saved) != backend.identity():
        raise SearchError("当前模型与建库模型/版本不一致；请使用原模型或新建独立索引。")
    n, ready = con.execute("SELECT count(*) FROM papers").fetchone()[0], vector_count(con)
    if ready != n and not allow_partial:
        raise SearchError(f"向量未完成或已过期：{ready}/{n}。重跑embed，或明确使用 --allow-partial。")
    meta_path = Path(folder) / "vectors.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    if meta.get("revision") != get_meta(con, "revision") or meta.get("profile") != saved or meta.get("count") != ready or not all((Path(folder)/f).exists() for f in ("vectors.npy", "vector_ids.npy")):
        snapshot(con, folder)
    if not ready:
        raise SearchError("没有可使用的向量。")
    matrix = np.load(Path(folder)/"vectors.npy", mmap_mode="r", allow_pickle=False)
    ids = np.load(Path(folder)/"vector_ids.npy", mmap_mode="r", allow_pickle=False)
    values = [QUERY_INSTRUCTION+q for q, _ in queries]
    if any(len(v) > backend.max_chars for v in values):
        raise SearchError("检索文本超过 embedding 字符预算；未截断，请缩短查询。")
    # A small hashed-query cache: no separate files, no plaintext query storage.
    cache_key = digest(saved+json.dumps(values, ensure_ascii=False))
    with con:
        con.execute("CREATE TABLE IF NOT EXISTS query_vectors(cache_key TEXT PRIMARY KEY,rows INTEGER,dimensions INTEGER,vector BLOB,touched REAL)")
    cached = con.execute("SELECT * FROM query_vectors WHERE cache_key=?", (cache_key,)).fetchone()
    if cached:
        qv = np.frombuffer(cached["vector"], dtype=np.float32).reshape(cached["rows"],cached["dimensions"])
    else:
        qv = backend.embed(values)
    with con:
        con.execute("INSERT OR REPLACE INTO query_vectors VALUES(?,?,?,?,?)", (cache_key,qv.shape[0],qv.shape[1],qv.astype(np.float32).tobytes(),time.time()))
        con.execute("DELETE FROM query_vectors WHERE cache_key IN (SELECT cache_key FROM query_vectors ORDER BY touched DESC LIMIT -1 OFFSET 256)")
    if qv.shape[1] != matrix.shape[1]:
        raise SearchError("查询向量维度不匹配。")
    clause, values = filters_sql(filters)
    allowed = {r[0] for r in con.execute("SELECT p.id FROM papers p WHERE 1=1"+clause, values)} if clause else None
    top = [[] for _ in queries]
    for offset in range(0, len(ids), 4096):
        block_ids = np.asarray(ids[offset:offset+4096])
        scores = np.asarray(matrix[offset:offset+4096]) @ qv.T
        mask = np.asarray([int(i) in allowed for i in block_ids]) if allowed is not None else np.ones(len(block_ids), dtype=bool)
        valid_indices = np.flatnonzero(mask)
        for lane in range(len(queries)):
            if not len(valid_indices):
                continue
            k = min(pool, len(valid_indices))
            chosen = valid_indices[np.argpartition(scores[valid_indices, lane], -k)[-k:]]
            for i in chosen:
                item = (float(scores[i, lane]), -int(block_ids[i]))
                if len(top[lane]) < pool:
                    heapq.heappush(top[lane], item)
                elif item > top[lane][0]:
                    heapq.heapreplace(top[lane], item)
    fused, similarity = defaultdict(float), {}
    for lane, heap in enumerate(top):
        for rank, (score, neg_id) in enumerate(sorted(heap, reverse=True), 1):
            pid = -neg_id
            fused[pid] += queries[lane][1]/(60+rank)
            similarity[pid] = max(similarity.get(pid, -1), score)
    del matrix, ids
    return sorted(fused, key=lambda pid: (-fused[pid], pid))[:pool], similarity, {"vectors": ready, "papers": n, "coverage": ready/n if n else 0, "scope": "entire_index" if ready == n else "partial_index", "query_embedding_cached":bool(cached)}


def citation_search(con, seeds, filters, pool):
    if not seeds or get_meta(con, "edges_revision") != get_meta(con, "revision"):
        return [], {}
    seeds = seeds[:12]
    marks = ",".join("?" for _ in seeds)
    clause, values = filters_sql(filters)
    rows = con.execute(f"""WITH relatives AS (
      SELECT target_id AS id,source_id AS anchor FROM edges WHERE source_id IN ({marks})
      UNION SELECT source_id AS id,target_id AS anchor FROM edges WHERE target_id IN ({marks}))
      SELECT p.id,count(DISTINCT r.anchor) votes FROM relatives r JOIN papers p ON p.id=r.id
      WHERE 1=1 {clause} GROUP BY p.id ORDER BY votes DESC,p.id LIMIT ?""", [*seeds,*seeds,*values,pool]).fetchall()
    return [r[0] for r in rows], {r[0]: r[1] for r in rows}


def mmr(rows, count, diversity=0.08):
    if not rows:
        return []
    # Automatic relevance remains primary; lightweight title/keyword diversity.
    chosen, remaining = [], rows[:]
    token_sets = {p["id"]: set(tokens(p["title"]+" "+p["keywords"])) for p in rows}
    scale = max(p["score"] for p in rows) or 1
    redundancy = defaultdict(float)
    while remaining and len(chosen) < count:
        best = max(remaining, key=lambda p: (1-diversity)*p["score"]/scale-diversity*redundancy[p["id"]])
        chosen.append(best)
        remaining.remove(best)
        if diversity:
            b = token_sets[best["id"]]
            for p in remaining:
                a = token_sets[p["id"]]
                overlap = len(a & b)/max(1,len(a | b))
                redundancy[p["id"]] = max(redundancy[p["id"]],overlap)
    return chosen


def search_library(folder, query, top_k=50, mode="auto", filters=None, pool=300, diversity=0.08, backend=None, allow_partial=False, citations=False):
    if not 1 <= top_k <= 500 or not 0 <= diversity <= 0.5:
        raise SearchError("top必须为1—500，diversity必须为0—0.5。")
    if mode not in ("auto", "hybrid", "lexical", "dense"):
        raise SearchError("mode须为auto/hybrid/lexical/dense。")
    filters = filters or {}
    if not isinstance(filters, dict) or not isinstance(filters.get("exclude", []), list):
        raise SearchError("filters须为对象，exclude须为字符串数组。")
    if filters.get("year_from") and filters.get("year_to") and int(filters["year_from"]) > int(filters["year_to"]):
        raise SearchError("起始年份不能晚于结束年份。")
    pool = max(top_k*3, min(5000, pool))
    started = time.monotonic()
    body, queries = make_queries(query)
    warnings, dense, similarities, coverage, timings = [], [], {}, {}, {}
    dense_used = False
    with locked(folder), contextlib.closing(connect(folder)) as con:
        lexical, matched_lanes, bm25 = lexical_search(con, queries, filters, pool) if mode != "dense" else ([], {}, {})
        timings["keyword_seconds"] = round(time.monotonic()-started,3)
        semantic_started = time.monotonic()
        if mode != "lexical":
            try:
                dense, similarities, coverage = dense_search(con, folder, queries, filters, pool, backend or Ollama(), allow_partial)
                dense_used = True
            except SearchError as exc:
                if mode != "auto":
                    raise
                warnings.append(f"降级为关键词检索：{exc}")
        timings["semantic_seconds"] = round(time.monotonic()-semantic_started,3)
        scores, paths = defaultdict(float), defaultdict(list)
        for name, ids, weight in (("keyword", lexical, 1.0), ("embedding", dense, 1.0)):
            for rank, pid in enumerate(ids, 1):
                scores[pid] += weight/(60+rank)
                paths[pid].append(name)
        refs, votes = [], {}
        if citations:
            seeds = sorted(scores, key=lambda pid: (-scores[pid], pid))
            refs, votes = citation_search(con, seeds, filters, pool)
            if get_meta(con, "edges_revision") != get_meta(con, "revision"):
                warnings.append("引用图未构建或已过期；可执行 links，仅在源数据包含references时有用。")
            for rank, pid in enumerate(refs, 1):
                scores[pid] += 0.15/(60+rank)
                paths[pid].append("reference")
        ids = sorted(scores, key=lambda pid: (-scores[pid], pid))[:max(top_k*4, pool)]
        papers = []
        query_tokens = set(tokens(body, query=True))
        for start in range(0, len(ids), 400):
            batch = ids[start:start+400]
            for r in con.execute("SELECT * FROM papers WHERE id IN ("+",".join("?" for _ in batch)+")", batch):
                p = dict(r)
                p.update(score=round(scores[p["id"]], 8), matched_by=paths[p["id"]], keyword_lanes=matched_lanes.get(p["id"], []), bm25=bm25.get(p["id"]), cosine=similarities.get(p["id"]), reference_votes=votes.get(p["id"], 0))
                overlap = query_tokens & set(tokens(p["title"]+" "+p["abstract"]+" "+p["keywords"]))
                p["matched_terms"] = sorted(overlap, key=lambda s: (-len(s),s))[:12]
                lexical_reason = "关键词/词组重合："+"、".join(p["matched_terms"][:6]) if p["matched_terms"] else "关键词通道命中（含框架分项、词干匹配）"
                p["retrieval_reason"] = "；".join(filter(None, [lexical_reason if "keyword" in p["matched_by"] else "", "向量语义召回" if "embedding" in p["matched_by"] else "", f"与{p['reference_votes']}篇种子文献有本地引用关系" if p["reference_votes"] else ""]))
                for private in ("title_key", "author_key", "refs", "text_hash"):
                    p.pop(private, None)
                papers.append(p)
        papers.sort(key=lambda p: (-p["score"],p["id"]))
        for rank, p in enumerate(papers, 1):
            p["fusion_rank"] = rank
        selected = mmr(papers, top_k, diversity)
        for rank, p in enumerate(selected, 1):
            p["rank"] = rank
        count = con.execute("SELECT count(*) FROM papers").fetchone()[0]
        if coverage and coverage.get("coverage", 1) < 1:
            warnings.append(f"本次明确允许部分向量：覆盖{coverage['vectors']}/{coverage['papers']}，不能称为全库向量检索。")
        if not selected:
            warnings.append("没有命中；检查过滤条件，增加中英文关键词，或完成向量建库。")
        actual_mode = ("dense" if mode == "dense" else "hybrid") if dense_used else "lexical"
        return {"query": body, "query_lanes": [{"text":q,"weight":w} for q,w in queries], "created_at": now(), "engine_version": VERSION, "requested_top": top_k, "returned":len(selected), "papers_in_index":count, "requested_mode":mode, "actual_mode": actual_mode, "filters": filters, "nonarticle_filter":not bool(filters.get("include_nonarticles")), "candidate_counts":{"lexical":len(lexical),"dense":len(dense),"references":len(refs)}, "dense_coverage":coverage, "diversity":diversity, "warnings":warnings, "seconds":round(time.monotonic()-started,3), "timings":timings, "note":"自动相关性候选，不是专家终审或创新性判断；分数不是相关概率。摘要保持源语言，不生成/翻译摘要。", "results": selected}


def clean_cell(value):
    value = text(value)
    # CSV is often opened in Excel: neutralize formula-like cells.
    return "'"+value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def render_export(result, fmt):
    if fmt == "json":
        return dump(result)
    if fmt == "csv":
        output = io.StringIO(newline="")
        fields = ["rank", "title", "authors", "year", "journal", "doi", "url", "abstract", "keywords", "score", "cosine", "retrieval_reason"]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for p in result["results"]:
            writer.writerow({f:clean_cell(p.get(f)) for f in fields})
        return "\ufeff"+output.getvalue()
    if fmt == "md":
        esc = lambda s: text(s).replace("|", "\\|").replace("\n", " ")
        lines = ["# 文献检索结果", "", f"研究问题：{esc(result['query'])}", "", f"模式：{result['actual_mode']}；返回{result['returned']}篇；耗时{result['seconds']}秒。", "", result["note"], ""]
        lines += ["> "+w for w in result["warnings"]]
        lines += ["", "|序号|文献|作者|年份|期刊|匹配依据|", "|---|---|---|---|---|---|"]
        for p in result["results"]:
            lines.append("|"+"|".join(esc(p.get(f)) for f in ("rank","title","authors","year","journal","retrieval_reason"))+"|")
        for p in result["results"]:
            lines += ["", f"## {p['rank']}. {esc(p['title'])}", "", f"作者：{esc(p['authors'])}；年份：{p['year']}；期刊：{esc(p['journal'])}", "", f"DOI：{esc(p['doi'])}", "", f"来源：{esc(p['url'])}", "", "摘要："+esc(p["abstract"]), ""]
        return "\n".join(lines)
    raise SearchError("导出格式须为json/csv/md。")


def save_export(result, path, overwrite=False):
    path = Path(path).expanduser()
    fmt = path.suffix.lower().lstrip(".")
    body = render_export(result, fmt)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w" if overwrite else "x", encoding="utf-8", newline="") as handle:
            handle.write(body)
    except FileExistsError as exc:
        raise SearchError("结果文件已存在；换文件名或明确加 --overwrite。") from exc


def status(folder):
    with locked(folder), contextlib.closing(connect(folder)) as con:
        n = con.execute("SELECT count(*) FROM papers").fetchone()[0]
        v = vector_count(con)
        profile = get_meta(con, "embedding_profile")
        return {"version":VERSION, "index_dir":str(Path(folder).resolve()), "papers":n, "missing_abstract":con.execute("SELECT count(*) FROM papers WHERE abstract='' ").fetchone()[0], "vectors_ready":v, "vector_coverage":v/n if n else 0, "embedding_profile":json.loads(profile) if profile else None, "reference_edges":con.execute("SELECT count(*) FROM edges").fetchone()[0], "reference_graph_current":get_meta(con,"edges_revision")==get_meta(con,"revision"), "indexed_at":get_meta(con,"indexed_at"), "revision":get_meta(con,"revision")}


UI = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>本地文献检索</title>
<style>body{font:16px/1.7 system-ui,sans-serif;color:#203044;background:#f3f5f8;margin:0}main{max-width:1180px;margin:32px auto;padding:0 24px}h1{font-size:30px}textarea,input,select,button{font:inherit;border:1px solid #cad2dd;border-radius:7px;padding:8px}textarea{box-sizing:border-box;width:100%;height:125px}button{background:#244f82;color:white;cursor:pointer}button:disabled{opacity:.5}.panel,article{background:white;border:1px solid #e0e5ec;border-radius:12px;padding:20px;margin:16px 0}.row{display:flex;gap:12px;flex-wrap:wrap;align-items:center}.row input{width:100px}.muted{color:#617084;font-size:14px}.warning{color:#9a4d13;white-space:pre-wrap}h2{font-size:19px;margin:0}details{margin-top:10px}pre{white-space:pre-wrap}a{color:#245b91}.pill{font-size:12px;background:#edf2f8;padding:3px 8px;border-radius:20px;margin-right:6px}</style>
<style>[hidden]{display:none!important}</style><main><h1>本地文献检索</h1><p class="muted">摘要库 → 关键词与全库语义双路检索 → 匹配依据与原始摘要。不生成论文结论，不判断创新性。</p><div id="status" class="muted">正在读取索引…</div>
<section class="panel"><label for="q">研究方向、问题或框架（多行可以分别描述问题、机制与研究场景）</label><textarea id="q" placeholder="例如：监管坏消息如何进入价格？&#10;上市公司违规从立案到处罚的逐步揭露、信息可验证性与投资者交易"></textarea>
<div class="row"><label>数量 <input id="top" type="number" min="1" max="500" value="50"></label><label>模式 <select id="mode"><option value="auto">自动（无向量则提示降级）</option><option value="hybrid">严格混合检索</option><option value="lexical">仅关键词</option><option value="dense">仅向量</option></select></label><label>年份 <input id="from" type="number" placeholder="不限"></label><span>—</span><input id="to" type="number" placeholder="不限"><button id="run">检索</button></div>
<details><summary>更多筛选</summary><div class="row"><input id="author" style="width:180px" placeholder="作者包含"><input id="journal" style="width:180px" placeholder="期刊包含"><input id="exclude" style="width:320px" placeholder="排除词，用分号分隔（硬过滤）"><label><input id="citations" type="checkbox" style="width:auto">引用补充</label></div></details></section>
<p id="message" class="warning" role="status"></p><div id="exports" class="row" hidden><button data-format="csv">下载CSV</button><button data-format="json">下载JSON（含审计）</button><button data-format="md">下载Markdown</button></div><section id="results"></section></main>
<script>let last=null;const el=id=>document.getElementById(id);const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const token='__TOKEN__';
fetch('/api/status').then(r=>r.json()).then(s=>{el('status').textContent=s.error||`索引 ${s.papers.toLocaleString()} 篇 · 有效向量 ${s.vectors_ready.toLocaleString()} 篇 · 摘要缺失 ${s.missing_abstract} 篇`}).catch(e=>el('status').textContent=e.message);
el('run').onclick=async()=>{el('run').disabled=true;el('message').textContent='检索中…';el('results').innerHTML='';el('exports').hidden=true;last=null;try{const filters={authors:el('author').value,journal:el('journal').value,exclude:el('exclude').value.split(/[;；]/).map(x=>x.trim()).filter(Boolean)};if(el('from').value)filters.year_from=Number(el('from').value);if(el('to').value)filters.year_to=Number(el('to').value);const r=await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token},body:JSON.stringify({query:el('q').value,top_k:Number(el('top').value),mode:el('mode').value,filters,citations:el('citations').checked})});const data=await r.json();if(!r.ok)throw Error(data.error||'检索失败');last=data;el('message').textContent=`返回 ${data.returned} 篇；实际模式 ${data.actual_mode}；${data.seconds} 秒。\n`+data.warnings.join('\n');el('exports').hidden=false;el('results').innerHTML=data.results.map(p=>{let url=p.doi?'https://doi.org/'+p.doi:p.url;const link=/^https?:\/\//i.test(url)?`<a target="_blank" rel="noopener noreferrer" href="${esc(url)}">打开来源 ↗</a>`:'';return `<article><h2>${p.rank}. ${esc(p.title)}</h2><div class="muted">${esc(p.authors)} · ${esc(p.year||'年份未知')} · ${esc(p.journal)}</div><p>${p.matched_by.map(x=>`<span class="pill">${esc(x)}</span>`).join('')} ${link}</p><div class="muted">${esc(p.retrieval_reason)}</div><details><summary>查看原始摘要与检索分数</summary><p style="white-space:pre-wrap">${esc(p.abstract||'源数据无摘要')}</p><p class="muted">RRF：${p.score}；余弦：${p.cosine??'未使用'}；分数不是相关概率。</p></details></article>`}).join('')}catch(e){el('message').textContent=e.message}finally{el('run').disabled=false}};
document.querySelectorAll('[data-format]').forEach(b=>b.onclick=async()=>{if(!last)return;try{const r=await fetch('/api/export?format='+b.dataset.format,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token},body:JSON.stringify(last)});if(!r.ok)throw Error('导出失败');const u=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=u;a.download='文献检索_'+new Date().toISOString().replace(/[:.]/g,'-')+'.'+b.dataset.format;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)}catch(e){el('message').textContent=e.message}});</script></html>'''


def serve(folder, backend, port=8765):
    token = os.urandom(24).hex()
    class Handler(BaseHTTPRequestHandler):
        def send(self, body, mime="application/json; charset=utf-8", code=200):
            encoded = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if not self.valid_host():
                return self.send(dump({"error":"只接受本地请求"}), code=403)
            if self.path == "/":
                return self.send(UI.replace("__TOKEN__", token), "text/html; charset=utf-8")
            if self.path == "/api/status":
                try:
                    return self.send(dump(status(folder)))
                except SearchError as exc:
                    return self.send(dump({"error":str(exc)}), code=400)
            self.send(dump({"error":"Not found"}), code=404)

        def valid_host(self):
            return self.headers.get("Host", "") in (f"127.0.0.1:{port}",f"localhost:{port}")

        def do_POST(self):
            if not self.valid_host() or self.headers.get("X-Local-Token") != token:
                return self.send(dump({"error":"本地访问校验失败，请从本机页面操作。"}), code=403)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16_000_000:
                    raise SearchError("请求体为空或过大。")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise SearchError("请求体须为JSON对象。")
                if self.path == "/api/search":
                    result = search_library(folder, data.get("query",""), top_k=int(data.get("top_k",50)), mode=data.get("mode","auto"), filters=data.get("filters",{}), backend=backend, citations=bool(data.get("citations",False)))
                    return self.send(dump(result))
                if self.path.startswith("/api/export?"):
                    fmt = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("format",["json"])[0]
                    return self.send(render_export(data,fmt), "text/plain; charset=utf-8")
                self.send(dump({"error":"Not found"}), code=404)
            except (SearchError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
                self.send(dump({"error":str(exc)}), code=400)

        def log_message(self, fmt, *args):
            pass  # Research queries are not copied into HTTP logs.

    server = ThreadingHTTPServer(("127.0.0.1",port),Handler)
    print(f"本地检索界面：http://127.0.0.1:{port}\n仅本机可访问。按Ctrl+C停止。", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def parse_mapping(items):
    mapping = {}
    for item in items:
        if "=" not in item:
            raise SearchError("--map格式：title=论文标题")
        left,right = item.split("=",1)
        mapping[left.strip()] = right.strip()
    return mapping


def load_query(args):
    if args.query_file:
        p = Path(args.query_file).expanduser()
        value = p.read_text(encoding="utf-8-sig")
        return json.loads(value) if p.suffix.lower()==".json" else value
    return args.query or input("请输入研究方向：").strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command",required=True)
    for name in ("inspect","index","embed","links","status","search","serve"):
        p = sub.add_parser(name)
        p.add_argument("--index-dir",type=Path,default=DEFAULT_DIR,help="工具自己的索引目录，不是原始摘要目录")
        if name in ("inspect","index"):
            p.add_argument("--input",nargs="+",required=True,type=Path)
            p.add_argument("--encoding",default="utf-8-sig")
            p.add_argument("--sheet",help="Excel工作表，默认第一张")
            p.add_argument("--table",default="papers",help="SQLite源表名")
            p.add_argument("--map",action="append",default=[],metavar="标准字段=源列名")
            p.add_argument("--limit",type=int,default=0,help="测试导入时每个文件的行数上限；0为全部")
            p.add_argument("--skip-excluded",action="store_true",help="导入旧索引时跳过excluded=1/true/yes的记录；CSV通常不需要")
        if name in ("embed","search","serve"):
            p.add_argument("--model",default=MODEL)
            p.add_argument("--base-url",default=BASE_URL)
            p.add_argument("--max-chars",type=int,default=16000,help="单篇embedding文本字符上限；更改后不能混用原向量")
            p.add_argument("--allow-remote",action="store_true",help="明确允许把摘要/查询发送到指定非本机模型服务")
        if name == "embed":
            p.add_argument("--batch-size",type=int,default=24)
            p.add_argument("--max-seconds",type=float,default=300,help="批次间软时间预算，默认300秒；明确设0为不限，可中断续算")
        if name == "serve":
            p.add_argument("--port",type=int,default=8765)
        if name == "search":
            group = p.add_mutually_exclusive_group()
            group.add_argument("--query","-q")
            group.add_argument("--query-file",type=Path,help="TXT/MD研究框架，或结构化JSON")
            p.add_argument("--top",type=int,default=50)
            p.add_argument("--mode",choices=["auto","hybrid","lexical","dense"],default="auto")
            p.add_argument("--pool",type=int,default=300)
            p.add_argument("--year-from",type=int)
            p.add_argument("--year-to",type=int)
            p.add_argument("--journal")
            p.add_argument("--author")
            p.add_argument("--language")
            p.add_argument("--exclude",action="append",default=[],help="标题/摘要包含该字串则硬排除；可重复")
            p.add_argument("--include-nonarticles",action="store_true",help="包含明确标识为目录、卷索引等的标题；默认过滤")
            p.add_argument("--diversity",type=float,default=0.08,help="标题/关键词多样性强度；0关闭")
            p.add_argument("--allow-partial",action="store_true")
            p.add_argument("--citations",action="store_true")
            p.add_argument("--output",type=Path,help=".json/.csv/.md；默认仅打印JSON")
            p.add_argument("--overwrite",action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command in ("embed","search","serve"):
            if args.max_chars < 256:
                raise SearchError("max-chars至少256。")
            backend = Ollama(args.base_url,args.model,args.max_chars,args.allow_remote)
        if args.command == "inspect":
            items = []
            for path in args.input:
                rows = read_records(path,args.encoding,args.sheet,args.table)
                try:
                    first = next(rows,None)
                    if first is None:
                        raise SearchError(f"空文件：{path}")
                    items.append({"file":str(path),"columns":list(first),"mapping":column_mapping(first,parse_mapping(args.map)),"first_record":{k:text(v)[:300] for k,v in first.items()}})
                finally:
                    rows.close()
            result = items
        elif args.command == "index":
            result = import_library(args.index_dir,args.input,parse_mapping(args.map),args.encoding,args.sheet,args.table,args.limit,args.skip_excluded)
        elif args.command == "embed":
            result = embed_library(args.index_dir,backend,args.batch_size,args.max_seconds)
        elif args.command == "links":
            result = build_links(args.index_dir)
        elif args.command == "status":
            result = status(args.index_dir)
        elif args.command == "serve":
            serve(args.index_dir,backend,args.port)
            return 0
        else:
            result = search_library(args.index_dir,load_query(args),args.top,args.mode,{"year_from":args.year_from,"year_to":args.year_to,"authors":args.author,"journal":args.journal,"language":args.language,"exclude":args.exclude,"include_nonarticles":args.include_nonarticles},args.pool,args.diversity,backend,args.allow_partial,args.citations)
            if args.output:
                save_export(result,args.output,args.overwrite)
                print(f"已保存 {args.output.resolve()}",file=sys.stderr)
        print(dump(result))
        return 0
    except KeyboardInterrupt:
        print("已停止；已提交的向量批次可续算，导入中的事务会回滚。",file=sys.stderr)
        return 130
    except (SearchError,OSError,ValueError,sqlite3.Error) as exc:
        print(f"错误：{exc}",file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
