import json
import time

import requests


class GenerationError(RuntimeError):
    pass


class Stopped(RuntimeError):
    pass


def complete(provider, system, user, on_update=None, should_stop=lambda: False, max_tokens=None):
    if not provider.get('api_key'):
        raise GenerationError('该接口尚未配置 API 密钥，请到“API 设置”填写。')
    body = dict(model=provider['model'], messages=[dict(role='system', content=system), dict(role='user', content=user)],
                max_tokens=max_tokens or provider['max_tokens'], stream=True)
    if provider.get('thinking') in ('enabled', 'disabled'):
        body['thinking'] = {'type': provider['thinking']}
    content, reasoning_count, usage, reason, last = '', 0, {}, None, 0
    session = requests.Session()
    session.trust_env = not provider.get('direct', False)
    try:
        with session.post(provider['endpoint'], headers={'Authorization': 'Bearer ' + provider['api_key'],
                          'Content-Type': 'application/json'}, json=body, stream=True,
                          timeout=(20, 240), allow_redirects=False) as response:
            if response.status_code != 200:
                hints = {401: '密钥无效或已过期', 402: '账户余额不足', 403: '接口拒绝访问',
                         404: '接口地址或模型不存在', 429: '请求过多或额度受限',
                         400: '模型不接受当前参数或输入超出上下文，请检查模型、思考模式及输入/输出预算'}
                raise GenerationError(f"接口返回 HTTP {response.status_code}：{hints.get(response.status_code, '服务暂时不可用，请检查配置或稍后手动重试')}。")
            # A few compatible providers ignore stream=True and return a normal response.
            if 'application/json' in response.headers.get('Content-Type', ''):
                data = response.json()
                choice = (data.get('choices') or [{}])[0]
                content = (choice.get('message') or {}).get('content') or ''
                reason, usage = choice.get('finish_reason'), data.get('usage', {})
            else:
                response.encoding = 'utf-8'
                for line in response.iter_lines(chunk_size=1, decode_unicode=True):
                    if should_stop():
                        raise Stopped('已停止，已返回的内容已保存。')
                    if not line or not line.startswith('data:'):
                        continue
                    payload = line[5:].strip()
                    if payload == '[DONE]':
                        break
                    try:
                        event = json.loads(payload)
                    except ValueError as exc:
                        raise GenerationError('接口返回了无法解析的数据；已返回的内容已保存。') from exc
                    if event.get('error'):
                        raise GenerationError('接口在生成中返回错误；已返回内容已保存，请检查模型服务。')
                    if event.get('usage'):
                        usage = event['usage']
                    for choice in event.get('choices', []):
                        if choice.get('index', 0) != 0:
                            continue
                        delta = choice.get('delta') or {}
                        addition = delta.get('content') or ''
                        if not isinstance(addition, str):
                            raise GenerationError('该模型返回的正文不是文本，请使用文本对话模型。')
                        content += addition
                        reasoning_count += len(delta.get('reasoning_content') or '')
                        reason = choice.get('finish_reason') or reason
                    if on_update and time.monotonic() - last > 1.5:
                        on_update(content, reasoning_count, usage)
                        last = time.monotonic()
        if should_stop():
            raise Stopped('已停止，已返回的内容已保存。')
        if reason == 'length':
            raise GenerationError('输出达到模型额度上限，报告尚未完成。已保存部分内容；请提高输出额度后重新分析。')
        if reason != 'stop':
            raise GenerationError('接口未确认正文完整结束，已保存部分内容。请检查服务后手动重试。')
        if not content.strip():
            raise GenerationError('模型没有返回正文。请增加输出额度，或调整思考模式后重试。')
        return content.strip(), usage
    except requests.Timeout as exc:
        raise GenerationError('接口等待超时，已返回内容已保存。为避免重复计费，没有自动重新发送。') from exc
    except requests.RequestException as exc:
        raise GenerationError('网络连接中断，已返回内容已保存。为避免重复计费，没有自动重新发送。') from exc
    finally:
        session.close()
        if on_update:
            on_update(content, reasoning_count, usage)
