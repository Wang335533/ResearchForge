# -*- coding: utf-8 -*-
"""
ResearchForge 四阶段提示词加载器
阶段1: 初步想法生成（仅凭大模型自身能力）
阶段2: 评审排名（七维度打分，融入《理论IDEA GEN 提示词》评审体系）
阶段3: 向量相似度文献检索（无生成提示词，本地 BM25/embedding）
阶段4: 基于文献知识库的 IDEA 优化（假说 + 理论贡献点名作者/年份/期刊）

提示词本体存放在独立文件夹 `prompts/*.json`，可手动编辑，也可经前端（登录后）在线编辑。
本模块仅负责加载 JSON 并提供 build_stage* 装配函数；每次调用都会重新读取，改动即时生效。
"""
import os
import json
import time
import re
import tempfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROMPTS_DIR = os.path.join(BASE_DIR, 'prompts')

_KEYS = ('stage1_system', 'stage1_user', 'stage2_system', 'stage2_user',
         'stage4_system', 'stage4_user')


class PromptError(RuntimeError):
    code = 'prompt_unavailable'


def load_prompt(key: str) -> str:
    """Only the current prompt file is authoritative; never fall back to old text."""
    if key not in _KEYS:
        raise PromptError(f'未知提示词：{key}')
    path = os.path.join(PROMPTS_DIR, key + '.json')
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise PromptError(f'当前提示词 {key}.json 无法读取或 JSON 已损坏；已停止，不回退旧版。') from exc
    content = data.get('content') if isinstance(data, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise PromptError(f'当前提示词 {key}.json 缺少非空文本 content；已停止，不回退旧版。')
    return content


def load_all() -> dict:
    """Read the six current files; any invalid file is an explicit error."""
    return {key: load_prompt(key) for key in _KEYS}


def replace_file(source, target, attempts=40):
    """os.replace that waits out a brief concurrent reader, which Windows treats as a lock."""
    for attempt in range(attempts):
        try:
            return os.replace(source, target)
        except PermissionError:
            if os.name != 'nt' or attempt == attempts - 1:
                raise
            time.sleep(0.05)


def save_prompt(key: str, content: str) -> bool:
    """Publish a complete prompt atomically; a failed write preserves the old file."""
    if key not in _KEYS or not isinstance(content, str) or not content.strip():
        return False
    path = os.path.join(PROMPTS_DIR, key + '.json')
    meta = {'name': key, 'description': _DESC.get(key, ''), 'content': content}
    try:
        # Unique siblings also keep concurrent saves from sharing a partial file.
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=PROMPTS_DIR,
                                         prefix='.' + key + '-', suffix='.pending', delete=False) as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
            temporary = f.name
        replace_file(temporary, path)
        return True
    except Exception:
        # Keep any pending content recoverable instead of permanently deleting it.
        return False


_DESC = {
    'stage1_system': '第1阶段 构想生成｜系统角色设定',
    'stage1_user': '第1阶段 构想生成｜用户任务与输出格式',
    'stage2_system': '第2阶段 评审排名｜系统角色设定',
    'stage2_user': '第2阶段 评审排名｜用户任务、七维评审与输出格式',
    'stage4_system': '第4阶段 文献判断与优化｜系统角色设定',
    'stage4_user': '第4阶段 文献判断与优化｜用户任务、去留判断、假说与引用',
}


def render_user(key: str, **values) -> str:
    try:
        return load_prompt(key).format(**values)
    except (KeyError, ValueError, IndexError, AttributeError) as exc:
        raise PromptError(f'当前提示词 {key}.json 的模板格式有误；已停止，不回退旧版。') from exc


def abstract_first_sentence(abstract: str) -> str:
    """Keep one sentence for S1 deduplication, without splitting common abbreviations."""
    text = abstract.strip()
    abbreviations = {'e.g', 'i.e', 'u.s', 'u.k', 'dr', 'mr', 'mrs', 'ms', 'prof',
                     'vs', 'fig', 'no', 'al', 'inc', 'ltd', 'co', 'jr', 'sr'}
    for ending in re.finditer(r'''[。！？]+[”’」』）)\]]*|[.!?]+["'”’）)\]]*(?=\s|$)''', text):
        if ending.group() == '.':
            word = re.search(r'([A-Za-z][A-Za-z.]*)$', text[:ending.start()])
            if word and (word.group().lower() in abbreviations or len(word.group()) == 1):
                # An abbreviation can also end a sentence ("in the U.S. We ...").
                if not re.match(r'\s+(?:We|Our|This|These|The|Using|It|In|A|An|They|Their|Results|Evidence|Findings|By|Such)\b', text[ending.end():]):
                    continue
        return text[:ending.end()]
    return text


def build_stage1(topic: str, n: int, avoid_ideas=None) -> tuple:
    avoid_ideas = avoid_ideas or []
    avoid_block = ''
    if avoid_ideas:
        shown = '\n\n'.join(f"--- 已有构想 {i} ---\n题目：{idea['title']}\n摘要首句：{abstract_first_sentence(idea['abstract'])}"
                            for i, idea in enumerate(avoid_ideas, 1))
        avoid_block = f"\n【避免重复】以下是全部已有构想的题目和摘要第一句；不得重复或高度相似：\n{shown}\n"
    system = load_prompt('stage1_system')
    user = render_user('stage1_user', topic=topic, n=n, avoid_block=avoid_block)
    return system, user


def build_stage2(ideas, selection_count=5) -> tuple:
    lines = []
    for it in ideas:
        lines.append(f"--- 构想 #{it.get('id')} ---\n题目：{it.get('title','')}\n摘要：{it.get('abstract','')}")
    system = load_prompt('stage2_system')
    user = render_user('stage2_user', n=len(ideas), selection_count=selection_count, ideas_block='\n\n'.join(lines))
    return system, user


def _paper_line(p, i):
    label = p.get('reference_label') or f'P{i}'
    return (f"[{label}] 引用 source_id 使用：{label}；原始文献编号：{p.get('source_id') or p.get('ut', '')}\n"
            f"题名：{p.get('title', '')}\n作者：{p.get('authors', '')}\n"
            f"期刊：{p.get('journal', '')}；年份：{p.get('year', '')}；DOI：{p.get('doi', '')}\n"
            f"文献类型：{p.get('document_types', '')}；语言：{p.get('language', '')}；证据范围：摘要\n"
            f"完整摘要：{p.get('abstract', '')}")


def build_stage4(idea: dict, papers: list, judge: dict | None) -> tuple:
    if judge is None:
        judge = {'total': '-', 'hook': '（未提供）', 'reason': '（未提供）'}
    lines = [_paper_line(p, i + 1) for i, p in enumerate(papers)]
    if not lines:
        lines = ['（未检索到相关文献）']
    else:
        lines.insert(0, '【中英文文献共同评估】以下候选来自同一文献库，可能包含中文与英文文献。'
                     '检索按中英文等额提供候选；依据问题、机制和摘要证据评估，不按语言预设价值，最终引用无需凑齐语言比例。'
                     'Journal Record 表示中文源文件未提供文献类型，不能据此断言是研究论文。'
                     '不得将已有中文研究误判为无人研究；引用保留原始作者、题名、期刊和年份，分析用中文。')
    system = load_prompt('stage4_system')
    user = render_user('stage4_user',
        title=idea.get('title', ''),
        abstract=idea.get('abstract', ''),
        total=judge.get('total', '-'),
        hook=judge.get('hook', ''),
        reason=judge.get('reason', ''),
        papers_block='\n'.join(lines),
    )
    return system, user


# Prompt files are read on demand, without a cached fallback.
