"""Conservative presentation cleanup and report completeness checks."""
import re

# Only remove decorative markers, never equations, variable names, APA text or DOI.
EMOJI = r'[\U0001F300-\U0001FAFF\u2705\u274C\u274E\u2757\u2753\u2728\u2B50\u26A0\u2611](?:[\uFE0F\u200D]*[\U0001F300-\U0001FAFF\u2705\u274C\u274E\u2757\u2753\u2728\u2B50\u26A0\u2611])*[\uFE0F]*'

EVIDENCE_GUIDANCE = '''【分段笔记的取舍】
这是供最终报告使用的证据底稿，不是另一份精读报告。以紧凑的事实条目记录，只列本片段实际出现的项目；未覆盖的项目合并为一句说明，不逐项重复“本片段未取得”。
不抄写邮寄地址、邮箱、常规作者单位列表、致谢名单、基金编号、版权声明、免责声明或分类代码，除非它们直接关系到研究设计、利益冲突或数据权限。保留论文题名、作者、年份、出版来源与 DOI 等必要书目信息。
同一个事实、推导或数字只完整记录一次，其他相关项目指向该条；文献综述和参考文献表重点保留理论、贡献、设计与后续阅读所需的文献，不复制整张无关书目表。已有重复结果可合并来源，不能合并不同口径、样本或规格。
保留重建研究所必需的公式、效应量、精度、样本、假设、竞争解释、反面证据与原文定位，不以压缩为由省略这些信息；不扩写评论、导读或生活故事。'''


def clean_presentation(text):
    text = re.sub(r'[（(]\s*(?:'+EMOJI+r'\s*)+[)）]', '', text)
    text = re.sub(r'(?m)^([ \t]*(?:#{1,6}[ \t]+|[-*+][ \t]+|\d+\.[ \t]+)?)(?:'+EMOJI+r'\s*)+', r'\1', text)
    text = re.sub(r'(?m)(?<=\S)[ \t]+(?:'+EMOJI+r'[ \t]*)+$', '', text)
    return text.strip()


_NUMBER = r'\d+(?:\s*(?:—|–|~|～|-|至)\s*\d+)?'
_LETTER_NUMBER = r'(?<![A-Za-z0-9_])L\s*\d+(?:\s*[—–~～-]\s*L?\s*\d+)?'
_SOURCE_LABEL = r'(?:(?:原文|正文|提取文本)\s*(?:PDF\s*)?|PDF\s*)'
_SOURCE_POSITION = re.compile(
    rf'(?:{_SOURCE_LABEL})?\s*(?:第\s*)?{_NUMBER}(?:\s*[、，,]\s*(?:第\s*)?{_NUMBER})*\s*(?:行|页|段)'
    rf'|(?:{_SOURCE_LABEL})?\s*(?:第\s*)?{_LETTER_NUMBER}(?:\s*[、，,]\s*{_LETTER_NUMBER})*\s*(?:行)?',
    re.I,
)
_REF_NUMBER = r'(?:[A-Za-z]{0,2}\s*\d+(?:\.\d+)*|[IVX]+)'
_REF_RANGE = rf'{_REF_NUMBER}(?:\s*[—–~～至-]\s*{_REF_NUMBER})?'
_OTHER_POSITION = re.compile(
    rf'(?:第\s*(?:{_NUMBER}(?:\.\d+)?|[一二三四五六七八九十]+)\s*节|'
    rf'(?:附表|表|图|附录|公式|方程|式|脚注|尾注)\s*{_REF_RANGE}'
    rf'(?:\s*[、，,]\s*{_REF_RANGE})*(?:\s*面板\s*[A-Z])?)', re.I)
