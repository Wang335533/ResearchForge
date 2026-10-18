"""Tencent Docs' official OAuth PKCE flow and spreadsheet MCP client."""
import base64
import hashlib
import json
import secrets
import threading
import time
import uuid
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import os

import requests
import chatgpt_oauth

if __package__:
    from .configuration import DATA, replace_file
else:
    from configuration import DATA, replace_file

# Tokens are encrypted for this Windows user (DPAPI), like the ChatGPT credentials.
AUTH_PATH = DATA / 'tencent_docs_auth.dpapi'
ISSUER = 'https://docs.qq.com'
RESOURCE = ISSUER + '/openapi/mcp'
SHEET_MCP = ISSUER + '/api/v6/sheet/mcp'
SCOPE = 'docs:read docs:write'
LOCK = threading.RLock()
PENDING = {}


class TencentError(ValueError):
    pass


class WriteUncertain(TencentError):
    pass


class TransientTencentError(TencentError):
    pass


class WriteConflict(WriteUncertain):
    pass


def _load():
    with LOCK:
        if not AUTH_PATH.exists():
            return {}
        try:
            return json.loads(chatgpt_oauth._protect(AUTH_PATH.read_bytes(), decrypt=True))
        except (ValueError, OSError, chatgpt_oauth.OAuthError) as exc:
            raise TencentError('腾讯文档授权文件无法读取，请重新连接。') from exc


def _save(value):
    with LOCK:
        temp = AUTH_PATH.with_name(AUTH_PATH.name + '.tmp')
        with temp.open('wb') as stream:
            stream.write(chatgpt_oauth._protect(json.dumps(value, ensure_ascii=False).encode('utf-8')))
            stream.flush()
            os.fsync(stream.fileno())
        replace_file(temp, AUTH_PATH)


def status():
    try:
        saved = _load()
    except TencentError as exc:
        return dict(connected=False, scope='', expires_at=0, error=str(exc))
    return dict(connected=bool(saved.get('access_token')), scope=saved.get('scope', ''),
                expires_at=saved.get('expires_at', 0))


def _official_url(value):
    parsed = urlsplit(str(value))
    if parsed.scheme != 'https' or parsed.hostname != 'docs.qq.com' or parsed.username or parsed.password:
        raise TencentError('腾讯文档返回了无法识别的授权地址。')
    return value


def _json_request(method, url, **kwargs):
    try:
        response = requests.request(method, _official_url(url), timeout=(10, 40), allow_redirects=False, **kwargs)
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise TencentError('腾讯文档连接未完成，请稍后重试。') from exc
    if not isinstance(body, dict):
        raise TencentError('腾讯文档返回的数据格式无法识别。')
    return response.status_code, body


def _metadata():
    code, data = _json_request('GET', ISSUER + '/.well-known/oauth-authorization-server')
    if code != 200 or data.get('issuer') != ISSUER:
        raise TencentError('暂时无法读取腾讯文档官方授权配置。')
    for key in ('registration_endpoint', 'authorization_endpoint', 'token_endpoint'):
        _official_url(data.get(key, ''))
    return data


def begin_authorization(redirect_uri):
    redirect = urlsplit(redirect_uri)
    if redirect.scheme != 'http' or redirect.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise TencentError('授权回调仅允许返回本机工作台。')
    metadata = _metadata()
    with LOCK:
        saved = _load()
        if not saved.get('client_id') or saved.get('redirect_uri') != redirect_uri:
            code, data = _json_request('POST', metadata['registration_endpoint'], json={
                'client_name': 'ResearchForge 组会文献填报',
                'grant_types': ['authorization_code', 'refresh_token'],
                'response_types': ['code'], 'redirect_uris': [redirect_uri],
                'token_endpoint_auth_method': 'none', 'scope': SCOPE,
            })
            if code not in (200, 201) or not data.get('client_id'):
                raise TencentError('腾讯文档未能创建本机连接，请稍后重试。')
            saved['client_id'] = data['client_id']
            saved['redirect_uri'] = redirect_uri
            _save(saved)
        sid = uuid.uuid4().hex
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        state = secrets.token_urlsafe(32)
        PENDING[sid] = dict(client_id=saved['client_id'], verifier=verifier, state=state,
                            redirect_uri=redirect_uri, expires_at=time.time() + 900,
                            token_endpoint=metadata['token_endpoint'], status='pending')
        u = urlsplit(metadata['authorization_endpoint'])
        query = dict(parse_qsl(u.query))
        query.update(client_id=saved['client_id'], response_type='code', redirect_uri=redirect_uri,
                     scope=SCOPE, resource=RESOURCE, state=state,
                     code_challenge=challenge, code_challenge_method='S256')
        url = urlunsplit((u.scheme,u.netloc,u.path,urlencode(query),''))
        return dict(id=sid, verification_url=url, user_code='', interval=3)


