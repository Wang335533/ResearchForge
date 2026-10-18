#!/usr/bin/env python3
"""Build this installation's local full-text lookup; no model or network calls.

Only file names and existing metadata are read, never the full texts. Each file gets
bibliographic fields from, in order of trust: the abstract library export (matched by
DOI, DOI-shaped file name or a unique title), the read-only file locator (titles and
DOIs taken from the Markdown headers), and the library's folder conventions
(`<field>/<journal>/.../<year>/`, `caj_<期刊>/<年-月>/<题名>_<作者>.md`,
`working_papers/<series>/<year>/`). Sample copies and non-paper folders are kept out
of search. The online literature API, when configured, is used at search time instead.
"""
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
import uuid
from urllib.parse import unquote

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'platform/paper_reader'))
from run import load_config, resolve_path
from documents import extract_document

SUFFIXES=('.md','.markdown','.txt','.pdf')
NON_LITERATURE={'writing skill'}
ACRONYMS={'acm','ieee','mis','nber','oecd','imf','rand','informs','sec','ssrn','jstor','cepr','bis'}
SMALL_WORDS={'a','an','and','as','at','by','for','from','in','into','of','on','or','the','to','with','vs','via'}
PLUMBING={'pdf','pdfs','output','md','markdown','files','sample'}
YEAR=re.compile(r'(1[89]\d\d|20\d\d)(?:$|[-_])')
CORPUS_FIELDS=('title','authors','source_title','publication_year','volume','issue','start_page','end_page','doi','abstract','keywords_plus')


def normalized(value):
    return re.sub(r'[\W_]+','',unicodedata.normalize('NFKC',value).casefold())


def doi_key(value):
    value=unquote(str(value or '')).strip().casefold()
    value=re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)','',value).rstrip(' .;,')
    return value if re.fullmatch(r'10\.\d{4,9}/\S+',value) else ''


def smart_title(text):
    """Title case for ALL-CAPS or lower-case English names, keeping acronyms and mixed case."""
    words=text.split()
    out=[]
    for i,word in enumerate(words):
        bare=word.strip('()[]:,.')
        if any(c.isdigit() for c in word) or (bare[1:]!=bare[1:].lower() and bare!=bare.upper()):
            out.append(word)
        elif bare.lower() in ACRONYMS:
            out.append(word.upper())
        elif bare.lower() in SMALL_WORDS and i:
            out.append(word.lower())
        else:
            out.append(word[:1].upper()+word[1:].lower())
    return ' '.join(out)


def clean_title(text):
    text=unicodedata.normalize('NFC',str(text or ''))
    text=re.sub(r'\\([*_#`\[\]()])',r'\1',text).replace('*','').strip(' #\t\r\n')
    text=re.sub(r'\s+',' ',text).strip()
    letters=[c for c in text if c.isalpha() and c.isascii()]
    if len(letters)>12 and all(c.isupper() for c in letters):
        text=smart_title(text)
    return text


def journal_name(folder):
    words=folder.replace('_',' ').split()
    # "journal of marketing research jmr" -> drop a trailing token that only repeats the initials.
    if len(words)>2:
        initials=''.join(w[0] for w in words[:-1] if w.lower() not in SMALL_WORDS)
        if words[-1].lower()==initials.lower():words=words[:-1]
    return smart_title(' '.join(words)) if re.search(r'[A-Za-z]',folder) and not re.search(r'[\u4e00-\u9fff]',folder) else ' '.join(words)


def path_info(parts,stem):
    """Journal, year and (for CNKI files) title/author encoded by the library's folder layout."""
    folders=list(parts[:-1])
    info={'sample':bool(folders and folders[0]=='sample')}
    if info['sample']:folders=folders[1:]
    if folders and folders[0] in NON_LITERATURE:
        info['exclude']='非论文资料'
        return info
    for folder in folders:
        m=YEAR.match(folder)
        if m:info['year']=int(m.group(1));break
    names=[f for f in folders if not YEAR.match(f)]
    if names and names[0]=='working_papers' and len(names)>1:
        info['journal']=names[1]+' Working Paper' if names[1].isupper() else names[1]
    elif names and (m:=re.match(r'(?:caj|cai|PDF)_(.+)',names[0])):
        info['journal']=m.group(1)
        if '_' in stem:
            title,author=stem.rsplit('_',1)
            if 0<len(author)<=20 and re.search(r'[\u4e00-\u9fff]',title):
                info['title'],info['authors']=title.strip().replace('_','：'),author.strip()
    elif len(names)>=2:
        candidates=[f for f in names[1:] if f.lower() not in PLUMBING and not re.search(r'_pdfs?$',f.lower())]
        if candidates:info['journal']=journal_name(max(candidates,key=len))
    return info


