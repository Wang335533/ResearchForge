#!/usr/bin/env python3
"""Build this installation's local full-text lookup; no model or network calls."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import unicodedata

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'platform/paper_reader'))
from run import load_config, resolve_path
from documents import extract_document


def normalized(value):
    return re.sub(r'[\W_]+','',unicodedata.normalize('NFKC',value).casefold())


def replace_file(source, target, attempts=40):
    """os.replace that waits out a brief concurrent reader, which Windows treats as a lock."""
    for attempt in range(attempts):
        try:
            return os.replace(source, target)
        except PermissionError:
            if os.name != 'nt' or attempt == attempts - 1:
                raise
            time.sleep(0.05)


def build(root,index,metadata=None):
    root,index=Path(root).resolve(),Path(index).resolve()
    if not root.is_dir():raise ValueError('全文目录不存在')
    values={}
    if metadata:
        with Path(metadata).open(encoding='utf-8-sig',newline='') as stream:
            for row in csv.DictReader(stream):
                key=row.get('path','').strip().replace('\\','/')
                if not key:raise ValueError('元数据每行都必须有相对于全文目录的 path')
                if key in values:raise ValueError('元数据的 path 不得重复')
                values[key]=row
    files=[p for p in sorted(root.rglob('*')) if p.is_file() and p.suffix.lower() in ('.md','.markdown','.txt','.pdf')]
    index.parent.mkdir(parents=True,exist_ok=True)
    pending=index.with_name(index.name+'.pending')
    pending.unlink(missing_ok=True)
    con=sqlite3.connect(pending)
    skipped=[];count=0
    try:
        con.executescript('''CREATE TABLE index_meta(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE papers(id INTEGER PRIMARY KEY,paper_uid TEXT UNIQUE,path TEXT,title TEXT,title_norm TEXT,
        abstract TEXT,intro TEXT,keywords TEXT,language TEXT,journal TEXT,year INTEGER,doi TEXT,mtime_ns INTEGER,excluded INTEGER);
        CREATE INDEX idx_papers_title_norm ON papers(title_norm);
        CREATE INDEX idx_papers_doi ON papers(doi);
        CREATE VIRTUAL TABLE papers_fts USING fts5(title,abstract,intro,keywords,content='papers',content_rowid='id',tokenize='trigram');''')
        for file in files:
            if file.is_symlink() or not file.resolve().is_relative_to(root):
                skipped.append({'file':str(file.relative_to(root)),'reason':'不跟随符号链接或目录外文件'});continue
            rel=file.relative_to(root).as_posix();row=values.get(rel,{})
            try:
                if file.stat().st_size>40*1024*1024:raise ValueError('超过40MB')
                # Extraction confirms readability. The index stores supplied metadata only.
                extract_document(file,file.name)
                year=int(row['year']) if row.get('year','').strip() else None
                if year is not None and not 1800<=year<=2100:raise ValueError('年份超出范围')
                title=row.get('title','').strip() or file.stem
                lang=row.get('language','').strip() or ('zh' if re.search(r'[\u4e00-\u9fff]',title) else 'en')
                uid='file:'+hashlib.sha256(rel.encode()).hexdigest()[:32]
                con.execute('INSERT INTO papers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (count+1,uid,str(file.resolve()),title,normalized(title),row.get('abstract',''),'',row.get('keywords',''),
                     lang,row.get('journal',''),year,row.get('doi',''),file.stat().st_mtime_ns,0))
                count+=1
            except Exception as exc:
                skipped.append({'file':rel,'reason':str(exc)})
        con.execute("INSERT INTO papers_fts(papers_fts) VALUES('rebuild')")
        con.executemany('INSERT INTO index_meta VALUES(?,?)',[('indexed_at',time.strftime('%Y-%m-%d %H:%M:%S')),('root',str(root))])
        con.commit();con.close();replace_file(pending,index)
    except Exception:
        con.close();pending.unlink(missing_ok=True);raise
    return {'indexed':count,'skipped':skipped,'index':str(index),'metadata_note':'未提供元数据的条目只按文件名检索，题名和年份需要人工核对。'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config');parser.add_argument('--metadata',help='列名见 examples/fulltext-metadata.template.csv')
    parser.add_argument('--root');parser.add_argument('--index')
    args=parser.parse_args();cfg=load_config(args.config)
    root=resolve_path(args.root or cfg.get('papers','user_data/papers'))
    index=resolve_path(args.index) if args.index else resolve_path(cfg.get('workspace','user_data'))/'fulltext-index.sqlite3'
    print(json.dumps(build(root,index,args.metadata),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
