"""Resolve bibliographic fields against this idea's S3 records, without model calls."""

import re
import unicodedata
from copy import deepcopy
from urllib.parse import unquote, urlsplit


_FIELDS = ('title', 'authors', 'year', 'journal', 'doi')
_IDENTIFIERS = ('source_id', 'ut', 'reference_label')
_FIELD_NAMES = {'title': '题名', 'authors': '作者', 'year': '年份',
                'journal': '期刊', 'doi': 'DOI'}


def is_insufficient_evidence(reason):
    text = str(reason or '')
    return '证据不足' in text or 'insufficient evidence' in text.casefold()


def _text(value):
    return value.strip() if isinstance(value, str) else ''


def _words(value):
    """Ignore typography only; never use fuzzy similarity to identify a paper."""
    value = unicodedata.normalize('NFKC', _text(value)).casefold()
    return ' '.join(re.findall(r'[^\W_]+', value))


def _doi(value):
    value = _text(value)
    if value.lower().startswith(('https://doi.org/', 'http://doi.org/',
                                 'https://dx.doi.org/', 'http://dx.doi.org/')):
        value = unquote(urlsplit(value).path.lstrip('/'))
    value = re.sub(r'^doi\s*:\s*', '', value, flags=re.I).strip().casefold()
    return value if re.fullmatch(r'10\.\d{4,9}/\S+', value) else ''


def _label(value):
    return _text(value).strip('[]').strip().casefold()


def paper_lookup(papers):
    if not isinstance(papers, list):
        raise ValueError('文献输入必须是列表')
    lookup = {}
    for paper in papers:
        if not isinstance(paper, dict):
            raise ValueError('文献输入包含无效记录')
        source_id = paper.get('source_id') or paper.get('ut')
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError('文献输入缺少有效的 source_id')
        source_id = source_id.strip()
        if source_id in lookup:
            raise ValueError(f'文献输入的 source_id 重复：{source_id}')
        if paper.get('ut') and paper['ut'] != source_id:
            raise ValueError(f'文献输入的 source_id 与原始文献编号不一致：{source_id}')
        lookup[source_id] = paper
    return lookup


def short_source_lookup(papers):
    """Short IDs are scoped to this retrieval; never infer a source ID from digits."""
    lookup = paper_lookup(papers)
    labels = {}
    for index, (sid, paper) in enumerate(lookup.items(), 1):
        label = paper.get('reference_label') or f'P{index}'
        if not isinstance(label, str) or not re.fullmatch(r'P[1-9][0-9]*', label):
            raise ValueError('文献短编号必须为 P1、P2 等格式')
        if label in labels:
            raise ValueError(f'文献短编号重复：{label}')
        labels[label] = sid
    return labels


def resolve_strict_reference(reference, lookup, labels):
    """New outputs must explicitly choose a current P ID or an external null."""
    if not isinstance(reference, dict) or 'source_id' not in reference:
        raise ValueError('引用必须显式提供 source_id：本次 P 编号或 JSON null')
    sid = reference['source_id']
    if sid is None:
        for key in ('title', 'authors', 'journal'):
            if not _text(reference.get(key)):
                raise ValueError(f'外部引用缺少非空 {key}')
        if type(reference.get('year')) is not int or reference['year'] <= 0:
            raise ValueError('外部引用 year 必须是正整数')
        reference.update(reference_original={k: deepcopy(reference[k]) for k in (*_FIELDS, *_IDENTIFIERS) if k in reference},
                         reference_status='external_unverified', reference_match_method='',
                         reference_corrected_fields=[], reference_missing_fields=[],
                         reference_note='模型声明为检索记录外引用，尚未核对')
        return
    if not isinstance(sid, str) or sid not in labels:
        raise ValueError(f'未知文献短编号 {sid!r}；请使用本次 P 编号，外部文献必须显式使用 JSON null')
    original = {k: deepcopy(reference[k]) for k in (*_FIELDS, *_IDENTIFIERS) if k in reference}
    # The explicit P ID selects the original record; all bibliography is supplied
    # by that record, not by approximate title matching or the model's guesses.
    canonical = {'source_id': labels[sid]}
    resolve_reference(canonical, lookup)
    reference.update(canonical)
    reference['reference_original'] = original
    reference['reference_match_method'] = 'short_source_id'
    reference['reference_corrected_fields'] = [k for k in _FIELDS if original.get(k) != reference.get(k)]


