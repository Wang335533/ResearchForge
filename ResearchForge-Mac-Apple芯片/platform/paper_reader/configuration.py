import copy
import json
import time
import os
import threading
from pathlib import Path
from urllib.parse import urlsplit

if __package__:
    from .prompts import DEFAULT_PROMPTS
else:
    from prompts import DEFAULT_PROMPTS

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('PAPERLENS_DATA_DIR', ROOT / 'data'))
DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
CONFIG_PATH = DATA / 'settings.json'
LOCK = threading.RLock()


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


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    with open(temp, 'w', encoding='utf-8') as f:
        os.chmod(temp, 0o600)
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    replace_file(temp, path)


def initial_settings():
    # Never inspect another installation or inherit credentials from the host.
    providers = [dict(id='deepseek', name='DeepSeek 官方', endpoint='https://api.deepseek.com/chat/completions',
                      api_key='', model='deepseek-flash', thinking='enabled', max_tokens=24000,
                      context_chars=180000, direct=False)]
    return dict(providers=providers, default_provider='deepseek', prompts=copy.deepcopy(DEFAULT_PROMPTS))


def settings():
    with LOCK:
        if not CONFIG_PATH.exists():
            atomic_json(CONFIG_PATH, initial_settings())
        saved = json.loads(read_text_file(CONFIG_PATH))
        changed = False
        for key, value in DEFAULT_PROMPTS.items():
            if key not in saved['prompts']:
                saved['prompts'][key] = value
                changed = True
        if changed:
            atomic_json(CONFIG_PATH, saved)
        return saved


def save_settings(value):
    with LOCK:
        atomic_json(CONFIG_PATH, value)


def public_provider(p):
    return {**{k: v for k, v in p.items() if k != 'api_key'}, 'has_key': bool(p.get('api_key'))}


def public_settings():
    c = settings()
    c['providers'] = [public_provider(p) for p in c['providers']]
    return c


def redact(message):
    result = str(message)
    for p in settings()['providers']:
        if p.get('api_key'):
            result = result.replace(p['api_key'], '[密钥已隐藏]')
    return result[:1600]


def validate_provider(p):
    for field in ('name', 'endpoint', 'model'):
        if not isinstance(p.get(field), str) or not p[field].strip() or len(p[field]) > 500:
            raise ValueError('请填写有效的名称、接口地址和模型名称。')
        p[field] = p[field].strip()
    u = urlsplit(p['endpoint'])
    if u.scheme not in ('https', 'http') or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError('接口地址需为完整 HTTP(S) 地址，不能包含账号、密钥或查询参数。')
    if u.scheme == 'http' and u.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('远程接口必须使用 HTTPS；本机服务可以使用 HTTP。')
    if not u.path.rstrip('/').endswith('/chat/completions'):
        raise ValueError('请填写完整的兼容接口地址，以 /chat/completions 结尾。')
    p['max_tokens'] = int(p.get('max_tokens', 24000))
    p['context_chars'] = int(p.get('context_chars', 90000))
    if not 1024 <= p['max_tokens'] <= 131072:
        raise ValueError('输出额度需在 1,024–131,072 tokens 之间。')
    if not 12000 <= p['context_chars'] <= 500000:
        raise ValueError('单次输入字符预算需在 12,000–500,000 之间。')
    if p.get('thinking') not in ('auto', 'enabled', 'disabled'):
        raise ValueError('无效的思考模式。')
    p['direct'] = bool(p.get('direct', False))
    return p
