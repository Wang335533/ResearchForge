"""Complete JSON only, with narrowly scoped and audited punctuation recovery.

No model calls, text truncation, missing values/brackets, guessed IDs or key merging.
"""
import json
import re
import copy
from contextvars import ContextVar

FIELD_COMPLETION_ALLOWED = ContextVar('field_completion_allowed', default=lambda: True)


JSON_INSTRUCTION = '''【机器读取格式约束】只返回一个完整 JSON 对象，不输出 Markdown 围栏或解释。
所有对象键及字符串使用英文双引号作为 JSON 定界符。正文引用术语请使用中文引号“”，不要插入未转义的英文双引号。
source_id 和 source_ids 中的 P 编号始终写成带引号的字符串，如 "P1"；不得漏写任何一侧引号。
字符串内反斜杠、换行和双引号必须使用合法 JSON 转义，不写尾逗号、重复键、NaN、Infinity、注释或省略号占位。
输出前核对本批数量、必填字段、评分之和、编号及所有括号闭合；不要改写研究任务或删去所需内容。'''

PROSE_FIELDS = {'title','title_zh','title_en','abstract','abstract_zh','abstract_en',
                'hook','reason','hypothesis','target_theory','how','theoretical_foundation',
                'improvement_notes','text','research_title','description','summary',
                '题目','研究题目','摘要','研究摘要'}


def document_text(text):
    if not isinstance(text, str):
        raise ValueError('模型正文不是文本')
    text = text.lstrip('\ufeff').strip()
    # Only remove a leading reasoning wrapper, never markers inside saved prose.
    match = re.match(r'^<think>[\s\S]*?</think>\s*(?=[{\[`])', text)
    if match:
        text = text[match.end():]
    fenced = re.fullmatch(r'```(?:json)?\s*([\s\S]*?)\s*```', text, re.I)
    return fenced.group(1) if fenced else text


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'JSON 包含重复字段 {key}；未覆盖或合并')
        result[key] = value
    return result


def _constant(value):
    raise ValueError(f'JSON 包含非标准数值 {value}')


def strict_loads(text):
    return json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)


