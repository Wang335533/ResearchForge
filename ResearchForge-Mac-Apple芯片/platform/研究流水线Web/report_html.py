"""Pure offline Chinese report rendering, independent of the research worker."""
import hashlib
import html
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote
from citations import is_insufficient_evidence

ASSETS = Path(__file__).with_name('report_assets')
TEMPLATE_VERSION = 'chinese-report-5'
TRANSLATION_VERSION = 'faithful-zh-1'
TERMS = json.loads((ASSETS / 'terminology.json').read_text(encoding='utf-8'))
LIMITS = {'importance': ('重要性', 20), 'insight': ('洞见', 20), 'mechanism': ('机制', 20),
          'dialogue': ('文献对话', 15), 'testability': ('可检验性', 10),
          'policy': ('政策价值', 10), 'expression': ('表达', 5)}


def text_key(text):
    return hashlib.sha256((TRANSLATION_VERSION + '\0' + text).encode('utf-8')).hexdigest()


def needs_translation(text):
    text = str(text or '')
    latin = len(re.findall('[A-Za-z]', text))
    han = len(re.findall('[\u3400-\u9fff]', text))
    return latin >= 3 and (not han or latin > 2 * han)


def chinese_terms(text):
    text = str(text or '')
    text = re.sub(r'\binsufficient evidence\b', '证据不足', text, flags=re.I)
    for en, zh in sorted(TERMS.items(), key=lambda pair: -len(pair[0])):
        pattern = r'(?<![A-Za-z])' + re.escape(en) + r'(?![A-Za-z])'
        text = re.sub(pattern + r'\s*[（(]([\u4e00-\u9fff]{2,24})[）)]', r'\1', text)
        text = re.sub(pattern, zh, text)
    return re.sub(r'([\u4e00-\u9fff]{2,24})\s*[（(]\1[）)]', r'\1', text)


def snapshot(task):
    """Keep the task's research results, excluding credentials and execution logs."""
    fields = ('id', 'topic', 'count', 'selection_count', 'status', 'created_at', 'updated_at',
              'revision', 'ideas', 'top5', 'judge_batches', 'lit', 'proposals')
    data = {k: task.get(k) for k in fields}
    # Missing fields on historical jobs stay missing, retaining their cached reports.
    for key in ('literature_checks', 'workflow_version'):
        if key in task:
            data[key] = task[key]
    return json.loads(json.dumps(data, ensure_ascii=False))


def source_fields(task):
    for check in (task.get('literature_checks') or {}).values():
        yield check.get('reason', '')
    for idea in task.get('ideas') or []:
        yield idea.get('title', '')
        yield idea.get('abstract', '')
    for judge in task.get('top5') or []:
        yield judge.get('hook', '')
        yield judge.get('reason', '')
    for batch in task.get('judge_batches') or []:
        for judge in batch.get('top5') or []:
            yield judge.get('hook', '')
            yield judge.get('reason', '')
    for papers in (task.get('lit') or {}).values():
        for paper in papers:
            for key in ('title', 'abstract', 'retrieval_reason'):
                yield paper.get(key, '')
    for item in task.get('proposals') or []:
        prop = item['proposal']
        for key in ('theoretical_foundation', 'improvement_notes'):
            yield prop.get(key, '')
        yield prop.get('title_zh') or prop.get('title_en', '')
        yield prop.get('abstract_zh') or prop.get('abstract_en', '')
        for hyp in prop.get('hypotheses') or []:
            yield hyp.get('hypothesis', '')
            for key in ('target_theory', 'how', 'title'):
                yield hyp.get('contribution', {}).get(key, '')
        for ref in prop.get('key_references') or []:
            yield ref.get('title', '')


def translation_units(task):
    return {text_key(text): text for text in source_fields(task) if needs_translation(text)}


def esc(value):
    return html.escape(str(value if value is not None else ''), quote=True)


def paper_key(paper):
    # Retrieval rank/reason can differ between ideas; the same paper body is stored once.
    fields = ('source_id', 'ut', 'title', 'abstract', 'authors', 'year', 'journal', 'doi')
    body = json.dumps({k: paper.get(k) for k in fields}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode('utf-8')).hexdigest()[:24]


