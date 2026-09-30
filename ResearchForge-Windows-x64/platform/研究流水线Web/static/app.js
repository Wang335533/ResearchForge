/* Presentation state only. Research data and execution live in the task API. */
const S = {
  stageModels: {}, currentModels: {}, topic: '', count: 20, selectionCount: 5,
  ideas: [], top5: [], lit: {}, proposals: [], literatureChecks: {}, running: false,
  health: null, retrieval: null, views: {}, tasks: [], drafting: false,
};
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = name => `<svg class="icon" aria-hidden="true"><use href="/ui/icons.svg#i-${name}"/></svg>`;
const transition = (update, type) => (window.RF?.transition ? window.RF.transition(update, type) : update());
function clearLegacyBrowserSettings() {
  for (const name of ['localStorage', 'sessionStorage']) {
    try { window[name].removeItem('rf_key'); } catch { /* Storage is optional. */ }
  }
}
clearLegacyBrowserSettings();
window.addEventListener('pageshow', clearLegacyBrowserSettings);

function toast(message, type = '') {
  const el = $('toast'); el.textContent = message; el.className = type;
  // Re-showing restarts the entrance animation so repeated notices stay noticeable.
  el.hidden = true; void el.offsetWidth; el.hidden = false;
  clearTimeout(el._timer); el._timer = setTimeout(() => { el.hidden = true; }, 4500);
}
function clog(message, type = 'info') {
  const body = $('cbody');
  if (!body.querySelector('.cline')) body.replaceChildren();
  const row = document.createElement('div'); row.className = 'cline ' + type;
  row.innerHTML = `<span class="ts">${new Date().toLocaleTimeString()}</span><span class="tx">${esc(message)}</span>`;
  body.appendChild(row);
  while (body.children.length > 400) body.firstChild.remove();
}
function findIdea(item) {
  const id = String(item.id ?? '').trim();
  return S.ideas.find(x => String(x.id) === id)
    || S.ideas.find(x => (x.title || '').trim().toLowerCase() === id.toLowerCase());
}
function viewState() {
  const key = S.taskId || 'draft';
  if (!S.views[key]) {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem('rf_view_' + key) || '{}'); } catch { /* Optional. */ }
    S.views[key] = {stage: 1, manual: false, expanded: {}, scroll: {}, ...saved};
    if (![1,2,3,4,5].includes(S.views[key].stage)) S.views[key].stage = 1;
    S.views[key].expanded ||= {}; S.views[key].scroll ||= {};
  }
  return S.views[key];
}
function saveView(captureScroll = false) {
  if (!S.taskId) return;
  const view = viewState();
  if (captureScroll) view.scroll[view.stage] = window.scrollY;
  try { localStorage.setItem('rf_view_' + S.taskId, JSON.stringify(view)); } catch { /* Optional. */ }
}
function selectStage(stage, manual = true, restoreScroll = true) {
  const view = viewState();
  if (manual) saveView(true);
  const changed = view.stage !== stage || $('sec-' + stage).hidden;
  view.stage = stage;
  if (manual) view.manual = true;
  document.querySelectorAll('.step').forEach(tab => {
    const selected = Number(tab.dataset.step) === stage;
    tab.classList.toggle('selected', selected); tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  const showPanel = () => { for (let n=1; n<=5; n++) $('sec-' + n).hidden = n !== stage; };
  if (manual && changed) transition(showPanel, 'rf-stage'); else showPanel();
  saveView();
  if (restoreScroll) requestAnimationFrame(() => window.scrollTo({top: view.scroll[stage] || 0, behavior:'instant'}));
}
function setStep(n, state, sub) {
  const node = $('tab-' + n);
  for (const cls of ['done','running','error']) node.classList.toggle(cls, cls === state);
  $('sub-' + n).textContent = sub || '';
}
function setStage(n, state, text) {
  const el = $('s' + n + '-status'); el.className = 'status ' + state;
  el.innerHTML = (state === 'running' ? '<span class="spin" aria-hidden="true"></span>' : '') + esc(text);
}
function resetAll() {
  if (typeof resetReportExport === 'function') resetReportExport();
  S.ideas=[]; S.top5=[]; S.lit={}; S.proposals=[]; S.literatureChecks={};
  for (let n=1; n<=4; n++) {
    const body = $('s' + n + '-body'); body.replaceChildren(); delete body.dataset.signature;
    setStage(n, 'idle', '等待开始'); setStep(n, '', '');
  }
  $('export-menu').classList.add('hide');
  $('cbody').innerHTML = '<p class="muted">暂无运行记录</p>';
  delete $('cbody').dataset.signature;
  const logTitle = $('log-dialog').querySelector('h2');
  logTitle.textContent = '运行记录'; logTitle.title = '';
  $('task-date').title = '';
}
function showResearch(show) {
  const apply = () => { $('sec-config').classList.toggle('hide', show); $('research-view').classList.toggle('hide', !show); };
  // Only a real switch between composing and reading animates; checkpoint polls repaint silently.
  if (S.booted && $('research-view').classList.contains('hide') === show) transition(apply); else apply();
  $('breadcrumb-title').textContent = show ? compactTopic(S.task?.topic) || '读取研究' : '新建研究';
}
function compactTopic(topic) {
  const text = String(topic || '').trim();
  if (Array.from(text).length <= 48 && !text.includes('\n')) return text;
  const first = text.split(/[。\n]/)[0];
  const chars = Array.from(first);
  return chars.length > 36 ? chars.slice(0, 36).join('') + '…' : first;
}
function setConnection(message, error = false, detail = '') {
  $('task-connection').textContent = message;
  $('task-connection').classList.toggle('error', error);
  $('task-connection').title = detail;
}
function bindExpansions(container) {
  const view = viewState();
  container.querySelectorAll('details[data-view-key]').forEach(details => {
    details.open = !!view.expanded[details.dataset.viewKey];
    details.addEventListener('toggle', () => {
      view.expanded[details.dataset.viewKey] = details.open; saveView();
    });
  });
}
function setSidebar(open) {
  const mobile = window.innerWidth <= 860;
  if (!open && mobile && $('sidebar').contains(document.activeElement)) $('sidebar-toggle').focus();
  document.body.classList.toggle('sidebar-open', open);
  $('sidebar-shade').hidden = !open; $('sidebar-toggle').setAttribute('aria-expanded', String(open));
  $('sidebar').toggleAttribute('inert', mobile && !open);
  $('sidebar').setAttribute('aria-hidden', String(mobile && !open));
  document.querySelector('.workspace').toggleAttribute('inert', mobile && open);
  if (open) $('btn-new').focus();
}
setSidebar(false);
window.matchMedia('(max-width: 860px)').addEventListener('change', () => setSidebar(false));
let sidebarViewportWidth = window.innerWidth;
window.addEventListener('resize', () => {
  if (window.innerWidth !== sidebarViewportWidth) { sidebarViewportWidth = window.innerWidth; setSidebar(false); }
});
$('sidebar-toggle').onclick = () => setSidebar(!document.body.classList.contains('sidebar-open'));
$('sidebar-shade').onclick = () => { setSidebar(false); $('sidebar-toggle').focus(); };
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && document.body.classList.contains('sidebar-open')) {
    setSidebar(false); $('sidebar-toggle').focus();
  }
});
document.querySelectorAll('.step').forEach(tab => {
  tab.onclick = () => selectStage(Number(tab.dataset.step));
  tab.onkeydown = e => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    let target;
    if (e.key === 'ArrowRight') target = Number(tab.dataset.step) % 5 + 1;
    if (e.key === 'ArrowLeft') target = (Number(tab.dataset.step) + 3) % 5 + 1;
    if (e.key === 'Home') target = 1;
    if (e.key === 'End') target = 5;
    if (target) { e.preventDefault(); selectStage(target); $('tab-' + target).focus(); }
  };
});
window.addEventListener('pagehide', () => saveView(true));
document.querySelectorAll('[data-close]').forEach(button => button.onclick = () => $(button.dataset.close).close());
$('btn-settings').onclick = $('btn-service').onclick = () => { setSidebar(false); $('settings-dialog').showModal(); };
$('btn-logs').onclick = () => { setSidebar(false); $('log-dialog').showModal(); };
document.addEventListener('click', e => { if (!$('export-menu').contains(e.target)) $('export-menu').open = false; });
function boundedCount(value, fallback) {
  const n = Number(value ?? fallback);
  return Number.isFinite(n) ? Math.max(0, Math.min(100, Math.trunc(n))) : fallback;
}
function restoreCounts(saved) {
  S.count = boundedCount(saved.count, 20); S.selectionCount = boundedCount(saved.selection_count, 5);
  $('cnt-val').value = S.count; $('select-val').value = S.selectionCount;
}
function selectionTarget() { return Math.min(S.count, S.selectionCount); }
function changeCount(field, delta) {
  S[field] = boundedCount(S[field] + delta, field === 'count' ? 20 : 5);
  $(field === 'count' ? 'cnt-val' : 'select-val').value = S[field];
  syncRunButton(); saveResearchDraft();
}
for (const [field, prefix] of [['count','cnt'],['selectionCount','select']]) {
  $(prefix + '-minus').onclick = () => changeCount(field, -1);
  $(prefix + '-plus').onclick = () => changeCount(field, 1);
  const input = $(prefix + '-val');
  input.addEventListener('input', () => {
    if (input.value !== '' && input.checkValidity()) { S[field] = Number(input.value); saveResearchDraft(); }
    syncRunButton();
  });
  input.addEventListener('change', () => {
    S[field] = boundedCount(input.value === '' ? S[field] : input.value, S[field]);
    input.value = S[field]; syncRunButton(); saveResearchDraft();
  });
}
$('topic').addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); runPipeline(); }
});