def filename_doi(stem):
    m=re.match(r'(10\.\d{4,9})_([^_\s]+)',stem)
    return doi_key(m.group(1)+'/'+m.group(2)) if m else ''


def load_corpus(path,progress=None):
    """Index the abstract library export by DOI, DOI suffix and normalized title."""
    by_doi,by_suffix,by_title={},{},{}
    csv.field_size_limit(1<<30)
    with Path(path).open(encoding='utf-8-sig',newline='') as stream:
        for row in csv.DictReader(stream):
            row={k:(row.get(k) or '').strip() for k in CORPUS_FIELDS}
            key=doi_key(row['doi'])
            if key:
                by_doi[key]=row
                suffix=key.split('/',1)[1]
                by_suffix[suffix]=None if suffix in by_suffix else row
            title=normalized(row['title'])
            if len(title)>=12:by_title.setdefault(title,[]).append(row)
    if progress:progress(f'摘要库已载入 {len(by_doi):,} 条 DOI 记录')
    return by_doi,by_suffix,by_title


def corpus_match(corpus,doi,stem,title_norm,year):
    by_doi,by_suffix,by_title=corpus
    if doi and doi in by_doi:return by_doi[doi]
    # Publisher file names such as 'j.1540-6261.2010.01624.x' are the DOI after its prefix.
    suffix=by_suffix.get(stem.casefold()) if len(stem)>=8 and re.search(r'[.-]',stem) else None
    if suffix:return suffix
    rows=by_title.get(title_norm,[]) if len(title_norm)>=12 else []
    if year:rows=[r for r in rows if not r['publication_year'].isdigit() or abs(int(r['publication_year'])-year)<=2]
    return rows[0] if len(rows)==1 else None


def person(name):
    """'ATLAS, CM' -> 'Atlas, CM': title-case an all-caps surname, keep initials."""
    surname,_,given=name.strip().partition(',')
    if surname.isupper() and len(surname)>2:surname=surname.title()
    return surname+(','+given if _ else '')


def clean_authors(value,language,fallback=''):
    """Author lists read from Markdown headers are noisy; keep them only when they look like names."""
    text=str(value or '').strip()
    if not text:return fallback
    if language=='zh' or re.search(r'[\u4e00-\u9fff]',text):
        names=[n for n in re.split(r'[\s,，;；、]+',text) if n]
        if 0<len(names)<=30 and all(re.fullmatch(r'[\u4e00-\u9fff·]{2,6}',n) for n in names):return '; '.join(names)
        return fallback
    if '#' in text or len(text)>600 or re.search(r'\d{3,}|https?://|@',text):return fallback
    return '; '.join(a.strip() for a in re.split(r'\s*;\s*',text) if a.strip())


def corpus_fields(row):
    pages='-'.join(p for p in (row['start_page'],row['end_page']) if p)
    return dict(title=clean_title(row['title']),authors='; '.join(person(a) for a in row['authors'].split('|') if a.strip()),
                journal=smart_title(row['source_title']) if row['source_title'].isupper() else row['source_title'],
                year=int(row['publication_year']) if row['publication_year'].isdigit() else None,
                volume=row['volume'],issue=row['issue'],pages=pages,doi=doi_key(row['doi']),abstract=row['abstract'],
                keywords='; '.join(k.strip() for k in row['keywords_plus'].split('|') if k.strip()))


def replace_file(source, target, attempts=40):
    """os.replace that waits out a brief concurrent reader, which Windows treats as a lock."""
    for attempt in range(attempts):
        try:
            return os.replace(source, target)
        except PermissionError:
            if os.name != 'nt' or attempt == attempts - 1:
                raise
            time.sleep(0.05)


def inventory(roots):
    """Enumerate names only; never follow symlinks or Windows junctions."""
    seen = set()
    for root in roots:
        def fail(error):
            raise error
        for directory, folders, files in os.walk(root, followlinks=False, onerror=fail):
            folders[:] = sorted(name for name in folders if not Path(directory, name).is_symlink()
                                and not getattr(Path(directory, name), 'is_junction', lambda: False)())
            for name in sorted(files):
                path = Path(directory, name)
                if path.suffix.lower() not in SUFFIXES or path.is_symlink():
                    continue
                resolved = path.resolve()
                if not resolved.is_relative_to(root) or resolved in seen:
                    continue
                seen.add(resolved)
                yield root, resolved


