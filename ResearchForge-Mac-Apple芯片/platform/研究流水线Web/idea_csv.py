"""Four-column, cumulative CSV projection of durable research tasks."""
import csv
import json
import os
from pathlib import Path
import time


HEADERS = ('主题', '标题', '摘要', '状态')
PAUSED = {'failed': '失败', 'stopped': '停止', 'interrupted': '中断'}


def task_rows(task):
    """Keep one row per idea, preferring the saved Chinese proposal text."""
    selected = {str(x['id']) for x in task['top5']}
    proposals = {str(x['idea']['id']): x['proposal'] for x in task['proposals']}
    inflight = task.get('inflight') or {}
    inflight_by_idea = {str(unit.get('idea_id')): unit for unit in task.get('inflight_units', [])}
    if inflight.get('idea_id') is not None:
        inflight_by_idea.setdefault(str(inflight['idea_id']), inflight)
    for position, idea in enumerate(task['ideas'], 1):
        key = str(idea['id'])
        proposal = proposals.get(key)
        if proposal is not None:
            title = proposal.get('title_zh') or proposal.get('title_en') or idea['title']
            abstract = proposal.get('abstract_zh') or proposal.get('abstract_en') or idea['abstract']
            status = '通过'
        else:
            title, abstract = idea['title'], idea['abstract']
            check = task.get('literature_checks', {}).get(key, {})
            if check.get('decision') == 'drop':
                status = 'S4 暂缓（证据不足）' if check.get('evidence_status') == 'insufficient' else 'S4 文献判断未通过'
            elif task.get('selection_count', 5) == 0:
                status = '未筛选'
            elif selected and key not in selected:
                status = 'S2 初筛淘汰'
            else:
                if not selected:
                    status = '初筛中' if inflight.get('stage') == 2 else '待初筛'
                elif key not in task['lit']:
                    status = '待检索'
                else:
                    status = '待优化'
                if key in inflight_by_idea:
                    status = {3: '检索中', 4: '优化中'}.get(inflight_by_idea[key]['stage'], status)
                if task['status'] in PAUSED:
                    # A failed task also pauses later ideas that were never attempted.
                    status = {'初筛中': '待初筛', '检索中': '待检索', '优化中': '待优化'}.get(status, status)
                    status += f"（任务在 S{task['stage']} {PAUSED[task['status']]}，可继续）"
                elif task['status'] == 'stopping':
                    status += '（已请求停止）'
        yield (task['id'], key, task['created_at'], position, task['topic'], title, abstract, status)


def save_task_rows(con, task):
    # The compound identity stays in SQLite, preserving exactly four CSV columns.
    con.executemany('INSERT INTO idea_csv_rows VALUES(?,?,?,?,?,?,?,?) '
                    'ON CONFLICT(task_id,idea_id) DO UPDATE SET '
                    'title=excluded.title,abstract=excluded.abstract,status=excluded.status',
                    task_rows(task))


def initialize(con):
    con.execute('CREATE TABLE IF NOT EXISTS idea_csv_rows ('
                'task_id TEXT NOT NULL, idea_id TEXT NOT NULL, created REAL NOT NULL, '
                'position INTEGER NOT NULL, topic TEXT NOT NULL, title TEXT NOT NULL, '
                'abstract TEXT NOT NULL, status TEXT NOT NULL, PRIMARY KEY(task_id,idea_id))')
    con.execute('CREATE TABLE IF NOT EXISTS idea_csv_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    if not con.execute("SELECT 1 FROM idea_csv_meta WHERE key='language_zh_v1'").fetchone():
        # Rebuild only the CSV projection from existing text; task state stays intact.
        for row in con.execute('SELECT state FROM tasks ORDER BY created,id'):
            save_task_rows(con, json.loads(row[0]))
        con.execute("INSERT OR IGNORE INTO idea_csv_meta VALUES('schema_v1','1')")
        con.execute("INSERT INTO idea_csv_meta VALUES('language_zh_v1','1')")


def replace_file(source, target, attempts=40):
    """os.replace that waits out a brief concurrent reader, which Windows treats as a lock."""
    for attempt in range(attempts):
        try:
            return os.replace(source, target)
        except PermissionError:
            if os.name != 'nt' or attempt == attempts - 1:
                raise
            time.sleep(0.05)


class IdeaCSV:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.snapshot = dict(path=str(self.path), rows=0, updated_at=None, error=None)

    def write(self, con):
        """Caller holds SQLite's writer lock, so older snapshots cannot overwrite newer ones."""
        # A fixed sibling is recoverable after a crash or an Excel sharing violation.
        temporary = self.path.with_name(self.path.name + '.pending')
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            count = 0
            with temporary.open('w', encoding='utf-8-sig', newline='') as out:
                writer = csv.writer(out)
                writer.writerow(HEADERS)
                for row in con.execute('SELECT topic,title,abstract,status FROM idea_csv_rows '
                                       'ORDER BY created,task_id,position'):
                    writer.writerow(row)
                    count += 1
                out.flush()
                os.fsync(out.fileno())
            replace_file(temporary, self.path)
            self.snapshot = dict(path=str(self.path), rows=count, updated_at=time.time(), error=None)
            return True
        except OSError as exc:
            self.snapshot = dict(self.snapshot, error=f'idea.csv 更新失败：{exc}。任务结果已保存，后台会重试；'
                                 '若正在 Excel 中打开此文件，请关闭文件以允许更新。')
            return False