function modelLabel(model) {
  return `${model.label} · ${model.model} · ${model.thinking === 'enabled' ? '思考模式' : `温度 ${model.temperature}`}`;
}
function renderModelConfig() {
  for (const stage of ['gen','judge','opt']) {
    const model = S.currentModels[stage]; $('model-' + stage).textContent = model ? modelLabel(model) : '暂不可用';
  }
  syncRunButton();
}
async function refreshHealth() {
  const response = await fetch('/api/health', {cache:'no-store'});
  if (!response.ok) throw new Error('服务状态不可用');
  S.health = await response.json(); S.currentModels = S.health.stage_models || {};
  S.stageModels = S.task?.stage_models || S.currentModels; renderModelConfig();
  refreshApiReadiness();
}
const PREPARATION_LABELS = {idle:'等待准备', starting:'开始准备', ollama:'检查本地模型', downloading:'下载模型', importing:'导入文献', embedding:'准备文献索引', snapshot:'整理检索索引', ready:'已就绪', error:'准备失败'};
async function pollPreparation() {
  try {
    const response = await fetch('/api/retrieval/status', {cache:'no-store'});
    if (!response.ok) throw new Error('状态查询失败');
    const old = S.retrieval; S.retrieval = await response.json(); const state = S.retrieval;
    const label = PREPARATION_LABELS[state.phase] || state.phase;
    $('prep-message').textContent = label + (state.ready && state.language_quota ? ` · 每个构想中文 ${state.language_quota.zh} 篇 / 英文 ${state.language_quota.en} 篇` : ''); $('prep-error').textContent = state.error || '';
    const languages = state.audit?.languages || {};
    const bilingualCounts = [languages.zh ? `中文 ${languages.zh.toLocaleString()} 篇` : '', languages.en ? `英文 ${languages.en.toLocaleString()} 篇` : ''].filter(Boolean).join(' · ');
    $('prep-counts').textContent = `${(state.vectors_ready || 0).toLocaleString()} / ${(state.papers || 0).toLocaleString()} 篇${bilingualCounts ? ' · ' + bilingualCounts : ''} · ${state.model}`;
    let progress = (state.coverage || 0) * 100;
    if (state.phase === 'downloading') {
      progress = state.download_total ? 100 * state.download_completed / state.download_total : 0;
      $('prep-counts').textContent = `${state.model} · ${(state.download_completed / 1048576 || 0).toFixed(1)} / ${(state.download_total / 1048576 || 0).toFixed(1)} MB`;
    }
    $('prep-progress').value = state.ready ? 100 : progress;
    S.retrievalProgress = state.ready ? 100 : progress;
    $('btn-prepare').classList.toggle('hide', !['idle','error'].includes(state.phase));
    $('service-label').textContent = state.ready ? '文献库已就绪' : PREPARING.has(state.phase) ? label : state.error ? '文献库准备失败' : '文献库未准备';
    $('service-dot').className = 'status-dot ' + (state.ready ? 'ready' : state.error ? 'error' : PREPARING.has(state.phase) ? 'running' : '');
    if (old?.phase !== state.phase) clog('文献库：' + label, state.error ? 'err' : 'info');
    if (state.ready && (!old?.ready || !['gen','judge','opt'].every(s => S.currentModels[s]))) await refreshHealth();
    $('run-hint').textContent = retrievalHint();
    $('run-hint').className = state.error ? 'error' : '';
  } catch (error) {
    S.retrieval = {...S.retrieval, ready:false};
    $('prep-error').textContent = '无法连接服务：' + error.message;
    $('service-label').textContent = '连接中断'; $('service-dot').className = 'status-dot error';
    $('run-hint').textContent = '正在重新连接服务'; $('run-hint').className = 'error';
  } finally {
    syncRunButton();
    // A single polling chain: an explicit refresh replaces the pending one.
    clearTimeout(S.prepTimer);
    S.prepTimer = setTimeout(pollPreparation, S.retrieval?.ready || !PREPARING.has(S.retrieval?.phase) ? 5000 : 1500);
  }
}
async function prepareRetrieval(button) {
  button.disabled = true;
  try {
    const data = await taskRequest('/api/retrieval/prepare', {});
    if (data?.started === false) toast(data.retrieval?.message || '摘要库尚未配置', 'err');
    else toast('开始准备文献库');
    pollPreparation();
  } catch (error) { toast(error.message, 'err'); }
  finally { button.disabled = false; }
}
$('btn-prepare').onclick = () => prepareRetrieval($('btn-prepare'));

