"""Read-only directory of existing analyses, grouped by known paper/document identity."""
import json
from flask import jsonify, request
if __package__:
    from . import storage, library, projects
else:
    import storage, library, projects


def projection(db, table, fields):
    columns = ','.join("json_extract(payload,'$.%s')" % f for f in fields)
    return [dict(zip(fields, row)) for row in db.execute('SELECT ' + columns + ' FROM ' + table)]


def listing(args):
    query = str(args.get('q', '')).strip()
    if len(query) > 400:
        raise ValueError('搜索内容最多 400 字符。')
    status = args.get('status', 'all')
    if status not in ('all', 'completed', 'active', 'unfinished'):
        raise ValueError('无效的阅读状态。')
    try:
        page = max(1, min(100000, int(args.get('page', 1))))
    except (ValueError, TypeError):
        raise ValueError('无效的页码。')
    project = projects.resolve(args.get('project'))
    # Never load reports, prompts, credentials or original text into this directory.
    with storage.connect() as db:
        jobs = projection(db, 'jobs', ['id','document_id','title','filename','created','updated','status','model','archived','project_id'])
        papers = projection(db, 'memory_papers', ['id','title','authors','year','journal','locations','project_id'])
        versions = projection(db, 'memory_versions', ['id','document_id','created','model','filename'])
        membership = dict(db.execute('SELECT job_id,paper_id FROM memory_versions'))
    meta = {p['id']: p for p in papers}
    doc_paper = {v['document_id']: membership[v['id']] for v in versions if v['id'] in membership and v['document_id']}
    known = {j['id'] for j in jobs}
    # Backups can contain memory versions without the original jobs table.
    jobs += [dict(v, status='completed', updated=v['created'], archived=True, project_id=meta.get(membership.get(v['id']), {}).get('project_id'))
             for v in versions if v['id'] not in known]
    if project:
        jobs = [j for j in jobs if (j.get('project_id') or projects.DEFAULT) == project]
    groups = {}
    for job in sorted(jobs, key=lambda j: j.get('created') or 0, reverse=True):
        pid = membership.get(job['id']) or doc_paper.get(job.get('document_id'))
        key = 'paper:' + pid if pid else 'document:' + (job.get('document_id') or job['id'])
        p = meta.get(pid, {})
        if key not in groups:
            locations = json.loads(p.get('locations') or '[]')
            groups[key] = dict(id=key, paper_id=pid, title=p.get('title') or job.get('title') or job.get('filename') or '未命名论文',
                authors=json.loads(p.get('authors') or '[]'), year=p.get('year'), journal=p.get('journal') or '',
                fields=list(dict.fromkeys(l.get('field') for l in locations if l.get('field'))) or ['待归类'],
                updated=job.get('updated') or job.get('created') or 0, versions=[])
        g = groups[key]
        g['updated'] = max(g['updated'], job.get('updated') or job.get('created') or 0)
        g['versions'].append({k:job.get(k) for k in ('id','document_id','created','status','model','archived')})
    items = list(groups.values())
    for p in items:
        p['completed'] = sum(v['status'] == 'completed' for v in p['versions'])
        p['active'] = any(v['status'] in ('queued','running','stopping') for v in p['versions'])
    terms = [library.normalized(t) for t in query.split() if library.normalized(t)]
    field, year = args.get('field', ''), args.get('year', '')
    def matches(p):
        if status == 'completed' and not p['completed']: return False
        if status == 'active' and not p['active']: return False
        if status == 'unfinished' and not any(v['status'] not in ('completed','queued','running','stopping') for v in p['versions']): return False
        if field and field not in p['fields']: return False
        if year and str(p.get('year') or '') != str(year): return False
        text = library.normalized(' '.join([p['title'], *p['authors'], p['journal'], *p['fields'], str(p['year'] or '')]))
        return all(t in text for t in terms)
    selected = sorted(filter(matches, items), key=lambda p: p['updated'], reverse=True)
    start = (page - 1) * 20
    return dict(items=selected[start:start+20], total=len(selected), page=page, has_more=len(selected)>start+20,
        counts=dict(papers=len(items), reports=len(jobs), completed=sum(bool(p['completed']) for p in items)),
        fields=sorted({f for p in items for f in p['fields']}), years=sorted({p['year'] for p in items if p['year']}, reverse=True))


def install(app):
    @app.get('/api/reading-catalog')
    def reading_catalog():
        return jsonify(listing(request.args))
