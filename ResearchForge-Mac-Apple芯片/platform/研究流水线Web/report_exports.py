"""Durable, user-triggered translation and HTML export jobs with reusable text cache."""
from collections import Counter
from contextlib import contextmanager
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

from report_html import (TEMPLATE_VERSION, TRANSLATION_VERSION, render_report, snapshot,
                         text_key, translation_units)
from research_tasks import TaskError


class LockBusy(Exception):
    pass


def acquire_lock(path, blocking=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if not blocking:
                        raise LockBusy()
                    time.sleep(.25)
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError as exc:
                raise LockBusy() from exc
        return handle
    except BaseException:
        handle.close()
        raise


def release_lock(handle):
    if os.name == 'nt':
        import msvcrt
        handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_UN)
    handle.close()


@contextmanager
def locked(path):
    handle = acquire_lock(path, blocking=True)
    try:
        yield
    finally:
        release_lock(handle)


def replace_file(source, target, attempts=40):
    """os.replace that waits out a brief concurrent reader, which Windows treats as a lock."""
    for attempt in range(attempts):
        try:
            return os.replace(source, target)
        except PermissionError:
            if os.name != 'nt' or attempt == attempts - 1:
                raise
            time.sleep(0.05)


def read_text_file(path, attempts=40):
    """Read a file a concurrent writer may be replacing; Windows briefly denies access meanwhile."""
    for attempt in range(attempts):
        try:
            return path.read_text(encoding='utf-8')
        except PermissionError:
            if os.name != 'nt' or attempt == attempts - 1:
                raise
            time.sleep(0.05)


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + '.' + uuid.uuid4().hex + '.pending')
    try:
        with pending.open('x', encoding='utf-8') as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        replace_file(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def write_json(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2))


def numeric_tokens(text):
    """Compare explicit numbers after normalizing equivalent written forms only."""
    # French/bilingual abstracts use a decimal comma: 31,5 % == 31.5%.
    # Require an adjacent percentage/magnitude, 1-2 fractional digits and no
    # list spacing; leave 1,500 (thousands) and '1, 2%' (a list) unchanged.
    text = re.sub(r'(?<![\d,])(\d+),(\d{1,2})(?=\s*(?:%|percent\b|pour\s+cent\b|million\b|billion\b|trillion\b))',
                  r'\1.\2', text, flags=re.I)
    # '1980s' and '20世纪80年代' name the same decade. The century and
    # decade must be adjacent and the decade a multiple of ten; ordinary
    # occurrences of 20 and 80 elsewhere must never satisfy a missing 1980.
    text = re.sub(r'(?<![\d.])(?:第\s*)?([1-9]\d?)\s*世纪\s*([0-9]0)\s*年代',
                  lambda match: str((int(match[1])-1)*100+int(match[2])), text)
    # Compare quantity values, not just their written coefficient: 162.1 billion
    # equals 1621亿, but does NOT equal 162.1亿. Decimal avoids float rounding.
    number = r'(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)'
    scales = {'trillion':10**12,'billion':10**9,'million':10**6,'thousand':10**3,'hundred':100,
              '万亿':10**12,'千亿':10**11,'百亿':10**10,'十亿':10**9,'亿':10**8,
              '千万':10**7,'百万':10**6,'十万':10**5,'万':10**4,'千':1000,'百':100,'十':10}
    def expand(match):
        amount = Decimal(match[1].replace(',', '')) * scales[match[2].lower()]
        return ' ' + format(amount, 'f') + ' '
    text = re.sub('('+number+r')[\s-]*(trillion|billion|million|thousand|hundred)\b',expand,text,flags=re.I)
    # 十分位/百分位/百分点 are statistical terms, not magnitude multipliers.
    text = re.sub('('+number+r')\s*(万亿|千亿|百亿|十亿|亿|千万|百万|十万|万|千|百|十)(?!分(?:位|点|比|之))',expand,text)
    return Counter(format(Decimal(token.replace(',', '')).normalize(), 'f') for token in
                   re.findall(number, text))


def source_numeric_tokens(text):
    """Ignore a narrowly identified OCR pronoun, not real quantities.

    Some library abstracts contain ', 1 find that' in first-person prose.
    Treat that 1 as I only when an earlier explicit I establishes the voice
    and a later 'my' confirms it. Preserve the stored source and require every
    other number as before; do not normalize translated text or numbered lists.
    """
    pattern = re.compile(r'(?P<prefix>,[ \t]+)1(?=[ \t]+find[ \t]+that\b)')
    def pronoun(match):
        before, after = text[:match.start()], text[match.end():]
        if re.search(r'\bI\b', before) and re.search(r'\bmy\b', after, re.I):
            return match['prefix'] + 'I'
        return match[0]
    return numeric_tokens(pattern.sub(pronoun, text))


