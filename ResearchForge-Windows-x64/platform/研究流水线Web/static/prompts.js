const PM = {open:false, authed:false, prompts:[], activeKey:null, drafts:{}, loading:false, saving:false};
function pmShow() {
  if (PM.open) return;
  PM.open = true; setSidebar(false);
  $('pm-modal').showModal();
  const url = new URL(location.href); url.searchParams.set('prompts','1'); history.replaceState(null,'',url);
  pmProbe();
}
function pmHide() { $('pm-modal').close(); }
function pmMsg(text, type = '') { $('pm-msg').textContent = text; $('pm-msg').className = 'msg ' + type; }
async function pmRequest(path, body) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(path, {method:body === undefined ? 'GET' : 'POST', credentials:'same-origin', cache:'no-store',
      headers:body === undefined ? {} : {'Content-Type':'application/json'},
      body:body === undefined ? undefined : JSON.stringify(body), signal:controller.signal});
    const data = await response.json();
    if (!response.ok) { const error = new Error(data.error || '请求失败，请重试'); error.status = response.status; throw error; }
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('连接超时，请重试');
    if (error instanceof TypeError) throw new Error('无法连接后端，请检查服务是否运行');
    throw error;
  } finally { clearTimeout(timer); }
}
async function pmProbe() {
  PM.loading = true; $('pm-foot').classList.add('hide');
  $('pm-authbadge').textContent = '正在连接…';
  $('pm-body').textContent = '正在读取登录状态…';
  try {
    const auth = await pmRequest('/api/admin/auth');
    if (auth.authed) await pmAuthed(auth.username); else renderLogin();
  } catch (error) { renderLogin(error.message); }
  finally { PM.loading = false; }
}
function renderLogin(error = '') {
  PM.authed = false;
  $('pm-modal').classList.add('login-mode');
  $('pm-authbadge').textContent = '未登录';
  $('pm-foot').classList.add('hide');
  $('pm-body').innerHTML = `<div class="pm-login"><h3>管理员登录</h3><p class="muted">登录后可编辑各阶段提示词。</p>
    <form id="pm-login-form"><div class="field"><label for="pm-user">用户名</label><input type="text" id="pm-user" autocomplete="username" value="admin" required></div>
    <div class="field"><label for="pm-pass">密码</label><input type="password" id="pm-pass" autocomplete="current-password" required></div>
    <label class="pm-remember"><input type="checkbox" id="pm-remember" checked>保持登录 7 天</label>
    <p class="pm-login-error" id="pm-login-error" role="alert"></p><button type="submit" class="btn btn-primary" id="pm-login-btn">登录并管理提示词</button></form></div>`;
  $('pm-login-error').textContent = error;
  $('pm-login-form').onsubmit = event => { event.preventDefault(); pmLogin(); };
  if (PM.open) $('pm-pass').focus();
}
async function pmLogin() {
  const button = $('pm-login-btn');
  if (button.disabled) return;
  button.disabled = true; button.textContent = '正在登录…'; $('pm-login-error').textContent = '';
  try {
    const auth = await pmRequest('/api/admin/login', {username:$('pm-user').value.trim(), password:$('pm-pass').value, remember:$('pm-remember').checked});
    await pmAuthed(auth.username);
  } catch (error) {
    $('pm-login-error').textContent = error.message;
    $('pm-pass').focus(); $('pm-pass').select();
  } finally { button.disabled = false; button.textContent = '登录并管理提示词'; }
}
async function pmAuthed(username) {
  PM.authed = true;
  $('pm-modal').classList.remove('login-mode');
  $('pm-authbadge').textContent = username ? username + ' · 已登录' : '已登录';
  await pmLoad();
}
async function pmLoad() {
  try {
    const data = await pmRequest('/api/prompts');
    PM.prompts = data.prompts || [];
    renderEditor();
  } catch (error) {
    $('pm-foot').classList.add('hide');
    $('pm-body').innerHTML = '<p class="error-text" id="pm-load-error"></p><button class="btn btn-ghost" id="pm-retry">重新读取</button>';
    $('pm-load-error').textContent = error.message; $('pm-retry').onclick = pmProbe;
  }
}
function pmDirty(key = PM.activeKey) { return Object.hasOwn(PM.drafts,key); }
function renderEditor() {
  $('pm-foot').classList.remove('hide');
  const selected = PM.prompts.find(prompt => prompt.key === PM.activeKey) || PM.prompts[0];
  PM.activeKey = selected?.key || null;
  $('pm-save').disabled = !selected || PM.saving;
  $('pm-body').innerHTML = `<div class="pm-tabs" id="pm-tabs">${PM.prompts.map(prompt =>
    `<button class="pm-tab${prompt.key === PM.activeKey ? ' active' : ''}" data-k="${esc(prompt.key)}">${esc(prompt.description)}</button>`).join('')}</div>
    <div class="pm-form"><label for="pm-ta" id="pm-editor-label"></label><textarea id="pm-ta" spellcheck="false" rows="18"></textarea></div>`;
  document.querySelectorAll('.pm-tab').forEach(button => button.onclick = () => pmSwitch(button.dataset.k));
  $('pm-ta').addEventListener('input', () => {
    const original = PM.prompts.find(prompt => prompt.key === PM.activeKey);
    if ($('pm-ta').value === original?.content) delete PM.drafts[PM.activeKey]; else PM.drafts[PM.activeKey] = $('pm-ta').value;
    pmMsg(pmDirty() ? '有未保存的修改' : '');
  });
  if (selected) pmSwitch(PM.activeKey); else pmMsg('没有可编辑的提示词','err');
}
function pmSwitch(key) {
  const prompt = PM.prompts.find(item => item.key === key);
  if (!prompt) return;
  PM.activeKey = key;
  document.querySelectorAll('.pm-tab').forEach(button => button.classList.toggle('active',button.dataset.k === key));
  $('pm-editor-label').textContent = prompt.description;
  $('pm-ta').value = PM.drafts[key] ?? prompt.content;
  pmMsg(pmDirty() ? '有未保存的修改' : '');
}
async function pmSave() {
  if (!PM.activeKey || PM.saving) return;
  const key = PM.activeKey, content = $('pm-ta').value;
  PM.drafts[key] = content; PM.saving = true;
  $('pm-save').disabled = true; $('pm-logout').disabled = true; pmMsg('正在保存…');
  try {
    await pmRequest('/api/prompts',{key,content});
    const prompt = PM.prompts.find(item => item.key === key); if (prompt) prompt.content = content;
    if (PM.drafts[key] === content) delete PM.drafts[key];
    pmMsg(pmDirty() ? '有未保存的修改' : '已保存',pmDirty() ? '' : 'ok');
  } catch (error) {
    if (error.status === 401) renderLogin('登录已过期，重新登录后可继续编辑。未保存的修改仍保留在本页。');
    else pmMsg('保存失败：' + error.message,'err');
  } finally { PM.saving = false; $('pm-save').disabled = false; $('pm-logout').disabled = false; }
}
async function pmLogout() {
  const button = $('pm-logout'); button.disabled = true;
  try { await pmRequest('/api/admin/logout',{}); renderLogin(); }
  catch (error) { pmMsg('退出失败：' + error.message,'err'); }
  finally { button.disabled = false; }
}
$('btn-prompts-nav').onclick = pmShow;
$('btn-prompts').onclick = () => { $('settings-dialog').close(); pmShow(); };
$('pm-modal').addEventListener('close', () => {
  PM.open = false;
  const url = new URL(location.href); url.searchParams.delete('prompts'); history.replaceState(null,'',url);
});
$('pm-close').onclick = pmHide;
$('pm-save').onclick = pmSave;
$('pm-logout').onclick = pmLogout;
if (new URLSearchParams(location.search).get('prompts') === '1') pmShow();