_LOCATION_LABEL = re.compile(r'书目信息(?:核对位置)?|完整规格|核对位置|对应原文|对应|出处|来源|脚注|Abstract|PDF', re.I)
_LOCATION_PARENS = re.compile(r'[（(][^()（）\n]{1,180}[）)]')
_AUDIT_SENTENCE = re.compile(
    r'(?m)(?:^|(?<=[。！？]))[ \t]*'
    r'(?:书目信息核对位置|书目信息见|作者顺序(?:与|、)题名|上述来源与用途|上述用途分别见|本文对其使用|证据定位|核对依据|'
    r'对应原文|相关界定见|以上依据见|上述依据见|上述文献定位见|证据参见|材料出处见|见提取文本)'
    r'[^。！？\n]{0,240}[。！？]'
)
_BARE_POSITION_SENTENCE = re.compile(rf'(?m)(?:^|(?<=[。！？]))[ \t]*(?:{_SOURCE_LABEL})?\s*(?:第\s*\d|L\s*\d)[^。！？\n]{{0,140}}[。！？]', re.I)
_INLINE_POSITION = re.compile(rf'{_SOURCE_LABEL}\s*(?:(?:第\s*)?{_NUMBER}(?:\s*[、，,]\s*(?:第\s*)?{_NUMBER})*\s*(?:行|页|段)|(?:第\s*)?{_LETTER_NUMBER}(?:\s*[、，,]\s*{_LETTER_NUMBER})*\s*(?:行)?)', re.I)
_SQUARE_POSITIONS = re.compile(rf'[ \t]*\[[ \t]*{_LETTER_NUMBER}[ \t]*\](?:[ \t]*,[ \t]*\[[ \t]*{_LETTER_NUMBER}[ \t]*\])*', re.I)
_LOOSE_LINES = re.compile(rf'(?P<connector>[，,、；;和与])?[ \t]*(?<![A-Za-z0-9_])L[ \t]*\d{{2,}}(?:[ \t]*[—–~～-][ \t]*L?[ \t]*\d+)?(?:[ \t]*[、，,][ \t]*{_LETTER_NUMBER})*',re.I)


def _only_source_position(value):
    if not _SOURCE_POSITION.search(value):
        return False
    remainder = _SOURCE_POSITION.sub('', value)
    remainder = _OTHER_POSITION.sub('', remainder)
    remainder = _LOCATION_LABEL.sub('', remainder)
    return not re.sub(r'[\s，,、；;：:。！？·见参照依据和与及原文正文如（）()]', '', remainder)


def _clean_location_parenthesis(match):
    value = match.group()
    if _only_source_position(value):
        return ''
    inside = value[1:-1]
    for separator in re.finditer(r'[，,；;]\s*(?:见|参见)?', inside):
        if _only_source_position(inside[separator.end():]):
            return value[0] + inside[:separator.start()].rstrip() + value[-1]
        if _only_source_position(inside[:separator.start()]):
            return value[0] + inside[separator.end():].strip() + value[-1]
    return value