def _store_tokens(tokens, client_id, preserve_refresh=False):
    if not isinstance(tokens.get('access_token'), str) or not tokens['access_token']:
        raise TencentError('腾讯文档没有返回有效授权，请重新连接。')
    with LOCK:
        saved = _load()
        if not preserve_refresh:
            saved.pop('refresh_token', None)
        saved.update(client_id=client_id, access_token=tokens['access_token'],
                     expires_at=time.time() + int(tokens.get('expires_in', 3600)),
                     scope=tokens.get('scope', SCOPE))
        if tokens.get('refresh_token'):
            saved['refresh_token'] = tokens['refresh_token']
        _save(saved)


def poll_authorization(sid):
    with LOCK:
        pending = PENDING.get(sid)
        if pending is None:
            raise TencentError('授权请求已失效，请重新点击连接。')
        if pending['status'] != 'pending':
            return dict(status=pending['status'])
        if time.time() >= pending['expires_at']:
            pending['status'] = 'expired'
            return dict(status='expired')
        return dict(status='pending')


def finish_authorization(state, code='', error=''):
    with LOCK:
        if not isinstance(state, str) or not state.isascii():
            raise TencentError('授权请求不匹配，请重新连接。')
        pending = next((p for p in PENDING.values() if secrets.compare_digest(p['state'], state)), None)
        if not pending or pending['status'] != 'pending' or time.time() >= pending['expires_at']:
            raise TencentError('授权请求已过期或不匹配，请回工作台重新连接。')
        if error or not code:
            pending['status'] = 'denied'
            raise TencentError('腾讯文档授权未完成，请返回工作台重试。')
        pending['status'] = 'exchanging'
        try:
            code, data = _json_request('POST', pending['token_endpoint'], data={
                'grant_type': 'authorization_code', 'code': code,
                'client_id': pending['client_id'], 'redirect_uri': pending['redirect_uri'],
                'code_verifier': pending.pop('verifier'), 'resource': RESOURCE,
            })
        except TencentError:
            pending['status'] = 'denied'
            raise
        if code != 200 or data.get('error'):
            pending['status'] = 'denied'
            raise TencentError('腾讯文档令牌交换未完成，请重新连接。')
        _store_tokens(data, pending['client_id'])
        pending['status'] = 'connected'
        return True


def access_token(force_refresh=False):
    with LOCK:
        saved = _load()
        if not saved.get('access_token'):
            raise TencentError('请先连接腾讯文档。')
        if not force_refresh and saved.get('expires_at', 0) > time.time() + 90:
            return saved['access_token']
        if not saved.get('refresh_token'):
            raise TencentError('腾讯文档授权已过期，请重新连接。')
        code, data = _json_request('POST', ISSUER + '/openapi/mcp/oauth/token', data={
            'grant_type': 'refresh_token', 'refresh_token': saved['refresh_token'],
            'client_id': saved['client_id'],
        })
        if code != 200 or data.get('error'):
            raise TencentError('腾讯文档授权已失效，请重新连接。')
        _store_tokens(data, saved['client_id'], preserve_refresh=True)
        return data['access_token']


def disconnect():
    with LOCK:
        try:
            saved = _load()
        except TencentError:
            saved = {}
        _save({k:saved[k] for k in ('client_id','redirect_uri') if k in saved})
        PENDING.clear()