def load_locator(locator):
    lookup=sqlite3.connect(Path(locator).resolve().as_uri()+'?mode=ro',uri=True)
    try:
        columns={r[1] for r in lookup.execute('PRAGMA table_info(files)')}
        wanted=[c for c in ('title','authors','doi_key','metadata_error') if c in columns]
        return {os.path.normcase(r[0]):dict(zip(wanted,r[1:])) for r in lookup.execute('SELECT md_path,'+','.join(wanted)+' FROM files')}
    finally:
        lookup.close()


def build(root,index,metadata=None,locator=None,verify_content=False,progress=None,corpus=None):
    supplied = [Path(p) for p in (root if isinstance(root,(list,tuple)) else [root])]
    if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in supplied):
        raise ValueError('请直接指定全文目录，不使用目录链接')
    roots = list(dict.fromkeys(p.resolve() for p in supplied))
    index=Path(index).resolve()
    if not roots or any(not p.is_dir() for p in roots):raise ValueError('全文目录不存在')
    if metadata and len(roots) != 1:
        raise ValueError('CSV 相对路径元数据请配合 --root 指定一个目录；多目录可使用 --locator 文件定位索引。')
    values={}
    if metadata:
        with Path(metadata).open(encoding='utf-8-sig',newline='') as stream:
            for row in csv.DictReader(stream):
                key=row.get('path','').strip().replace('\\','/')
                if not key:raise ValueError('元数据每行都必须有相对于全文目录的 path')
                if key in values:raise ValueError('元数据的 path 不得重复')
                values[key]=row
    cached=load_locator(locator) if locator else {}
    library=load_corpus(corpus,progress) if corpus and Path(corpus).is_file() else None
    files=list(inventory(roots))
    relatives={(root,file.relative_to(root).as_posix()) for root,file in files}
    index.parent.mkdir(parents=True,exist_ok=True)
    # A failed build keeps its unique pending file; never delete earlier work.
    pending=index.with_name(index.name+'.'+uuid.uuid4().hex+'.pending')
    con=sqlite3.connect(pending)
    con.execute('PRAGMA cache_size=-131072')
    skipped=[];count=0
    stats=dict(cached_titles=0,from_abstract_library=0,with_authors=0,with_journal=0,with_year=0,with_abstract=0,
               sample_copies=0,non_literature=0)
    try:
        con.executescript('''CREATE TABLE index_meta(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE papers(id INTEGER PRIMARY KEY,paper_uid TEXT UNIQUE,path TEXT,title TEXT,title_norm TEXT,authors TEXT,
        abstract TEXT,keywords TEXT,language TEXT,journal TEXT,year INTEGER,volume TEXT,issue TEXT,pages TEXT,doi TEXT,
        stem_key TEXT,mtime_ns INTEGER,excluded INTEGER,exclude_reason TEXT,meta_source TEXT);
        CREATE INDEX idx_papers_title_norm ON papers(title_norm);
        CREATE INDEX idx_papers_doi ON papers(doi);
        CREATE INDEX idx_papers_stem ON papers(stem_key);
        CREATE VIRTUAL TABLE papers_fts USING fts5(title,authors,journal,abstract,keywords,content='papers',content_rowid='id',tokenize='trigram');''')
        for root,file in files:
            parts=file.relative_to(root).parts;rel='/'.join(parts);row=dict(values.get(rel,{}))
            try:
                stat=file.stat()
                if stat.st_size>40*1024*1024:raise ValueError('超过40MB')
                if not stat.st_size:raise ValueError('空文件')
                # Normal indexing touches filenames and cached metadata only.
                # Read/parse an actual full text when the user previews it.
                if verify_content:extract_document(file,file.name)
                info=path_info(parts,file.stem)
                source='csv' if row else 'path'
                hit=cached.get(os.path.normcase(str(file)))
                if hit and not hit.get('metadata_error'):
                    if hit.get('title') and not row.get('title'):row['title']=clean_title(hit['title']);source='locator'
                    if hit.get('authors') and not row.get('authors'):row['locator_authors']=hit['authors']
                    if hit.get('doi_key') and not row.get('doi'):row['doi']=hit['doi_key']
                    if hit.get('title'):stats['cached_titles']+=1
                # CNKI file names are '<题名>_<作者>' and are more reliable than headings read from the Markdown.
                if info.get('title') and not values.get(rel,{}).get('title'):row['title']=info['title'];source='path'
                for field in ('title','journal'):
                    if info.get(field) and not row.get(field,'').strip():row[field]=info[field]
                year=int(row['year']) if str(row.get('year','')).strip() else info.get('year')
                if year is not None and not 1800<=year<=2100:raise ValueError('年份超出范围')
                title=clean_title(row.get('title','')) or file.stem.replace('_',' ')
                doi=doi_key(row.get('doi')) or filename_doi(file.stem)
                lang=row.get('language','').strip() or ('zh' if re.search(r'[\u4e00-\u9fff]',title) else 'en')
                authors=row.get('authors','').strip() or clean_authors(row.get('locator_authors'),lang,info.get('authors',''))
                fields=dict(title=title,authors=authors,journal=row.get('journal','').strip(),year=year,
                            volume=row.get('volume',''),issue=row.get('issue',''),pages=row.get('pages',''),doi=doi,
                            abstract=row.get('abstract',''),keywords=row.get('keywords',''))
                if library and lang=='en':
                    match=corpus_match(library,doi,file.stem,normalized(title),year)
                    if match:
                        fields.update({k:v for k,v in corpus_fields(match).items() if v not in ('',None)})
                        source='abstract-library';stats['from_abstract_library']+=1
                excluded,reason=0,''
                if info.get('exclude'):
                    excluded,reason=1,info['exclude'];stats['non_literature']+=1
                elif info['sample'] and (root,'/'.join(parts[1:])) in relatives:
                    excluded,reason=1,'重复副本';stats['sample_copies']+=1
                if not excluded:
                    for field,stat_key in (('authors','with_authors'),('journal','with_journal'),('year','with_year'),('abstract','with_abstract')):
                        stats[stat_key]+=bool(fields[field])
                identity=rel if len(roots)==1 and not locator else os.path.normcase(str(file))
                uid='file:'+hashlib.sha256(identity.encode()).hexdigest()[:32]
                keywords=' '.join(filter(None,[fields['keywords'],file.stem.replace('_',' '),fields['doi']]))
                con.execute('INSERT INTO papers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (count+1,uid,str(file),fields['title'],normalized(fields['title']),fields['authors'],fields['abstract'],keywords,
                     lang,fields['journal'],fields['year'],fields['volume'],fields['issue'],fields['pages'],fields['doi'],
                     normalized(file.stem),stat.st_mtime_ns,excluded,reason,source))
                count+=1
                if progress and count % 20000 == 0:progress(f'已登记 {count:,} 个全文文件…')
            except (ValueError,OSError) as exc:
                skipped.append({'file':str(file),'reason':str(exc)})
        if progress:progress(f'正在为 {count:,} 个文件建立检索索引…')
        con.execute("INSERT INTO papers_fts(papers_fts) VALUES('rebuild')")
        meta={'indexed_at':time.strftime('%Y-%m-%d %H:%M:%S'),'root':str(roots[0]),'roots':json.dumps([str(r) for r in roots],ensure_ascii=False),
              'identity_verification':'preview' if locator or not metadata else 'metadata','schema':'2',
              'available_count':str(count-stats['sample_copies']-stats['non_literature']),
              'metadata_only':'0' if stats['with_abstract'] else '1','has_years':'1' if stats['with_year'] else '0',
              'stats':json.dumps(stats)}
        con.executemany('INSERT INTO index_meta VALUES(?,?)',meta.items())
        con.commit();con.close();replace_file(pending,index)
    except Exception:
        con.close();raise
    return {'indexed':count,**stats,'skipped':skipped,'index':str(index),
            'metadata_note':'书目信息来自摘要库、文件定位记录与目录结构；选中论文后请核对原文。'+('已逐一验证文本提取。' if verify_content else '未读取整库正文。')}


