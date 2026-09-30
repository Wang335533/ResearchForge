"""Persist this local installation's session signing key across restarts."""
import os
from pathlib import Path
import secrets


def session_secret(data_dir):
    configured = os.environ.get('RESEARCHFORGE_SECRET')
    if configured:
        return configured
    path = Path(data_dir) / 'session.secret'
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation avoids replacing a key used by another process.
        try:
            with path.open('x', encoding='ascii') as stream:
                stream.write(secrets.token_hex(32))
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            pass
    value = path.read_text(encoding='ascii').strip()
    if len(value) < 32:
        raise RuntimeError('本机登录配置无法读取，请检查 data/session.secret')
    return value
