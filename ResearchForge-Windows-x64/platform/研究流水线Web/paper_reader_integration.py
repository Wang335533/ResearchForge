"""Mount paper reading in the same local process and share provider credentials."""
import importlib
import importlib.util
import os
from pathlib import Path
import sys

from flask import redirect
from werkzeug.middleware.dispatcher import DispatcherMiddleware


def install(app, llm_config, reader_root=None, task_loader=None):
    if 'paper_reader' in app.extensions:
        return app.extensions['paper_reader']
    root = Path(reader_root or os.environ.get('RF_PAPER_READER_ROOT') or
                Path(__file__).resolve().parents[1] / 'paper_reader').resolve()
    # Reuse the existing reading module's isolated dependencies without upgrading
    # or shadowing ResearchForge's already installed runtime packages.
    site = root / '.venv/lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'
    if site.is_dir() and str(site) not in sys.path:
        sys.path.append(str(site))
    name = 'researchforge_papers'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, root / '__init__.py', submodule_search_locations=[str(root)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[name] = package
        spec.loader.exec_module(package)
    reader = importlib.import_module(name + '.app')
    if task_loader:reader.app.config['RESEARCH_TASK_LOADER']=task_loader
    config = importlib.import_module(name + '.configuration')

    with config.LOCK:
        saved = config.settings()
        known = {p['id'] for p in saved['providers']}
        for pid, p in llm_config.PROVIDERS.items():
            if pid not in known:
                stage = next((s for s in llm_config.STAGE_MODELS.values() if s['provider']==pid), {})
                saved['providers'].append(dict(id=pid, name=p['label'], endpoint=p['endpoint'], api_key=p['api_key'],
                    model=stage.get('model',''), thinking=stage.get('thinking','auto'), max_tokens=24000,
                    context_chars=180000 if pid=='deepseek' else 90000, direct=pid=='nuoda'))
        if len(known) != len(saved['providers']):
            config.save_settings(saved)

    original_stage = llm_config.stage_config
    original_public = llm_config.public_stage_config
    original_redact = llm_config.redact_secrets

    def connection(provider):
        item = next((p for p in config.settings()['providers'] if p['id']==provider), None)
        if item is None:
            return dict(llm_config.PROVIDERS[provider])
        return dict(label=item['name'], endpoint=item['endpoint'], api_key=item['api_key'])

    def stage_config(stage):
        result = original_stage(stage)
        result['api_key'] = connection(result['provider'])['api_key']
        return result

    def public_stages():
        result = original_public()
        for value in result.values():
            shared = connection(value['provider'])
            value.update(label=shared['label'], endpoint=shared['endpoint'])
        return result

    llm_config.connection_config = connection
    llm_config.stage_config = stage_config
    llm_config.public_stage_config = public_stages
    llm_config.redact_secrets = lambda text: config.redact(original_redact(text))
    app.add_url_rule('/papers', 'paper_reader_redirect', lambda: redirect('/papers/'))
    app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {'/papers': reader.app})
    app.extensions['paper_reader'] = reader
    reader.memory_jobs.start()
    return reader
