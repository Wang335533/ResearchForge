"""Route the Nuoda gateway directly without changing other providers' proxies."""
from urllib.parse import urlsplit

import requests


class ModelConnectionError(RuntimeError):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def use_direct_connection(endpoint):
    url = urlsplit(endpoint)
    return url.scheme == 'https' and url.hostname == 'api.nuoda.vip'


def post_completion(endpoint, *, headers, json):
    """Send once; never replay a possibly accepted generation request."""
    direct = use_direct_connection(endpoint)
    route = 'Nuoda 直连接口' if direct else '模型接口'
    options = dict(headers=headers, json=json, timeout=(20, 1800), allow_redirects=False)
    try:
        if direct:
            # An empty proxies dict still inherits macOS/environment proxies.
            # Keep this session private to one call (research/export can overlap).
            with requests.Session() as connection:
                connection.trust_env = False
                return connection.post(endpoint, **options)
        return requests.post(endpoint, **options)
    except requests.exceptions.ProxyError as exc:
        raise ModelConnectionError(
            '模型接口的代理连接中断，未取得完整响应。请检查代理后继续；已完成结果已保存，本次未自动重发。',
            'api_proxy_error') from exc
    except requests.exceptions.SSLError as exc:
        raise ModelConnectionError(
            f'{route}的安全连接校验失败。请检查系统时间与网络证书后继续；已完成结果已保存，本次未自动重发。',
            'api_tls_error') from exc
    except requests.exceptions.ConnectTimeout as exc:
        raise ModelConnectionError(
            f'{route}连接超时（20 秒内未建立连接）。请在网络恢复后继续；已完成结果已保存，本次未自动重发。',
            'api_connect_timeout') from exc
    except requests.exceptions.Timeout as exc:
        raise ModelConnectionError(
            f'{route}等待响应超时，未取得完整结果。请稍后继续；已完成结果已保存，本次未自动重发。',
            'api_response_timeout') from exc
    except requests.exceptions.RequestException as exc:
        raise ModelConnectionError(
            f'{route}连接中断，未取得完整响应。请在网络恢复后继续；已完成结果已保存，本次未自动重发。',
            'api_connection_error') from exc
