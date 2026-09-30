"""Durable research jobs with one coordinator and bounded parallel calls."""
from concurrent.futures import ThreadPoolExecutor
import contextlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from idea_csv import IdeaCSV, initialize as initialize_idea_csv, save_task_rows
from prompts import abstract_first_sentence
from output_format import FIELD_COMPLETION_ALLOWED


ACTIVE = {'queued', 'running', 'stopping'}
GENERATION_BATCH_SIZE = 10
JUDGE_BATCH_SIZE = 20
DEFAULT_CONCURRENCY = dict(max_parallel_tasks=3, max_api_calls=4, max_task_calls=3)


def validate_concurrency(values):
    limits = dict(DEFAULT_CONCURRENCY, **values)
    if set(limits) != set(DEFAULT_CONCURRENCY):
        raise ValueError('并发配置包含未知字段')
    for key, value in limits.items():
        if type(value) is not int or not 1 <= value <= 8:
            raise ValueError(f'并发配置 {key} 必须是 1–8 的整数')
    return limits


def load_concurrency(path):
    path = Path(path)
    return validate_concurrency(json.loads(path.read_text(encoding='utf-8')) if path.exists() else {})


class UnitCancelled(Exception):
    """A reserved unit was stopped before making its external call."""


def selection_target(task):
    return min(task.get('selection_count', 5), task['count'])


class TaskError(Exception):
    def __init__(self, message, code='task_error', status=400):
        super().__init__(message)
        self.code, self.status = code, status


def note(task, message):
    task['message'] = message
    task['events'].append({'at': time.time(), 'message': message})
    task['events'] = task['events'][-200:]


def summary(task):
    return dict({k: task[k] for k in ('id', 'topic', 'count', 'status', 'stage', 'message',
                                    'created_at', 'updated_at', 'revision')},
                selection_count=task.get('selection_count', 5))