/* Readiness: what the first research run still needs, with the fix one click away. */
const PREPARING = new Set(['starting','ollama','downloading','importing','embedding','snapshot']);
function retrievalHint() {
  const state = S.retrieval;
  if (!state) return '正在连接研究服务…';
  if (state.ready) return '进度自动保存';
  if (state.error) return '文献库准备失败';
  if (PREPARING.has(state.phase)) return '文献库准备中，就绪后可筛选与检索';
  return '文献库未就绪';
}
function renderReadiness() {
  const item = $('ready-corpus'), state = S.retrieval;
  if (!item) return;
  let mode = 'loading', text = '正在检查…', action = '';
  if (state) {
    const languages = state.audit?.languages || {};
    const split = [languages.zh ? `中文 ${languages.zh.toLocaleString()}` : '', languages.en ? `英文 ${languages.en.toLocaleString()}` : ''].filter(Boolean).join(' / ');
    if (state.ready) { mode = 'ok'; text = `已就绪 · ${(state.papers || 0).toLocaleString()} 篇${split ? '（' + split + '）' : ''}`; }
    else if (state.error) { mode = 'error'; text = state.error; action = '重新准备'; }
    else if (PREPARING.has(state.phase)) { mode = 'busy'; text = `${PREPARATION_LABELS[state.phase] || state.phase} · ${Math.round(S.retrievalProgress || 0)}%`; }
    else { mode = 'warn'; text = '尚未准备'; action = '准备检索'; }
  }
  item.dataset.state = mode;
  item.style.setProperty('--progress', (mode === 'busy' ? S.retrievalProgress || 0 : mode === 'ok' ? 100 : 0) + '%');
  $('ready-corpus-text').textContent = text; $('ready-corpus-text').title = state?.message || '';
  $('ready-corpus-action').hidden = !action; $('ready-corpus-action').textContent = action;
  $('ready-ideas-only').hidden = !state || state.ready || S.selectionCount === 0 || PREPARING.has(state.phase);
}
$('ready-corpus-action').onclick = () => prepareRetrieval($('ready-corpus-action'));
$('ready-ideas-only').onclick = () => {
  S.selectionCount = 0; $('select-val').value = 0; syncRunButton(); saveResearchDraft();
  toast('已切换为只生成构想'); $('topic').focus();
};
async function refreshApiReadiness() {
  const item = $('ready-api');
  if (!item) return;
  try {
    const response = await fetch('/papers/api/settings', {cache:'no-store'});
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const settings = await response.json();
    const used = [...new Set(['gen','judge','opt'].map(stage => S.currentModels[stage]?.provider).filter(Boolean))];
    const providers = used.length ? used.map(id => settings.providers.find(p => p.id === id)).filter(Boolean)
      : settings.providers.filter(p => p.id === settings.default_provider);
    const missing = providers.filter(p => !p.has_key);
    item.dataset.state = !providers.length ? 'warn' : missing.length ? 'warn' : 'ok';
    $('ready-api-text').textContent = !providers.length ? '尚未配置接口'
      : missing.length ? `${missing.map(p => p.name).join('、')} · 未填写密钥`
      : providers.map(p => `${p.name} · ${p.model}`).join('；');
    $('ready-api-action').hidden = !(missing.length || !providers.length);
  } catch {
    item.dataset.state = 'warn'; $('ready-api-text').textContent = '无法读取接口配置'; $('ready-api-action').hidden = false;
  }
}
window.addEventListener('focus', () => { if (!$('sec-config').classList.contains('hide')) refreshApiReadiness(); });
