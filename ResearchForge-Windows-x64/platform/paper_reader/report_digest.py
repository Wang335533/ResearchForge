"""Read a saved close-reading report into a literature card. Pure parsing: no model, network or storage.

The report template fixes the section order and asks for "作者（年份）《原文题名》" citations, APA
entries for measurement sources and a "领域定位" line closing section 2. This module turns those into
card text (Markdown, rendered like the report), a preliminary map location, and the works the paper is in dialogue with,
so the literature map and network fill in even when the follow-up model call is unavailable.
"""
import re
import unicodedata

if __package__:
    from . import jel
else:
    import jel

VERSION = 2  # 2: card fields keep Markdown (tables, formulas) and are rendered like the report
CJK = '一-鿿'
TEXT_LIMIT = 5000

# Card field -> heading keywords, tried in order. Headings are matched on "## " sections only.
CARD_SECTIONS = {
    'abstract': ('摘要',), 'research_question': ('研究问题',), 'theory': ('核心理论', '理论逻辑'),
    'data': ('数据',), 'methods': ('研究设计',), 'findings': ('主要发现',), 'contribution': ('研究贡献',),
    'limitations': ('局限',), 'open_questions': ('未来研究', '后续研究'),
}
# What citing a work in each numbered section says about the relation.
ROLES = {2: 'dialogue', 3: 'theory', 4: 'theory', 6: 'measure', 7: 'method', 8: 'evidence', 9: 'evidence', 10: 'contribution'}
ROLE_LABELS = {'dialogue': '文献对话', 'theory': '理论来源', 'measure': '测量来源', 'method': '方法借鉴',
               'evidence': '结果比较', 'contribution': '贡献对照', 'mention': '其他提及'}
ROLE_WEIGHT = {'contribution': 4, 'dialogue': 3, 'theory': 3, 'measure': 2, 'method': 2, 'evidence': 1, 'mention': 1}
PLACEHOLDER = re.compile(r'^(?:待核实|未取得|未提供|不适用|无)')

SURNAME = r"[A-Z][a-zà-öø-ÿ]+(?:(?:['’\-]?[A-Z]|['’\-])[a-zà-öø-ÿ]+)*"  # Sant’Anna, McDonald, Mrázová
NAME = r"(?:(?:van|von|de|der|den|da|di|du|la|le)\s)?" + SURNAME + r"(?:\s" + SURNAME + r")?"
AUTHORS = NAME + r"(?:\s*(?:、|，|,|和|与|及|and|&)\s*" + NAME + r")*(?:\s*等人?|\s+et\s+al\.?)?"
PROSE_LATIN = re.compile('(' + AUTHORS + r')\s*[（(]\s*((?:19|20)\d{2}[a-z]?)\s*[)）](?:\s*的)?\s*(?:《([^》\n]{2,300})》)?')
# Chinese names have no spaces to delimit them, so Chinese works are recognised by year plus title.
PROSE_CJK = re.compile('[（(]\\s*((?:19|20)\\d{2}[a-z]?)\\s*[)）](?:\\s*的)?\\s*《([^》\\n]*[' + CJK + '][^》\\n]{1,300})》')
APA_AUTHOR = r"[A-Z][\w'’\-]+(?:[\s\-][A-Z][\w'’\-]+)*,\s(?:[A-Z]\.(?:\s?-?[A-Z]\.)*)"
APA = re.compile(r"((?:" + APA_AUTHOR + r"(?:,\s|,?\s&\s|\s)?)+(?:\.\.\.\s?" + APA_AUTHOR + r")?)\s*\(((?:19|20)\d{2}[a-z]?)\)\.\s*(.+?)\.(?=\s|\*|$)")
DOI = re.compile(r'(?:doi\.org/|doi[：:]\s*)?(10\.\d{4,9}/[^\s；;，,。）)\]>]+)', re.I)


def norm(value):
    return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', str(value or '')).casefold())


def sections(report):
    """[(heading, body)] for each "## " section; body stops at the next "#"/"##" heading."""
    return [(m[1].strip(), m[2].strip()) for m in re.finditer(r'^##[ \t]+([^\n]+)\n(.*?)(?=^#{1,2}[ \t]|\Z)', report, re.M | re.S)]


def section(report, *needles):
    for heading, body in sections(report):
        if any(n in heading for n in needles):
            return body
    return ''


def number(heading):
    m = re.match(r'(\d+)[.．、]', heading)
    return int(m[1]) if m else None


def inline(text):
    return re.sub(r'\s{2,}$', '', text.replace('**', '').replace('__', '').replace('`', '')).rstrip()