class TaskStore:
    def __init__(self, path, csv_path=None):
        self.path = Path(path)
        self.idea_csv = IdeaCSV(csv_path or self.path.with_name('idea.csv'))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('BEGIN IMMEDIATE')
            con.execute('CREATE TABLE IF NOT EXISTS tasks '
                        '(id TEXT PRIMARY KEY, created REAL NOT NULL, status TEXT NOT NULL, '
                        'summary TEXT NOT NULL, state TEXT NOT NULL)')
            con.execute('CREATE INDEX IF NOT EXISTS task_queue ON tasks(status,created)')
            initialize_idea_csv(con)

    def sync_csv(self):
        # Commit checkpoints first. CSV sharing/permission errors never lose a result
        # or cause another paid model call; the worker retries only this file write.
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            return self.idea_csv.write(con)

    @contextlib.contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=15)
        try:
            con.execute('PRAGMA synchronous=FULL')
            with con:
                yield con
        finally:
            con.close()

    def get(self, task_id):
        with self.connect() as con:
            row = con.execute('SELECT state FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not row:
            raise TaskError('研究任务不存在', 'task_not_found', 404)
        return json.loads(row[0])

    def all(self):
        with self.connect() as con:
            return [json.loads(row[0]) for row in con.execute('SELECT state FROM tasks ORDER BY created DESC')]

    def summaries(self):
        with self.connect() as con:
            # Number repeated topics across the entire history, before limiting
            # the sidebar. Reading history never rewrites a running checkpoint.
            rows = con.execute('''
                SELECT summary,
                       ROW_NUMBER() OVER (
                           PARTITION BY json_extract(summary, '$.topic')
                           ORDER BY created, id) AS run_number
                FROM tasks ORDER BY created DESC, id DESC LIMIT 50
            ''')
            return [dict(json.loads(row[0]), run_number=row[1]) for row in rows]

    def next_queued(self):
        with self.connect() as con:
            row = con.execute("SELECT id FROM tasks WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
        return row[0] if row else None

    def queued_ids(self):
        with self.connect() as con:
            return [row[0] for row in con.execute(
                "SELECT id FROM tasks WHERE status='queued' ORDER BY created,id")]

    def create(self, task_id, topic, count, models, selection_count=5):
        try:
            task_id = str(uuid.UUID(task_id))
        except (ValueError, TypeError, AttributeError):
            raise TaskError('request_id 必须是有效 UUID')
        if not isinstance(topic, str) or not topic.strip() or len(topic) > 4000:
            raise TaskError('研究主题需为 1–4000 字符')
        if type(count) is not int or not 0 <= count <= 100:
            raise TaskError('构想数量必须是 0–100 之间的整数')
        if type(selection_count) is not int or not 0 <= selection_count <= 100:
            raise TaskError('筛选数量必须是 0–100 之间的整数')
        topic = topic.strip()
        stamp = time.time()
        task = dict(id=task_id, topic=topic, count=count, selection_count=selection_count,
                    judge_batches=[], created_at=stamp, updated_at=stamp,
                    revision=1, status='queued', stage=1, stage_models=models,
                    ideas=[], top5=[], lit={}, proposals=[], literature_checks={},
                    workflow_version='literature-gate-1', stop_requested=False,
                     inflight=None, inflight_units=[], error=None, events=[], message='')
        note(task, '研究任务已保存，等待执行')
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            old = con.execute('SELECT state FROM tasks WHERE id=?', (task_id,)).fetchone()
            if old:
                old = json.loads(old[0])
                if (old['topic'], old['count'], old.get('selection_count', 5)) != (topic, count, selection_count):
                    raise TaskError('同一个 request_id 不能用于不同研究任务', 'request_conflict', 409)
                return old, False
            con.execute('INSERT INTO tasks VALUES(?,?,?,?,?)', (task_id, stamp, task['status'],
                json.dumps(summary(task), ensure_ascii=False), json.dumps(task, ensure_ascii=False)))
        return task, True

    def edit(self, task_id, change):
        # Always edit the latest committed state, retaining stop requests that arrive
        # while a long model call is in progress.
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT state FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not row:
                raise TaskError('研究任务不存在', 'task_not_found', 404)
            task = json.loads(row[0])
            change(task)
            task['revision'] += 1
            task['updated_at'] = time.time()
            con.execute('UPDATE tasks SET status=?,summary=?,state=? WHERE id=?', (task['status'],
                json.dumps(summary(task), ensure_ascii=False), json.dumps(task, ensure_ascii=False), task_id))
            save_task_rows(con, task)
        self.sync_csv()
        return task

    def recover(self):
        # Queued work has not made an external call. In-flight work may have been
        # billed before the crash; never replay it without an explicit resume.
        with self.connect() as con:
            ids = [row[0] for row in con.execute("SELECT id FROM tasks WHERE status IN ('running','stopping')")]
        for task_id in ids:
            if self.get(task_id)['status'] in ('running', 'stopping'):
                def interrupt(t):
                    t['status'] = 'interrupted'
                    t['stop_requested'] = False
                    t['error'] = {'code': 'server_interrupted', 'message':
                        '后端运行中断，已有结果已保存。中断时的请求可能已被模型处理；继续会重新请求尚未保存的这一项。'}
                    note(t, t['error']['message'])
                self.edit(task_id, interrupt)


class ResearchTasks:
    def __init__(self, path, execute, models, ready, sanitize=str, *, csv_path=None,
                 max_parallel_tasks=3, max_api_calls=4, max_task_calls=3):
        self.limits = validate_concurrency(dict(max_parallel_tasks=max_parallel_tasks,
            max_api_calls=max_api_calls, max_task_calls=max_task_calls))
        self.store = TaskStore(path, csv_path)
        self.execute, self.models, self.ready, self.sanitize = execute, models, ready, sanitize
        self._wake = threading.Event()
        self._closing = threading.Event()
        self._guard = threading.Lock()
        self._thread = None
        self._lock_file = None
        self.worker_error = None
        self._metrics = dict(running_tasks=0, active_api_calls=0, active_retrievals=0)

    def concurrency_status(self):
        return dict(self.limits, retrieval_workers=1, **self._metrics,
                    queued_tasks=len(self.store.queued_ids()))

    def _acquire_worker(self):
        # OS locks release on process exit, including crashes. Another process
        # must never recover or consume the same queue while its owner is alive.
        handle = open(self.store.path.with_suffix('.worker.lock'), 'a+b')
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise TaskError('另一后端进程正在执行研究任务，请使用原服务', 'worker_owned', 503)
        self._lock_file = handle

    def _release_worker(self):
        handle, self._lock_file = self._lock_file, None
        if handle:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def start(self):
        with self._guard:
            if self._thread and self._thread.is_alive():
                return
            self._acquire_worker()
            try:
                self.store.recover()
                self.store.sync_csv()
                self._closing.clear()
                self.worker_error = None
                self._thread = threading.Thread(target=self._loop, name='researchforge-jobs', daemon=True)
                self._thread.start()
            except Exception:
                self._release_worker()
                raise

    def close(self, timeout=5):
        """Finish the current unit before stopping; mainly used by isolated tests."""
        self._closing.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)
        return not self._thread or not self._thread.is_alive()

    def create(self, task_id, topic, count, selection_count=5):
        self.start()
        task, created = self.store.create(task_id, topic, count, self.models(), selection_count)
        self._wake.set()
        return task, created

    def stop(self, task_id):
        def change(t):
            if t['status'] == 'queued':
                t['status'] = 'stopped'
                note(t, '任务已停止，已有结果已保存')
            elif t['status'] in ('running', 'stopping'):
                t['stop_requested'] = True
                t['status'] = 'stopping'
                note(t, '已请求停止；不再派发新请求，正在进行的调用结束并保存后停止')
        task = self.store.edit(task_id, change)
        self._wake.set()
        return task

    def resume(self, task_id):
        self.start()
        def change(t):
            if t['status'] == 'stopping':
                raise TaskError('当前调用尚未结束，请等待任务停止后继续', 'task_stopping', 409)
            if t['status'] in ('completed', 'running', 'queued'):
                return
            t.update(status='queued', stop_requested=False, error=None, inflight=None, inflight_units=[],
                     stage_models=self.models())
            note(t, '已请求继续，将保留全部已完成结果')
        task = self.store.edit(task_id, change)
        self._wake.set()
        return task

    def _loop(self):
        # Only this coordinator reserves units and commits their results. HTTP stop
        # requests edit the latest SQLite state; model threads never overwrite it.
        owned, pending = {}, {}
        api_pool = ThreadPoolExecutor(self.limits['max_api_calls'], thread_name_prefix='rf-api')
        retrieval_pool = ThreadPoolExecutor(1, thread_name_prefix='rf-retrieval')
        try:
            while not self._closing.is_set() or pending or owned:
                self._wake.clear()
                if self.store.idea_csv.snapshot['error']:
                    self.store.sync_csv()
                for future in list(pending):
                    if not future.done():
                        continue
                    task_id, unit = pending.pop(future)
                    run = owned[task_id]
                    run['keys'].discard(self.unit_key(unit))
                    try:
                        result = future.result()
                        def save(t):
                            self.save_unit(t, unit[0], unit[2], result)
                            self.finish_inflight(t, unit)
                        self.store.edit(task_id, save)
                    except UnitCancelled:
                        self.store.edit(task_id, lambda t: self.finish_inflight(t, unit))
                    except Exception as exc:
                        run['cancel'].set()
                        if run['error'] is None:
                            run['error'] = {'code': getattr(exc, 'code', 'stage_failed'),
                                            'message': self.sanitize(str(exc))}
                        def failed_unit(t):
                            self.finish_inflight(t, unit)
                            t['error'] = run['error']
                            note(t, f'{unit[1]}失败：' + run['error']['message'] +
                                 '；不再派发新请求，等待已发出的调用保存。')
                        self.store.edit(task_id, failed_unit)

                for task_id, run in list(owned.items()):
                    task = self.store.get(task_id)
                    stopping = task['stop_requested'] or self._closing.is_set()
                    if stopping:
                        run['cancel'].set()
                    if run['keys']:
                        continue
                    if stopping or run['error'] or not self.next_units(task):
                        def finish(t):
                            requested_stop = stopping or t['stop_requested']
                            t.update(inflight=None, inflight_units=[], stop_requested=False)
                            if requested_stop:
                                t['status'] = 'stopped'
                                note(t, '任务已停止，全部已返回结果已保存，可继续')
                            elif run['error']:
                                t.update(status='failed', error=run['error'])
                                note(t, '执行暂停：' + run['error']['message'])
                            else:
                                t['status'] = 'completed'
                                dropped = sum(c.get('decision') == 'drop' for c in t.get('literature_checks', {}).values())
                                note(t, f"研究流程已完成：{len(t['proposals'])} 份提案，{dropped} 个方向未进入提案，结果与理由已保存" if selection_target(t) else
                                     '构想已保存，已跳过筛选与后续步骤' if t['count'] else '空研究任务已保存，未调用模型')
                        self.store.edit(task_id, finish)
                        del owned[task_id]

                if not self._closing.is_set():
                    for task_id in self.store.queued_ids():
                        if len(owned) >= self.limits['max_parallel_tasks']:
                            break
                        task = self.store.get(task_id)
                        if selection_target(task) and not self.ready():
                            continue
                        def begin(t):
                            if t['status'] == 'queued':
                                t.update(status='running', inflight=None, inflight_units=[])
                                note(t, '后台开始执行，支持多项研究并行，关闭页面不影响任务')
                        task = self.store.edit(task_id, begin)
                        if task['status'] == 'running':
                            owned[task_id] = dict(keys=set(), error=None, cancel=threading.Event())

                    # Round-robin: give each active research one slot before filling
                    # another slot for the same research. Pools never queue excess work.
                    for _ in range(self.limits['max_task_calls']):
                        for task_id, run in owned.items():
                            if run['cancel'].is_set() or run['error']:
                                continue
                            task = self.store.get(task_id)
                            if task['stop_requested']:
                                continue
                            units = [u for u in self.next_units(task) if self.unit_key(u) not in run['keys']]
                            if not units:
                                continue
                            unit = units[0]
                            stage, label, payload = unit
                            limit = 1 if stage in (1, 3) else self.limits['max_task_calls']
                            if len(run['keys']) >= limit:
                                continue
                            local = stage == 3
                            occupied = sum((u[0] == 3) == local for _, u in pending.values())
                            if occupied >= (1 if local else self.limits['max_api_calls']):
                                continue
                            key = self.unit_key(unit)
                            def preparing(t):
                                if t['stop_requested']:
                                    return
                                t['stage'] = stage
                                entry = dict(key=key, stage=stage, label=label, started_at=time.time())
                                if stage in (3, 4):
                                    entry['idea_id'] = payload['idea_id'] if stage == 3 else payload['idea']['id']
                                t.setdefault('inflight_units', []).append(entry)
                                self.refresh_inflight(t)
                                note(t, t['inflight']['label'])
                            task = self.store.edit(task_id, preparing)
                            if task['stop_requested']:
                                continue
                            run['keys'].add(key)
                            pool = retrieval_pool if local else api_pool
                            future = pool.submit(self.execute_unit, task_id, unit, run['cancel'])
                            pending[future] = task_id, unit
                            future.add_done_callback(lambda _future: self._wake.set())

                self._metrics = dict(running_tasks=len(owned),
                    active_api_calls=sum(u[0] != 3 for _, u in pending.values()),
                    active_retrievals=sum(u[0] == 3 for _, u in pending.values()))
                if owned:
                    first = next(iter(owned))
                    owned[first] = owned.pop(first)
                if self._closing.is_set() and not pending and not owned:
                    break
                if not any(f.done() for f in pending):
                    self._wake.wait(1)
        except Exception as exc:
            self.worker_error = self.sanitize(str(exc))
        finally:
            for run in owned.values():
                run['cancel'].set()
            api_pool.shutdown(wait=True)
            retrieval_pool.shutdown(wait=True)
            self._release_worker()

    @staticmethod
    def next_unit(t):
        return next(iter(ResearchTasks.next_units(t)), None)

    @staticmethod
    def next_units(t):
        if len(t['ideas']) < t['count']:
            n = min(GENERATION_BATCH_SIZE, t['count'] - len(t['ideas']))
            return [(1, f"生成构想 {len(t['ideas']) + 1}–{len(t['ideas']) + n}", {
                'topic': t['topic'], 'count': n,
                'avoid': [{'title': x['title'], 'abstract': abstract_first_sentence(x['abstract'])} for x in t['ideas']]})]
        target = selection_target(t)
        if target == 0:
            return []
        if not t['top5']:
            reviewed = {i for batch in t.get('judge_batches', []) for i in batch['idea_ids']}
            units = []
            for offset in range(0, len(t['ideas']), JUDGE_BATCH_SIZE):
                ideas = [idea for idea in t['ideas'][offset:offset + JUDGE_BATCH_SIZE] if idea['id'] not in reviewed]
                if ideas:
                    units.append((2, f"评审构想 {ideas[0]['id']}–{ideas[-1]['id']}", {
                        'ideas': ideas, 'selection_count': min(target, len(ideas))}))
            return units
        units = []
        for judge in t['top5']:
            idea = next(x for x in t['ideas'] if x['id'] == judge['id'])
            if str(judge['id']) not in t['lit']:
                units.append((3, f"检索 Top {judge['rank']} 文献", {'query': f"{idea['title']}. {idea['abstract']}", 'idea_id': idea['id']}))
        if units:
            return units
        for judge in t['top5']:
            idea = next(x for x in t['ideas'] if x['id'] == judge['id'])
            dropped = t.get('literature_checks', {}).get(str(idea['id']), {}).get('decision') == 'drop'
            if not dropped and not any(p['idea']['id'] == idea['id'] for p in t['proposals']):
                units.append((4, f"文献判断与优化 Top {judge['rank']}", {'idea': idea, 'judge': judge, 'papers': t['lit'][str(idea['id'])]}))
        return units

    @staticmethod
    def unit_key(unit):
        stage, _, payload = unit
        value = (len(payload['avoid']) if stage == 1 else
                 ','.join(str(i['id']) for i in payload['ideas']) if stage == 2 else
                 payload['idea_id'] if stage == 3 else payload['idea']['id'])
        return f'{stage}:{value}'

    @staticmethod
    def refresh_inflight(t):
        units = t.get('inflight_units', [])
        t['inflight'] = dict(units[0], parallel_count=len(units)) if units else None
        if len(units) > 1:
            t['inflight']['label'] = f'并行处理 {len(units)} 项：' + '；'.join(u['label'] for u in units)

    @staticmethod
    def finish_inflight(t, unit):
        key = ResearchTasks.unit_key(unit)
        t['inflight_units'] = [u for u in t.get('inflight_units', []) if u['key'] != key]
        ResearchTasks.refresh_inflight(t)
        if t['inflight'] and not t.get('error') and not t['stop_requested']:
            note(t, t['inflight']['label'])

    def execute_unit(self, task_id, unit, cancel):
        def allowed():
            return not (cancel.is_set() or self._closing.is_set() or self.store.get(task_id)['stop_requested'])
        if not allowed():
            raise UnitCancelled()
        token = FIELD_COMPLETION_ALLOWED.set(allowed)
        try:
            return self.execute(unit[0], unit[2])
        except Exception:
            # Prevent the coordinator from dispatching more work while this failed
            # future is waiting to be collected, and suppress optional repair calls.
            cancel.set()
            raise
        finally:
            FIELD_COMPLETION_ALLOWED.reset(token)

    @staticmethod
    def save_unit(t, stage, payload, result):
        if stage == 1:
            for idea in result['ideas']:
                t['ideas'].append(dict(idea, id=len(t['ideas']) + 1))
        elif stage == 2:
            batches = t.setdefault('judge_batches', [])
            ids = [idea['id'] for idea in payload['ideas']]
            if any(set(batch['idea_ids']) & set(ids) for batch in batches):
                raise ValueError('评审批次重复，未覆盖已有结果')
            batches.append({'idea_ids': ids, 'top5': result['top5']})
            order = {idea['id']: index for index, idea in enumerate(t['ideas'])}
            batches.sort(key=lambda batch: min(order[i] for i in batch['idea_ids']))
            if {i for batch in batches for i in batch['idea_ids']} == set(order):
                candidates = [item for batch in batches for item in batch['top5']]
                ranked = sorted(candidates, key=lambda item: -item['total'])[:selection_target(t)]
                t['top5'] = [dict(item, rank=rank) for rank, item in enumerate(ranked, 1)]
        elif stage == 3:
            t['lit'][str(payload['idea_id'])] = result['papers']
            t.setdefault('retrieval_audits', {})[str(payload['idea_id'])] = result.get('retrieval', {})
            for warning in result.get('retrieval', {}).get('warnings', []):
                note(t, '检索提示：' + str(warning))
        else:
            check = result.get('literature_check')
            proposal = result.get('proposal')
            if check:
                if check.get('decision') not in ('pass', 'drop') or (check['decision'] == 'pass') != bool(proposal):
                    raise ValueError('文献判断与提案状态不一致，未保存')
                t.setdefault('literature_checks', {})[str(payload['idea']['id'])] = check
            elif not proposal:
                raise ValueError('缺少文献判断或有效提案，未保存')
            if proposal:
                if any(p['idea']['id'] == payload['idea']['id'] for p in t['proposals']):
                    raise ValueError('提案重复，未覆盖已有结果')
                t['proposals'].append({'idea': payload['idea'], 'judge': payload['judge'], 'proposal': proposal})
                t['proposals'].sort(key=lambda p: p['judge']['rank'])
        t['inflight'] = None
        if result.get('_format_correction'):
            audit = result['_format_correction']
            t.setdefault('format_corrections', []).append(dict(audit,stage=stage,at=time.time()))
            note(t, f"S{stage} 已纠正 {audit['edits']} 处 JSON 标点并通过完整校验；原始返回和纠正记录已保存")
        if result.get('_field_completion'):
            audit = result['_field_completion']
            t.setdefault('field_completions', []).append(dict(audit,stage=stage,at=time.time()))
            note(t, f"S{stage} 已定向补全 {audit['fields']} 个缺失文本字段（额外调用1次），其余已有内容和评分保持不变")
        if stage == 4 and result.get('literature_check', {}).get('decision') == 'drop':
            note(t, f"S4 构想 {payload['idea']['id']} 未进入提案，判断理由已保存")
        else:
            note(t, f'S{stage} 本项结果已保存')