def reference_status(ref):
    labels = {'matched_local_record': '已匹配本次检索记录',
              'unmatched_local_record': '未匹配本次检索记录',
              'external_unverified': '未匹配本次检索文献，尚未核对',
              'ambiguous_local_record': '对应记录不唯一，待核对',
              'conflicting_local_record': '引用信息存在冲突，待核对'}
    parts = [labels.get(ref.get('reference_status'), '来源尚未核对')]
    parts += [str(ref[k]) for k in ('reference_label', 'source_id') if ref.get(k)]
    if ref.get('reference_corrected_fields'):
        parts.append('项目已回填书目信息')
    if ref.get('reference_note'):
        parts.append(ref['reference_note'])
    return ' · '.join(parts)


def render_report(task, translations, *, preserve_original=False):
    ideas = task.get('ideas') or []
    proposals = sorted(task.get('proposals') or [], key=lambda p: p['judge']['rank'])
    judges = {j['id']: j for j in task.get('top5') or []}
    checks = task.get('literature_checks') or {}
    batch_reviews = {}
    for number, batch in enumerate(task.get('judge_batches') or [], 1):
        for judge in batch.get('top5') or []:
            batch_reviews.setdefault(judge['id'], []).append((number, judge))
    proposal_ids = {p['idea']['id'] for p in proposals}
    idea_ids = [i['id'] for i in ideas]
    if len(idea_ids) != len(set(idea_ids)) or any(type(i) is not int or i <= 0 for i in idea_ids):
        raise ValueError('构想编号缺失或重复，无法生成可靠的报告目录')
    if not set(judges).issubset(idea_ids) or not proposal_ids.issubset(idea_ids):
        raise ValueError('评审或提案对应的原始构想缺失')
    if any(not str(i).isdigit() or int(i) not in judges for i in checks):
        raise ValueError('文献判断对应的入选构想缺失')
    missing = set() if preserve_original else set(translation_units(task)) - set(translations)
    if missing:
        raise ValueError(f'还有 {len(missing)} 段英文未完成翻译')

    def zh(text):
        text = str(text or '')
        if preserve_original:
            return text
        return translations[text_key(text)] if needs_translation(text) else chinese_terms(text)

    def paras(text, translate=True):
        return ''.join(f'<p>{esc(p)}</p>' for p in (zh(text) if translate else str(text or '')).split('\n') if p.strip())

    def block(label, text, translate=True):
        return f'<div class="text-block"><h4>{esc(label)}</h4>{paras(text,translate)}</div>'

    def original(label, text, force=False):
        if preserve_original and not force:
            return ''  # The main body already contains the complete source.
        if not needs_translation(text):
            return ''
        return f'<details class="original"><summary>{esc(label)}</summary><div lang="en">{paras(text, False)}</div></details>'

    def citation(ref, key=False):
        title = ref.get('title') or ''
        authors = str(ref.get('authors') or '作者未提供').replace('|', '；')
        meta = f'{authors}（{ref.get("year") or "年份未提供"}） · {ref.get("journal") or "期刊未提供"}'
        doi = ref.get('doi')
        link = f'<a href="https://doi.org/{quote(str(doi), safe="/():.-")}" target="_blank" rel="noopener noreferrer">DOI：{esc(doi)}</a>' if doi else ''
        tag, cls = ('li', 'key-reference') if key else ('div', 'representative-reference')
        return f'<{tag} class="{cls}"><p class="ref-title">{esc(zh(title) if title else "文献题名未提供")}</p><p class="bibliography">{esc(meta)}</p><p class="ref-status">{esc(reference_status(ref))}</p>{link}{original("原文题名",title)}</{tag}>'

    def review(j, label='项目最终评审'):
        limits = dict(LIMITS)
        if j.get('rubric_version') == 'gen_prompt_2':
            limits.update(dialogue=('理论贡献',15), policy=('现实解释力',10))
        scores = ''.join(f'<div><span>{label}</span><strong>{esc(j.get("scores",{}).get(k,"—"))}<small>/{maximum}</small></strong></div>' for k,(label,maximum) in limits.items())
        return f'<div class="review"><div class="review-head"><span>{esc(label)} · 第 {esc(j.get("rank"))} 名</span><strong>{esc(j.get("total"))}<small> / 100</small></strong></div><p><strong>核心亮点：</strong>{esc(zh(j.get("hook")))}</p>{paras(j.get("reason"))}<div class="scores">{scores}</div></div>'

    def decision_label(check):
        if check.get('decision') == 'pass':
            return '通过文献判断'
        return '暂缓：证据不足' if check.get('evidence_status') == 'insufficient' or is_insufficient_evidence(check.get('reason')) else '未通过文献判断'

    def literature_check(ident, anchor=False):
        check = checks.get(str(ident))
        if not check:
            return ''
        if check.get('decision') not in ('pass', 'drop') or not check.get('reason'):
            raise ValueError('文献判断缺少有效的结论或理由')
        if check['decision'] == 'drop' and ident in proposal_ids:
            raise ValueError('未通过文献判断的构想不能同时标为完成提案')
        matches = (task.get('lit') or {}).get(str(ident), [])
        labels = {p.get('reference_label') or f'P{i}':p for i,p in enumerate(matches,1)}
        ids = check.get('source_ids')
        if not isinstance(ids,list) or any(not isinstance(sid,str) or sid not in labels for sid in ids) or len(ids) != len(set(ids)):
            raise ValueError('文献判断的来源未对应到本次检索文献')
        if check.get('resolved_source_ids') is not None and check['resolved_source_ids'] != [labels[sid].get('source_id') or labels[sid].get('ut') for sid in ids]:
            raise ValueError('文献判断的短编号与原始来源对应不一致')
        links = ''.join(f'<li><a href="#paper-{paper_key(labels[sid])}">[{esc(sid)}] {esc(zh(labels[sid].get("title")))}</a> · {esc(labels[sid].get("source_id") or labels[sid].get("ut"))}</li>' for sid in ids)
        scope = '当前检索证据不足以支持继续优化，不代表构想没有研究价值。' if decision_label(check) == '暂缓：证据不足' else '本判断基于本次检索摘要；书目匹配不代表文献内容解读已核验。'
        anchor_attr = f' id="check-{ident}"' if anchor else ''
        return f'<section class="review"{anchor_attr}><h4>文献判断 · {decision_label(check)}</h4>{paras(check["reason"])}<ul>{links}</ul><p class="meta">{scope}</p>{original("文献判断原稿",check["reason"])}</section>'

    idea_cards, idea_toc = [], []
    for idea in ideas:
        ident = idea['id']
        title, abstract = zh(idea.get('title')), zh(idea.get('abstract'))
        if ident in proposal_ids:
            badge = f'<a class="badge selected" href="#proposal-{ident}">入选第 {judges[ident]["rank"]} 名 · 查看提案 →</a>'
        elif checks.get(str(ident),{}).get('decision') == 'drop':
            badge = f'<a class="badge" href="#check-{ident}">{decision_label(checks[str(ident)])} · 未生成提案</a>'
        elif ident in judges:
            badge = f'<span class="badge selected">入选第 {judges[ident]["rank"]} 名 · 提案尚未完成</span>'
        elif judges:
            badge = '<span class="badge">未入选最终提案</span>'
        else:
            badge = '<span class="badge">尚无筛选结果</span>'
        batch_notes = ''.join(review(j, f'第 {number} 批评审') for number, j in batch_reviews.get(ident, []))
        if batch_notes:
            batch_notes = f'<details class="batch-review print-expand"><summary>查看分批评审记录</summary>{batch_notes}</details>'
        idea_cards.append(f'<article id="idea-{ident}" class="idea-card searchable" data-kind="idea" data-selected="{str(ident in judges).lower()}"><div class="card-top"><span class="number">构想 {ident:02d}</span>{badge}</div><h3>{esc(title)}</h3><div class="abstract">{paras(abstract,False)}</div>{review(judges[ident]) if ident in judges and ident not in proposal_ids else ""}{literature_check(ident,True)}{batch_notes}{original("原始标题与摘要 · 展开核对",idea.get("title","")+chr(10)+idea.get("abstract",""))}</article>')
        idea_toc.append(f'<a href="#idea-{ident}" data-toc="idea-{ident}"><span>{ident:02d}</span>{esc(title)}</a>')

    papers, paper_origins, literature_groups = {}, {}, []
    retrieval_rows = 0
    for ident, matches in (task.get('lit') or {}).items():
        if not str(ident).isdigit() or int(ident) not in idea_ids:
            raise ValueError('检索文献对应的原始构想缺失')
        idea = next(i for i in ideas if i['id'] == int(ident))
        links = []
        for position, paper in enumerate(matches, 1):
            key = paper_key(paper)
            papers.setdefault(key, paper)
            paper_origins.setdefault(key, set()).add(int(ident))
            method = '、'.join({'keyword':'关键词', 'embedding':'语义向量'}.get(m, str(m)) for m in paper.get('matched_by') or [])
            score = paper.get('semantic_similarity', paper.get('sim'))
            language = paper.get('language') or ('zh' if str(paper.get('ut') or paper.get('source_id') or '').startswith('CNKI:') else 'en' if str(paper.get('ut') or paper.get('source_id') or '').startswith('WOS:') else '')
            metadata = [f'文献原文语言：{ {"zh":"中文", "en":"英文"}.get(language, "未标注") }']
            if method:
                metadata.append(f'召回方式：{method}')
            if score is not None:
                metadata.append(f'语义相似度：{score}')
            if paper.get('retrieval_score') is not None:
                metadata.append(f'检索得分：{paper["retrieval_score"]}')
            reason = f'<p class="retrieval-reason">{esc(zh(paper["retrieval_reason"]))}</p>' if paper.get('retrieval_reason') else ''
            label = paper.get('reference_label') or f'P{position}'
            links.append(f'<li class="retrieval-match"><a href="#paper-{key}"><span class="number">{esc(label)}</span> {esc(zh(paper.get("title")) or "题名未提供")}</a><p class="meta">{esc(" · ".join(metadata))}</p>{reason}</li>')
        retrieval_rows += len(matches)
        language_counts = {language:sum((paper.get('language') or ('zh' if str(paper.get('ut') or paper.get('source_id') or '').startswith('CNKI:') else 'en' if str(paper.get('ut') or paper.get('source_id') or '').startswith('WOS:') else '')) == language for paper in matches) for language in ('zh','en')}
        language_summary = f"中文 {language_counts['zh']} 篇 · 英文 {language_counts['en']} 篇"
        literature_groups.append(f'<details class="literature-group print-expand" id="literature-{ident}"><summary>构想 {int(ident):02d} · {esc(zh(idea.get("title")))} · {len(matches)} 篇 · {language_summary}</summary><p><a href="#idea-{ident}">返回原始构想 ↗</a></p><ol>{"".join(links)}</ol></details>')
    paper_cards = []
    for number, (key, paper) in enumerate(papers.items(), 1):
        meta = f'{str(paper.get("authors") or "作者未提供").replace("|", "；")}（{paper.get("year") or "年份未提供"}） · {paper.get("journal") or "期刊未提供"}'
        origins = '、'.join(f'<a href="#literature-{ident}">构想 {ident:02d}</a>' for ident in sorted(paper_origins[key]))
        doi = paper.get('doi')
        link = f'<a href="https://doi.org/{quote(str(doi), safe="/():.-")}" target="_blank" rel="noopener noreferrer">DOI：{esc(doi)}</a>' if doi else ''
        abstract = block('完整摘要 · 原文' if preserve_original else '完整中文摘要', paper['abstract']) if paper.get('abstract') else '<p>本次检索记录未提供摘要。</p>'
        paper_cards.append(f'<details id="paper-{key}" class="paper-card searchable print-expand" data-kind="paper" data-selected="{str(bool(paper_origins[key] & set(judges))).lower()}"><summary><span class="number">文献 {number:03d}</span> {esc(zh(paper.get("title")) or "题名未提供")}</summary><p class="bibliography">{esc(meta)}</p><p class="meta">来源编号：{esc(paper.get("source_id") or paper.get("ut") or "未提供")} · 检索于 {origins}</p>{link}{abstract}{original("原始题名与完整摘要 · 展开核对", str(paper.get("title") or "")+chr(10)+str(paper.get("abstract") or ""))}</details>')

    proposal_cards, proposal_toc = [], []
    for proposal_number, item in enumerate(proposals, 1):
        ident, judge, prop = item['idea']['id'], item['judge'], item['proposal']
        title = zh(prop.get('title_zh') or prop.get('title_en'))
        hypotheses = []
        for hyp in prop.get('hypotheses') or []:
            c = hyp.get('contribution') or {}
            hypotheses.append(f'<section class="hypothesis"><h5><span>{esc(hyp.get("id"))}</span> {esc(zh(hyp.get("hypothesis")))}</h5><p class="theory-tag">目标理论 · {esc(zh(c.get("target_theory")))}</p>{block("理论贡献",c.get("how"))}<h6>对应代表文献</h6>{citation(c)}</section>')
        refs = prop.get('key_references') or []
        abstract_label = '英文摘要' if preserve_original and not prop.get('abstract_zh') and prop.get('abstract_en') else '中文摘要'
        legacy_parts = [prop.get(en,'') for zh_field,en in (('title_zh','title_en'),('abstract_zh','abstract_en'))
                        if not preserve_original or (prop.get(zh_field) and prop.get(en) != prop.get(zh_field))]
        legacy_original = original('历史英文原稿 · 展开核对', '\n'.join(legacy_parts), force=preserve_original)
        proposal_cards.append(f'''<article id="proposal-{ident}" class="proposal-card searchable" data-kind="proposal" data-selected="true">
<div class="card-top"><span class="number">最终提案 {proposal_number:02d} · 初筛第 {judge['rank']} 名</span><a class="badge selected" href="#idea-{ident}">对应构想 {ident:02d} ↗</a></div><h3>{esc(title)}</h3>{review(judge)}
{literature_check(ident)}
{block(abstract_label,prop.get('abstract_zh') or prop.get('abstract_en'))}
{block('理论基础',prop.get('theoretical_foundation'))}
<div class="text-block"><h4>研究假说与逐项理论贡献</h4>{''.join(hypotheses)}</div>
{block('相对原始构想的改进',prop.get('improvement_notes'))}
<div class="text-block"><h4>关键参考文献 · {len(refs)} 条</h4><ol class="references">{''.join(citation(r,True) for r in refs)}</ol></div>
{legacy_original}</article>''')
        proposal_toc.append(f'<a href="#proposal-{ident}" data-toc="proposal-{ident}"><span>{proposal_number:02d}</span>{esc(title)}</a>')

    rows = []
    for judge in sorted(judges.values(), key=lambda j:j['rank']):
        ident = judge['id']
        prop = next((p['proposal'] for p in proposals if p['idea']['id']==ident),None)
        title = zh((prop.get('title_zh') or prop.get('title_en')) if prop else next(i['title'] for i in ideas if i['id']==ident))
        anchor = f'proposal-{ident}' if prop else f'idea-{ident}'
        check = checks.get(str(ident))
        status_note = f'<span class="table-note">{decision_label(check)}</span>' if check else ''
        rows.append(f'<tr><td><span class="rank">{judge["rank"]:02d}</span></td><td><a href="#{anchor}">{esc(title)}</a><span class="table-note">对应原始构想 {ident:02d}</span>{status_note}</td><td class="score-cell">{esc(judge.get("total"))}</td></tr>')

    topic = str(task.get('topic') or '研究主题未提供')
    context_parts = []
    lines = topic.splitlines()
    index = 0
    while index < len(lines):
        if '\t' in lines[index]:
            group = []
            while index < len(lines) and '\t' in lines[index]:
                group.append(lines[index].split('\t')); index += 1
            context_parts.append('<div class="table-scroll"><table class="data-table"><tbody>'+''.join('<tr>'+''.join(f'<td>{esc(cell)}</td>' for cell in row)+'</tr>' for row in group)+'</tbody></table></div>')
        else:
            context_parts.append(paras(lines[index],False)); index += 1
    counts = {'ideas':len(ideas),'proposals':len(proposals),'hypotheses':sum(len(p['proposal'].get('hypotheses') or []) for p in proposals),
              'references':sum(len(p['proposal'].get('key_references') or []) for p in proposals),
              'papers':len(papers), 'retrieval_groups':len(literature_groups), 'retrieval_rows':retrieval_rows}
    gate_summary = ''
    if checks:
        counts.update(literature_checks=len(checks), dropped=sum(c['decision']=='drop' for c in checks.values()),
                      insufficient=sum(decision_label(c)=='暂缓：证据不足' for c in checks.values()))
        gate_summary = f'<p class="editor-note">已完成 {len(checks)} 条文献判断 · {counts["dropped"]} 个方向未进入提案，其中 {counts["insufficient"]} 个因证据不足暂缓。判断理由与对应文献收录在原始构想正文中；提案数按实际通过数量保留。</p>'
    n,m,h,r = (counts[k] for k in ('ideas','proposals','hypotheses','references'))
    stamp = lambda value: datetime.fromtimestamp(value,timezone(timedelta(hours=8))).strftime('%Y年%m月%d日 %H:%M') if isinstance(value,(int,float)) else '未记录'
    state = {'completed':'研究已完成','failed':'研究失败，导出已保存内容','stopped':'研究已停止，导出已保存内容','interrupted':'研究已中断，导出已保存内容'}.get(task.get('status'),'已保存结果快照')
    css = (ASSETS/'report.css').read_text(encoding='utf-8')
    js = (ASSETS/'report.js').read_text(encoding='utf-8')
    edition = '完整原文' if preserve_original else '中文全文'
    reading_note = ('报告汇集本次研究说明、全部构想、分批及最终评审、检索文献与提案，沿用已保存内容和原始语言。'
                    '英文摘要保留英文，中文摘要保留中文；标题、理论、假说、评分和引用信息均按原稿展示。'
                    '导出只在本地整理排版，不调用模型、不翻译或改写。历史提案同时保存中英文原稿时，两种原稿均保留。'
                    '本次导出未进行实证检验，书目匹配不代表论文内容解读已核验。') if preserve_original else (
                    '报告汇集本次研究说明、构想、分批及最终评审、检索文献与提案，研究正文统一以中文展示。每份提案只显示一份中文摘要，优先采用项目中文原稿，缺少时全文翻译历史外文摘要。原文中的数值、制度假设与“研究发现”按原稿保留，本次导出未进行实证检验。作者、原刊名及书目核对状态沿用项目记录；历史外文和文献原稿可展开核对。')
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>研究报告 · {n}个构想与{m}份提案 · {edition}</title><style>{css}</style></head><body><div class="reading-progress" aria-hidden="true"></div>
<header class="topbar"><a class="brand" href="#home">RESEARCHFORGE / {'完整阅读版' if preserve_original else '中文阅读版'}</a><nav aria-label="主导航"><a href="#ideas">{n} 个构想</a><a href="#literature">检索文献</a><a href="#proposals">{m} 份提案</a><button id="print" type="button">打印 / 保存 PDF</button></nav></header>
<div class="layout"><aside class="sidebar" aria-label="全文目录"><h2>阅读目录</h2><a class="side-home" href="#overview">{'入选构想 · 判断与排名' if checks else '入选提案 · 排名总览'} ↗</a><details open><summary>全部原始构想 · {n}</summary>{''.join(idea_toc)}</details><a class="side-home" href="#literature">文献检索与完整摘要 · {len(papers)} ↗</a><details open><summary>最终优化提案 · {m}</summary>{''.join(proposal_toc)}</details></aside><main id="home">
<section class="hero"><div class="eyebrow">研究工作台 · {'完整分析报告' if preserve_original else '中文分析报告'}</div><h1>研究构想<br>与最终提案</h1><p class="subtitle">{esc(topic.splitlines()[0][:100])}{'…' if len(topic.splitlines()[0])>100 else ''}</p>
<div class="stats"><div><strong>{n}</strong><span>原始构想 · {'完整保留' if preserve_original else '全文翻译'}</span></div><div><strong>{m}</strong><span>最终提案 · 完整保留</span></div><div><strong>{h}</strong><span>研究假说 · 逐项理论贡献</span></div></div>
<p class="meta">创建：{stamp(task.get('created_at'))}　｜　快照：{stamp(task.get('updated_at'))}　｜　北京时间<br>{state} · 已保存 {n} / {esc(task.get('count',n))} 个构想<br>任务编号：{esc(task.get('id'))}</p>
<aside class="editor-note"><strong>阅读说明</strong>　{reading_note}</aside>
<details class="context" id="context"><summary>完整研究说明与数据覆盖 · 展开查看</summary><div class="context-inner">{''.join(context_parts)}</div></details></section>
<section class="overview" id="overview"><div class="eyebrow">项目评审结果</div><h2>{'入选构想 · 文献判断与排名' if checks else '入选提案 · 排名总览'}</h2><p class="meta">评分与排名来自已保存结果。点击题名可跳转至正文。</p>{gate_summary}<div class="table-scroll"><table><thead><tr><th>排名</th><th>提案 / 构想</th><th>总分</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>{'<p>尚无筛选结果。</p>' if not rows else ''}</section>
<section id="ideas"><div class="section-heading"><div><div class="eyebrow">01 / 初始方向</div><h2>全部原始构想</h2></div><p>{n} 篇{'完整原文' if preserve_original else '中文全文'} · 保留原始编号</p></div>
<div class="tools" role="search"><input id="search" type="search" aria-label="搜索构想、文献与提案全文" placeholder="搜索题名、机制或全文关键词…"><label><input id="selected-only" type="checkbox"> 仅看入选构想</label><button id="reset" type="button">显示全部</button><p id="search-status" role="status" aria-live="polite"></p></div><p id="no-results" hidden>没有找到匹配内容。请更换关键词或点击“显示全部”。</p>{''.join(idea_cards)}</section>
<section id="literature"><div class="section-heading"><div><div class="eyebrow">02 / 文献检索</div><h2>检索结果与完整摘要</h2></div><p>{len(literature_groups)} 组检索 · {retrieval_rows} 条匹配 · {len(papers)} 篇去重文献</p></div><p class="meta">按构想保留检索排序、来源编号与召回说明；同一文献的完整摘要收录一次。展开构想查看匹配列表，点击文献题名阅读{'摘要原文' if preserve_original else '中文摘要及原文'}。检索相关性不等于支持研究假说。</p>{''.join(literature_groups)}<h3>文献摘要库 · {len(papers)} 篇</h3>{''.join(paper_cards)}{'<p>当前尚无已保存的检索结果。</p>' if not literature_groups else ''}</section>
<section id="proposals"><div class="section-heading"><div><div class="eyebrow">03 / 筛选与深化</div><h2>最终优化提案</h2></div><p>{m} 份完整提案 · 按评审排名排列</p></div><p class="meta">保留评审、评分、摘要、理论、假说、改进说明及关键文献。书目匹配不代表文献内容解读已经核验。</p>{''.join(proposal_cards)}{('<p>本次文献判断已全部完成，没有方向进入提案。判断理由已保存在各构想正文中。</p>' if judges and all(checks.get(str(i),{}).get('decision')=='drop' for i in judges) else '<p>当前尚无已完成提案。</p>') if not proposals else ''}</section>
<footer><p>ResearchForge · {'完整报告' if preserve_original else '中文报告'}可独立离线打开，无需运行项目服务器。构想与提案正文默认展开，文献摘要按需展开；打印时自动展开完整内容，可另存 PDF。</p><p>原始构想 {n}　·　检索文献 {len(papers)}　·　最终提案 {m}　·　研究假说 {h}　·　关键文献记录 {r}</p></footer></main></div>
<script id="report-counts" type="application/json">{json.dumps(counts)}</script><script>{js}</script></body></html>'''