def _unpack(body):
    if body.get('error'):
        error = body['error']
        code = error.get('code', '') if isinstance(error, dict) else str(error)
        if str(code) in ('400006', 'invalid_token'):
            raise TencentError('腾讯文档授权已失效，请重新连接。')
        if str(code) == '400007':
            raise TencentError('腾讯文档账户暂不支持此接口，请在腾讯文档检查账户权限。')
        raise TencentError('腾讯文档操作未完成，错误码：' + str(code))
    result = body.get('result', body)
    if not isinstance(result, dict):
        raise TencentError('腾讯文档接口返回格式无法识别。')
    if result.get('isError'):
        raise TencentError('腾讯文档拒绝了本次操作，请核对编辑权限和工作表。')
    if isinstance(result.get('structuredContent'), dict):
        result = result['structuredContent']
    elif 'content' in result:
        blocks = [item.get('text', '') for item in result['content'] if item.get('type') == 'text']
        try:
            result = json.loads('\n'.join(blocks))
        except (ValueError, TypeError) as exc:
            raise TencentError('腾讯文档接口未返回可核验的数据。') from exc
    if not isinstance(result, dict):
        raise TencentError('腾讯文档接口返回格式无法识别。')
    if result.get('error') or any(str(result.get(k, 0)) not in ('0', 'None', '') for k in ('ret', 'retcode', 'code')):
        raise TencentError('腾讯文档操作未完成，请核对文档权限。')
    if isinstance(result.get('data'), dict):
        return result['data']
    return result


class SheetClient:
    def __init__(self, file_url):
        parsed = urlsplit(file_url)
        if parsed.scheme != 'https' or parsed.hostname != 'docs.qq.com' or not parsed.path.startswith('/sheet/'):
            raise TencentError('请填写腾讯文档在线表格链接。')
        self.file_url = file_url

    def call(self, name, arguments, write=False):
        token = access_token()
        payload = dict(jsonrpc='2.0', id=uuid.uuid4().hex, method='tools/call',
                       params=dict(name=name, arguments=dict(file_url=self.file_url, **arguments)))
        read_retries=0
        refreshed=False
        # A read may be repeated safely. A write with an unknown result is never resent here.
        for attempt in range(4):
            try:
                response = requests.post(SHEET_MCP, json=payload,
                    headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json, text/event-stream'},
                    timeout=(10, 60), allow_redirects=False)
                if response.status_code==401:
                    body={}
                elif 'text/event-stream' in response.headers.get('Content-Type', ''):
                    messages = [json.loads(line[5:].strip()) for line in response.text.splitlines()
                                if line.startswith('data:') and line[5:].strip() != '[DONE]']
                    body = next((m for m in messages if m.get('id') == payload['id']), {})
                else:
                    body = response.json()
            except (requests.RequestException, ValueError) as exc:
                if not write and read_retries<2:
                    time.sleep((0.3,1.0)[read_retries]);read_retries+=1
                    continue
                error = WriteUncertain if write else TransientTencentError
                raise error('腾讯文档响应中断，写入结果需要核对。' if write else '读取腾讯文档失败，请稍后重试。') from exc
            rpc_error = body.get('error') if isinstance(body, dict) else None
            auth_error = response.status_code == 401 or isinstance(rpc_error, dict) and str(rpc_error.get('code')) in ('400006','invalid_token')
            if auth_error and not refreshed:
                token = access_token(force_refresh=True)
                refreshed=True
                continue
            if not write and (response.status_code in (408,429) or response.status_code>=500) and read_retries<2:
                time.sleep((0.3,1.0)[read_retries]);read_retries+=1
                continue
            if response.status_code >= 500 and write:
                raise WriteUncertain('腾讯文档服务返回异常，写入结果需要核对。')
            if not write and (response.status_code in (408,429) or response.status_code>=500):
                raise TransientTencentError('腾讯文档暂时无法读取，HTTP '+str(response.status_code)+'。')
            if response.status_code != 200 or not isinstance(body, dict):
                raise TencentError('腾讯文档接口请求失败，HTTP ' + str(response.status_code))
            try:
                return _unpack(body)
            except TencentError as exc:
                if write:
                    raise WriteUncertain(str(exc) + ' 重试前将核对原行。') from exc
                raise
        raise TransientTencentError('腾讯文档请求暂未完成，进度已保存，请稍后继续；仅在提示授权失效时重新连接。')

    def sheets(self):
        return self.call('get_sheet_info', {})

    def cells(self, sheet_id, start_row, end_row, start_col, end_col):
        return self.call('get_cell_data', dict(sheet_id=sheet_id, start_row=start_row, end_row=end_row,
                         start_col=start_col, end_col=end_col, return_csv=False, include_formula=True))

    def write_row(self, sheet_id, row, values):
        cells = [dict(row=row, col=col, value_type='STRING', string_value=value)
                 for col, value in enumerate(values) if value]
        if len(json.dumps(cells, ensure_ascii=False).encode()) > 900000:
            raise TencentError('待填内容超过单次写入上限，未上传。')
        return self.call('set_range_value', dict(sheet_id=sheet_id, values=cells), write=True)
