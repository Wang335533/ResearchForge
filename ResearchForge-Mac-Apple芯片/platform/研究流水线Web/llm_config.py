"""Share edition: no bundled secrets; stage models use the local configuration."""
import json
import os
PROVIDERS={'deepseek':{'label':'DeepSeek 官方','endpoint':'https://api.deepseek.com/chat/completions','api_key':''}}
_defaults={stage:dict(provider='deepseek',model='deepseek-flash',temperature=None,thinking='enabled') for stage in ('gen','judge','opt')}
_overrides=json.loads(os.environ.get('RF_SHARE_STAGES','{}'))
STAGE_MODELS={stage:{**value,**_overrides.get(stage,{})} for stage,value in _defaults.items()}
# Additional provider connections are created in the shared API settings UI.
for config in STAGE_MODELS.values():
    PROVIDERS.setdefault(config['provider'],{'label':config['provider'],'endpoint':'','api_key':''})

def stage_config(stage):
    config=dict(STAGE_MODELS[stage]);config['api_key']=PROVIDERS[config['provider']]['api_key']
    return config

def public_stage_config():
    return {stage:{**config,'label':PROVIDERS[config['provider']]['label'],'endpoint':PROVIDERS[config['provider']]['endpoint']} for stage,config in STAGE_MODELS.items()}

def redact_secrets(message):
    result=str(message)
    for p in PROVIDERS.values():
        if p['api_key']:result=result.replace(p['api_key'],'[REDACTED]')
    return result