def readable(md, limit=TEXT_LIMIT):
    """Report Markdown as plain card text: subheadings become 【…】 lines, tables become ｜-separated rows."""
    paragraphs = []
    for para in re.split(r'\n[ \t]*\n', (md or '').strip()):
        stripped = para.strip()
        if re.fullmatch(r'\*\*[^*\n]{1,40}\*\*[：:]?', stripped):  # a bold line alone is a label
            paragraphs.append('【' + stripped.strip('*：: ') + '】')
            continue
        lines = []
        for line in para.splitlines():
            if re.fullmatch(r'\s*\|?\s*:?-{3,}.*', line):
                continue
            heading = re.match(r'^\s*#{3,6}\s+(.+)$', line)
            if heading:
                lines.append('【' + inline(heading[1]).strip() + '】')
            elif line.lstrip().startswith('|'):
                cells = [inline(c).strip() for c in re.split(r'(?<!\\)\|', line.strip().strip('|'))]
                lines.append('｜'.join(c for c in cells if c))
            else:
                lines.append(inline(re.sub(r'^(\s*)[-*+]\s+', r'\1· ', line)))
        text = '\n'.join(l for l in lines if l.strip())
        if text:
            paragraphs.append(text)
    text = '\n\n'.join(paragraphs)
    if len(text) > limit:
        cut = text.rfind('\n', 0, limit)
        text = text[:cut if cut > limit // 2 else limit].rstrip() + '\n……（完整内容见“完整精读”）'
    return text


def card_markdown(md, limit=TEXT_LIMIT):
    """Section Markdown kept for rendering (tables, formulas, lists); subheadings drop a level to sit under the card heading."""
    text = re.sub(r'^(#{3,5})(?=\s)', r'#\1', (md or '').strip(), flags=re.M)
    if len(text) > limit:
        cut = text.rfind('\n\n', 0, limit)  # never inside a table or paragraph
        text = text[:cut if cut > limit // 3 else limit].rstrip() + '\n\n……（完整内容见“完整精读”）'
    return text


def preview(md, limit=300):
    """One plain line of card text for lists and map cards."""
    text = re.sub(r'【[^】\n]*】\s*', '', readable(md, limit * 3))
    return re.sub(r'\s+', ' ', text).strip()[:limit]


def field_line(block, *labels):
    for label in labels:
        m = re.search(r'^\s*(?:[-*]\s*)?(?:\*\*)?' + label + r'[^\n：:*]{0,24}(?:\*\*)?\s*[：:]\s*(?:\*\*)?\s*(.+)$', block, re.M)
        if m:
            return inline(m[1]).strip(' *')
    return ''


def strip_locator(text):
    return re.sub(r'\s*[（(](?:原文|参考文献)[^（）()]{0,80}[)）]\s*$', '', text).strip(' 。；;')


def find_doi(*texts):
    for text in texts:
        for m in DOI.finditer(text or ''):
            doi = m[1].rstrip('.,;*')
            if re.fullmatch(r'10\.\d{4,9}/\S{2,180}', doi):
                return doi
    return ''


def bibliography(report):
    basic = section(report, '基本信息')
    apa = section(report, 'APA')
    title = strip_locator(field_line(basic, '原文题名', '题名', '标题'))
    yearline = field_line(basic, '发表年份', '年份')
    year = re.search(r'\b(18\d{2}|19\d{2}|20\d{2})\b', yearline)
    if '待核实' in yearline and not any(x in yearline for x in ('版权信息', '发表年份为', '发表于')):
        year = None
    journal = field_line(basic, '期刊', '发表期刊', '出版')
    journal = '' if PLACEHOLDER.search(journal) else strip_locator(journal.split('。', 1)[0])[:200]
    doi = find_doi(field_line(basic, 'DOI'), apa)
    evidence = card_markdown(basic, 3000) + ('\n\n#### APA 参考文献\n\n' + card_markdown(apa, 1800) if apa else '')
    return dict(title=title, year=int(year[1]) if year else None, year_note=yearline[:600], journal=journal,
                doi=doi, bibliography_evidence=evidence[:5000])


def locations(report):
    """The report's own map position: "领域定位：领域 → 主题 → 问题", plus optional "跨领域关联"."""
    found = []
    block = section(report, '文献位置') or report
    for label, many in (('领域定位', False), ('跨领域关联', True)):
        value = field_line(block, label)
        for part in (re.split(r'[；;]', value) if many else [value]):
            path = [inline(x).strip(' 。“”"') for x in re.split(r'\s*(?:→|->|＞|>)\s*', part) if x.strip()]
            if len(path) >= 3 and not any(PLACEHOLDER.search(x) for x in path[:3]):
                found.append(dict(field=path[0][:200], topic=path[1][:200], question='→'.join(path[2:])[:200],
                                  reason='据精读报告“文献位置与研究缺口”一节的领域定位整理，尚未与已有分类统一。',
                                  evidence='精读报告第2节'))
    return jel.normalize_locations(found)


def sentence_at(text, start, end):
    left = max(text.rfind(c, 0, start) for c in '。！？\n')
    right = min([i for i in (text.find(c, end) for c in '。！？\n') if i >= 0] or [len(text)])
    sentence = re.sub(r'\s*\|\s*', '｜', inline(text[left + 1:right + 1])).strip(' ·-｜')
    return sentence if len(sentence) <= 320 else sentence[:320] + '……'


def split_authors(text):
    names = re.split(r'\s*(?:、|，|,|和|与|及|\band\b|&)\s*', re.sub(r'\s*(?:等人?|et\s+al\.?)\s*$', '', text.strip()))
    return [n for n in names if n]


def _add(found, own_title, *, authors, year, title, doi, role, context, more=False):
    title = (title or '').strip(' .。')
    if own_title and title and norm(title) == own_title:
        return
    first = norm(authors[0]) if authors else ''
    key = (first or 't' + norm(title)[:40]) + ':' + year
    item = found.get(key)
    if not item:
        item = found[key] = dict(key=key, authors=[], more=False, year=year, title='', doi='', roles=[], contexts=[], mentions=0)
    item['more'] = item['more'] or more
    if len(authors) > len(item['authors']):
        item['authors'] = authors[:20]
    if len(title) > len(item['title']):
        item['title'] = title[:300]
    item['doi'] = item['doi'] or doi
    if role not in item['roles']:
        item['roles'].append(role)
    item['mentions'] += 1
    if context and len(item['contexts']) < 4 and not any(c['text'] == context for c in item['contexts']):
        item['contexts'].append(dict(role=role, text=context))


def citations(report, own_title=''):
    """Works the paper is in dialogue with, from prose citations and the APA measurement sources."""
    own = norm(own_title)
    found = {}
    first_part = re.split(r'^#[ \t]+二、', report, maxsplit=1, flags=re.M)[0]  # never "与我的研究对比"
    for heading, body in sections(first_part):
        n = number(heading)
        if n is None or n in (14, 15):
            continue  # bibliography and this paper's own APA entry, recap and story
        role = ROLES.get(n, 'theory' if n == 13 else 'mention')
        for m in PROSE_LATIN.finditer(body):
            names = split_authors(m[1])
            _add(found, own, authors=names, year=m[2], title=m[3] or '', doi='', role=role,
                 context=sentence_at(body, m.start(), m.end()), more=bool(re.search(r'(?:等人?|et\s+al\.?)\s*$', m[1])))
        for m in PROSE_CJK.finditer(body):
            _add(found, own, authors=[], year=m[1], title=m[2], doi='', role=role, context=sentence_at(body, m.start(), m.end()))
        for m in APA.finditer(body):
            names = re.findall(r"([A-Z][\w'’\-]+(?:[\s\-][A-Z][\w'’\-]+)*),\s(?:[A-Z]\.)", m[1])
            tail = body[m.end():m.end() + 400]
            stop = re.search(r'[；;]|\s\d+[.、]\s|\n', tail)
            _add(found, own, authors=names, year=m[2], title=inline(m[3]).strip(' *'), doi=find_doi(tail[:stop.start()] if stop else tail),
                 role='measure' if n == 6 else role, context=sentence_at(body, m.start(), m.end()))
    items = list(found.values())
    for item in items:
        item['score'] = sum(ROLE_WEIGHT[r] for r in item['roles']) + min(item['mentions'], 6)
    items.sort(key=lambda x: (-x['score'], x['year'], x['key']))
    return items[:80]


def card(report, fallback_title=''):
    """Card fields, preliminary locations and cited works read from one report."""
    bib = bibliography(report)
    result = dict(bib, title=bib['title'] or fallback_title)
    for field, needles in CARD_SECTIONS.items():
        result[field] = card_markdown(section(report, *needles))
    result['locations'] = locations(report)
    result['cited'] = citations(report, result['title'])
    return result


def label(item):
    authors = item.get('authors') or []
    many = len(authors) > 2 or (item.get('more') and len(authors) == 1)
    who = authors[0] + (' 等' if many else ' 和 ' + authors[1] if len(authors) == 2 else '') if authors else ''
    return (who + '（' + item['year'] + '）' if who else '（' + item['year'] + '）') + ('《' + item['title'] + '》' if item.get('title') else '')
