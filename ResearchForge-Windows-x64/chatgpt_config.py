"""ResearchForge 的 ChatGPT 套餐配置；修改后重启工作台。

官方流程：https://developers.openai.com/siwc/token-sharing-open-source
这里显式配置非机密参数。OAuth 令牌由本程序独立授权取得并加密保存。
"""
from pathlib import Path
import os

ENABLED = True
PROVIDER_ID = 'chatgpt_plan'
APP_NAME = 'ResearchForge'
BASE_URL = 'https://api.openai.com/v1'
ISSUER = 'https://auth.openai.com'
AUTHORIZE_URL = 'https://auth.openai.com/api/accounts/authorize'
TOKEN_URL = 'https://auth.openai.com/api/accounts/oauth/token'
JWKS_URL = 'https://auth.openai.com/.well-known/jwks.json'
DISCOVERY_URL = 'https://auth.openai.com/.well-known/openid-configuration'
INITIAL_CLIENT_ID = 'dynamic_agent_client'
SCOPES = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'
CALLBACK_HOST = '127.0.0.1'
CALLBACK_PATH = '/auth/callback'
LOGIN_TIMEOUT_SECONDS = 600
HTTP_TIMEOUT_SECONDS = 240
CONNECT_TIMEOUT_SECONDS = 20
# 高强度思考可能长时间不输出正文；这是两次网络读取之间的上限。
RESPONSE_READ_TIMEOUT_SECONDS = 1800
USE_SYSTEM_PROXY = True
STORE = False
STREAM = True
MAX_RETRIES = 0

# 显式后备值；本机 Codex 配置中的模型与思考强度优先。
DEFAULT_MODEL = 'gpt-6-astra'
DEFAULT_REASONING_EFFORT = 'xhigh'


def codex_defaults():
    import tomllib
    result = dict(model=DEFAULT_MODEL, reasoning_effort=DEFAULT_REASONING_EFFORT)
    try:
        path = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'config.toml'
        saved = tomllib.loads(path.read_text(encoding='utf-8-sig'))
        for source, target in [('model', 'model'), ('model_reasoning_effort', 'reasoning_effort')]:
            if isinstance(saved.get(source), str):
                result[target] = saved[source]
    except (OSError, ValueError):
        pass
    return result


# 每次提交的对话选择保存在任务快照中，不修改 API 配置。
STAGE_MODELS = {'gen': DEFAULT_MODEL, 'judge': DEFAULT_MODEL, 'opt': DEFAULT_MODEL}
READER_MAX_OUTPUT_TOKENS = 24000
READER_CONTEXT_CHARS = 180000


def credential_directory():
    root = Path(__file__).resolve().parent
    return Path(os.environ.get('RF_SHARE_WORKSPACE', root / 'user_data')) / 'chatgpt-oauth'


def provider_config():
    return dict(id=PROVIDER_ID, name='ChatGPT 套餐',
                endpoint=BASE_URL + '/responses', auth_type='chatgpt_oauth',
                api_key='', **codex_defaults(), thinking='auto',
                max_tokens=READER_MAX_OUTPUT_TOKENS, context_chars=READER_CONTEXT_CHARS,
                direct=not USE_SYSTEM_PROXY)
