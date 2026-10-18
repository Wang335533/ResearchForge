"""Account model catalog and per-model reasoning choices for the Responses route."""
import copy
import json
import os
from pathlib import Path
import threading
import time

import chatgpt_oauth
import chatgpt_config

API_EFFORTS = {'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'}
EFFORT_LABELS = {'none': '无 · None', 'minimal': '最低 · Minimal', 'low': '低 · Low',
                 'medium': '中 · Medium', 'high': '高 · High', 'xhigh': '极高 · Extra High',
                 'max': '最高 · Max', 'ultra': 'Ultra · Codex 多代理模式（此接口不可用）'}
_CACHE = {}
_LOCK = threading.RLock()


def _local_catalog():
    candidates = [Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'models_cache.json',
                  Path(__file__).with_name('codex_models.json')]
    for path in candidates:
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(value.get('models'), list):
                return value['models']
        except (OSError, ValueError):
            continue
    return []


def normalize(models):
    result, seen = [], set()
    for model in models:
        slug = model.get('slug')
        if not isinstance(slug, str) or not slug or slug in seen or model.get('visibility', 'list') != 'list':
            continue
        levels, efforts = [], set()
        for item in model.get('supported_reasoning_levels') or []:
            effort = item.get('effort') if isinstance(item, dict) else item
            if not isinstance(effort, str) or effort in efforts:
                continue
            efforts.add(effort)
            levels.append(dict(effort=effort, label=EFFORT_LABELS.get(effort, effort), enabled=effort in API_EFFORTS))
        default = model.get('default_reasoning_level')
        if default not in {x['effort'] for x in levels if x['enabled']}:
            default = next((x['effort'] for x in levels if x['enabled']), '')
        result.append(dict(slug=slug, display_name=model.get('display_name') or slug,
                           reasoning_levels=levels, default_reasoning_effort=default))
        seen.add(slug)
    return result


def catalog(refresh=False):
    result = _catalog(refresh)
    defaults = chatgpt_config.codex_defaults()
    selected = next((m for m in result['models'] if m['slug'] == defaults['model']), None)
    if selected is None:
        selected = result['models'][0] if result['models'] else None
    if selected:
        allowed = {x['effort'] for x in selected['reasoning_levels'] if x['enabled']}
        defaults = dict(model=selected['slug'], reasoning_effort=defaults['reasoning_effort']
                        if defaults['reasoning_effort'] in allowed else selected['default_reasoning_effort'])
    result['defaults'] = defaults
    return result


def _catalog(refresh=False):
    state = chatgpt_oauth.status()
    if not state['connected']:
        return dict(models=normalize(_local_catalog()), source='codex_local', verified=False,
                    message='登录 ChatGPT 后会显示你账户可用的模型')
    account = state['active']
    with _LOCK:
        cached = _CACHE.get(account)
        if not refresh and cached and time.monotonic() - cached[0] < 300:
            return copy.deepcopy(cached[1])
        models = normalize(chatgpt_oauth.list_models())
        current = chatgpt_oauth.status()
        if not current['connected'] or current['active'] != account:
            raise chatgpt_oauth.OAuthError('同步期间账户已切换，请重新读取模型列表。')
        if not models:
            raise chatgpt_oauth.OAuthError('当前账户没有返回可选模型，请检查套餐权限。')
        result = dict(models=models, source='openai_account', verified=True,
                      message='已同步当前 ChatGPT 账户可用模型。')
        _CACHE[account] = (time.monotonic(), result)
        return copy.deepcopy(result)


def validate_selection(model, effort):
    selected = next((m for m in catalog()['models'] if m['slug'] == model), None)
    if selected is None:
        raise ValueError('所选模型不在当前账户模型目录中，请刷新模型列表。')
    allowed = {item['effort'] for item in selected['reasoning_levels'] if item['enabled']}
    if effort is None:
        effort = selected['default_reasoning_effort']
    if effort == 'ultra':
        raise ValueError('Ultra 是 Codex 多代理模式，当前 Responses 接口不支持；请选择 Low–Max。')
    if effort not in allowed and not (effort == '' and not allowed):
        raise ValueError('所选模型不支持这一思考强度，请重新选择。')
    return dict(model=model, reasoning_effort=effort)
