"""Local HTML export of saved research, with no model or translation dependency."""
import hashlib
import threading
import time

from report_exports import (ReportExports, LockBusy, acquire_lock, release_lock,
                            write_json, atomic_write)
from report_html import render_report, snapshot
from research_tasks import TaskError


class OriginalReportExports(ReportExports):
    def __init__(self, root, sanitize=str):
        super().__init__(root, translate=None, sanitize=sanitize)

    @staticmethod
    def version(task):
        # Keep old translated reports and their exact URLs accessible.
        return hashlib.sha256(('original-report-1\0' + ReportExports.version(task)).encode()).hexdigest()

    def status(self, task, version=None):
        version = version or self.version(task)
        record = self._record(version)
        if record is None:
            return {'status':'idle','mode':'original','version':version,'completed':0,'total':1,
                    'message':'在本地导出全部已保存研究结果；英文保留英文，中文保留中文，无需翻译。'}
        result = super().status(task, version)
        result['mode'] = record.get('mode', 'translation')
        if result['mode'] == 'original':
            if result['status'] == 'interrupted':
                result['message'] = '上次本地排版已中断；点击重新导出，研究结果保持不变。'
            elif result['status'] == 'failed' and record['status'] == 'completed':
                result['message'] = '报告文件缺失；点击重新导出即可根据已保存结果恢复。'
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
                record = {'task':task,'version':version,'mode':'original','status':'queued',
                          'completed':0,'total':1,'created_at':time.time(),'error':None,
                          'phase':'preparing','message':'正在整理已保存的研究结果…','issues':[]}
                write_json(self.job_path(version), record)
                thread = threading.Thread(target=self._run, args=(record,handle), daemon=True,
                                          name='original-report-'+version[:8])
                self.threads[version] = thread
                thread.start()
            except BaseException:
                self.threads.pop(version, None)
                release_lock(handle)
                raise
        return self.status(task)

    def _run(self, record, handle):
        version = record['version']
        try:
            record.update(status='running', phase='rendering', message='正在生成 HTML 排版与目录…')
            write_json(self.job_path(version), record)
            document = render_report(record['task'], {}, preserve_original=True)
            atomic_write(self.root/'reports'/(version+'.html'), document)
            record.update(status='completed', phase='completed', completed=1, finished_at=time.time(),
                          message='完整 HTML 报告已生成，全部内容按原文保留，可预览或下载。', error=None)
        except Exception as error:
            record.update(status='failed', error=self.sanitize(str(error)), finished_at=time.time(),
                          message='本地报告生成失败；研究结果仍完整保存，可重新导出。')
        finally:
            try:
                write_json(self.job_path(version), record)
            finally:
                release_lock(handle)
                with self.guard:
                    self.threads.pop(version, None)