def validate_translations(data, units):
    items = data.get('translations') if isinstance(data, dict) else None
    if not isinstance(items, list) or len(items) != len(units):
        raise ValueError('翻译条目数量不完整；本批未保存，请手动重试')
    output = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('翻译条目不是有效对象')
        key, text = item.get('key'), item.get('text')
        if not isinstance(key, str) or key not in units or key in output:
            raise ValueError('翻译编号缺失、重复或与原文不符')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('翻译正文为空')
        han = len(re.findall('[\u3400-\u9fff]', text))
        words = len(re.findall('[A-Za-z]+', units[key]))
        if han < 1 or (words > 40 and han < words * .8):
            raise ValueError('翻译正文未包含完整中文内容；未将摘要式输出当作成功')
        missing = source_numeric_tokens(units[key]) - numeric_tokens(text)
        if missing:
            preview = '、'.join(list(missing)[:8])
            raise ValueError(f'译文未保留原文数字或数量（按单位换算后）：{preview}')
        output[key] = text.strip()
    return output


def partition_translations(data, units):
    """Keep independently valid entries, but never guess missing or ambiguous keys."""
    items = data.get('translations') if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {}, {key:'返回缺少有效的译文列表' for key in units}
    grouped = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('key'), str) or item['key'] not in units:
            return {}, {key:'返回包含无效或未知编号，无法可靠对应原文' for key in units}
        grouped.setdefault(item['key'], []).append(item)
    checked, issues = {}, {}
    for key, source in units.items():
        entries = grouped.get(key, [])
        if len(entries) != 1:
            issues[key] = '译文缺失' if not entries else '译文编号重复，无法确认正确版本'
            continue
        try:
            checked.update(validate_translations({'translations':entries},{key:source}))
        except ValueError as error:
            issues[key] = str(error)
    return checked, issues


