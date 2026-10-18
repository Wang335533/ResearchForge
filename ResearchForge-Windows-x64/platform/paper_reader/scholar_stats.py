"""Author metadata and distinct-paper statistics, without external lookups."""
import re
from collections import Counter

if __package__:
    from . import memory
else:
    import memory


def clean_author(name):
    name = str(name).replace('**', '').strip(' *\\，,。；;')
    name = re.sub(r'[（(](?:独著|通讯作者|共同.*?|按.*?)[）)]', '', name)
    name = re.split(r'[，,]\s*(?:独著|按原文|共同|通讯|作者|署名)|[。；;]|（原文|\(原文', name, 1)[0]
    return re.sub(r'\s+', ' ', name).strip(' *\\，,。；;')


def report_authors(report):
    basic = memory.section(report, '基本信息').replace('**', '')
    match = re.search(r'^\s*(?:[-*]\s*)?作者[^\n：:]{0,35}[：:]\s*([^\n]+)', basic, re.M)
    if not match:
        return []
    line = match[1].split('。', 1)[0]
    if any(word in line for word in ('待核实', '未识别', '未提供')):
        return []
    line = re.sub(r'[（(][^（）()]*[）)]', '', line)
    names = re.split(r'、|；|;|\s+and\s+|\s*&\s*', line)
    # Commas alone are ambiguous (surname, given name): never guess their order.
    return list(dict.fromkeys(x for n in names if (x := clean_author(n)) and len(x) <= 180))


def backfill():
    """Repair only missing authors and known annotation suffixes; respect manual edits."""
    count = 0
    for paper in memory.all_papers():
        if 'authors' in paper.get('protected', []):
            continue
        old = paper.get('authors', [])
        authors = list(dict.fromkeys(clean_author(n) for n in old if clean_author(n)))
        evidence = ''
        if not authors:
            for version in memory.versions(paper['id']):
                authors = report_authors(version['report'])
                if authors:
                    evidence = memory.section(version['report'], '基本信息')
                    break
        if authors != old:
            changes = {'authors': authors}
            if evidence and not paper.get('bibliography_evidence') and 'bibliography_evidence' not in paper.get('protected', []):
                changes['bibliography_evidence'] = evidence
            memory.change(paper['id'], changes, expected_revision=paper['revision'], manual=False)
            count += 1
    return count


def overview(query='', field='', year='', project=''):
    all_papers = memory.all_papers(project)
    fields = sorted({x['field'] for p in all_papers for x in p['locations']})
    years = sorted({str(p['year']) for p in all_papers if p.get('year')}, reverse=True)
    papers = [p for p in all_papers if (not field or field in [x['field'] for x in p['locations']])
              and (not year or year == str(p.get('year') or 'unknown'))]
    rows = {}
    unknown = []
    for p in papers:
        names, seen = [], set()
        for n in p['authors']:
            name=clean_author(n); key=memory.norm(name)
            if key and key not in seen:
                names.append(name); seen.add(key)
        brief = {k: p.get(k) for k in ('id', 'title', 'year', 'authors', 'latest_job_id')}
        brief['fields'] = list(dict.fromkeys(x['field'] for x in p['locations']))
        if not names:
            unknown.append(brief)
        for index, name in enumerate(names):
            key = memory.norm(name)
            row = rows.setdefault(key, dict(id=key, name=name, variants=set(), papers=[], first_author=0, coauthors=Counter(), fields=Counter()))
            if any(x['id'] == p['id'] for x in row['papers']):
                continue
            row['variants'].add(name)
            row['papers'].append(brief)
            row['first_author'] += index == 0
            row['fields'].update(brief['fields'])
            row['coauthors'].update(memory.norm(n) for n in names if memory.norm(n) != key)
    results = []
    q = memory.norm(query)
    for row in rows.values():
        row['count'] = len(row['papers'])
        row['variants'] = sorted(row['variants'])
        row['coauthors'] = [{'name': rows[n]['name'], 'count': c} for n, c in row['coauthors'].most_common()]
        row['fields'] = dict(row['fields'])
        if not q or q in memory.norm(row['name'] + ' '.join(p['title'] for p in row['papers'])):
            results.append(row)
    results.sort(key=lambda r: (-r['count'], r['name'].casefold()))
    return dict(items=results, total=len(results), fields=fields, years=years, missing=unknown,
                counts=dict(scholars=len(rows), papers=len(papers), missing_authors=len(unknown)),
                note='按已完成精读的独立论文统计，共同作者各计一篇，多份分析不重复计数。仅统计本篇作者；被引用学者不混入。姓名缩写不与全名自动合并，同名身份仍需核对。')
