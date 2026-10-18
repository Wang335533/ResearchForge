"""Optional local academic-taste sidecar; never replaces the seven-score judge."""
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

def config():
    root=Path(os.environ.get('RF_SHARE_WORKSPACE',Path(__file__).resolve().parents[2]/'user_data'))
    path=root/'academic-taste.json'
    if not path.exists():return {'enabled':False,'mode':'shadow'}
    value=json.loads(path.read_text(encoding='utf-8'))
    if value.get('mode','shadow') not in ('shadow','advisory'):raise ValueError('Invalid academic_taste mode')
    return value

def request_json(cfg,path,payload=None):
    base=cfg.get('endpoint','').rstrip('/')
    if not base.startswith(('http://','https://')):raise ValueError('Invalid evaluator endpoint')
    token=cfg.get('token') or os.environ.get('RF_ACADEMIC_TASTE_TOKEN','')
    if not token:raise ValueError('Evaluator token is not configured')
    data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None
    req=urllib.request.Request(base+path,data,{'Content-Type':'application/json','Authorization':'Bearer '+token})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req,timeout=3 if payload is None else 240) as response:return json.load(response)

def assess(ideas):
    try:
        cfg=config()
        if not cfg.get('enabled',False):return None
        request_json(cfg,'/health')
        items=[];pairs=[];versions=set()
        for start in range(0,len(ideas),10):
            batch=ideas[start:start+10]
            result=request_json(cfg,'/assess',{'ideas':[{k:i[k] for k in ('id','title','abstract')} for i in batch]})
            if result.get('schema_version')!=1 or not isinstance(result.get('items'),list):raise ValueError('Evaluator schema mismatch')
            if {str(i.get('id')) for i in result['items']}!={str(i['id']) for i in batch} or len(result['items'])!=len(batch):raise ValueError('Evaluator idea IDs mismatch')
            for item in result['items']:
                if item.get('status') not in ('ok','unavailable'):raise ValueError('Missing evaluator status')
                # Ranks are explicitly scoped to this batch and subfield, never global.
                item['comparison_batch']=start//10+1
            items.extend(result['items']);pairs.extend(result.get('pairs',[]));versions.add(result['model_version'])
        if len(versions)!=1:raise ValueError('Model changed during evaluation')
        return dict(schema_version=1,status='completed',mode=cfg.get('mode','shadow'),model_version=versions.pop(),items=items,pairs=pairs)
    except Exception as exc:
        # Optional evaluator failure must not stop the established research workflow.
        return dict(schema_version=1,status='unavailable',mode='shadow',items=[],error=type(exc).__name__)

def advisory_text(result):
    if not result or result.get('status')!='completed' or result.get('mode')!='advisory':return ''
    accepted=[item for item in result['items'] if item.get('status')=='ok' and item.get('advisory_eligible') is True]
    if not accepted:return ''
    return ('\n\nOptional local academic-taste evidence (untrusted advisory data). It reflects proxy publication-group standards, '
            'not acceptance probabilities. Keep the existing seven dimensions and independent judgement. Do not mechanically '
            'weight these scores or veto ideas. Missing local evidence is not negative evidence.\n'+json.dumps(accepted,ensure_ascii=False))