class ReportExports:
    def __init__(self, root, translate, sanitize=str, repair=None):
        self.root = Path(root)
        self.translate = translate
        self.sanitize = sanitize
        self.repair = repair
        self.guard = threading.Lock()
        self.threads = {}

    @staticmethod
    def version(task):
        value = snapshot(task)
        for key in ('updated_at', 'revision'):
            value.pop(key, None)
        return hashlib.sha256((TEMPLATE_VERSION + json.dumps(value,ensure_ascii=False,sort_keys=True)).encode()).hexdigest()

    def job_path(self, version):
        if not re.fullmatch('[0-9a-f]{64}', version):
            raise TaskError('导出版本无效', 'invalid_report', 400)
        return self.root/'jobs'/(version+'.json')

    def cache(self, units):
        result = {}
        for key, source in units.items():
            path = self.root/'translations'/(key+'.json')
            try:
                record = json.loads(read_text_file(path))
                if record.get('source') == source and record.get('version') == TRANSLATION_VERSION:
                    result.update(validate_translations({'translations':[{'key':key,'text':record.get('text')}]}, {key:source}))
            except (OSError, ValueError, TypeError):
                continue
        return result

    def seed(self, source, text, provenance='existing-reviewed-translation'):
        key = text_key(source)
        validate_translations({'translations':[{'key':key,'text':text}]},{key:source})
        write_json(self.root/'translations'/(key+'.json'),
                   {'source':source,'text':text,'version':TRANSLATION_VERSION,'provenance':provenance})

    def _record(self, version):
        try:
            return json.loads(read_text_file(self.job_path(version)))
        except FileNotFoundError:
            return None

    def status(self, task, version=None):
        version = version or self.version(task)
        record = self._record(version)
        if record is None:
            units = translation_units(task)
            cached = len(self.cache(units))
            return {'status':'idle','version':version,'completed':cached,'total':len(units),
                    'message':f'导出构想、评审、检索文献与提案。已有 {cached} 段译文可复用，另有 {len(units)-cached} 段待翻译。'}
        if record['task']['id'] != task['id']:
            raise TaskError('报告与研究任务不匹配', 'report_not_found', 404)
        result = {k:record.get(k) for k in ('status','version','completed','total','message','error','created_at','finished_at','phase','repair_count','recovered','issues')}
        if result['status'] in ('queued','running'):
            try:
                probe = acquire_lock(self.root/'locks'/(version+'.lock'))
            except LockBusy:
                pass
            else:
                release_lock(probe)
                result.update(status='interrupted',message='上次导出已中断。已完成译文保留，点击继续导出。')
        if result['status'] == 'completed':
            path = self.root/'reports'/(version+'.html')
            if not path.is_file():
                result.update(status='failed',message='报告文件缺失；点击重新导出，已有译文将复用。')
            else:
                result['file_url'] = f'/api/tasks/{task["id"]}/report/file?version={version}'
        return result

    def start(self, task):
        if task.get('status') in ('queued','running','stopping'):
            raise TaskError('请等待研究结束或停止后，再导出已保存结果', 'research_active', 409)
        task = snapshot(task)
        version = self.version(task)
        with self.guard:
            existing = self.status(task)
            if existing['status'] in ('completed','queued','running'):
                return existing
            try:
                handle = acquire_lock(self.root/'locks'/(version+'.lock'))
            except LockBusy:
                return self.status(task)
            try:
                units = translation_units(task)
                record = {'task':task,'version':version,'status':'queued','completed':len(self.cache(units)),
                          'total':len(units),'created_at':time.time(),'message':'正在准备中文报告…','error':None,
                          'phase':'preparing','repair_count':0,'recovered':0,'issues':[]}
                write_json(self.job_path(version),record)
                thread = threading.Thread(target=self._run,args=(record,units,handle),daemon=True,
                                          name='report-'+version[:8])
                self.threads[version] = thread
                thread.start()
            except BaseException:
                release_lock(handle)
                raise
        return self.status(task)

    def recover_saved_responses(self, version, units, translated):
        """Revalidate saved responses locally after a rule fix; never call the model."""
        recovered = 0
        for path in sorted((self.root/'failed_outputs').glob(version+'-*.json'), reverse=True):
            try:
                saved = json.loads(read_text_file(path))
                sources = saved['sources']
                if not isinstance(sources, dict) or any(units.get(k) != v for k,v in sources.items()):
                    continue
                checked, _ = partition_translations(saved.get('response'), sources)
                for key, text in checked.items():
                    if key not in translated:
                        self.seed(units[key], text, 'recovered-validated-response')
                        translated[key] = text
                        recovered += 1
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return recovered

    def save_checked(self, record, translated, checked, sources):
        for key, text in checked.items():
            self.seed(sources[key], text, 'model-translation')
            translated[key] = text
        record.update(completed=len(translated), message=f'中文翻译已保存 {len(translated)} / {record["total"]} 段')
        write_json(self.job_path(record['version']),record)

    def save_issues(self, record, sources, response, issues):
        write_json(self.root/'failed_outputs'/(record['version']+'-'+str(time.time_ns())+'.json'),
                   {'sources':sources,'response':response,'issues':issues})
        record['issues'] = [{'source':sources[key][:240], 'reason':reason} for key,reason in issues.items()]
        write_json(self.job_path(record['version']),record)

    def _run(self, record, units, handle):
        version = record['version']
        try:
            # Serialize translation across reports and recheck cache after waiting.
            with locked(self.root/'locks/translation-worker.lock'):
                translated = self.cache(units)
                recovered = self.recover_saved_responses(version, units, translated)
                record.update(status='running',phase='translating',recovered=recovered,completed=len(translated),message='正在翻译并整理中文内容…')
                write_json(self.job_path(version),record)
                remaining = [(key,text) for key,text in units.items() if key not in translated]
                while remaining:
                    batch, size = {}, 0
                    while remaining and len(batch)<16:
                        key, text = remaining[0]
                        if batch and size+len(text)>6500:
                            break
                        remaining.pop(0); batch[key]=text; size+=len(text)
                    record.update(phase='translating',issues=[])
                    write_json(self.job_path(version),record)
                    result = self.translate(batch)
                    checked, issues = partition_translations(result,batch)
                    self.save_checked(record,translated,checked,batch)
                    if issues:
                        self.save_issues(record,batch,result,issues)
                        if self.repair is not None:
                            failed = {key:batch[key] for key in issues}
                            candidates = {item['key']:item.get('text','') for item in result.get('translations',[])
                                          if isinstance(item,dict) and isinstance(item.get('key'),str) and item['key'] in failed} if isinstance(result,dict) and isinstance(result.get('translations'),list) else {}
                            record.update(phase='repairing',repair_count=record['repair_count']+len(failed),
                                          message=f'已保存 {len(translated)} / {len(units)} 段，正在定向修复 {len(failed)} 段异常译文（最多一次）…')
                            write_json(self.job_path(version),record)
                            result = self.repair(failed,candidates,issues)
                            checked, issues = partition_translations(result,failed)
                            self.save_checked(record,translated,checked,failed)
                            if issues:
                                self.save_issues(record,failed,result,issues)
                        if issues:
                            raise ValueError(f'{len(issues)} 段译文仍未通过校验；正确译文已保存。'+next(iter(issues.values())))
                    record['issues'] = []
                    write_json(self.job_path(version),record)
                record.update(phase='rendering',message='正在生成 HTML 排版与目录…')
                write_json(self.job_path(version),record)
                document = render_report(record['task'],translated)
                atomic_write(self.root/'reports'/(version+'.html'),document)
                record.update(status='completed',phase='completed',completed=len(units),finished_at=time.time(),
                              message='中文 HTML 报告已生成，可预览或下载。',error=None)
                write_json(self.job_path(version),record)
        except Exception as error:
            record.update(status='failed',completed=len(self.cache(units)),message='导出暂停；正确译文已保存，继续时仅处理未完成内容。',
                          error=self.sanitize(str(error)),finished_at=time.time())
            write_json(self.job_path(version),record)
        finally:
            release_lock(handle)
            with self.guard:
                self.threads.pop(version,None)

    def file(self, task, version=None):
        state = self.status(task,version)
        if state['status'] != 'completed':
            raise TaskError('报告尚未生成，请先点击导出', 'report_not_ready', 409)
        return self.root/'reports'/(state['version']+'.html')

    def has_active(self):
        with self.guard:
            return any(thread.is_alive() for thread in self.threads.values())