def _match(reference, lookup):
    records = list(lookup.items())
    hints = []
    # An explicit but unknown identifier must not fall back to a similar title.
    for key in _IDENTIFIERS:
        if reference.get(key) is None:
            continue
        value = _text(reference[key])
        if key == 'reference_label':
            hits = {sid for i, (sid, p) in enumerate(records, 1)
                    if value and _label(value) == _label(p.get('reference_label') or f'P{i}')}
        else:
            hits = {sid for sid, _ in records if value and sid.casefold() == value.casefold()}
        if not hits:
            return None, 'unmatched_local_record', '', '所提供的来源编号未匹配本次检索文献'
        hints.append((key, hits))
    if reference.get('doi') not in (None, ''):
        value = _doi(reference['doi'])
        hits = {sid for sid, p in records if value and _doi(p.get('doi')) == value}
        if not hits and not hints:
            return None, 'unmatched_local_record', '', '所提供的 DOI 未匹配本次检索文献'
        if hits:
            hints.append(('doi', hits))

    title = _words(reference.get('title'))
    title_hits = {sid for sid, p in records if title and _words(p.get('title')) == title}
    if hints:
        candidates = set.intersection(*(hits for _, hits in hints))
        if title_hits:
            candidates &= title_hits
        method = '+'.join(key for key, _ in hints)
        if not candidates:
            return None, 'conflicting_local_record', '', '来源编号、DOI或题名指向不同记录，未自动替换'
    elif title:
        candidates, method = title_hits, 'title'
    else:
        # Contributions in the existing schema have no paper title. Only an
        # exact, unique author/year/journal tuple can identify them without IDs.
        authors, journal = _words(reference.get('authors')), _words(reference.get('journal'))
        year = reference.get('year')
        candidates = {sid for sid, p in records
                      if authors and journal and type(year) is int and year > 0
                      and _words(p.get('authors')) == authors and p.get('year') == year
                      and _words(p.get('journal')) == journal}
        method = 'authors_year_journal'
    if not candidates:
        return None, 'unmatched_local_record', '', '书目信息未匹配本次检索文献；不代表文献不存在'
    if len(candidates) != 1:
        return None, 'ambiguous_local_record', '', '存在多个对应记录，未自动选择或替换'
    return next(iter(candidates)), 'matched_local_record', method, ''


def resolve_reference(reference, lookup, *, require_title=True):
    if not isinstance(reference, dict):
        raise ValueError('引用必须是有效对象')
    # Never trust a verification status supplied by the model. Preserve its
    # original bibliographic fields separately when filling from source data.
    original = {key: deepcopy(reference[key]) for key in (*_FIELDS, *_IDENTIFIERS)
                if key in reference}
    source_id, status, method, note = _match(reference, lookup)
    if source_id is None:
        required = ('title', 'authors', 'journal') if require_title else ('authors', 'journal')
        for key in required:
            if not _text(reference.get(key)):
                value = reference.get(key)
                problem = '缺失或为空' if value is None or value == '' else f'应为非空文本，实际类型为 {type(value).__name__}'
                identifier = repr(reference.get('source_id'))[:120]
                raise ValueError(f'未匹配引用的 {key} {problem}（source_id={identifier}；{note}）；未作为成功结果保存')
        if type(reference.get('year')) is not int or reference['year'] <= 0:
            raise ValueError('未匹配引用缺少有效年份；未作为成功结果保存')
        reference['reference_original'] = original
        reference['reference_status'] = ('external_unverified' if status == 'unmatched_local_record'
                                         and 'source_id' in reference and reference['source_id'] is None
                                         else status)
        reference['reference_note'] = note
        reference['reference_match_method'] = ''
        reference['reference_corrected_fields'] = []
        reference['reference_missing_fields'] = []
        return

    paper = lookup[source_id]
    # Missing source metadata stays missing. Never substitute model-invented fields.
    corrected, missing = [], []
    for key in _FIELDS:
        value = paper.get(key, None if key == 'year' else '')
        if original.get(key) != value:
            corrected.append(key)
        reference[key] = value
        if value in (None, ''):
            missing.append(key)
    reference['source_id'] = source_id
    reference['reference_label'] = paper.get('reference_label') or f'P{list(lookup).index(source_id) + 1}'
    reference['reference_original'] = original
    reference['reference_status'] = status
    reference['reference_match_method'] = method
    reference['reference_corrected_fields'] = corrected
    reference['reference_missing_fields'] = missing
    reference['reference_note'] = ('原记录缺少' + '、'.join(_FIELD_NAMES[k] for k in missing)
                                   + '，未猜测补全') if missing else ''


def resolve_proposal_references(proposal, papers, *, strict=False):
    lookup = paper_lookup(papers)
    labels = short_source_lookup(papers) if strict else {}
    def resolve(ref, require_title=True):
        if strict:
            resolve_strict_reference(ref, lookup, labels)
        else:
            resolve_reference(ref, lookup, require_title=require_title)
    for index, hypothesis in enumerate(proposal['hypotheses'], 1):
        try:
            resolve(hypothesis['contribution'], require_title=False)
        except ValueError as exc:
            label = hypothesis.get('id') or f'第 {index} 条'
            raise ValueError(f'假说 {label} 的理论贡献引用：{exc}') from exc
    for index, reference in enumerate(proposal['key_references'], 1):
        try:
            resolve(reference)
        except ValueError as exc:
            raise ValueError(f'参考文献第 {index} 条：{exc}') from exc
