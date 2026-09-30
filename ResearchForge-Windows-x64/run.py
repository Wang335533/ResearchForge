#!/usr/bin/env python3
"""Launch an isolated, empty ResearchForge installation."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


def load_config(path=None):
    path = Path(path) if path else ROOT / 'config.json'
    if not path.is_file():
        path = ROOT / 'config.example.json'
    cfg = json.loads(path.read_text(encoding='utf-8'))
    if type(cfg.get('port',8331)) is not int or not 1024 <= cfg.get('port',8331) <= 65535:
        raise ValueError('port 必须为 1024–65535 的整数。')
    if type(cfg.get('auto_prepare',False)) is not bool:
        raise ValueError('auto_prepare 必须是 true 或 false。')
    if cfg.get('languages',['zh','en']) not in (['zh','en'],['zh'],['en']):
        raise ValueError('languages 必须为 ["zh","en"]、["zh"] 或 ["en"]。')
    return cfg


def resolve_path(value):
    path = Path(value).expanduser()
    return (ROOT / path).resolve() if not path.is_absolute() else path.resolve()


def configure(cfg, workspace_override=None):
    workspace = resolve_path(workspace_override or cfg.get('workspace','user_data'))
    workspace.mkdir(parents=True, exist_ok=True)
    papers = resolve_path(cfg.get('papers','user_data/papers'))
    imports = resolve_path(cfg.get('prompt_import','user_data/prompt-import'))
    corpus = resolve_path(cfg.get('corpus','user_data/corpus.csv'))
    if workspace_override:
        # Tests and portable demo runs cannot read this installation's real input.
        papers,imports,corpus=workspace/'papers',workspace/'prompt-import',workspace/'corpus.csv'
    for folder in (workspace/'research',workspace/'reader',papers,imports):
        folder.mkdir(parents=True,exist_ok=True)
    os.environ.update({
        'RF_SHARE_ROOT':str(ROOT), 'RF_SHARE_WORKSPACE':str(workspace),
        'RF_SHARE_STAGES':json.dumps(cfg.get('stages',{})),
        'RF_PORT':str(cfg.get('port',8331)),
        'RF_TASK_DB':str(workspace/'research/research_tasks.sqlite3'),
        'RF_RESEARCH_DATA':str(workspace/'research'),
        'RF_CORPUS_PATH':str(corpus), 'RF_RETRIEVAL_INDEX':str(workspace/'abstract-index'),
        'RF_PAPER_READER_ROOT':str(ROOT/'platform/paper_reader'),
        'PAPERLENS_DATA_DIR':str(workspace/'reader'),
        'PAPERLENS_LIBRARY_ROOT':str(papers),
        'PAPERLENS_LIBRARY_INDEX':str(workspace/'fulltext-index.sqlite3'),
        'PAPERLENS_PROMPT_SOURCE':str(imports), 'PAPERLENS_NO_IMPORT':'1',
        'RF_AUTO_PREPARE':'1' if cfg.get('auto_prepare',False) else '0',
        'OLLAMA_HOST':cfg.get('ollama_host','http://127.0.0.1:11434'),
        'RF_EMBEDDING_MODEL':cfg.get('embedding_model','qwen3-embedding:0.6b'),
        'RF_RETRIEVAL_LANGUAGES':json.dumps(cfg.get('languages',['zh','en'])),
    })
    # Session identity belongs only to this installation.
    os.environ.pop('RESEARCHFORGE_SECRET',None)
    credential=workspace/'admin-password.txt'
    if not credential.exists():
        import secrets
        with credential.open('x',encoding='utf-8') as stream:
            stream.write(secrets.token_urlsafe(18)+'\n')
        credential.chmod(0o600)
    os.environ['RF_SHARE_ADMIN_PASSWORD']=credential.read_text().strip()
    return workspace


def main():
    parser=argparse.ArgumentParser(description='ResearchForge 分享版：数据与配置独立')
    parser.add_argument('--config',help='配置 JSON 路径')
    parser.add_argument('--workspace',help='指定全新数据目录；隔离全部输入与记录')
    parser.add_argument('--port',type=int)
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    cfg=load_config(args.config)
    if args.port:
        if not 1024 <= args.port <= 65535:parser.error('端口超出范围')
        cfg['port']=args.port
    configure(cfg,args.workspace)
    sys.path.insert(0,str(ROOT/'platform/研究流水线Web'))
    import server
    server.main(['--no-browser'] if args.no_browser else [])


if __name__=='__main__':main()
