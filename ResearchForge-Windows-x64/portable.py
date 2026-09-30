"""Run the bundled workbench and its local retrieval engine, without installation."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get_json(url, timeout=2):
    with HTTP.open(url, timeout=timeout) as response:
        return json.load(response)


def free_port(preferred=0):
    candidates = range(preferred, min(preferred + 100, 65536)) if preferred else [0]
    for candidate in candidates:
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', candidate))
                return sock.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError('没有找到可用的本机端口。')


def self_check():
    for module in ('flask', 'requests', 'numpy', 'pyarrow', 'bleach', 'markdown', 'pymdownx', 'pypdf', 'certifi'):
        importlib.import_module(module)
    with sqlite3.connect(':memory:') as con:
        con.execute('CREATE VIRTUAL TABLE check_fts USING fts5(value, tokenize="trigram")')
    executable = ROOT / 'runtime/ollama' / ('ollama.exe' if os.name == 'nt' else 'ollama')
    if not executable.is_file():
        raise RuntimeError('缺少随包提供的检索引擎，请完整解压压缩包。')
    manifest_path = ROOT / 'runtime/models/manifests/registry.ollama.ai/library/qwen3-embedding/0.6b'
    manifest = json.loads(manifest_path.read_text())
    for layer in [manifest['config'], *manifest['layers']]:
        blob = ROOT / 'runtime/models/blobs' / layer['digest'].replace(':', '-')
        if not blob.is_file() or blob.stat().st_size != layer['size']:
            raise RuntimeError('内置嵌入模型不完整，请重新完整解压。')
    return executable


@contextlib.contextmanager
def instance_lock(workspace):
    handle = (workspace / 'portable.lock').open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0)
            if not handle.read(1):
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        yield False
        return
    try:
        yield True
    finally:
        handle.close()


def existing_url(state_file, instance_id):
    try:
        state = json.loads(state_file.read_text())
        port = state['port']
        if type(port) is not int or not 1024 <= port <= 65535:
            return None
        url = f'http://127.0.0.1:{port}'
        if get_json(url + '/api/portable/health').get('instance') == instance_id:
            return url
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def windows_job(child):
    """Close owned engine processes when the Windows console is closed."""
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                    ('flags', wintypes.DWORD), ('min_working_set', ctypes.c_size_t),
                    ('max_working_set', ctypes.c_size_t), ('active_limit', wintypes.DWORD),
                    ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                    ('scheduling', wintypes.DWORD)]
    class Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
    class Extended(ctypes.Structure):
        _fields_ = [('basic', Basic), ('io', Counters), ('process_memory', ctypes.c_size_t),
                    ('job_memory', ctypes.c_size_t), ('peak_process_memory', ctypes.c_size_t),
                    ('peak_job_memory', ctypes.c_size_t)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    info = Extended()
    info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(job, wintypes.HANDLE(int(child._handle))):
        if job:
            kernel.CloseHandle(job)
        raise RuntimeError('无法为本地检索服务建立进程管理，请检查系统进程限制。')
    return lambda: kernel.CloseHandle(job)


def main():
    parser = argparse.ArgumentParser(description='ResearchForge 解压即用版')
    parser.add_argument('--check', action='store_true', help='仅检查随包运行环境')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--workspace', help='独立数据目录，用于迁移或测试')
    parser.add_argument('--port', type=int)
    args = parser.parse_args()
    engine = self_check()
    if args.check:
        print('环境完整：Python、程序依赖、SQLite、本地检索引擎和嵌入模型均已就绪。')
        return
    import run
    import certifi
    cfg = run.load_config()
    workspace = run.resolve_path(args.workspace or cfg.get('workspace', 'user_data'))
    workspace.mkdir(parents=True, exist_ok=True)
    state_file = workspace / 'portable-state.json'
    instance_id = hashlib.sha256(str(workspace).encode()).hexdigest()
    with instance_lock(workspace) as acquired:
        if not acquired:
            for _ in range(30):
                url = existing_url(state_file, instance_id)
                if url:
                    print('此工作台已经启动：' + url)
                    if not args.no_browser:
                        webbrowser.open(url)
                    return
                time.sleep(0.5)
            raise RuntimeError('当前文件夹已有启动进程，请查看原来的启动窗口。')
        preferred = args.port or cfg.get('port', 8331)
        if not 1024 <= preferred <= 65535:
            raise RuntimeError('端口须为 1024 至 65535 的整数。')
        cfg['port'] = free_port(preferred)
        cfg['ollama_host'] = f'http://127.0.0.1:{free_port()}'
        run.configure(cfg, args.workspace)
        os.environ.update({
            'PATH': str(engine.parent) + os.pathsep + os.environ.get('PATH', ''),
            'OLLAMA_MODELS': str(ROOT / 'runtime/models'),
            'OLLAMA_NO_CLOUD': '1', 'OLLAMA_MAX_LOADED_MODELS': '1',
            'SSL_CERT_FILE': certifi.where(), 'REQUESTS_CA_BUNDLE': certifi.where(),
        })
        def stop_signal(_signum, _frame):
            raise KeyboardInterrupt
        for name in ('SIGTERM', 'SIGHUP'):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), stop_signal)
        child = None
        close_job = None
        try:
            with (workspace / 'ollama.log').open('ab') as engine_log:
                options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
                child = subprocess.Popen([str(engine), 'serve'], stdin=subprocess.DEVNULL, stdout=engine_log, stderr=subprocess.STDOUT, env=os.environ.copy(), **options)
            close_job = windows_job(child)
            for _ in range(60):
                if child.poll() is not None:
                    raise RuntimeError('内置检索服务启动失败，详细信息在 user_data/ollama.log。')
                try:
                    get_json(cfg['ollama_host'] + '/api/tags')
                    break
                except (OSError, ValueError):
                    time.sleep(0.5)
            else:
                raise RuntimeError('内置检索服务启动超时，请查看 user_data/ollama.log。')
            sys.path.insert(0, str(ROOT / 'platform/研究流水线Web'))
            import server
            server.app.add_url_rule('/api/portable/health', 'portable_health', lambda: {'instance': instance_id, 'edition': 'portable'})
            url = f"http://127.0.0.1:{cfg['port']}"
            state_file.write_text(json.dumps({'port': cfg['port'], 'pid': os.getpid(), 'engine_pid': child.pid}))
            def open_when_ready():
                for _ in range(60):
                    if existing_url(state_file, instance_id):
                        if not args.no_browser:
                            webbrowser.open(url)
                        return
                    time.sleep(0.25)
            threading.Thread(target=open_when_ready, daemon=True).start()
            print('工作台地址：' + url, flush=True)
            print('请保持启动窗口打开，按 Ctrl+C 停止。', flush=True)
            server.main(['--no-browser'])
        except KeyboardInterrupt:
            print('\n正在关闭工作台和内置检索服务。', flush=True)
        finally:
            state_file.unlink(missing_ok=True)
            if child and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            if close_job:
                close_job()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('启动失败：' + str(exc), file=sys.stderr)
        sys.exit(1)
