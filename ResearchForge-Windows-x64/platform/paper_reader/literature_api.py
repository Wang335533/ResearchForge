"""Bibliographic search through the literature website API (optional).

The API is the metadata authority (title, authors, journal, year, DOI, abstract,
attachment filenames); local files are only located afterwards. Connection details
come from an existing manifest (`api_base_url`, `api_key`, `api_mode`), named by
PAPERLENS_LITERATURE_MANIFEST. The key is read when needed and never returned,
logged or sent anywhere except the configured HTTPS origin.
"""
import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TIMEOUT = 20
CACHE_SECONDS = 600
CACHE_LIMIT = 300
LANGUAGES = {'zh': 'chinese', 'en': 'english'}
FIELDS = {'title', 'author', 'doi', 'abstract'}


class ApiError(RuntimeError):
    """A user-facing reason the online search is unavailable (never contains the key)."""
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the credential to a redirect target.


_opener = urllib.request.build_opener(_NoRedirect)
_cache, _cache_lock = {}, threading.Lock()
_state = {'mode': None}


def manifest_path():
    value = os.environ.get('PAPERLENS_LITERATURE_MANIFEST', '').strip()
    return Path(value) if value else None


def configured():
    path = manifest_path()
    return bool(path and path.is_file())


def _config():
    path = manifest_path()
    if not path or not path.is_file():
        raise ApiError('没有配置文献网站接口。')
    values = {}
    for raw in path.read_text(encoding='utf-8-sig').splitlines():
        line = raw.strip()
        if line and not line.startswith('#') and ':' in line:
            key, value = line.split(':', 1)
            values[key.strip()] = value.strip().strip('\'"')
    base = values.get('api_base_url', '').rstrip('/')
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.query or parsed.fragment:
        raise ApiError('文献网站地址必须是不含账号与参数的 HTTPS 地址。')
    if not values.get('api_key'):
        raise ApiError('文献网站接口缺少访问密钥。')
    mode = values.get('api_mode', 'auto')
    if mode not in ('auto', 'admin', 'member'):
        raise ApiError('文献网站接口的 api_mode 只能是 auto、admin 或 member。')
    return base, values['api_key'], mode


def _request(base, key, path, params=None, body=None):
    url = base + path + ('?' + urllib.parse.urlencode(params, doseq=True) if params else '')
    headers = {'Authorization': 'Bearer ' + key, 'Accept': 'application/json', 'User-Agent': 'ResearchForge-library/1'}
    data = None
    if body is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(body).encode('utf-8')
    request = urllib.request.Request(url, headers=headers, data=data)
    for attempt in range(2):
        try:
            with _opener.open(request, timeout=TIMEOUT) as response:
                payload = json.load(response)
            if not isinstance(payload, dict):
                raise ApiError('文献网站返回的数据格式不正确。')
            return payload
        except urllib.error.HTTPError as exc:
            status, delay = exc.code, exc.headers.get('Retry-After', '2')
            exc.close()
            if status in (429, 503) and attempt == 0 and delay.isdigit() and int(delay) <= 5:
                time.sleep(max(1, int(delay)))
                continue
            message = {401: '文献网站访问密钥已失效，请更新接口配置。', 403: '当前密钥没有这项检索权限。',
                       404: '文献网站没有这条记录。', 429: '文献网站检索次数已达上限，请稍后再试。',
                       503: '文献网站繁忙，请稍后再试。'}.get(status, f'文献网站返回错误（HTTP {status}）。')
            raise ApiError(message, status) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ApiError('连不上文献网站，请检查网络或代理。') from None
        except (json.JSONDecodeError, UnicodeError):
            raise ApiError('文献网站返回的数据无法读取。') from None


def _call(route, params=None, body=None):
    base, key, mode = _config()
    modes = [_state['mode']] if mode == 'auto' and _state['mode'] else (['admin', 'member'] if mode == 'auto' else [mode])
    for current in modes:
        prefix = '/api/v1/admin' if current == 'admin' else '/api/v1'
        call_params = dict(params or {})
        if current == 'member':
            call_params.pop('hasPdf', None)  # Only administrators may filter by attached PDFs.
        try:
            result = _request(base, key, prefix + route, call_params or None, body)
            _state['mode'] = current
            return result, current
        except ApiError as exc:
            if not (mode == 'auto' and current == 'admin' and exc.status == 403):
                raise
    raise ApiError('当前密钥没有文献检索权限。', 403)