class PunctuationParser:
    def __init__(self, text, source_labels=()):
        self.text, self.pos, self.edits = text, 0, []
        self.labels = set(source_labels)

    def fail(self):
        raise ValueError(f'JSON 在第 {self.pos + 1} 个字符附近仍不完整或存在歧义')

    def ws(self):
        while self.pos < len(self.text) and self.text[self.pos] in ' \t\r\n':
            self.pos += 1

    def edit(self, start, end, replacement, kind, path):
        if len(self.edits) >= 128:
            self.fail()
        self.edits.append(dict(start=start, end=end, replacement=replacement, kind=kind,
                               path='.'.join(map(str, path))))

    def string(self, path, key=False):
        start = self.pos
        self.pos += 1
        field = next((p for p in reversed(path) if isinstance(p, str)), '')
        prose = not key and field in PROSE_FIELDS
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if ord(ch) < 32:
                self.fail()  # A missing closing quote must not absorb following lines/fields.
            if ch == '\\':
                if self.pos + 1 >= len(self.text):
                    self.fail()
                escaped = self.text[self.pos + 1]
                if escaped in '"\\/bfnrt':
                    self.pos += 2
                    continue
                if escaped == 'u' and re.fullmatch('[0-9a-fA-F]{4}', self.text[self.pos+2:self.pos+6]):
                    self.pos += 6
                    continue
                # Apostrophes need no JSON escape; preserve the literal slash itself.
                if prose and escaped == "'":
                    self.edit(self.pos,self.pos,'\\','escape_literal_backslash',path)
                    self.pos += 2
                    continue
                self.fail()
            if ch == '"':
                after = self.pos + 1
                while after < len(self.text) and self.text[after] in ' \t\r\n':
                    after += 1
                following = self.text[after:after+1]
                valid_end = following == ':' if key else not following or following in ',]}'
                if valid_end:
                    end = self.pos + 1
                    self.pos = end
                    return self.text[start:end]
                # Only quotation marks inside known prose fields. Never join strings,
                # swallow adjacent JSON keys, repair IDs or modify numeric tokens.
                if prose and following not in ':{}[]"' and following:
                    self.edit(self.pos,self.pos,'\\','escape_prose_quote',path)
                    self.pos += 1
                    continue
                self.fail()
            self.pos += 1
        self.fail()

    def value(self, path=(), depth=0):
        if depth > 80:
            self.fail()
        self.ws()
        ch = self.text[self.pos:self.pos+1]
        if ch == '{':
            self.pos += 1
            self.ws()
            if self.text[self.pos:self.pos+1] == '}':
                self.pos += 1
                return
            while True:
                self.ws()
                if self.text[self.pos:self.pos+1] != '"':
                    self.fail()
                key = strict_loads(self.string(path, key=True))
                self.ws()
                if self.text[self.pos:self.pos+1] != ':':
                    self.fail()
                self.pos += 1
                self.value((*path,key), depth+1)
                if self.end_or_comma('}',path):
                    return
        elif ch == '[':
            self.pos += 1
            self.ws()
            if self.text[self.pos:self.pos+1] == ']':
                self.pos += 1
                return
            index = 0
            while True:
                self.value((*path,index),depth+1)
                index += 1
                if self.end_or_comma(']',path):
                    return
        elif ch == '"':
            self.string(path)
        else:
            field = next((p for p in reversed(path) if isinstance(p,str)), '')
            if field in ('source_ids','source_id'):
                missing = re.match(r'(P[1-9][0-9]*)"(?=\s*[,}\]])',self.text[self.pos:])
                if missing and missing.group(1) in self.labels:
                    self.edit(self.pos,self.pos,'"','open_known_source_quote',path)
                    self.pos += len(missing.group())
                    return
            match = re.match(r'(?:true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)(?=\s*[,}\]]|\s*$)',self.text[self.pos:])
            if not match:
                self.fail()
            self.pos += len(match.group())

    def end_or_comma(self, closing, path):
        self.ws()
        if self.text[self.pos:self.pos+1] == closing:
            self.pos += 1
            return True
        if self.text[self.pos:self.pos+1] != ',':
            self.fail()
        comma = self.pos
        self.pos += 1
        self.ws()
        if self.text[self.pos:self.pos+1] == closing:
            self.edit(comma,comma+1,'','remove_trailing_comma',path)
            self.pos += 1
            return True
        return False

    def parse(self):
        self.value()
        self.ws()
        if self.pos != len(self.text) or not self.edits:
            self.fail()
        normalized = self.text
        for edit in reversed(self.edits):
            normalized = normalized[:edit['start']] + edit['replacement'] + normalized[edit['end']:]
        return strict_loads(normalized), normalized, self.edits


def parse_complete(text, source_labels=()):
    cleaned = document_text(text)
    try:
        return strict_loads(cleaned), cleaned, []
    except json.JSONDecodeError as original:
        try:
            return PunctuationParser(cleaned,source_labels).parse()
        except ValueError as exc:
            raise ValueError(f'模型 JSON 格式错误（第 {original.lineno} 行、第 {original.colno} 列：{original.msg}）；有限标点纠正未通过，已停止，未截断或自动重试。') from exc


