"""Join saved research citations to local full texts without model calls."""
import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit

if __package__:
    from . import library, storage
else:
    import library, storage

FIELDS=('title','authors','year','journal','doi','source_id','reference_label','reference_status')


def initialize():
    with storage.connect() as db:db.execute('CREATE TABLE IF NOT EXISTS research_imports(import_key TEXT PRIMARY KEY,document_id TEXT NOT NULL)')


def doi(value):
    value=str(value or '').strip().casefold()
    if value.startswith(('http://','https://')):value=unquote(urlsplit(value).path.lstrip('/'))
    value=re.sub(r'^doi\s*:\s*','',value).rstrip('.,;')
    return value if re.fullmatch(r'10\.\d{4,9}/\S+',value) else ''


def reference_identity(ref):
    return (ref.get('reference_status'),ref.get('source_id') or '',None if ref.get('source_id') else tuple(ref.get(k) for k in ('title','authors','year','journal')))


def context(task,idea_id,kind):
    idea_id=str(idea_id)
    idea=next((i for i in task.get('ideas',[]) if str(i.get('id'))==idea_id),None)
    if not idea:raise ValueError('原研究中没有这个构想。')
    record=next((p for p in task.get('proposals',[]) if str(p.get('idea',{}).get('id'))==idea_id),{})
    proposal=record.get('proposal') or {}
    papers=task.get('lit',{}).get(idea_id,[])
    if kind=='literature':refs=papers
    elif kind=='references':
        refs=list(proposal.get('key_references',[]))
        for hyp in proposal.get('hypotheses',[]):
            ref=hyp.get('contribution') or {}
            if not any(reference_identity(r)==reference_identity(ref) for r in refs):refs.append(ref)
    else:raise ValueError('请选择检索文献或提案参考文献。')
    # Matched references use persisted retrieval metadata, never browser-supplied titles.
    refs=[dict(r,**{k:p[k] for k in FIELDS if k in p}) if (p:=next((p for p in papers if r.get('source_id') and p.get('source_id')==r['source_id']),None)) else dict(r) for r in refs]
    parts=['研究主题：'+task.get('topic',''),'原始构想：'+idea.get('title',''),idea.get('abstract','')]
    if proposal:
        parts+=['优化提案（研究设想，尚非已证实结果）：'+(proposal.get('title_zh') or proposal.get('title_en') or ''),proposal.get('abstract_zh') or proposal.get('abstract_en') or '',proposal.get('theoretical_foundation') or '']
        parts+=['待检验假说：'+h.get('hypothesis','') for h in proposal.get('hypotheses',[])]
    research='\n\n'.join(x for x in parts if x)
    if len(research)>19500:research=research[:19400]+'\n\n（研究说明过长，已截取前段；完整内容请返回原构想查看。）'
    origin=dict(task_id=task['id'],idea_id=idea_id,kind=kind,idea_title=idea['title'],task_topic=task.get('topic',''),research=research,
                return_url='/?task='+str(uuid.UUID(task['id'])))
    return refs,origin


def candidate(row,reason):
    path=Path(row['path']).resolve();root=library.LIBRARY_ROOT.resolve()
    available=path.is_relative_to(root) and path.suffix.lower() in ('.md','.markdown','.txt','.pdf') and path.is_file()
    if available:available=path.stat().st_size<=40*1024*1024
    return dict(uid=row['paper_uid'],title=library.plain(row['title']),year=row['year'],journal=library.plain(row['journal']),
                doi=row['doi'] or '',filename=path.name,available=available,match_reason=reason)


