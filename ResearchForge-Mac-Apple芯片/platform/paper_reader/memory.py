"""Durable, versioned reading memory. No model or network calls in this module."""
import copy
import hashlib
import io
import json
import re
import sqlite3
import time
import unicodedata
import uuid
import zipfile
from pathlib import Path

if __package__:
    from . import storage
    from .configuration import DATA, atomic_json
else:
    import storage
    from configuration import DATA, atomic_json

DOCS = DATA / 'documents'
KINDS = ('问题相近', '理论关联', '方法借鉴', '引用', '支持', '存在分歧')
FIELDS = ('title', 'authors', 'year', 'doi', 'journal', 'abstract', 'research_question',
          'contribution', 'theory', 'methods', 'data', 'findings', 'limitations', 'open_questions',
          'locations', 'tags', 'bibliography_evidence', 'year_note')


def norm(value):
    return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', str(value)).casefold())


def initialize():
    with storage.connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS memory_papers(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memory_versions(job_id TEXT PRIMARY KEY,paper_id TEXT NOT NULL,payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS memory_version_paper ON memory_versions(paper_id);
        CREATE TABLE IF NOT EXISTS memory_identities(identity TEXT PRIMARY KEY,paper_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memory_edges(id TEXT PRIMARY KEY,source TEXT NOT NULL,target TEXT NOT NULL,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memory_meta(key TEXT PRIMARY KEY,payload TEXT NOT NULL);
        ''')
        for row in db.execute('SELECT id,payload FROM memory_papers').fetchall():
            p=json.loads(row[1])
            if p.get('classification_status')=='processing':
                p.update(classification_status='failed',classification_error='上次定位中断，卡片已保存，可手动重试。')
                put(db,p)


def put(db,p):
    db.execute('INSERT OR REPLACE INTO memory_papers VALUES (?,?)',(p['id'],json.dumps(p,ensure_ascii=False)))


def all_papers():
    with storage.connect() as db:
        rows=db.execute('SELECT payload FROM memory_papers').fetchall()
    return sorted((json.loads(r[0]) for r in rows),key=lambda p:p['created'],reverse=True)


def get(pid,db=None):
    if db is None:
        with storage.connect() as con:return get(pid,con)
    row=db.execute('SELECT payload FROM memory_papers WHERE id=?',(pid,)).fetchone()
    if not row:raise ValueError('未找到这张文献卡片。')
    return json.loads(row[0])


def versions(pid):
    with storage.connect() as db:
        rows=db.execute('SELECT payload FROM memory_versions WHERE paper_id=?',(pid,)).fetchall()
    return sorted((json.loads(r[0]) for r in rows),key=lambda v:v['created'],reverse=True)


def version(job_id):
    with storage.connect() as db:row=db.execute('SELECT paper_id,payload FROM memory_versions WHERE job_id=?',(job_id,)).fetchone()
    if not row:raise ValueError('未找到这次精读的记忆版本。')
    return row[0],json.loads(row[1])


def edges(pid=None):
    with storage.connect() as db:
        rows=db.execute('SELECT payload FROM memory_edges WHERE source=? OR target=?',(pid,pid)).fetchall() if pid else db.execute('SELECT payload FROM memory_edges').fetchall()
    return [json.loads(r[0]) for r in rows]


def section(report,needle):
    for block in re.split(r'^##\s+',report,flags=re.M)[1:]:
        heading,_,body=block.partition('\n')
        if needle in heading:return body.strip()[:6000]
    return ''


def clean_field(text):
    return re.sub(r'\s*(?:\[L\d[^\]]*\]|[（(]L\d[^）)]*[）)]|[。.]$)', '',text.replace('**','')).strip(' *。；')


def local_metadata(job):
    report=job['report'];basic=section(report,'基本信息')
    def line(label):
        m=re.search(r'^\s*[-*]?\s*\**'+label+r'\**\s*[：:]\s*(.+)',basic,re.M)
        return clean_field(m.group(1)) if m else ''
    title=line('原文题名') or line('题名') or job['title']
    author=line('作者').split('。',1)[0];author=re.sub(r'[（(][^（）()]*[）)]','',author)
    authors=[x.strip(' ，,。\\') for x in re.split(r'、|；|;|\s+and\s+|\s*&\s*',author) if x.strip()]
    if any(x in author for x in ('待核实','未识别','未提供')):authors=[]
    yearline=line('年份(?:与期刊状态|、期刊/工作论文状态)?') or line('发表年份')
    match=re.search(r'\b(18\d{2}|19\d{2}|20\d{2})\b',yearline)
    if '待核实' in yearline and not any(x in yearline for x in ('版权信息','发表年份为','发表于')):match=None
    return dict(title=title,authors=authors,year=int(match.group(1)) if match else None,year_note=yearline,
        doi='',journal=line('期刊'),abstract=section(report,'摘要'),research_question=section(report,'研究问题'),
        contribution=section(report,'研究贡献'),theory=section(report,'理论逻辑'),methods=section(report,'研究设计'),
        data=section(report,'数据'),findings=section(report,'主要发现'),limitations=section(report,'局限'),
        open_questions=section(report,'后续研究'),bibliography_evidence=basic[:5000],
        locations=[dict(field='待分类',topic='待定位',question='待定位',reason='已保存精读报告，等待自动定位。',evidence='')],tags=[])


def document(doc_id):
    if not re.fullmatch(r'[a-f0-9]{32}',doc_id):raise ValueError('无效的文档标识。')
    path=DOCS/(doc_id+'.json')
    if not path.exists():raise ValueError('原文快照暂不可用，报告仍保存在文献记忆中。')
    return json.loads(path.read_text())


def identity_keys(meta,source_hash='',library_uid=''):
    keys=[]
    if source_hash:keys.append('source:'+source_hash)
    if library_uid:keys.append('library:'+library_uid)
    if meta.get('doi'):keys.append('doi:'+meta['doi'].casefold())
    authors=meta.get('authors') or []
    if meta.get('year') and authors and len(norm(meta['title']))>=12:
        keys.append('bibliography:'+hashlib.sha256((norm(meta['title'])+'|'+norm(authors[0])+'|'+str(meta['year'])).encode()).hexdigest())
    return keys


def remember(job,doc):
    if job['status']!='completed' or not job.get('report'):return None,False
    sha=hashlib.sha256(doc['text'].encode()).hexdigest()
    keys=identity_keys({},sha,doc.get('source_library',{}).get('uid',''))
    now=time.time()
    with storage.DB_LOCK,storage.connect() as db:
        old=db.execute('SELECT paper_id FROM memory_versions WHERE job_id=?',(job['id'],)).fetchone()
        if old:return old[0],False
        pid=None
        for key in keys:
            row=db.execute('SELECT paper_id FROM memory_identities WHERE identity=?',(key,)).fetchone()
            if row:pid=row[0];break
        if pid:
            p=get(pid,db);p['version_count']+=1;p['latest_job_id']=job['id'];p['updated']=now
        else:
            pid=uuid.uuid4().hex
            p=dict(id=pid,created=now,updated=now,revision=0,version_count=1,latest_job_id=job['id'],
                classification_status='pending',classification_error='',reviewed=False,protected=[],**local_metadata(job))
        v={k:copy.deepcopy(job.get(k)) for k in ('id','created','report','research','analysis_basis','warnings','checks','model','filename','document_id','source_research')}
        v['snapshot']=copy.deepcopy(job.get('snapshot',{}))
        v['snapshot'].get('provider',{}).pop('api_key',None)
        v['source_hash']=sha
        db.execute('INSERT INTO memory_versions VALUES (?,?,?)',(job['id'],pid,json.dumps(v,ensure_ascii=False)))
        for key in keys:db.execute('INSERT OR IGNORE INTO memory_identities VALUES (?,?)',(key,pid))
        put(db,p)
    return pid,True


def reconcile():
    added=[]
    with storage.connect() as db:known={r[0] for r in db.execute('SELECT job_id FROM memory_versions')}
    for job in storage.all_jobs():
        if job['status']=='completed' and job.get('report') and job['id'] not in known:
            try:doc=document(job['document_id'])
            except ValueError:doc={'text':job['report']}
            pid,new=remember(job,doc)
            if new:added.append(pid)
    return list(dict.fromkeys(added))


def validate_metadata(data):
    if not isinstance(data,dict):raise ValueError('文献卡片格式无效。')
    result={}
    for key in FIELDS:
        value=data.get(key)
        if key in ('authors','tags'):
            if not isinstance(value,list) or len(value)>80 or any(not isinstance(x,str) or len(x)>180 for x in value):raise ValueError('作者或标签格式无效。')
            result[key]=list(dict.fromkeys(x.strip() for x in value if x.strip()))
        elif key=='year':
            if value in ('',None):value=None
            elif type(value) is not int or not 1800<=value<=2100:raise ValueError('年份需为 1800–2100 的整数，无法确认时留空。')
            result[key]=value
        elif key=='locations':
            if not isinstance(value,list) or not 1<=len(value)<=8:raise ValueError('需要 1–8 个领域定位。')
            loc=[]
            for item in value:
                if not isinstance(item,dict):raise ValueError('领域定位格式无效。')
                row={}
                for k in ('field','topic','question','reason','evidence'):
                    v=item.get(k,'')
                    if not isinstance(v,str) or len(v)>(2000 if k in ('reason','evidence') else 200):raise ValueError('领域定位内容过长。')
                    row[k]=v.strip()
                if not all(row[k] for k in ('field','topic','question')):raise ValueError('领域、主题与问题均需填写。')
                if not any(all(x[k]==row[k] for k in ('field','topic','question')) for x in loc):loc.append(row)
            result[key]=loc
        else:
            if value is None:value=''
            if not isinstance(value,str) or len(value)>12000:raise ValueError('文献卡片文本格式无效或过长。')
            result[key]=value.strip()
    if not 2<=len(result['title'])<=600:raise ValueError('请填写有效的论文题目。')
    if result['doi']:
        doi=re.sub(r'^https?://(?:dx\.)?doi\.org/','',result['doi'],flags=re.I).strip()
        if not re.fullmatch(r'10\.\d{4,9}/\S{2,180}',doi):raise ValueError('DOI 格式无效；无法确认时请留空。')
        result['doi']=doi.rstrip('.,;')
    return result


def change(pid,data,expected_revision=None,manual=True):
    with storage.DB_LOCK,storage.connect() as db:
        p=get(pid,db)
        if expected_revision is not None and p['revision']!=expected_revision:raise ValueError('这张卡片已更新，请重新打开后编辑。')
        revised=validate_metadata({**p,**{k:v for k,v in data.items() if k in FIELDS}})
        changed=[k for k in FIELDS if revised[k]!=p.get(k)]
        p.update(revised,updated=time.time(),revision=p['revision']+1)
        if manual:
            p['protected']=list(set(p.get('protected',[])+changed))
            p['reviewed']=bool(data.get('reviewed',p.get('reviewed',False)))
            if p['reviewed']:p['protected']=list(FIELDS)
        put(db,p)
        refresh_local_edges(db)
    return p


def mark(pid,**changes):
    with storage.DB_LOCK,storage.connect() as db:
        p=get(pid,db);p.update(changes,updated=time.time());put(db,p)
    return p


def canonical_locations(locations,db):
    aliases=[json.loads(r[0]) for r in db.execute("SELECT payload FROM memory_meta WHERE key LIKE 'alias:%'")]
    for loc in locations:
        for rule in aliases:
            if all(norm(loc[k])==norm(v) for k,v in rule['old'].items()):loc.update(rule['new'])
    # Keep spelling consistent with the existing hierarchy.
    existing=[json.loads(r[0]) for r in db.execute('SELECT payload FROM memory_papers')]
    for loc in locations:
        for p in existing:
            for old in p['locations']:
                if norm(loc['field'])==norm(old['field']):
                    loc['field']=old['field']
                    if norm(loc['topic'])==norm(old['topic']):
                        loc['topic']=old['topic']
                        if norm(loc['question'])==norm(old['question']):loc['question']=old['question']
    return locations


def apply_classification(pid,data,usage,model):
    meta=validate_metadata(data)
    with storage.DB_LOCK,storage.connect() as db:
        p=get(pid,db)
        meta['locations']=canonical_locations(meta['locations'],db)
        for k,v in meta.items():
            if k not in p.get('protected',[]):p[k]=v
        p.update(classification_status='ready',classification_error='',updated=time.time(),revision=p['revision']+1,
                 classification_model=model,classification_usage=usage)
        # Merge only exact DOI or title + first author + year, never fuzzy titles.
        for key in identity_keys(p):
            row=db.execute('SELECT paper_id FROM memory_identities WHERE identity=?',(key,)).fetchone()
            if row and row[0]!=pid:
                target=get(row[0],db)
                db.execute('UPDATE memory_versions SET paper_id=? WHERE paper_id=?',(target['id'],pid))
                db.execute('UPDATE memory_identities SET paper_id=? WHERE paper_id=?',(target['id'],pid))
                for e in edges_in_db(db,pid):
                    db.execute('DELETE FROM memory_edges WHERE id=?',(e['id'],))
                    a=target['id'] if e['source']==pid else e['source'];b=target['id'] if e['target']==pid else e['target']
                    if a!=b:put_edge(db,a,b,e['kind'],e['reason'],e.get('evidence',''),e['status'],e.get('origin','manual'))
                db.execute('DELETE FROM memory_papers WHERE id=?',(pid,))
                target['version_count']=db.execute('SELECT COUNT(*) FROM memory_versions WHERE paper_id=?',(target['id'],)).fetchone()[0]
                vs=[json.loads(r[0]) for r in db.execute('SELECT payload FROM memory_versions WHERE paper_id=?',(target['id'],))]
                target['latest_job_id']=max(vs,key=lambda v:v['created'])['id']
                target.update(updated=time.time(),revision=target['revision']+1)
                put(db,target);refresh_local_edges(db)
                return target['id']
            db.execute('INSERT OR IGNORE INTO memory_identities VALUES (?,?)',(key,pid))
        put(db,p)
        known={r[0] for r in db.execute('SELECT id FROM memory_papers')}
        for e in (data.get('related') if isinstance(data.get('related'),list) else [])[:12]:
            if isinstance(e,dict) and e.get('target') in known and e['target']!=pid and e.get('kind') in KINDS:
                reason=str(e.get('reason',''))[:2000];evidence=str(e.get('evidence',''))[:4000]
                if reason and (e['kind'] not in ('引用','支持','存在分歧') or evidence):
                    put_edge(db,pid,e['target'],e['kind'],reason,evidence,'suggested','model')
        refresh_local_edges(db)
    return pid


def edges_in_db(db,pid):
    return [json.loads(r[0]) for r in db.execute('SELECT payload FROM memory_edges WHERE source=? OR target=?',(pid,pid))]


def put_edge(db,source,target,kind,reason,evidence,status,origin):
    if kind!='引用':source,target=sorted((source,target))
    eid=hashlib.sha256((source+'|'+target+'|'+kind).encode()).hexdigest()[:32]
    old=db.execute('SELECT payload FROM memory_edges WHERE id=?',(eid,)).fetchone()
    if old:
        previous=json.loads(old[0])
        if origin!='manual' and previous.get('origin')=='manual':return eid
        if origin=='local' and previous.get('origin')=='model':return eid
    e=dict(id=eid,source=source,target=target,kind=kind,reason=reason,evidence=evidence,status=status,origin=origin,updated=time.time())
    db.execute('INSERT OR REPLACE INTO memory_edges VALUES (?,?,?,?)',(eid,source,target,json.dumps(e,ensure_ascii=False)))
    return eid


def refresh_local_edges(db):
    # An explicit shared topic creates a suggested relation, never a verified
    # citation or agreement. Rejected suggestions are retained and not recreated.
    for row in db.execute('SELECT id,payload FROM memory_edges').fetchall():
        e=json.loads(row[1])
        if e.get('origin')=='local':db.execute('DELETE FROM memory_edges WHERE id=?',(row[0],))
    groups={}
    for row in db.execute('SELECT payload FROM memory_papers'):
        p=json.loads(row[0])
        for loc in p['locations']:
            if loc['field']=='待分类':continue
            groups.setdefault((loc['field'],loc['topic']),set()).add(p['id'])
    for (field,topic),members in groups.items():
        members=sorted(members)
        for i,a in enumerate(members):
            # Bound dense topic groups; each paper links to nearby stable IDs.
            for b in members[i+1:i+7]:put_edge(db,a,b,'问题相近',f'两篇论文均被归入「{field} → {topic}」，建议比较具体问题与证据。','领域定位；尚未核实文献之间的直接关系。','suggested','local')


def save_edge(data):
    if not isinstance(data,dict):raise ValueError('关联格式无效。')
    source=data.get('source');target=data.get('target');kind=data.get('kind');status=data.get('status','suggested')
    if source==target or kind not in KINDS or status not in ('suggested','confirmed','rejected'):raise ValueError('请选择两篇不同论文及有效关联类型。')
    reason=data.get('reason','');evidence=data.get('evidence','')
    if not isinstance(reason,str) or not reason.strip() or len(reason)>2000 or not isinstance(evidence,str) or len(evidence)>4000:raise ValueError('请填写关联理由，且不要超过长度限制。')
    if status=='confirmed' and kind in ('引用','支持','存在分歧') and not evidence.strip():raise ValueError('确认引用、支持或分歧关系前，请填写出处与可比性说明。')
    with storage.DB_LOCK,storage.connect() as db:
        get(source,db);get(target,db)
        eid=put_edge(db,source,target,kind,reason.strip(),evidence.strip(),status,'manual')
    return eid


def rename_category(data):
    old=data.get('old');new=data.get('new')
    if not isinstance(old,dict) or not isinstance(new,dict) or set(old)!=set(new) or set(old) not in ({'field'},{'field','topic'},{'field','topic','question'}):raise ValueError('请选择完整的分类层级。')
    if any(not isinstance(v,str) or not v.strip() or len(v)>200 for v in [*old.values(),*new.values()]):raise ValueError('分类名称需要 1–200 个字符。')
    old={k:v.strip() for k,v in old.items()};new={k:v.strip() for k,v in new.items()};count=0
    with storage.DB_LOCK,storage.connect() as db:
        for row in db.execute('SELECT payload FROM memory_papers').fetchall():
            p=json.loads(row[0]);changed=False
            for loc in p['locations']:
                if all(loc[k]==v for k,v in old.items()):loc.update(new);changed=True
            if changed:
                p['locations']=validate_metadata(p)['locations'];p['protected']=list(set(p.get('protected',[])+['locations']))
                p.update(updated=time.time(),revision=p['revision']+1);put(db,p);count+=1
        if not count:raise ValueError('没有找到这个分类，请刷新后重试。')
        key='alias:'+hashlib.sha256(json.dumps(old,sort_keys=True).encode()).hexdigest()
        db.execute('INSERT OR REPLACE INTO memory_meta VALUES (?,?)',(key,json.dumps(dict(old=old,new=new),ensure_ascii=False)))
        refresh_local_edges(db)
    return count


def tokens(text):
    words=set(re.findall(r'[a-z][a-z0-9-]{3,}',text.casefold()))
    for group in re.findall(r'[\u4e00-\u9fff]{2,}',text):words.update(group[i:i+2] for i in range(len(group)-1))
    return words-{'研究','论文','影响','结果','作者','问题','theory','study','paper','that','with','from','this','were','have','using'}


def related_candidates(text,exclude=None,limit=8):
    query=tokens(text);ranked=[]
    for p in all_papers():
        if p['id']==exclude:continue
        text2=' '.join([p['title'],p['research_question'][:1000],p['contribution'][:1000],*p['tags']])
        terms=tokens(text2);shared=query&terms
        score=len(shared)/(max(1,len(query)*len(terms))**.5)
        if len(shared)>=3 and score>.05:ranked.append((score,p))
    return [p for _,p in sorted(ranked,key=lambda item:item[0],reverse=True)[:limit]]


def reading_context(doc):
    sha=hashlib.sha256(doc.get('text','').encode()).hexdigest()
    with storage.connect() as db:row=db.execute('SELECT paper_id FROM memory_identities WHERE identity=?',('source:'+sha,)).fetchone()
    candidates=related_candidates(doc.get('text','')[:12000],exclude=row[0] if row else None,limit=3)
    result=[]
    for p in candidates:
        result.append(dict(id=p['id'],title=p['title'],authors=p['authors'],year=p['year'],question=p['research_question'][:500],
                           contribution=p['contribution'][:500],findings=p['findings'][:500],reviewed=p['reviewed']))
    return result


def export_bundle():
    with storage.DB_LOCK,storage.connect() as db:
        payload={key:[dict(zip(cols,r)) for r in db.execute('SELECT '+','.join(cols)+' FROM '+table)] for key,table,cols in (
            ('papers','memory_papers',['id','payload']),('versions','memory_versions',['job_id','paper_id','payload']),
            ('identities','memory_identities',['identity','paper_id']),('edges','memory_edges',['id','source','target','payload']),('meta','memory_meta',['key','payload']))}
    docids={json.loads(v['payload'])['document_id'] for v in payload['versions']}
    files={}
    for did in docids:
        doc=document(did)
        for filename in (did+'.json',did+doc['suffix']):
            path=DOCS/filename
            if not path.is_file():raise ValueError('备份中有原文快照缺失，请先修复文件：'+filename)
            files['documents/'+filename]=path
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('memory.json',json.dumps(dict(format='researchforge-memory',version=1,created=time.time(),tables=payload),ensure_ascii=False))
        for name,path in files.items():z.write(path,name)
    output.seek(0);return output


def restore_bundle(raw):
    try:z=zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:raise ValueError('请上传本平台导出的文献记忆 ZIP 备份。') from exc
    with z:
        infos=z.infolist()
        if len(infos)>30000 or sum(i.file_size for i in infos)>1024*1024*1024:raise ValueError('备份解压后超过 1 GB 或文件过多，请分批恢复。')
        if len({i.filename for i in infos})!=len(infos):raise ValueError('备份包含重复文件名。')
        for i in infos:
            if i.filename!='memory.json' and not re.fullmatch(r'documents/[a-f0-9]{32}\.(?:json|md|markdown|txt|pdf)',i.filename):raise ValueError('备份包含不允许的路径。')
            if i.file_size>45*1024*1024 and i.filename!='memory.json':raise ValueError('备份中的单文件过大。')
        try:bundle=json.loads(z.read('memory.json'))
        except (KeyError,ValueError) as exc:raise ValueError('备份清单缺失或格式无效。') from exc
        if not isinstance(bundle,dict) or bundle.get('format')!='researchforge-memory' or bundle.get('version')!=1:raise ValueError('不支持这个备份版本。')
        tables=bundle.get('tables',{})
        if not isinstance(tables,dict):raise ValueError('备份内容无效。')
        rows={k:tables.get(k,[]) for k in ('papers','versions','identities','edges','meta')}
        if any(not isinstance(v,list) or len(v)>100000 for v in rows.values()):raise ValueError('备份内容无效。')
        papers={}
        for row in rows['papers']:
            p=json.loads(row['payload']);p.update(validate_metadata(p))
            if not re.fullmatch(r'[a-f0-9]{32}',row['id']) or p['id']!=row['id']:raise ValueError('备份论文标识无效。')
            if any(type(p.get(k)) not in (int,float) for k in ('created','updated','revision')) or not isinstance(p.get('protected'),list) or any(k not in FIELDS for k in p['protected']):raise ValueError('备份卡片状态无效。')
            if p.get('classification_status') not in ('ready','pending','processing','failed') or type(p.get('reviewed')) is not bool:raise ValueError('备份卡片状态无效。')
            papers[p['id']]=p
        docs={}
        for row in rows['versions']:
            v=json.loads(row['payload']);did=v.get('document_id','')
            if row['paper_id'] not in papers or not re.fullmatch(r'[a-f0-9]{32}',row['job_id']) or not re.fullmatch(r'[a-f0-9]{32}',did):raise ValueError('备份版本关联无效。')
            if v.get('id')!=row['job_id'] or not isinstance(v.get('report'),str) or not isinstance(v.get('created'),(int,float)):raise ValueError('备份报告格式无效。')
            if not isinstance(v.get('snapshot',{}),dict) or not isinstance(v.get('snapshot',{}).get('provider',{}),dict):raise ValueError('备份分析配置格式无效。')
            v.get('snapshot',{}).get('provider',{}).pop('api_key',None)
            row['payload']=json.dumps(v,ensure_ascii=False)
            try:
                d=json.loads(z.read('documents/'+did+'.json'));suffix=d['suffix']
                if suffix not in ('.md','.markdown','.txt','.pdf') or d.get('id')!=did or not isinstance(d.get('text'),str):raise ValueError('备份原文格式无效。')
                docs[did]=(d,z.read('documents/'+did+suffix))
            except KeyError as exc:raise ValueError('备份缺少原文文件。') from exc
        # Check every document before writing any file or merging the database.
        for did,(d,content) in docs.items():
            source=DOCS/(did+d['suffix']);meta=DOCS/(did+'.json')
            if source.exists() and source.read_bytes()!=content:raise ValueError('备份与现有原文标识冲突，未覆盖任何数据。')
            if meta.exists() and json.loads(meta.read_text()).get('text')!=d['text']:raise ValueError('备份与现有正文冲突，未覆盖任何数据。')
        for e in rows['edges']:
            p=json.loads(e['payload'])
            if e['source'] not in papers or e['target'] not in papers or p.get('source')!=e['source'] or p.get('target')!=e['target'] or p.get('kind') not in KINDS or p.get('status') not in ('suggested','confirmed','rejected'):raise ValueError('备份关联格式无效。')
            if not isinstance(p.get('reason'),str) or not isinstance(p.get('evidence',''),str) or p.get('origin') not in ('manual','model','local'):raise ValueError('备份关联内容无效。')
        for row in rows['identities']:
            if row['paper_id'] not in papers or not isinstance(row['identity'],str):raise ValueError('备份索引无效。')
        for row in rows['meta']:
            value=json.loads(row['payload'])
            if not row['key'].startswith('alias:') or not isinstance(value.get('old'),dict) or not isinstance(value.get('new'),dict):raise ValueError('备份分类规则无效。')
            if set(value['old'])!=set(value['new']) or set(value['old']) not in ({'field'},{'field','topic'},{'field','topic','question'}) or any(not isinstance(v,str) or not v or len(v)>200 for v in [*value['old'].values(),*value['new'].values()]):raise ValueError('备份分类规则无效。')
        with storage.DB_LOCK,storage.connect() as db:
            mapping={}
            for pid in papers:
                keys=[r['identity'] for r in rows['identities'] if r['paper_id']==pid]
                match=next((db.execute('SELECT paper_id FROM memory_identities WHERE identity=?',(k,)).fetchone() for k in keys if db.execute('SELECT 1 FROM memory_identities WHERE identity=?',(k,)).fetchone()),None)
                mapping[pid]=match[0] if match else pid
            added=0
            for pid,p in papers.items():
                target=mapping[pid]
                if not db.execute('SELECT 1 FROM memory_papers WHERE id=?',(target,)).fetchone():
                    p['id']=target
                    if p.get('classification_status') in ('pending','processing',None):
                        p.update(classification_status='failed',classification_error='备份中的定位尚未完成，可手动重试。')
                    put(db,p);added+=1
            for did,(d,content) in docs.items():
                source=DOCS/(did+d['suffix'])
                if not source.exists():source.write_bytes(content);source.chmod(0o600)
                if not (DOCS/(did+'.json')).exists():atomic_json(DOCS/(did+'.json'),d)
            for row in rows['versions']:db.execute('INSERT OR IGNORE INTO memory_versions VALUES (?,?,?)',(row['job_id'],mapping[row['paper_id']],row['payload']))
            for row in rows['identities']:db.execute('INSERT OR IGNORE INTO memory_identities VALUES (?,?)',(row['identity'],mapping[row['paper_id']]))
            for row in rows['edges']:
                e=json.loads(row['payload']);a=mapping[e['source']];b=mapping[e['target']]
                if e['kind']!='引用':a,b=sorted((a,b))
                exists=db.execute('SELECT 1 FROM memory_edges WHERE id=?',(hashlib.sha256((a+'|'+b+'|'+e['kind']).encode()).hexdigest()[:32],)).fetchone()
                if a!=b and not exists:put_edge(db,a,b,e['kind'],e['reason'],e.get('evidence',''),e['status'],e.get('origin','manual'))
            for row in rows['meta']:db.execute('INSERT OR IGNORE INTO memory_meta VALUES (?,?)',(row['key'],row['payload']))
            for pid in set(mapping.values()):
                p=get(pid,db);vs=[json.loads(r[0]) for r in db.execute('SELECT payload FROM memory_versions WHERE paper_id=?',(pid,))]
                p['version_count']=len(vs)
                if vs:p['latest_job_id']=max(vs,key=lambda v:v['created'])['id']
                put(db,p)
    return added
