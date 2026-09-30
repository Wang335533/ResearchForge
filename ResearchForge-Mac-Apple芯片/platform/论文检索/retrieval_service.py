"""ResearchForge adapter: authoritative WOS/CNKI corpus and retrieval.

Importing this module is read-only. Call SERVICE.start() at application startup.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import urlparse

import requests
import literature_search as ls

BASE = Path(__file__).resolve().parent
SOURCE = Path(os.environ.get("RF_CORPUS_PATH", BASE.parents[1] / "user_data/corpus.csv"))
INDEX = Path(os.environ.get("RF_RETRIEVAL_INDEX", BASE.parents[1] / "user_data/abstract-index"))
SCOPE_VERSION = "wos-article-review-cnki-journal-nonempty-abstract-v2"
RECENT_PAPER_LIMIT = 20
SUPPLEMENT_PAPER_LIMIT = 30
LANGUAGE_PAPER_LIMIT = 25
LANGUAGE_RECENT_LIMIT = 10
LANGUAGES = tuple(json.loads(os.environ.get("RF_RETRIEVAL_LANGUAGES", '["zh", "en"]')))
LANGUAGE_POLICY = "balanced-configured-languages-v1"
COLUMNS = ("ut", "title", "authors", "source_title", "publication_year", "doi",
           "abstract", "keywords_plus", "document_types")
OPTIONAL_COLUMNS = ("language", "source_database")


class NotReady(ls.SearchError):
    pass


def find_ollama():
    executable = shutil.which("ollama")
    if executable:
        return executable
    if os.name == "nt":
        # A desktop app launched before installation may still have the old PATH.
        local = Path(os.environ.get("LOCALAPPDATA", Path.home()/"AppData"/"Local"))
        candidate = local / "Programs" / "Ollama" / "ollama.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def fingerprint(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def import_corpus(source=SOURCE, folder=INDEX, progress=None):
    """Synchronize eligible records by UT in one transaction; retain raw text."""
    import pyarrow as pa
    import pyarrow.csv as csv

    source, folder = Path(source).resolve(), Path(folder).resolve()
    sha = fingerprint(source)
    report = progress or (lambda **kwargs: None)
    with ls.locked(folder), contextlib.closing(ls.connect(folder, True)) as con:
        if ls.get_meta(con, "source_sha256") == sha and ls.get_meta(con, "scope_version") == SCOPE_VERSION:
            result = json.loads(ls.get_meta(con, "source_audit", "{}"))
            report(phase="importing", scanned=result.get("rows", 0), papers=result.get("papers", 0))
            return {**result, "reused": True}
        reader = csv.open_csv(source, read_options=csv.ReadOptions(block_size=8*1024*1024),
            parse_options=csv.ParseOptions(newlines_in_values=True),
            convert_options=csv.ConvertOptions(include_columns=list(COLUMNS + OPTIONAL_COLUMNS),
                include_missing_columns=True,
                column_types={c: pa.string() for c in COLUMNS + OPTIONAL_COLUMNS}, strings_can_be_null=False))
        # Only the new metadata columns are optional; malformed old schemas fail.
        import csv as std_csv
        with source.open(encoding="utf-8-sig", newline="") as f:
            header = next(std_csv.reader(f))
        if any(c not in header for c in COLUMNS):
            raise ls.SearchError("语料缺少必需字段；未更改索引。")
        audit = dict(rows=0, papers=0, excluded_type=0, excluded_abstract=0,
                     added=0, updated=0, removed=0, source_sha256=sha, source=str(source),
                     languages={}, databases={})
        with con:
            con.execute("CREATE TEMP TABLE seen_ut(ut TEXT PRIMARY KEY)")
            for batch in reader:
                for row in batch.to_pylist():
                    audit["rows"] += 1
                    types = {x.strip() for x in row["document_types"].split("|")}
                    database = (row.get("source_database") or "WOS").strip()
                    cnki_journal = database == "CNKI" and row["ut"].startswith("CNKI:") and "Journal Record" in types
                    if not types & {"Article", "Review"} and not cnki_journal:
                        audit["excluded_type"] += 1
                        continue
                    if not row["abstract"].strip():
                        audit["excluded_abstract"] += 1
                        continue
                    ut = row["ut"].strip()
                    if not ut or not row["title"].strip():
                        raise ls.SearchError(f"第 {audit['rows']} 条合格记录缺少 ut 或题名，导入已回滚。")
                    try:
                        con.execute("INSERT INTO seen_ut VALUES(?)", (ut,))
                    except Exception as exc:
                        raise ls.SearchError(f"重复的文献 ut：{ut}；未自动合并，导入已回滚。") from exc
                    yr = row["publication_year"].strip()
                    if not re.fullmatch(r"\d{4}", yr):
                        raise ls.SearchError(f"{ut} 的年份不是四位数字。")
                    p = dict(ut=ut, title=row["title"], title_key=ls.key(row["title"]),
                        authors=row["authors"], author_key=ls.key(re.split(r"[;；|]", row["authors"])[0]),
                        abstract=row["abstract"], keywords=row["keywords_plus"], year=int(yr),
                        journal=row["source_title"], doi=row["doi"].strip(), url="",
                        language=(row.get("language") or ("zh" if database == "CNKI" else "en")).strip(),
                        refs="[]", document_types=row["document_types"])
                    p["text_hash"] = ls.digest(ls.document_text(p))
                    old = con.execute("SELECT * FROM papers WHERE ut<>'' AND ut=?", (ut,)).fetchone()
                    changed = old is None or any(old[k] != v for k, v in p.items())
                    if changed:
                        p["updated_at"] = ls.now()
                        if old:
                            pid = old["id"]
                            con.execute("UPDATE papers SET "+",".join(f"{k}=?" for k in p)+" WHERE id=?", [*p.values(), pid])
                            con.execute("DELETE FROM search WHERE rowid=?", (pid,))
                            audit["updated"] += 1
                        else:
                            pid = con.execute("INSERT INTO papers("+",".join(p)+") VALUES("+",".join("?" for _ in p)+")", list(p.values())).lastrowid
                            audit["added"] += 1
                        con.execute("INSERT INTO search(rowid,title,keywords,abstract,authors,journal) VALUES(?,?,?,?,?,?)",
                            [pid, *(ls.fts_text(p[f]) for f in ("title", "keywords", "abstract", "authors", "journal"))])
                    audit["papers"] += 1
                    audit["languages"][p["language"]] = audit["languages"].get(p["language"], 0) + 1
                    audit["databases"][database] = audit["databases"].get(database, 0) + 1
                report(phase="importing", scanned=audit["rows"], papers=audit["papers"])
            # The CSV is authoritative; departing UTs lose their derived index entries.
            gone = "SELECT id FROM papers WHERE ut NOT IN (SELECT ut FROM seen_ut)"
            audit["removed"] = con.execute("SELECT count(*) FROM papers WHERE ut NOT IN (SELECT ut FROM seen_ut)").fetchone()[0]
            con.execute(f"DELETE FROM sources WHERE paper_id IN ({gone})")
            con.execute(f"DELETE FROM vectors WHERE paper_id IN ({gone})")
            con.execute(f"DELETE FROM edges WHERE source_id IN ({gone}) OR target_id IN ({gone})")
            con.execute(f"DELETE FROM search WHERE rowid IN ({gone})")
            con.execute(f"DELETE FROM papers WHERE id IN ({gone})")
            if not audit["papers"]:
                raise ls.SearchError("没有符合收录规则的文献，导入已回滚。")
            if fingerprint(source) != sha:
                raise ls.SearchError("CSV 在导入过程中发生变化，导入已回滚，请重试。")
            audit["duplicate_doi_groups"] = con.execute("SELECT count(*) FROM (SELECT lower(doi) FROM papers WHERE doi<>'' GROUP BY lower(doi) HAVING count(*)>1)").fetchone()[0]
            if any(audit[k] for k in ("added", "updated", "removed")):
                ls.set_meta(con, "revision", str(int(ls.get_meta(con, "revision", "0"))+1))
            for k, v in {"source_sha256": sha, "source_path": str(source), "scope_version": SCOPE_VERSION,
                         "source_audit": json.dumps(audit, ensure_ascii=False), "indexed_at": ls.now()}.items():
                ls.set_meta(con, k, v)
        return {**audit, "reused": False}


class LocalOllama(ls.Ollama):
    """Proxy-free loopback requests with bounded transient retries."""
    def __init__(self, base=None, model=None, max_chars=16000):
        model = model or os.environ.get("RF_EMBEDDING_MODEL", ls.MODEL)
        base = (base or os.environ.get("OLLAMA_HOST", ls.BASE_URL)).strip().rstrip("/")
        if not base.startswith(("http://", "https://")):
            base = "http://" + base
        base = base.replace("://0.0.0.0", "://127.0.0.1")
        super().__init__(base, model, max_chars)
        self.session = requests.Session()
        self.session.trust_env = False
        self._profile = None

    def request(self, endpoint, payload=None, timeout=90):
        for attempt in range(3):
            try:
                r = self.session.request("GET" if payload is None else "POST", self.base+endpoint,
                                         json=payload, timeout=(8, timeout))
                if r.status_code >= 500:
                    raise requests.ConnectionError(f"Ollama HTTP {r.status_code}: {r.text[:200]}")
                if not r.ok:
                    raise ls.SearchError(f"Ollama HTTP {r.status_code}: {r.text[:500]}")
                return r.json()
            except (requests.RequestException, ValueError) as exc:
                if attempt == 2:
                    raise ls.SearchError(f"Ollama 请求失败，已重试：{exc}") from exc
                time.sleep(2*(attempt+1))

    def identity(self):
        models = self.request("/api/tags", timeout=8).get("models", [])
        m = next((m for m in models if self.model in (m.get("name"), m.get("model"))), None)
        if not m:
            raise ls.SearchError(f"缺少 embedding 模型 {self.model}，请重新准备检索。")
        return {"backend": "ollama", "model": self.model, "digest": m.get("digest", ""),
                "max_chars": self.max_chars, "text_version": 1, "query_instruction": ls.QUERY_INSTRUCTION}

    def embed(self, values):
        np = ls.np_module()
        r = self.request("/api/embed", {"model": self.model, "input": values, "truncate": False, "keep_alive": "10m"}, timeout=300)
        a = np.asarray(r.get("embeddings", []), dtype=np.float32)
        if a.ndim != 2 or a.shape[0] != len(values) or not a.shape[1] or not np.isfinite(a).all():
            raise ls.SearchError("Embedding 返回无效向量。")
        norm = np.linalg.norm(a, axis=1, keepdims=True)
        if (norm <= 1e-12).any():
            raise ls.SearchError("Embedding 返回零向量。")
        return a / norm

    def ensure(self, progress):
        progress(phase="ollama", message="检查本机 Ollama 服务")
        try:
            r = self.session.get(self.base+"/api/tags", timeout=2)
            r.raise_for_status()
        except requests.RequestException:
            executable = find_ollama()
            if not executable:
                raise ls.SearchError("未安装 Ollama。请安装 Ollama 后点击重新准备。")
            # Do not start a service on somebody else's remote host.
            if urlparse(self.base).hostname not in ("localhost", "127.0.0.1", "::1"):
                raise ls.SearchError("只能自动启动本机 Ollama。")
            options = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
            if os.name == "nt":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW
            subprocess.Popen([executable, "serve"], env={**os.environ, "OLLAMA_HOST": self.base}, **options)
            for _ in range(60):
                try:
                    r = self.session.get(self.base+"/api/tags", timeout=2)
                    if r.ok:
                        break
                except requests.RequestException:
                    pass
                time.sleep(1)
            else:
                raise ls.SearchError("Ollama 启动超时，请检查本机服务后重试。")
        models = self.request("/api/tags", timeout=8).get("models", [])
        if not any(self.model in (m.get("name"), m.get("model")) for m in models):
            progress(phase="downloading", message=f"下载 {self.model}", download_completed=0, download_total=0)
            for attempt in range(3):
                try:
                    layers = {}
                    with self.session.post(self.base+"/api/pull", json={"model": self.model, "stream": True},
                                           stream=True, timeout=(8, 300)) as r:
                        r.raise_for_status()
                        success = False
                        for line in r.iter_lines():
                            if not line:
                                continue
                            item = json.loads(line)
                            if item.get("error"):
                                raise ls.SearchError(item["error"])
                            if item.get("digest"):
                                previous = layers.get(item["digest"], (0, 0))
                                layers[item["digest"]] = (item.get("completed", previous[0]), item.get("total", previous[1]))
                            progress(phase="downloading", message=item.get("status", "下载中"),
                                     download_completed=sum(x[0] for x in layers.values()), download_total=sum(x[1] for x in layers.values()))
                            success = item.get("status") == "success"
                        if not success:
                            raise ls.SearchError("模型下载流提前结束。")
                    break
                except (requests.RequestException, ValueError, ls.SearchError) as exc:
                    if attempt == 2:
                        raise ls.SearchError(f"模型下载失败，可重试续传：{exc}") from exc
                    time.sleep(2*(attempt+1))
        self.identity()  # Pull completion alone is not sufficient proof of readiness.


class RetrievalService:
    def __init__(self, source=SOURCE, index_dir=INDEX, backend=None):
        self.source, self.index_dir = Path(source).resolve(), Path(index_dir).resolve()
        self.backend = backend or LocalOllama()
        self._guard = threading.Lock()
        self._thread = None
        self._state = dict(phase="idle", ready=False, model=getattr(self.backend, "model", ls.MODEL),
            papers=0, vectors_ready=0, scanned=0, error=None, message="请先配置自己的摘要库，再点击准备检索", started_at=None, finished_at=None)
        self._source_stat = None

    def update(self, **values):
        with self._guard:
            self._state.update(values)
            self._state["updated_at"] = time.time()

    def status(self):
        with self._guard:
            s = dict(self._state)
        s["coverage"] = s["vectors_ready"]/s["papers"] if s["papers"] else 0
        s["elapsed_seconds"] = round((s["finished_at"] or time.time())-s["started_at"], 1) if s["started_at"] else 0
        s["source"] = str(self.source)
        s["language_policy"] = LANGUAGE_POLICY
        s["language_quota"] = {language:LANGUAGE_PAPER_LIMIT for language in LANGUAGES}
        return s

    def start(self):
        if not self.source.is_file():
            self.update(phase='idle', ready=False, message='请先在配置指南中添加自己的摘要 CSV，再点击准备检索。')
            return False
        import csv
        with self.source.open(encoding='utf-8-sig', newline='') as source:
            rows = csv.reader(source)
            next(rows, None)
            has_records = next(rows, None) is not None
        if not has_records:
            self.update(phase='idle', ready=False, message='摘要 CSV 只有字段名，请先添加自己的文献。')
            return False
        with self._guard:
            if self._thread is not None and self._thread.is_alive():
                return False
            if self._state["ready"]:
                return False
            self._state.update(ready=False, phase="starting", error=None, message="开始准备", started_at=time.time(), finished_at=None)
            self._thread = threading.Thread(target=self._run, name="researchforge-retrieval", daemon=True)
            self._thread.start()
            return True

    def _run(self):
        try:
            # A different lock file serializes complete preparations across processes.
            with ls.locked(self.index_dir.parent / (".prepare-"+self.index_dir.name)):
                if hasattr(self.backend, "ensure"):
                    self.backend.ensure(self.update)
                self.update(phase="importing", message="导入合格文献")
                audit = import_corpus(self.source, self.index_dir, self.update)
                self.update(papers=audit["papers"], source_sha256=audit["source_sha256"], audit=audit)
                profile = json.dumps(self.backend.identity(), sort_keys=True, ensure_ascii=False)
                with ls.locked(self.index_dir), contextlib.closing(ls.connect(self.index_dir)) as con:
                    saved = ls.get_meta(con, "embedding_profile")
                    if saved and saved != profile:
                        with con:
                            con.execute("DELETE FROM vectors")
                            con.execute("DROP TABLE IF EXISTS query_vectors")
                            ls.set_meta(con, "embedding_profile", profile)
                ls.embed_library(self.index_dir, self.backend, batch_size=24, progress=self.update)
                state = ls.status(self.index_dir)
                if not state["papers"] or state["vectors_ready"] != state["papers"]:
                    raise ls.SearchError("全库向量覆盖率不足，准备未完成。")
                if json.dumps(self.backend.identity(), sort_keys=True, ensure_ascii=False) != profile:
                    raise ls.SearchError("构建期间模型版本发生变化，请重新准备；不同模型的向量不可混用。")
                if fingerprint(self.source) != audit["source_sha256"]:
                    raise ls.SearchError("向量构建期间 CSV 发生变化，请重新准备以同步语料。")
                vector_meta = json.loads((self.index_dir/"vectors.json").read_text(encoding="utf-8"))
                stat = self.source.stat()
                self._source_stat = (stat.st_size, stat.st_mtime_ns)
                self.update(phase="ready", ready=True, error=None, message="全库混合检索已就绪",
                            papers=state["papers"], vectors_ready=state["vectors_ready"], dimensions=vector_meta["dimensions"], finished_at=time.time())
        except Exception as exc:
            self.update(phase="error", ready=False, error=str(exc), message="准备失败，可点击重试", finished_at=time.time())

    def retrieve(self, query, recent_year_min):
        if not self.status()["ready"]:
            raise NotReady("检索尚未就绪，请等待模型与全库向量准备完成。")
        # Validate before index/model access. Bad input must not invalidate readiness.
        ls.make_queries(query)
        try:
            st = self.source.stat()
            if self._source_stat != (st.st_size, st.st_mtime_ns):
                self.update(ready=False, phase="error", error="语料文件已变化，请重新准备。")
                raise NotReady("语料文件已变化，请重新准备。")
            by_language, searches, pools, warnings = {}, [], {}, []
            for language in LANGUAGES:
                recent = ls.search_library(self.index_dir, query, top_k=LANGUAGE_RECENT_LIMIT,
                    mode="hybrid", filters={"year_from":recent_year_min,"language":language,
                                            "include_nonarticles":True}, backend=self.backend)
                all_years = ls.search_library(self.index_dir, query, top_k=LANGUAGE_PAPER_LIMIT,
                    mode="hybrid", filters={"language":language,"include_nonarticles":True}, backend=self.backend)
                searches.extend((recent,all_years))
                warnings.extend(recent["warnings"]+all_years["warnings"])
                chosen, seen = [], set()
                # Fill a shortage of recent papers from the same language's full corpus.
                for pool, result in (("recent",recent),("supplement",all_years)):
                    for paper in result["results"]:
                        if paper["language"] != language:
                            raise ls.SearchError("检索语言筛选结果不一致，未保存。")
                        if paper["ut"] in seen or len(chosen) >= LANGUAGE_PAPER_LIMIT:
                            continue
                        seen.add(paper["ut"])
                        chosen.append((paper,pool))
                by_language[language] = chosen
            # Equality is deliberate: never silently replace a missing language
            # with additional papers from the other one or duplicate references.
            count = min(LANGUAGE_PAPER_LIMIT, *(len(by_language[x]) for x in LANGUAGES))
            if count < LANGUAGE_PAPER_LIMIT:
                warnings.append(f"中英文候选不足：中文 {len(by_language['zh'])} 篇、英文 {len(by_language['en'])} 篇；为保持等额，本次各选 {count} 篇，未用另一语言补齐。")
            selected = []
            for rank in range(count):
                for language in LANGUAGES:
                    selected.append(by_language[language][rank])
            for language in LANGUAGES:
                group = by_language[language][:count]
                recent_count = sum(pool == "recent" for _,pool in group)
                pools[language] = {"recent":recent_count,"supplement":len(group)-recent_count}
            papers = []
            for i, (p, pool) in enumerate(selected, 1):
                sim = p.get("cosine")
                papers.append({"ut": p["ut"], "source_id": p["ut"], "reference_label": f"P{i}",
                    "title": p["title"], "authors": p["authors"], "journal": p["journal"],
                    "year": p["year"], "doi": p["doi"], "abstract": p["abstract"],
                    "keywords": p["keywords"], "document_types": p["document_types"],
                    "language": p["language"],
                    "source_database": "CNKI" if p["ut"].startswith("CNKI:") else "WOS" if p["ut"].startswith("WOS:") else "自建",
                    "evidence_scope": "abstract", "recent": p["year"] >= recent_year_min,
                    "selection_pool": pool, "retrieval_score": p["score"], "sim": sim,
                    "semantic_similarity": sim, "matched_by": p["matched_by"],
                    "retrieval_reason": p["retrieval_reason"]})
            recent_count = sum(pool == "recent" for _,pool in selected)
            return {"papers":papers,"corpus":searches[-1]["papers_in_index"],"recent_year_min":recent_year_min,
                    "retrieval":{"mode":"hybrid","model":getattr(self.backend,"model",ls.MODEL),
                        "language_policy":LANGUAGE_POLICY,
                        "language_quota":{language:LANGUAGE_PAPER_LIMIT for language in LANGUAGES},
                        "language_counts":{language:count for language in LANGUAGES},"language_pools":pools,
                        "dense_coverage":searches[-1]["dense_coverage"],"recent_count":recent_count,
                        "supplement_count":len(selected)-recent_count,
                        "seconds":sum(result["seconds"] for result in searches),
                        "warnings":list(dict.fromkeys(warnings))}}
        except (NotReady, ls.QueryLimitError):
            raise
        except Exception as exc:
            self.update(ready=False, phase="error", error=str(exc), message="检索不可用，请重新准备")
            raise ls.SearchError(str(exc)) from exc


SERVICE = RetrievalService()


def main():
    parser = argparse.ArgumentParser(description="ResearchForge 文献索引维护")
    parser.add_argument("command", choices=("prepare", "status", "search"))
    parser.add_argument("--query", "-q")
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--index-dir", type=Path, default=INDEX)
    args = parser.parse_args()
    service = RetrievalService(args.source, args.index_dir)
    if args.command == "status":
        print(json.dumps(ls.status(args.index_dir), ensure_ascii=False, indent=2))
        return
    service.start()
    while service._thread.is_alive():
        print(json.dumps(service.status(), ensure_ascii=False), flush=True)
        service._thread.join(10)
    state = service.status()
    print(json.dumps(state, ensure_ascii=False, indent=2), flush=True)
    if not state["ready"]:
        raise SystemExit(1)
    if args.command == "search":
        if not args.query:
            parser.error("search 需要 --query")
        from datetime import datetime
        print(json.dumps(service.retrieve(args.query, datetime.now().year-5), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