def _cached(key, compute):
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    value = compute()
    with _cache_lock:
        if len(_cache) >= CACHE_LIMIT:
            for old in sorted(_cache, key=lambda k: _cache[k][0])[:CACHE_LIMIT // 4]:
                _cache.pop(old, None)
        _cache[key] = (now, value)
    return value


def _plain(value):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]*>', ' ', str(value or '')))).strip()


def _people(value):
    if isinstance(value, list):
        value = '; '.join(str(a.get('name', '') if isinstance(a, dict) else a) for a in value)
    return '; '.join(p.strip() for p in re.split(r'[;；]', str(value or '')) if p.strip())


def normalize(item):
    """The fields the library shows, independent of the API's spelling."""
    keywords = item.get('keywordsDisplay') or item.get('keywords') or ''
    if isinstance(keywords, list):
        keywords = '; '.join(map(str, keywords))
    language = str(item.get('language') or '').lower()
    year = item.get('year')
    return dict(
        document_id=str(item.get('documentId') or ''), title=_plain(item.get('title')),
        authors=_people(item.get('authorsDisplay') or item.get('authors')), abstract=_plain(item.get('abstract')),
        keywords='; '.join(k.strip() for k in re.split(r'[|;]', str(keywords)) if k.strip()),
        journal=_plain(item.get('journal')), year=int(year) if str(year or '').isdigit() else None,
        volume=str(item.get('volume') or ''), issue=str(item.get('issue') or ''), pages=str(item.get('pages') or ''),
        doi=str(item.get('doi') or '').strip(), url=str(item.get('articleUrl') or ''),
        language='zh' if language.startswith(('chinese', 'zh')) else 'en' if language.startswith(('english', 'en')) else '',
        document_type=str(item.get('documentType') or ''),
        assets=[dict(filename=str(a.get('filename') or ''), status=str(a.get('mappingStatus') or ''))
                for a in item.get('assets') or [] if isinstance(a, dict) and a.get('filename')])


def search(query, scope='all', language='', year_from=None, year_to=None, page=1, page_size=20, has_pdf=True):
    """One page of bibliographic records; `has_pdf` keeps to records with attached full texts."""
    def compute():
        if scope in FIELDS:
            body = {'conditions': [{'field': scope, 'term': query, 'operator': 'and'}], 'pageSize': page_size,
                    'page': page, 'sort': 'relevance', 'documentTypes': []}
            for key, value in (('languages', [LANGUAGES[language]] if language else None), ('yearFrom', year_from), ('yearTo', year_to)):
                if value is not None:
                    body[key] = value
            raw, mode = _call('/search/advanced', body=body)
            filtered = False
        else:
            params = {'q': query, 'pageSize': page_size, 'page': page, 'sort': 'relevance'}
            for key, value in (('language', LANGUAGES.get(language)), ('yearFrom', year_from), ('yearTo', year_to),
                               ('hasPdf', 'true' if has_pdf else None)):
                if value:
                    params[key] = value
            raw, mode = _call('/search', params=params)
            filtered = has_pdf and mode == 'admin'
        items = [normalize(i) for i in raw.get('items', []) if isinstance(i, dict)]
        total = raw.get('total') if isinstance(raw.get('total'), int) else len(items)
        pages = raw.get('pages') if isinstance(raw.get('pages'), int) else page
        return dict(items=items, total=total, page=page, pages=pages, pdf_filtered=filtered, release=raw.get('releaseId'))
    return _cached(('search', query, scope, language, year_from, year_to, page, page_size, has_pdf), compute)


def document(document_id):
    if not re.fullmatch(r'[A-Za-z0-9_\-:.]{4,120}', str(document_id or '')):
        raise ApiError('无效的文献编号。')
    return _cached(('document', document_id),
                   lambda: normalize(_call('/documents/' + urllib.parse.quote(document_id, safe=''))[0]))
