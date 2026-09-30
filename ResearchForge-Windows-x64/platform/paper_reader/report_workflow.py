"""Conservative presentation cleanup and report completeness checks."""
import re

# Only remove decorative markers, never equations, variable names, APA text or DOI.
EMOJI = r'[\U0001F300-\U0001FAFF\u2705\u274C\u274E\u2757\u2753\u2728\u2B50\u26A0\u2611](?:[\uFE0F\u200D]*[\U0001F300-\U0001FAFF\u2705\u274C\u274E\u2757\u2753\u2728\u2B50\u26A0\u2611])*[\uFE0F]*'


def clean_presentation(text):
    text = re.sub(r'[（(]\s*(?:'+EMOJI+r'\s*)+[)）]', '', text)
    text = re.sub(r'(?m)^([ \t]*(?:#{1,6}[ \t]+|[-*+][ \t]+|\d+\.[ \t]+)?)(?:'+EMOJI+r'\s*)+', r'\1', text)
    text = re.sub(r'(?m)(?<=\S)[ \t]+(?:'+EMOJI+r'[ \t]*)+$', '', text)
    return text.strip()


def checks(text, expanded=True):
    result = []
    if re.findall(r'^#\s+(.+)$', text, re.M) != ['一、论文精读', '二、与我的研究对比']:
        result.append('报告的两个一级标题与规范不一致，请核对。')
    required = [('摘要', r'^##\s+.*摘要'), ('研究贡献', r'^##\s+.*贡献')]
    if expanded:
        numbers = [int(n) for n in re.findall(r'^##[ \t]+(\d+)\.[ \t]+', text, re.M)]
        if numbers != list(range(1, 16)):
            result.append('15 项精读分析的编号不完整或顺序不一致，请核对。')
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
