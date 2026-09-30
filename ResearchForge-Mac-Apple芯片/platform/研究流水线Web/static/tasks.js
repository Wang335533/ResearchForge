/* Durable jobs: submit quickly, poll checkpoints, retain the reader's place. */
const TASK_LABELS = {queued:'排队中', running:'进行中', stopping:'正在停止', stopped:'已停止', failed:'需要处理', interrupted:'运行中断', completed:'已完成'};
const TASK_ACTIVE = new Set(['queued','running','stopping']);
const TASK_PAUSED = new Set(['stopped','failed','interrupted']);
Object.assign(S, {taskId:null, task:null, revision:0, submitting:false, recovering:true});
let taskTimer = null, taskListTimer = null, taskPollSerial = 0, taskNavigation = 0;

function taskStorage(key, value) {
  try {
    if (arguments.length === 1) return localStorage.getItem(key);
    if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value);
  } catch { /* The server and URL still restore saved research. */ }
  return null;
}
function pendingTask() { try { return JSON.parse(taskStorage('rf_pending_task') || 'null'); } catch { return null; } }
function draftResearch() { try { return JSON.parse(taskStorage('rf_research_draft') || 'null') || {topic:'',count:20}; } catch { return {topic:'',count:20}; } }
function saveResearchDraft(topic = $('topic').value, count = S.count) {
  taskStorage('rf_research_draft', JSON.stringify({topic,count,selection_count:S.selectionCount}));
}
$('topic').addEventListener('input', () => saveResearchDraft());
function syncRunButton() {
  const locked = S.recovering || S.submitting || S.running;
  const target = selectionTarget();
  const invalid = ['cnt-val','select-val'].some(id => $(id).value === '' || !$(id).checkValidity());
  $('btn-run').disabled = locked || invalid || (S.count > 0 && !S.currentModels.gen) || (target > 0 && (!S.retrieval?.ready || !['judge','opt'].every(stage => S.currentModels[stage])));
  $('btn-run').innerHTML = `<span>${S.submitting ? '正在提交…' : S.count === 0 ? '保存空任务' : '开始研究'}</span>${icon('arrow')}`;
  $('count-hint').textContent = S.count === 0 ? '不生成构想，不调用模型。' : target === 0 ? `只生成 ${S.count} 个构想，不做筛选与检索。` : `生成 ${S.count} 个构想，筛选 ${target} 个做文献判断，通过者形成提案。`;
  if (S.health && S.retrieval) {
    const blocked = target > 0 && !S.retrieval.ready;
    $('run-hint').textContent = !blocked ? (S.concurrency ? `最多 ${S.concurrency.max_parallel_tasks} 项并行 · 进度自动保存` : '进度自动保存') : retrievalHint();
    $('run-hint').className = blocked && S.retrieval.error ? 'error' : '';
  }
  renderReadiness();
  $('btn-stop').classList.toggle('hide', !S.running);
  $('btn-stop').disabled = S.submitting || S.task?.status === 'stopping';
  $('btn-stop').textContent = S.task?.status === 'stopping' ? '正在停止…' : '停止';
  $('btn-stop').title = '停止当前研究的新请求，已发出的调用返回后保存结果；其他研究继续运行';
  $('btn-resume').classList.toggle('hide', !TASK_PAUSED.has(S.task?.status));
  $('btn-resume').disabled = S.submitting || S.running || S.recovering;
  $('btn-new').disabled = S.submitting;
  $('topic').disabled = locked;
  for (const [prefix, value] of [['cnt',S.count],['select',S.selectionCount]]) {
    $(prefix + '-minus').disabled = locked || value <= 0;
    $(prefix + '-plus').disabled = locked || value >= 100;
    $(prefix + '-val').disabled = locked;
  }
  document.querySelectorAll('.task-item').forEach(button => button.disabled = S.submitting);
}
async function taskRequest(path, body) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(path, {method:body === undefined ? 'GET' : 'POST',
      headers:body === undefined ? {} : {'Content-Type':'application/json'},
      body:body === undefined ? undefined : JSON.stringify(body), signal:controller.signal, cache:'no-store'});
    if (response.status === 204) return null;
    const data = await response.json();
    if (!response.ok) { const error = new Error(data.error || `HTTP ${response.status}`); error.status = response.status; throw error; }
    return data;
  } finally { clearTimeout(timer); }
}
function rememberTask(id) {
  S.taskId = id; S.drafting = false; taskStorage('rf_active_task', id);
  const url = new URL(location.href); url.searchParams.set('task', id); url.searchParams.delete('new'); history.replaceState(null, '', url);
}
function taskRunNumber(task) {
  if (task.run_number) return task.run_number;
  // Also supports the previous backend while an existing run finishes.
  const siblings = S.tasks.filter(item => item.topic === task.topic)
    .sort((a,b) => a.created_at - b.created_at || a.id.localeCompare(b.id));
  return Math.max(1, siblings.findIndex(item => item.id === task.id) + 1);
}
function taskRunMark(task) {
  // The short mark is only for display; storage and routing always use the full UUID.
  return 'RF-' + String(task.id).slice(0,8).toUpperCase();
}
function renderTaskContext() {
  const task = S.task;
  if (!task) return;
  const listed = S.tasks.find(item => item.id === task.id) || task;
  $('task-date').textContent = `第 ${taskRunNumber(listed)} 次 · 运行标记 ${taskRunMark(task)} · ` + new Date(task.created_at * 1000).toLocaleString('zh-CN',{year:'numeric',month:'long',day:'numeric',hour:'2-digit',minute:'2-digit'});
  $('task-date').title = '完整任务编号：' + task.id;
  const logTitle = $('log-dialog').querySelector('h2');
  logTitle.textContent = '运行记录 · ' + taskRunMark(task);
  logTitle.title = '完整任务编号：' + task.id;
  if (task.status === 'queued') {
    const ahead = S.tasks.filter(item => item.id !== task.id && item.status === 'queued' &&
      (item.created_at < task.created_at || (item.created_at === task.created_at && item.id < task.id))).length;
    $('task-status').textContent = `排队中${ahead ? `，前面还有 ${ahead} 项` : ''}`;
  }
}
function renderHistory() {
  const query = $('history-search').value.trim().toLocaleLowerCase();
  const tasks = S.tasks.filter(task => [task.topic, task.id, taskRunMark(task)].some(value => String(value).toLocaleLowerCase().includes(query)));
  const root = $('task-list'), signature = JSON.stringify([S.taskId, query, tasks]);
  if (root.dataset.signature === signature) return;
  root.dataset.signature = signature;
  const scroll = root.scrollTop;
  const focused = root.contains(document.activeElement) ? document.activeElement.dataset.taskId : null;
  const today = new Date().toDateString(); let lastGroup = '';
  root.replaceChildren();
  for (const task of tasks) {
    const date = new Date(task.created_at * 1000), group = date.toDateString() === today ? '今天' : '更早';
    if (lastGroup !== group) { const label = document.createElement('p'); label.className = 'history-group'; label.textContent = group; root.appendChild(label); lastGroup = group; }
    const button = document.createElement('button'); button.className = 'task-item' + (task.id === S.taskId ? ' active' : '');
    button.dataset.taskId = task.id; button.disabled = S.submitting;
    if (task.id === S.taskId) button.setAttribute('aria-current', 'page');
    button.title = `${task.topic}\n运行标记：${taskRunMark(task)}\n完整任务编号：${task.id}`;
    button.innerHTML = `<strong>${esc(compactTopic(task.topic))}</strong><small><span class="status-dot ${esc(task.status)}"></span>${esc(TASK_LABELS[task.status] || task.status)} · 第 ${taskRunNumber(task)} 次<time datetime="${date.toISOString()}">${date.toLocaleDateString('zh-CN',{month:'numeric',day:'numeric'})}</time></small>`;
    button.onclick = () => { selectTask(task.id); setSidebar(false); };
    root.appendChild(button);
  }
  if (!tasks.length) root.innerHTML = `<p class="sidebar-empty">${query ? '没有找到相关研究' : '还没有研究任务'}</p>`;
  root.scrollTop = scroll;
  if (focused) [...root.querySelectorAll('.task-item')].find(button => button.dataset.taskId === focused)?.focus({preventScroll:true});
}
function updateHistoryTask(task) {
  const summary = Object.fromEntries(['id','topic','count','selection_count','status','stage','message','created_at','updated_at','revision'].map(key => [key,task[key]]));
  const index = S.tasks.findIndex(item => item.id === task.id);
  if (index >= 0) S.tasks[index] = {...S.tasks[index], ...summary}; else S.tasks.unshift(summary);
  renderHistory();
}
function renderTask(task) {
  if (task.id !== S.taskId || task.revision < S.revision) return;
  const first = !S.task;
  S.recovering = false;
  if (pendingTask()?.request_id === task.id) taskStorage('rf_pending_task', null);
  S.task = task; S.revision = task.revision; S.topic = task.topic; restoreCounts(task);
  S.ideas = task.ideas; S.top5 = task.top5; S.lit = task.lit; S.proposals = task.proposals;
  S.literatureChecks = task.literature_checks || {};
  S.running = TASK_ACTIVE.has(task.status); S.stageModels = task.stage_models || S.currentModels;
  $('topic').value = task.topic;
  showResearch(true); renderModelConfig();
  $('task-title').textContent = compactTopic(task.topic);
  $('task-topic').classList.toggle('hide', compactTopic(task.topic) === task.topic);
  if (first) {
    $('task-topic-text').textContent = task.topic;
    $('task-topic').open = !!viewState().expanded['research-topic'];
  }
  $('task-badge').className = 'status ' + task.status; $('task-badge').textContent = TASK_LABELS[task.status] || task.status;
  const dropped = Object.values(S.literatureChecks).filter(c => c.decision === 'drop').length;
  $('task-summary').textContent = `${S.ideas.length} 个构想 · ${S.proposals.length} 份提案${dropped ? ' · '+dropped+' 个方向未进入提案' : ''}`;
  setConnection(S.workerError ? '执行器异常' : '已保存', !!S.workerError, S.workerError || '研究进度已保存，关闭页面后仍可继续查看');
  renderResearchContents();
  const total = S.top5.length, target = selectionTarget(), targets = [S.count,target,target,target];
  const counts = [S.ideas.length,total,Object.keys(S.lit).length,S.proposals.length+dropped];
  for (let n=1; n<=4; n++) {
    const done = counts[n-1] === targets[n-1], current = n === task.stage;
    const paused = current && TASK_PAUSED.has(task.status), active = current && S.running;
    const state = done ? 'done' : paused ? 'error' : active ? 'running' : 'idle';
    const label = targets[n-1] === 0 ? '已跳过' : done ? '已完成' : paused ? TASK_LABELS[task.status] : active ? TASK_LABELS[task.status] : '等待开始';
    const progress = n === 2 && target > 0 && !done && (task.stage === 2 || reviewedIdeaCount(task) > 0)
      ? `已评审 ${reviewedIdeaCount(task)}/${S.count}` : `${counts[n-1]}/${targets[n-1]}`;
    setStage(n, state, label); setStep(n, state, progress);
  }
  const message = $('task-status');
  message.classList.toggle('hide', task.status === 'completed');
  message.classList.toggle('error', TASK_PAUSED.has(task.status) && task.status !== 'stopped');
  if (TASK_PAUSED.has(task.status)) message.textContent = task.error?.message || '已停止，已完成的结果已保留。';
  else if (task.status === 'stopping') message.textContent = `正在停止，等待 ${task.inflight_units?.length ?? (task.inflight ? 1 : 0)} 个进行中的请求返回`;
  else if (task.status === 'queued') message.textContent = '排队中';
  else message.textContent = task.message || ['正在生成研究构想','正在评审与排名','正在检索相关文献','正在完善研究提案'][task.stage-1] || '';
  $('export-menu').classList.toggle('hide', !S.ideas.length);
  const view = viewState(), desired = view.manual ? view.stage : task.status === 'completed' ? (target ? 4 : 1) : task.stage;
  if (first || desired !== view.stage) selectStage(desired, false, first);
  const eventSignature = JSON.stringify(task.events);
  if ($('cbody').dataset.signature !== eventSignature) {
    const body = $('cbody'), bottom = body.scrollHeight - body.scrollTop - body.clientHeight < 35;
    const scroll = body.scrollTop; body.dataset.signature = eventSignature;
    body.innerHTML = task.events.map(event => `<div class="cline"><span class="ts">${esc(new Date(event.at*1000).toLocaleTimeString())}</span><span class="tx">${esc(event.message)}</span></div>`).join('');
    body.scrollTop = bottom ? body.scrollHeight : scroll;
  }
  updateHistoryTask(task); renderTaskContext(); syncRunButton();
  if (typeof renderReportExport === 'function') renderReportExport(task);
}
function renderIdeaCSV(csv) {
  if (!csv) return;
  $('idea-csv-status').textContent = csv.error || (csv.updated_at ? `idea.csv · 已保存 ${csv.rows} 个构想 · ${new Date(csv.updated_at*1000).toLocaleTimeString()}` : '新构想将自动保存到 idea.csv');
  $('idea-csv-status').style.color = csv.error ? 'var(--red)' : '';
  $('idea-csv-status').title = csv.path || '';
}
async function refreshTasks() {
  const data = await taskRequest('/api/tasks');
  S.tasks = data.tasks.map(task => {
    const old = S.tasks.find(item => item.id === task.id);
    return old && old.revision > task.revision ? old : task;
  });
  S.workerError = data.worker_error || '';
  S.concurrency = data.concurrency || null;
  const concurrency = $('concurrency-status');
  if (concurrency) concurrency.textContent = S.concurrency ?
    `上限 ${S.concurrency.max_parallel_tasks} 项研究 · ${S.concurrency.max_api_calls} 个并发请求（单项 ${S.concurrency.max_task_calls} 个）。当前 ${S.concurrency.running_tasks} 项运行，${S.concurrency.queued_tasks} 项排队。` : '尚未载入并行配置';
  $('worker-error').textContent = S.workerError ? '后台执行器异常：' + S.workerError + '。重启后端后可继续已保存的研究。' : '';
  if (S.workerError) setConnection('执行器异常', true, S.workerError);
  renderHistory(); renderTaskContext(); renderIdeaCSV(data.idea_csv); return S.tasks;
}
async function pollTask() {
  clearTimeout(taskTimer);
  const id = S.taskId;
  if (!id) return;
  const serial = ++taskPollSerial;
  const current = () => S.taskId === id && taskPollSerial === serial;
  try {
    const data = await taskRequest(`/api/tasks/${encodeURIComponent(id)}?after_revision=${S.revision}`);
    if (!current()) return;
    if (data) renderTask(data.task);
    else setConnection(S.workerError ? '执行器异常' : '已保存', !!S.workerError, S.workerError);
  } catch (error) {
    if (current()) {
      setConnection(error.status === 404 ? '未找到任务' : '正在重新连接', true, error.status === 404 ? error.message : '连接恢复后将自动更新，已保存的结果会保留');
      if (error.status === 404) {
        S.recovering = false; S.running = false;
        const pending = pendingTask();
        if (pending?.request_id === id) {
          $('topic').value = pending.topic; restoreCounts(pending); showResearch(false);
          $('run-hint').textContent = '提交未确认，可再次提交同一研究';
        } else { $('task-title').textContent = '未找到这项研究'; $('task-badge').textContent = '不可用'; $('task-status').textContent = '可以从研究历史选择其他任务，或新建研究。'; }
        syncRunButton();
      }
    }
  } finally { if (current()) taskTimer = setTimeout(pollTask, S.running ? 1500 : 4000); }
}
async function selectTask(id) {
  if (!id || S.submitting) return;
  if (id === S.taskId && S.task) { setSidebar(false); return; }
  if (!$('sec-config').classList.contains('hide') && !S.recovering) saveResearchDraft();
  taskNavigation++; taskPollSerial++;
  saveView(true); clearTimeout(taskTimer); rememberTask(id); S.task = null; S.revision = 0;
  S.running = false; S.recovering = true; resetAll(); showResearch(true);
  $('task-title').textContent = '正在读取研究…'; $('task-date').textContent = '';
  $('task-topic').classList.add('hide');
  $('task-summary').textContent = ''; $('task-badge').textContent = '读取中'; $('task-badge').className = 'status idle';
  $('task-status').className = 'task-message'; $('task-status').textContent = '正在恢复已保存的结果';
  setConnection('连接中'); renderHistory(); syncRunButton(); await pollTask();
}
function newResearch() {
  if (S.submitting) return;
  if (!$('sec-config').classList.contains('hide') && !S.recovering) saveResearchDraft();
  taskNavigation++; taskPollSerial++;
  saveView(true); clearTimeout(taskTimer); S.taskId = null; S.task = null; S.revision = 0;
  S.running = false; S.recovering = false; S.drafting = true; S.stageModels = S.currentModels;
  taskStorage('rf_active_task', null);
  const url = new URL(location.href); url.searchParams.delete('task'); url.searchParams.set('new','1'); history.replaceState(null, '', url);
  resetAll(); showResearch(false); setSidebar(false); setConnection('');
  const draft = draftResearch(); $('topic').value = draft.topic; restoreCounts(draft);
  renderModelConfig(); renderHistory(); syncRunButton();
  window.scrollTo({top:0,behavior:'instant'}); $('topic').focus();
}
async function runPipeline() {
  if ($('btn-run').disabled) return;
  const topic = $('topic').value.trim(), count = S.count, selection_count = S.selectionCount;
  if (!topic) { toast('请填写研究主题', 'err'); $('topic').focus(); return; }
  if (topic.length > 4000) { toast('研究主题最多 4000 字符', 'err'); return; }
  const pending = pendingTask();
  const body = pending && pending.topic === topic && pending.count === count && (pending.selection_count ?? 5) === selection_count ? pending : {request_id:crypto.randomUUID(),topic,count,selection_count};
  taskStorage('rf_pending_task', JSON.stringify(body)); saveView(true);
  clearTimeout(taskTimer); rememberTask(body.request_id); S.task = null; S.revision = 0; resetAll();
  S.submitting = true; syncRunButton(); setConnection('正在保存');
  try {
    const data = await taskRequest('/api/tasks', body);
    if (S.taskId === body.request_id) renderTask(data.task);
    taskStorage('rf_pending_task', null); saveResearchDraft('', count); toast(count === 0 ? '空任务已保存' : '研究已开始，可随时离开');
    refreshTasks().catch(() => {});
  } catch (error) {
    toast(error.status ? error.message : '正在确认提交结果，请稍候', error.status ? 'err' : '');
  } finally { S.submitting = false; syncRunButton(); pollTask(); }
}
async function controlTask(action) {
  if (!S.taskId || S.submitting) return;
  const id = S.taskId; S.submitting = true; syncRunButton();
  try {
    const data = await taskRequest(`/api/tasks/${encodeURIComponent(id)}/${action}`, {});
    if (id === S.taskId) renderTask(data.task);
    refreshTasks().catch(() => {});
  } catch (error) { toast(error.message, 'err'); }
  finally { S.submitting = false; syncRunButton(); pollTask(); }
}
function resumeTask() { return controlTask('resume'); }
$('btn-run').onclick = runPipeline;
$('btn-stop').onclick = () => controlTask('stop');
$('btn-resume').onclick = resumeTask;
$('btn-new').onclick = newResearch;
document.querySelector('.brand').onclick = event => { event.preventDefault(); newResearch(); };
$('history-search').oninput = renderHistory;
$('task-topic').addEventListener('toggle', () => {
  if (!S.task) return;
  viewState().expanded['research-topic'] = $('task-topic').open; saveView();
});
$('task-refresh').onclick = async () => {
  $('task-refresh').disabled = true;
  try { await refreshTasks(); }
  catch (error) { toast('暂时无法读取研究历史', 'err'); }
  finally { $('task-refresh').disabled = false; }
};
async function discoverTasks() {
  clearTimeout(taskListTimer);
  try { await refreshTasks(); }
  catch { if (!S.taskId) setConnection('正在重新连接', true); }
  finally { taskListTimer = setTimeout(discoverTasks, 10000); }
}
(async function initTasks() {
  const initialNavigation = taskNavigation;
  pollPreparation(); refreshHealth().catch(error => clog('等待服务连接：' + error.message));
  const url = new URL(location.href);
  S.drafting = url.searchParams.get('new') === '1';
  let selected = S.drafting ? null : url.searchParams.get('task') || taskStorage('rf_active_task');
  try {
    const tasks = await refreshTasks();
    if (!selected && !S.drafting && tasks.length) selected = tasks[0].id;
  } catch { setConnection('正在重新连接', true); }
  if (initialNavigation !== taskNavigation) { S.booted = true; taskListTimer = setTimeout(discoverTasks, 10000); return; }
  if (selected) await selectTask(selected);
  else {
    const saved = (!S.drafting && pendingTask()) || draftResearch();
    $('topic').value = saved.topic; restoreCounts(saved);
    S.recovering = false; syncRunButton();
  }
  S.booted = true;
  taskListTimer = setTimeout(discoverTasks, 10000);
})();
