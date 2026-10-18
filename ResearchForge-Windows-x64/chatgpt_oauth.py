"""Official ChatGPT plan OAuth + streaming Responses, independent of Codex login.

Credentials never enter shared provider settings, job snapshots, or logs.
Windows DPAPI protects the store; a process/file lock serializes token rotation.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
import base64
import ctypes
import hashlib
import hmac
import json
import os
import re
import secrets
import ssl
import threading
import time
import urllib.request
import uuid
import webbrowser

import httpx2 as httpx
import jwt
from openai import OpenAI, APIStatusError, APIConnectionError, APITimeoutError
import chatgpt_config as cfg

_LOCK = threading.RLock()
_LOGIN_LOCK = threading.Lock()
_LOGIN = {'pending': False, 'message': ''}
_SESSION_REVISION = 0
_SECRET_FIELDS = ('access_token', 'refresh_token', 'id_token')
_TERMINAL_REFRESH = {'invalid_grant', 'invalid_refresh_token', 'token_expired',
                     'refresh_token_expired', 'refresh_token_invalidated', 'refresh_token_reused'}


class OAuthError(RuntimeError):
    def __init__(self, message, *, code='', partial='', usage=None):
        super().__init__(message)
        self.code, self.output, self.usage = code, partial, usage or {}


def _protect(raw, decrypt=False):
    """Encrypt for this Windows user without a UI prompt."""
    if os.name != 'nt':
        raise OAuthError('此安装包的 OAuth 凭据存储需要 Windows DPAPI。')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('length', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buf = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise OAuthError('无法读取或保护本机 OAuth 凭据，请使用原 Windows 账户。')
    try:
        return ctypes.string_at(target.data, target.length)
    finally:
        kernel.LocalFree(target.data)


@contextmanager
def _store_lock():
    import msvcrt
    with _LOCK:
        directory = cfg.credential_directory()
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / 'refresh.lock').open('a+b') as handle:
            handle.seek(0)
            if not handle.read(1):
                handle.write(b'0')
                handle.flush()
            deadline = time.monotonic() + 40
            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise OAuthError('另一个进程正在更新授权，请稍后重试。')
                    time.sleep(0.1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _read():
    path = cfg.credential_directory() / 'credentials.dpapi'
    if not path.exists():
        return {'host_id': 'urn:uuid:' + str(uuid.uuid4()), 'accounts': {}, 'active': None}
    try:
        return json.loads(_protect(path.read_bytes(), decrypt=True))
    except (ValueError, OSError) as exc:
        raise OAuthError('OAuth 凭据文件不可读取；原文件已保留。') from exc


def _write(data):
    path = cfg.credential_directory() / 'credentials.dpapi'
    tmp = path.with_suffix('.tmp')
    with tmp.open('wb') as stream:
        stream.write(_protect(json.dumps(data, ensure_ascii=False).encode('utf-8')))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def _permitted(account):
    return bool(account.get('access_token') and 'chatgpt.tokens.use.direct' in account.get('scopes', [])
                and (account.get('expires_at', 0) > time.time() or account.get('refresh_token')))


def status():
    with _store_lock():
        data = _read()
        active = data['accounts'].get(data.get('active'), {})
        accounts = [dict(client_id=k, label=(v.get('email') or 'ChatGPT 账户') + ' · ' + k[-8:],
                         connected=_permitted(v)) for k, v in data['accounts'].items()]
    return dict(connected=_permitted(active), active=data.get('active'), accounts=accounts,
                pending=_LOGIN['pending'], message=_LOGIN['message'], model=cfg.DEFAULT_MODEL)


def is_ready():
    return status()['connected']


def _client():
    return httpx.Client(timeout=httpx.Timeout(cfg.HTTP_TIMEOUT_SECONDS, connect=cfg.CONNECT_TIMEOUT_SECONDS),
                        trust_env=cfg.USE_SYSTEM_PROXY, follow_redirects=False,
                        mounts=_windows_proxy_mounts())


def _windows_proxy_mounts():
    """NO_PROXY alone must not hide the Windows user's configured HTTP proxy.

    urllib.getproxies() treats any environment entry (even just NO_PROXY) as
    a complete proxy configuration. Keep HTTPX's normal environment precedence
    and bypass mounts, supplying only the missing Windows proxy transports.
    """
    if os.name != 'nt' or not cfg.USE_SYSTEM_PROXY:
        return None
    env = urllib.request.getproxies_environment()
    if not env or any(key in env for key in ('http', 'https', 'all')):
        return None
    if '*' in {host.strip() for host in env.get('no', '').split(',')}:
        return None
    system = urllib.request.getproxies_registry()
    return {scheme + '://': httpx.HTTPTransport(
                proxy=value if '://' in value else 'http://' + value, trust_env=True)
            for scheme in ('http', 'https', 'all') if (value := system.get(scheme))}


def _network_error(exc, *, partial='', usage=None):
    # Inspect types, never raw exception strings that can contain proxy credentials.
    chain, seen = [], set()
    while exc is not None and id(exc) not in seen:
        chain.append(exc)
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    def has(kind):
        return any(isinstance(error, kind) for error in chain)
    if has(httpx.ProxyError):
        code, message = 'proxy_error', '系统代理连接失败，请检查代理程序是否正在运行。'
    elif has(ssl.SSLError):
        code, message = 'tls_error', 'OpenAI 安全连接失败，请检查系统时间与代理证书。'
    elif has(httpx.ConnectTimeout):
        code, message = 'connect_timeout', '连接 OpenAI 超时，请检查系统代理和网络连接。'
    elif has(httpx.ReadTimeout):
        code, message = 'read_timeout', '等待 OpenAI 流式响应超时，可能仍在思考或网络已中断。'
    elif has(httpx.WriteTimeout):
        code, message = 'write_timeout', '向 OpenAI 发送请求超时，请检查网络连接。'
    elif has(httpx.ConnectError):
        code, message = 'connect_error', '无法连接 OpenAI，请检查系统代理、DNS 和网络连接。'
    elif has(httpx.RemoteProtocolError):
        code, message = 'interrupted_stream', 'OpenAI 响应连接被中途关闭。'
    elif has((httpx.TimeoutException, APITimeoutError)):
        code, message = 'network_timeout', 'OpenAI 网络请求超时。'
    else:
        code, message = 'network_error', 'OpenAI 网络连接中断。'
    return OAuthError(message + ' 未自动重新发送；已有结果已保留。',
                      code=code, partial=partial, usage=usage)


def _safe(value, secrets_to_hide=()):
    text = str(value)
    for secret in secrets_to_hide:
        if secret:
            text = text.replace(secret, '[令牌已隐藏]')
    text = re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[令牌已隐藏]', text)
    return text[:700]


def _http_error(response, tokens=()):
    try:
        body = response.json()
    except ValueError:
        body = {}
    error = body.get('error', {}) if isinstance(body, dict) else {}
    code = error.get('code', '') if isinstance(error, dict) else str(error)
    param = error.get('param') if isinstance(error, dict) else None
    detail = (error.get('message') if isinstance(error, dict) else '') or (body.get('detail', '') if isinstance(body, dict) else '')
    hints = {
        'subscription_sharing_usage_limit_exceeded': '套餐或本应用额度受限，请查看 ChatGPT 设置 → Usage。',
        'subscription_sharing_user_not_eligible': '当前账户或工作区不支持套餐授权调用。',
        'subscription_sharing_usage_unavailable': '暂时无法核验套餐额度，请稍后重试。',
    }
    request_id = response.headers.get('x-request-id', '')
    msg = f'OpenAI HTTP {response.status_code}；code={code or "unknown"}；param={param or "-"}；request_id={request_id or "-"}。'
    msg += hints.get(code, _safe(detail, tokens))
    return OAuthError(_safe(msg, tokens), code=code)


def _token_request(form):
    try:
        with _client() as client:
            response = client.post(cfg.TOKEN_URL, data=form)
        if response.status_code != 200:
            raise _http_error(response, form.values())
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OAuthError('OAuth 网络请求未完成；已有凭据保留，请稍后重试。') from exc


class _IdentityKeyClient(jwt.PyJWKClient):
    def fetch_data(self):
        # PyJWT's urllib transport has the same NO_PROXY-only Windows bug.
        try:
            with _client() as client:
                response = client.get(self.uri, timeout=20)
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError):
            raise OAuthError('暂时无法获取 OpenAI 身份验证公钥，请检查系统代理后重试。',
                             code='identity_keys_unavailable') from None


def _validate_identity(token, client_id, nonce=None, subject=None):
    # Fixed issuer/JWKS/algorithm: never trust a URL or algorithm from the token.
    try:
        key = _IdentityKeyClient(cfg.JWKS_URL, timeout=20).get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=['RS256'], audience=client_id, issuer=cfg.ISSUER,
                            options={'require': ['iss', 'aud', 'exp', 'sub', 'iat']})
    except jwt.PyJWTError as exc:
        raise OAuthError('OpenAI 身份令牌验证失败（' + type(exc).__name__ + '），未启用新凭据。',
                         code='identity_verification_failed') from None
    if nonce is not None and not hmac.compare_digest(str(claims.get('nonce', '')), nonce):
        raise OAuthError('授权返回的 nonce 无效；未保存凭据。')
    if subject is not None and claims['sub'] != subject:
        raise OAuthError('授权返回的账户与已选账户不一致；未替换凭据。')
    return claims


def _merge_tokens(old, tokens):
    if not tokens.get('access_token') or str(tokens.get('token_type', '')).lower() != 'bearer':
        raise OAuthError('OAuth 返回缺少有效 Bearer 令牌。')
    new = dict(old)
    for field in _SECRET_FIELDS:
        if tokens.get(field):
            new[field] = tokens[field]
    new['expires_at'] = tokens.get('_received_at', time.time()) + int(tokens['expires_in'])
    new['saved_at'] = datetime.now(timezone.utc).isoformat()
    new['scopes'] = tokens['scope'].split() if 'scope' in tokens else old.get('scopes', [])
    new['earliest_refresh_at'] = tokens.get('earliest_refresh_at')
    return new


def access_token():
    with _store_lock():
        data = _read()
        key = data.get('active')
        account = data['accounts'].get(key, {})
        if not _permitted(account):
            raise OAuthError('请在共享 API 设置中点击 Continue with ChatGPT，授权使用套餐额度。')
        if account['expires_at'] > time.time() + 60:
            return account['access_token']
        if not account.get('refresh_token'):
            if account['expires_at'] > time.time():
                return account['access_token']
            raise OAuthError('登录已到期，请重新授权。')
        earliest = account.get('earliest_refresh_at')
        if earliest:
            try:
                earliest = float(earliest)
            except (TypeError, ValueError):
                earliest = datetime.fromisoformat(str(earliest).replace('Z', '+00:00')).timestamp()
            if time.time() < earliest:
                if account['expires_at'] > time.time():
                    return account['access_token']
                raise OAuthError('尚未到允许续期时间，请稍后重试。')
        tokens = account.get('pending_refresh')
        if not tokens:
            try:
                tokens = _token_request(dict(grant_type='refresh_token', client_id=key,
                    refresh_token=account['refresh_token'], resource=cfg.BASE_URL))
            except OAuthError as exc:
                if exc.code in _TERMINAL_REFRESH:
                    for field in _SECRET_FIELDS:
                        account.pop(field, None)
                    _write(data)
                raise
            # Persist rotated credentials encrypted before network-dependent ID
            # verification. They are never used until verification succeeds.
            tokens['_received_at'] = time.time()
            account['pending_refresh'] = tokens
            _write(data)
        if tokens.get('id_token'):
            _validate_identity(tokens['id_token'], key, subject=account['subject'])
        account = _merge_tokens(account, tokens)
        account.pop('pending_refresh', None)
        data['accounts'][key] = account
        _write(data)
        if not _permitted(account):
            raise OAuthError('续期后未获套餐调用权限，请重新授权。')
        if account['expires_at'] <= time.time():
            raise OAuthError('缓存的续期响应已过期；轮换凭据已保存，请重试。', code='stale_refresh')
        return account['access_token']


def _finish_login(query, attempt):
    global _SESSION_REVISION
    def one(name):
        values = query.get(name, [])
        return values[0] if len(values) == 1 else ''
    if not hmac.compare_digest(one('state'), attempt['state']):
        raise OAuthError('授权回调 state 不匹配；未交换令牌。')
    if one('error'):
        raise OAuthError('授权未完成：' + _safe(one('error')))
    issued = one('client_id') or (attempt['client_id'] if not attempt['new'] else '')
    if not issued or issued == cfg.INITIAL_CLIENT_ID or not one('code'):
        raise OAuthError('授权回调缺少 code 或正式 client_id。')
    if not attempt['new'] and issued != attempt['client_id']:
        raise OAuthError('授权回调 client_id 与原注册不一致。')
    with _store_lock():
        data = _read()
        data['pending_client_id'] = issued
        _write(data)
    tokens = _token_request(dict(grant_type='authorization_code', client_id=issued,
        code=one('code'), code_verifier=attempt['verifier'], redirect_uri=attempt['redirect_uri'], resource=cfg.BASE_URL))
    claims = _validate_identity(tokens['id_token'], issued, attempt['nonce'], attempt.get('subject'))
    account = _merge_tokens(dict(client_id=issued, subject=claims['sub'], issuer=cfg.ISSUER,
                                  email=claims.get('email', '')), tokens)
    with _store_lock():
        data = _read()
        data['accounts'][issued] = account
        data['active'] = issued
        data.pop('pending_client_id', None)
        _write(data)
        _SESSION_REVISION += 1
    return '授权成功，返回 ResearchForge 即可使用。' if _permitted(account) else '已登录，但未授权套餐调用；请重新登录并同意使用套餐额度。'


def start_login(client_id=None, reconsent=False):
    if not _LOGIN_LOCK.acquire(blocking=False):
        raise OAuthError('已有授权窗口等待完成，请先处理该窗口。')
    listener = None
    try:
        with _store_lock():
            data = _read()
            if client_id and client_id not in data['accounts']:
                raise OAuthError('未找到所选账户的注册记录。')
            issued = client_id or data.get('pending_client_id') or cfg.INITIAL_CLIENT_ID
            old = data['accounts'].get(issued, {})
            _write(data)  # Persist host ID BEFORE first authorization.
        attempt = dict(client_id=issued, new=issued == cfg.INITIAL_CLIENT_ID,
                       state=secrets.token_urlsafe(32), nonce=secrets.token_urlsafe(32),
                       verifier=secrets.token_urlsafe(64), subject=old.get('subject'))
        done = threading.Event()
        class Callback(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # Callback query contains the one-time authorization code.

            def do_GET(self):
                parsed = urlsplit(self.path)
                if parsed.path != cfg.CALLBACK_PATH or done.is_set():
                    self.send_error(404)
                    return
                query = parse_qs(parsed.query)
                if len(query.get('state', [])) != 1 or not hmac.compare_digest(query['state'][0], attempt['state']):
                    self.send_error(400, 'Invalid OAuth state')
                    return
                done.set()  # Consume the attempt once, before network exchange.
                try:
                    message = _finish_login(query, attempt)
                except OAuthError as exc:
                    message = str(exc)
                except Exception:
                    message = '授权令牌或身份校验失败，未启用新凭据。请重新登录。'
                _LOGIN['message'] = message
                payload = ('ResearchForge\n\n' + message + '\n可关闭此页。').encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        listener = HTTPServer((cfg.CALLBACK_HOST, 0), Callback)
        listener.timeout = 0.5
        attempt['redirect_uri'] = f'http://{cfg.CALLBACK_HOST}:{listener.server_port}{cfg.CALLBACK_PATH}'
        query = dict(client_id=issued, ext_agent_host_id=data['host_id'], response_type='code',
                     redirect_uri=attempt['redirect_uri'], scope=cfg.SCOPES, resource=cfg.BASE_URL,
                     state=attempt['state'], nonce=attempt['nonce'], code_challenge_method='S256',
                     code_challenge=base64.urlsafe_b64encode(hashlib.sha256(attempt['verifier'].encode()).digest()).decode().rstrip('='))
        if attempt['new']:
            query['agent_name_hint'] = cfg.APP_NAME
        if old.get('id_token'):
            query['id_token_hint'] = old['id_token']
        if reconsent:
            query['prompt'] = 'consent'
        _LOGIN.update(pending=True, message='等待在浏览器完成 ChatGPT 登录与授权。')
        def serve():
            try:
                deadline = time.monotonic() + cfg.LOGIN_TIMEOUT_SECONDS
                while not done.is_set() and time.monotonic() < deadline:
                    listener.handle_request()
                if not done.is_set():
                    _LOGIN['message'] = '授权等待已到期，可以重新登录。'
            finally:
                listener.server_close()
                _LOGIN['pending'] = False
                _LOGIN_LOCK.release()
        # Listener is bound before the browser is opened. No auth URL is logged.
        url = cfg.AUTHORIZE_URL + '?' + urlencode(query)
        opened = webbrowser.open(url)
        if not opened:
            raise OAuthError('无法打开系统浏览器，请设置默认浏览器后重试。')
        threading.Thread(target=serve, daemon=True, name='chatgpt-oauth-callback').start()
        return {'started': True}
    except Exception:
        if listener:
            listener.server_close()
        _LOGIN['pending'] = False
        _LOGIN_LOCK.release()
        raise


def select_account(client_id):
    global _SESSION_REVISION
    with _store_lock():
        data = _read()
        if client_id not in data['accounts']:
            raise OAuthError('未找到所选账户。')
        data['active'] = client_id
        _write(data)
        _SESSION_REVISION += 1


def sign_out():
    global _SESSION_REVISION
    confirmed = True
    with _store_lock():
        data = _read()
        account = data['accounts'].get(data.get('active'), {})
        refresh = account.get('pending_refresh', {}).get('refresh_token') or account.get('refresh_token')
        if refresh:
            try:
                with _client() as client:
                    discovery = client.get(cfg.DISCOVERY_URL)
                    discovery.raise_for_status()
                    endpoint = discovery.json()['revocation_endpoint']
                    if urlsplit(endpoint).scheme != 'https' or urlsplit(endpoint).netloc != 'auth.openai.com':
                        raise ValueError('Invalid revocation endpoint')
                    response = client.post(endpoint, data=dict(token=refresh,
                        token_type_hint='refresh_token', client_id=account['client_id']))
                    confirmed = response.status_code == 200
            except (httpx.HTTPError, KeyError, ValueError):
                confirmed = False
        for field in _SECRET_FIELDS:
            account.pop(field, None)
        account.pop('pending_refresh', None)
        _write(data)
        _SESSION_REVISION += 1
    return {'ok': True, 'message': '已退出登录。' if confirmed else '已清除本机令牌，未确认远端撤销；可在 ChatGPT 设置中断开本应用。'}


def list_models():
    token = access_token()
    try:
        with _client() as client:
            response = client.get(cfg.BASE_URL + '/models', headers={'Authorization': 'Bearer ' + token}, timeout=12)
        if response.status_code != 200:
            raise _http_error(response, [token])
        return [{k: m[k] for k in ('slug', 'display_name', 'visibility', 'supported_reasoning_levels', 'default_reasoning_level') if k in m}
                for m in response.json()['models'] if m.get('visibility') == 'list']
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise OAuthError('无法取得该账户的模型列表，请稍后重试。') from exc


def complete(system, user, *, model=None, reasoning_effort=None, max_tokens=24000, on_update=None, should_stop=lambda: False, output_format=None):
    from chatgpt_models import API_EFFORTS
    if should_stop():
        raise OAuthError('已停止，未发送新的请求。', code='stopped')
    if reasoning_effort and reasoning_effort not in API_EFFORTS:
        raise OAuthError('当前 Responses 接口不支持所选思考强度。')
    revision = _SESSION_REVISION
    token = access_token()
    content, usage, completed, last_update = '', {}, False, 0.0
    try:
        with _client() as http:
            with OpenAI(api_key=token, base_url=cfg.BASE_URL, max_retries=cfg.MAX_RETRIES, http_client=http,
                        timeout=httpx.Timeout(cfg.HTTP_TIMEOUT_SECONDS, connect=cfg.CONNECT_TIMEOUT_SECONDS,
                                              read=cfg.RESPONSE_READ_TIMEOUT_SECONDS)) as client:
                extra = {'reasoning': {'effort': reasoning_effort}} if reasoning_effort else {}
                if output_format is not None:
                    extra['text'] = {'format': output_format}
                with client.responses.create(model=model or cfg.DEFAULT_MODEL,
                    instructions=system, input=[{'role': 'user', 'content': user}],
                    store=cfg.STORE, stream=cfg.STREAM, **extra) as stream:
                    for event in stream:
                        if should_stop() or revision != _SESSION_REVISION:
                            raise OAuthError('已停止，已返回内容已保存。', code='stopped')
                        if event.type == 'response.output_text.delta':
                            content += event.delta
                        elif event.type == 'response.completed':
                            completed = True
                            raw = event.response.usage
                            usage = raw.model_dump() if raw else {}
                            usage.update(prompt_tokens=usage.get('input_tokens', 0), completion_tokens=usage.get('output_tokens', 0))
                        elif event.type in ('response.failed', 'response.incomplete', 'error'):
                            response = getattr(event, 'response', None)
                            error = getattr(response, 'error', None)
                            details = getattr(response, 'incomplete_details', None)
                            code = getattr(error, 'code', None) or getattr(details, 'reason', None) or getattr(event, 'code', None) or event.type
                            hint = '请检查 ChatGPT 设置 → Usage。' if 'usage' in str(code) else '未自动重试，部分输出已保留。'
                            raise OAuthError(f'OpenAI 流式调用未完成：{_safe(code, [token])}。{hint}', code=str(code))
                        if on_update and time.monotonic() - last_update > 1.5:
                            on_update(content, 0, usage)
                            last_update = time.monotonic()
        if should_stop() or revision != _SESSION_REVISION:
            raise OAuthError('已停止，已返回内容已保存。', code='stopped')
        if not completed:
            raise OAuthError('响应中断，未收到 response.completed；未自动重试。', code='interrupted_stream')
        if not content.strip():
            raise OAuthError('模型完成了请求但没有返回正文。', code='empty_output')
        return content.strip(), usage
    except APIStatusError as exc:
        raise _http_error(exc.response, [token]) from None
    except (APIConnectionError, APITimeoutError, httpx.HTTPError) as exc:
        raise _network_error(exc, partial=content, usage=usage) from None
    except OAuthError as exc:
        exc.output, exc.usage = content, usage
        raise
    finally:
        if on_update:
            on_update(content, 0, usage)