def resolve(refs):
    """One bounded indexed batch, including case-insensitive DOI lookup."""
    titles=list({library.normalized(str(r.get('title') or '')) for r in refs}-{''})
    dois=list({doi(r.get('doi')) for r in refs}-{''})
    rows={};bytitle={};bydoi={}
    columns='paper_uid,path,title,title_norm,year,journal,doi,excluded'
    if titles or dois:
        with library.connect() as db:
            if titles:
                for r in db.execute('SELECT '+columns+' FROM papers INDEXED BY idx_papers_title_norm WHERE title_norm IN ('+','.join('?' for _ in titles)+')',titles):
                    if not r['excluded']:rows[r['paper_uid']]=dict(r)
            if dois:
                # Scan only the small covering DOI index, not abstracts/full texts.
                sql='WITH hits AS MATERIALIZED (SELECT id FROM papers INDEXED BY idx_papers_doi WHERE lower(doi) IN ('+','.join('?' for _ in dois)+')) SELECT '+','.join('p.'+c for c in columns.split(','))+' FROM hits JOIN papers p ON p.id=hits.id'
                for r in db.execute(sql,dois):
                    if not r['excluded']:rows[r['paper_uid']]=dict(r)
    for r in rows.values():
        bytitle.setdefault(r['title_norm'],[]).append(r)
        if doi(r['doi']):bydoi.setdefault(doi(r['doi']),[]).append(r)
    results=[]
    for ref in refs:
        title=library.normalized(str(ref.get('title') or ''));wanted=doi(ref.get('doi'))
        th=bytitle.get(title,[]);dh=bydoi.get(wanted,[])
        found={r['paper_uid']:r for r in [*th,*dh]};options=[];exact=[]
        for r in found.values():
            title_equal=bool(title and r['title_norm']==title);doi_equal=bool(wanted and doi(r['doi'])==wanted)
            conflict=bool(wanted and doi(r['doi']) and not doi_equal)
            year_diff=bool(ref.get('year') and r['year'] and str(ref['year'])!=str(r['year']))
            reason='DOI 与题名一致' if doi_equal and title_equal else '完整题名一致' if title_equal else 'DOI 一致，题名需核对'
            if conflict:reason+='；DOI 不一致，需核对版本或索引'
            if year_diff:reason+='；年份不同，需核对发表版本'
            c=candidate(r,reason);options.append(c)
            if c['available'] and not conflict and not year_diff and title_equal:exact.append(c)
        if len(found)==1 and len(exact)==1:
            results.append(dict(status='matched',message=exact[0]['match_reason'],candidates=options,uid=exact[0]['uid']))
        elif options:
            results.append(dict(status='review',message='有候选原文，请预览核对后选择。' if any(c['available'] for c in options) else '索引记录存在，但原文件已移动、不可读或超过限制。',candidates=options))
        else:results.append(dict(status='missing',message='未在本地原文索引中找到准确匹配，可继续搜索。',candidates=[]))
    # Expose existing reading-map cards only after the local identity is known.
    with storage.connect() as db:
        for result in results:
            for c in result['candidates']:
                keys=['library:'+c['uid']]+(['doi:'+doi(c['doi'])] if doi(c['doi']) else [])
                hits={r[0] for r in db.execute('SELECT paper_id FROM memory_identities WHERE identity IN ('+','.join('?' for _ in keys)+')',keys)}
                if len(hits)==1:c['memory_paper_id']=next(iter(hits))
    return results


def group(task,idea_id,kind):
    refs,origin=context(task,idea_id,kind)
    if len(refs)>400:raise ValueError('单组文献超过 400 篇，请缩小范围。')
    try:matches=resolve(refs)
    except ValueError as exc:matches=[dict(status='unavailable',message=str(exc),candidates=[]) for _ in refs]
    return dict(origin=origin,items=[dict(index=i,reference={k:r.get(k) for k in FIELDS},match=m) for i,(r,m) in enumerate(zip(refs,matches))])


def import_reference(task,idea_id,kind,index,uid,confirmed,importer,get_doc,document_dir):
    refs,origin=context(task,idea_id,kind)
    if type(index) is not int or not 0<=index<len(refs):raise ValueError('参考文献位置已变化，请刷新后重新选择。')
    ref=refs[index]
    if not confirmed:
        matched=resolve([ref])[0]
        if matched['status']!='matched' or matched['uid']!=uid:raise ValueError('没有唯一、可靠的原文匹配，请预览并确认选择。')
    record=library.paper(uid);st=Path(record['path']).stat()
    origin.update(index=index,reference={k:ref.get(k) for k in FIELDS},match_method='用户预览后选定' if confirmed else '准确书目匹配',imported_at=time.time())
    key=hashlib.sha256(json.dumps([origin['task_id'],origin['idea_id'],kind,index,ref,uid,st.st_mtime_ns,st.st_size,origin['research']],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    with storage.DB_LOCK,storage.connect() as db:
        row=db.execute('SELECT document_id FROM research_imports WHERE import_key=?',(key,)).fetchone()
        if row:
            try:
                doc=get_doc(row[0])
                if (Path(document_dir)/(doc['id']+doc['suffix'])).is_file():return doc,False
            except (KeyError,ValueError):pass
        doc=importer(record,origin)
        db.execute('INSERT OR REPLACE INTO research_imports VALUES (?,?)',(key,doc['id']))
    return doc,True