def main():
    parser=argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--config');parser.add_argument('--metadata',help='列名见 examples/fulltext-metadata.template.csv')
    parser.add_argument('--root');parser.add_argument('--index')
    parser.add_argument('--locator',help='只读复用现有 local_documents.sqlite3 的题名、作者与 DOI')
    parser.add_argument('--corpus',help='摘要库 CSV（默认用配置中的 corpus），用于补全作者、期刊、年份与摘要')
    parser.add_argument('--verify-content',action='store_true',help='逐一解析原文；大库默认不扫描正文')
    args=parser.parse_args();cfg=load_config(args.config)
    root=resolve_path(args.root) if args.root else [resolve_path(p) for p in [cfg.get('papers','user_data/papers'),*cfg.get('fulltext_roots',[])]]
    index=resolve_path(args.index) if args.index else resolve_path(cfg.get('workspace','user_data'))/'fulltext-index.sqlite3'
    locator=args.locator or cfg.get('fulltext_locator')
    corpus=args.corpus or cfg.get('corpus')
    progress=lambda message:print(message,file=sys.stderr,flush=True)
    result=build(root,index,args.metadata,resolve_path(locator) if locator else None,args.verify_content,progress,
                 resolve_path(corpus) if corpus else None)
    result['skipped']=result['skipped'][:50]+([{'more':len(result['skipped'])-50}] if len(result['skipped'])>50 else [])
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