def presentation_report(text):
    """Hide audit locators in the reading copy while retaining the saved report."""
    text = _LOCATION_PARENS.sub(_clean_location_parenthesis, text)
    text = _AUDIT_SENTENCE.sub(lambda m: '' if _SOURCE_POSITION.search(m.group()) else m.group(), text)
    text = _BARE_POSITION_SENTENCE.sub(lambda m: '' if _only_source_position(m.group()) else m.group(), text)
    text = _INLINE_POSITION.sub('', text)
    text = _SQUARE_POSITIONS.sub('', text)
    text = _LOOSE_LINES.sub('', text)
    text = re.sub(r'([（(])\s*[，,、；;]\s*', r'\1', text)
    text = re.sub(r'[（(]\s*[，,、；;。]\s*[）)]', '', text)
    text = re.sub(r'[，,]\s*(?=[。！？；;\n])', '', text)
    text = re.sub(r'[和与]\s*(?=[。！？；;\n])', '', text)
    text = re.sub(r'(?m)[ \t]+([，。；：！？])', r'\1', text)
    text = re.sub(r'(?m)[ \t]+$', '', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def checks(text, expanded=True, template=None):
    result = []
    if re.findall(r'^#\s+(.+)$', text, re.M) != ['一、论文精读', '二、与我的研究对比']:
        result.append('报告的两个一级标题与规范不一致，请核对。')
    required = [('摘要', r'^##\s+.*摘要'), ('研究贡献', r'^##\s+.*贡献')]
    if expanded:
        numbers = [int(n) for n in re.findall(r'^##[ \t]+(\d+)\.[ \t]+', text, re.M)]
        expected = ([int(n) for n in re.findall(r'^##[ \t]+(\d+)\.[ \t]+', template, re.M)]
                    if template else list(range(1, 16)))
        if numbers != expected:
            result.append(f'{len(expected)} 项精读分析的编号不完整或顺序不一致，请核对。')
        required += [('APA 参考文献', r'^##\s+.*APA.*参考文献'), ('核心理论', r'^##\s+.*核心理论'),
                     ('研究假说', r'^##\s+.*假说'), ('数据与可获得性', r'^##\s+.*数据.*可获得性'),
                     ('局限性', r'^##\s+.*局限'), ('未来研究', r'^##\s+.*未来研究'),
                     ('生活故事', r'^##\s+.*生活故事')]
    missing = [label for label, pattern in required if not re.search(pattern, text, re.M)]
    if missing:
        result.append('以下内容未单列，请核对完整性：'+'、'.join(missing)+'。')
    if re.search(EMOJI, text):
        result.append('报告仍包含正文内的表情符号，请在导出前核对；未自动改动句内证据内容。')
    return result


def outline(template):
    """Use the user's actual editable outline, including any added sections."""
    headings = re.findall(r'^#{1,2}[ \t]+[^\n]+', template, re.M)
    if [h for h in headings if h.startswith('# ')] != ['# 一、论文精读', '# 二、与我的研究对比']:
        return []
    return headings


def heading_key(heading):
    heading = heading.rstrip(' #\t')
    number = re.match(r'^##\s+(\d+)[.．、]\s*', heading)
    if number:
        return 'section', int(number[1])
    if heading.startswith('## ') and 'APA' in heading:
        return 'apa',
    return heading,


def completed_prefix(text, expected, finished):
    """Only accept consecutive sections. A length-limited last section is retried whole.

    This avoids stitching partial sentences or assuming a visible heading means the
    section is finished. Never skip, duplicate or silently drop an interior section.
    """
    text = clean_presentation(text)
    matches = list(re.finditer(r'^#{1,2}[ \t]+[^\n]+', text, re.M))
    if not matches:
        return '', 0
    valid = 0
    for i, match in enumerate(matches):
        if i >= len(expected) or heading_key(match[0]) != heading_key(expected[i]):
            # A heading itself can be cut off by the token limit.
            if not finished and i == len(matches) - 1 and i < len(expected) and expected[i].startswith(match[0]):
                break
            raise ValueError('返回的章节顺序与提示词不一致；原始草稿已保留，请核对提示词后继续。')
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[match.end():end].strip()
        if (match[0].startswith('## ') or match[0] == '# 二、与我的研究对比') and not body:
            if i != len(matches) - 1:
                raise ValueError('返回的小节只有标题而无正文，尚未标记为完成。')
            break
        valid += 1
    if not finished and valid == len(matches):
        valid -= 1
    if valid and expected[valid - 1] == '# 一、论文精读':
        valid -= 1  # Keep the container heading together with its first subsection.
    if not valid:
        return '', 0
    sections = []
    for i in range(valid):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append(expected[i] + text[matches[i].end():end].rstrip())
    return '\n\n'.join(sections), valid


def continuation_scope(expected, previous):
    return ('\n\n【本次分段交付范围】\n这是程序安排的继续生成步骤。覆盖模板中“一次输出整份”的要求，'
            '但不改变分析深度、证据标准和最终报告结构。只输出下面列出的标题及正文，标题依次使用原样文字；'
            '写完最后一节后结束。不要重写已完成章节，不加开场白、总结、上半部、下半部或待续。'
            '若前次在小节中间截断，本次从该小节标题重新完整写起，程序会替换那一小节的未完成草稿。\n'
            + '\n'.join(expected) + '\n\n<已完成正文末尾，仅供衔接，不是论文原文>\n'
            + previous[-1400:] + '\n</已完成正文末尾，仅供衔接，不是论文原文>')