def missing_text_fields(data, stage):
    """Whitelist only missing prose; never repair scores, IDs, decisions or structure."""
    fields = []
    def inspect(obj, names, path):
        if not isinstance(obj,dict):
            return
        for name in names:
            if name not in obj or obj[name] is None or obj[name] == '' or (isinstance(obj[name],str) and not obj[name].strip()):
                fields.append({'key':f'F{len(fields)+1}','path':[*path,name]})
    if stage in (1,2):
        items, prefix = data, []
        if isinstance(data,dict):
            candidates = [k for k,v in data.items() if isinstance(v,list) and
                          (k in ('ideas','data','research_ideas') if stage==1 else re.fullmatch(r'top[0-9]+',k))]
            if len(candidates)!=1:
                return []
            prefix=[candidates[0]];items=data[candidates[0]]
        if isinstance(items,list):
            for i,item in enumerate(items):
                if stage==2:
                    inspect(item,('hook','reason'),[*prefix,i])
                elif isinstance(item,dict):
                    for canonical,aliases in [('title',('title','title_zh','title_en','research_title','题目','研究题目','name')),
                                               ('abstract',('abstract','abstract_zh','abstract_en','摘要','研究摘要','summary','description'))]:
                        if not any(isinstance(item.get(k),str) and item[k].strip() for k in aliases):
                            inspect(item,(canonical,),[*prefix,i])
    elif stage==4 and isinstance(data,dict):
        check=data.get('literature_check')
        inspect(check,('reason',),['literature_check'])
        if isinstance(check,dict) and check.get('decision')=='pass':
            inspect(data,('title_zh','abstract_zh','theoretical_foundation','improvement_notes'),[])
            if isinstance(data.get('hypotheses'),list):
                for i,hyp in enumerate(data['hypotheses']):
                    inspect(hyp,('hypothesis',),['hypotheses',i])
                    if isinstance(hyp,dict):
                        inspect(hyp.get('contribution'),('target_theory','how'),['hypotheses',i,'contribution'])
    return fields if len(fields)<=20 else []


def fill_fields(data, fields, values):
    candidate=copy.deepcopy(data)
    for field in fields:
        target=candidate
        for part in field['path'][:-1]:
            target=target[part]
        target[field['path'][-1]]=values[field['key']]
    return candidate


def completion_plan(data, stage, validator):
    fields=missing_text_fields(data,stage)
    if not fields:
        return []
    # All other requirements must already pass. This cannot be used to hide
    # bad IDs, missing candidates/hypotheses, wrong scores or reference errors.
    preflight=fill_fields(data,fields,{f['key']:'待补全文本。'*20 for f in fields})
    try:
        validator(preflight)
    except ValueError:
        return []
    return fields


def validate_field_patch(patch, fields, original=None):
    # Some gateways return the entire draft despite a patch-only instruction.
    # Accept it only if every existing value and every other key is byte-for-byte
    # identical after JSON serialization; extract only the allowed new fields.
    if original is not None and not (isinstance(patch,dict) and set(patch)=={'patches'}):
        values={}
        try:
            for field in fields:
                value=patch
                for part in field['path']:
                    value=value[part]
                if not isinstance(value,str) or not value.strip():
                    raise ValueError('缺失字段仍未补全')
                values[field['key']]=value
            candidate=fill_fields(original,fields,values)
            canonical=lambda x:json.dumps(x,ensure_ascii=False,sort_keys=True,allow_nan=False)
            if canonical(candidate)!=canonical(patch):
                raise ValueError('补全响应改动了已有字段；未采用')
            return values
        except (KeyError,IndexError,TypeError) as exc:
            raise ValueError('补全响应未提供全部指定字段') from exc
    if not isinstance(patch,dict) or set(patch)!={'patches'} or not isinstance(patch['patches'],list):
        raise ValueError('定向补全返回须仅含 patches 数组')
    expected={f['key'] for f in fields};values={}
    for item in patch['patches']:
        if not isinstance(item,dict) or set(item)!={'key','text'}:
            raise ValueError('定向补全条目格式错误')
        key,value=item['key'],item['text']
        if not isinstance(key,str) or key not in expected or key in values or not isinstance(value,str) or not value.strip():
            raise ValueError('定向补全包含未知、重复或空字段')
        values[key]=value.strip()
    if set(values)!=expected:
        raise ValueError('定向补全仍缺少字段；不再自动请求')
    return values
