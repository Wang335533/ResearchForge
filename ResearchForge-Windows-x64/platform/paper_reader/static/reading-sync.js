'use strict';
const BASE = document.querySelector('meta[name="app-base"]')?.content || '';
const $ = id => document.getElementById(id);
const STATUS = {queued:'等待整理',preparing:'整理内容',ready:'待确认上传',retry_wait:'等待自动恢复',upload_queued:'等待上传',checking:'核对腾讯表格',uploading:'上传并回读',synced:'已同步',duplicate:'已有记录',failed:'待处理',uncertain:'待核对写入'};
let state, selectedRecord, authTimer, polling = false, initialized = false, pollFailed = false, pendingPublish = false;
// A report's 填入组会表格 link preselects it; the Tencent sign-in returns here with its outcome.
const ENTRY = new URLSearchParams(location.search);
history.replaceState(null, '', location.pathname);
async function api(path, data) {
  const options = data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-PaperLens':'1'},body:JSON.stringify(data)};
  const response = await fetch(BASE + '/api/reading-sync' + path, options);
  let body;try {body=await response.json();}catch(e){throw new Error('工作台暂时没有响应；已保存的进度不会丢失，恢复连接后会继续显示。');}
  if (!response.ok) throw new Error(body.error || '请求未完成，请重试。');
  return body;
}
function message(id, text='') { $(id).textContent = text; $(id).hidden = !text; }
async function action(button, task, error='page-error') {
  button.disabled = true; message(error);
  try { await task(); } catch(e) { message(error,e.message); } finally { button.disabled = false; }
}
function el(tag, text, cls) { const node=document.createElement(tag); if(text!==undefined) node.textContent=text; if(cls)node.className=cls; return node; }
function sheetLink(target) { const url=new URL(target.file_url); url.searchParams.set('tab',target.sheet_id); return url.href; }
function setSheets(sheets, current) {
  $('sheet-id').replaceChildren(...sheets.map(s=>{const o=el('option',s.name || s.id);o.value=s.id;return o;}));
  $('sheet-id').value=current;
}
function setColumns(headers,excluded=[]) {
  $('column-settings').hidden=!headers.length;
  $('excluded-columns').replaceChildren(...headers.map((h,i)=>{
    const label=el('label');const input=el('input');input.type='checkbox';input.value=i;input.checked=excluded.includes(i);
    input.disabled=['姓名','时间','日期','阅读人','阅读日期'].includes(h.replace(/\s/g,''));
    label.append(input,el('span',h || `第 ${i+1} 列（无表头）`));return label;
  }));
}
function fillSettings(c) {
  $('file-url').value=c.file_url;$('reader').value=c.reader;$('header-row').value=c.header_row;$('enabled').checked=c.enabled;
  setSheets(c.sheet_id?[{id:c.sheet_id,name:c.sheet_name}]:[],c.sheet_id);setColumns(c.headers,c.excluded_columns);
}
function settingsData() {
  return {file_url:$('file-url').value,sheet_id:$('sheet-id').value,sheet_name:$('sheet-id').selectedOptions[0]?.textContent || '',reader:$('reader').value,header_row:Number($('header-row').value),enabled:$('enabled').checked,excluded_columns:[...$('excluded-columns').querySelectorAll('input:checked')].map(e=>Number(e.value))};
}
function render(s) {
  state=s;if(!initialized){fillSettings(s.config);initialized=true;}
  $('connection-state').textContent=s.auth.connected?'腾讯文档已连接':'腾讯文档待授权';$('connection-state').classList.toggle('ok',s.auth.connected);
  const connectLabel=$('connect').querySelector('span'),connectText=s.auth.connected?'重新授权':'连接腾讯文档';if(connectLabel.textContent!==connectText)connectLabel.textContent=connectText;$('disconnect').hidden=!s.auth.connected;
  $('inspect').disabled=!s.auth.connected;$('prepare').disabled=!s.auth.connected;
  $('auto-state').textContent=s.config.enabled?(s.auth.connected?'已开启':'已开启，等待授权'):'已暂停';
  $('target-link').hidden=!s.config.file_url;if(s.config.file_url)$('target-link').href=sheetLink(s.config);
  // The 5-second refresh rebuilds the dropdown and the table only when they changed, so an open
  // dropdown is not closed and the table does not flicker.
  const jobsKey=JSON.stringify(s.jobs.map(j=>[j.id,j.title]));
  if($('job-select').dataset.key!==jobsKey){$('job-select').dataset.key=jobsKey;
  const previous=$('job-select').value;
  $('job-select').replaceChildren(...s.jobs.map(j=>{const option=el('option',j.title);option.value=j.id;return option;}));
  if(s.jobs.some(j=>j.id===previous))$('job-select').value=previous;
  else if(ENTRY.get('job')&&s.jobs.some(j=>j.id===ENTRY.get('job')))$('job-select').value=ENTRY.get('job');
  if(!s.jobs.length){const option=el('option','暂无已完成的精读');option.value='';$('job-select').append(option);}}
  if(!s.jobs.length)$('prepare').disabled=true;
  $('record-count').textContent=`${s.records.length} 条`;$('empty-records').hidden=!!s.records.length;
  const recordsKey=JSON.stringify([s.records,state.config.enabled,s.records.some(r=>r.status==='retry_wait')?Math.floor(Date.now()/1000):0]);
  if($('records').dataset.key===recordsKey)return;
  $('records').dataset.key=recordsKey;
  $('records').replaceChildren(...s.records.map(r=>{
    const tr=el('tr');const title=el('td',undefined,'record-title');const readingLink=el('a',r.title);readingLink.href=BASE+'/#'+encodeURIComponent(r.job_id);readingLink.title='在阅读论文中查看报告';title.append(readingLink,el('small',new Date(r.created*1000).toLocaleString('zh-CN')));
    const status=el('td');status.append(el('span',STATUS[r.status]||r.status,'status-label '+r.status));if(r.stage&&!['synced','duplicate'].includes(r.status))status.append(el('small',progressText(r)));if(r.error)status.append(el('small',r.error));
    const row=el('td',r.target_row===null || r.target_row===undefined?'':`第 ${r.target_row+1} 行`);
    const controls=el('td'),button=el('button','查看','text-btn');button.addEventListener('click',()=>action(button,()=>openPreview(r.id)));controls.append(button);
    tr.append(title,status,row,controls);return tr;
  }));
}
async function refresh() {render(await api(''));}
async function inspect() {
  const saved=await api('/settings',settingsData());
  const info=await api('/inspect',{});setSheets(info.sheets,saved.config.sheet_id);setColumns(info.headers,saved.config.excluded_columns);
  message('settings-state',info.headers.length?`已读取 ${info.headers.length} 列`:'请选择工作表后再次读取表头');await refresh();
}
async function pollAuth(id,interval) {
  try {
    const result=await api('/connect/'+encodeURIComponent(id));
    if(result.status==='connected'){$('auth-pending').hidden=true;await refresh();await inspect();return;}
    if(!['pending','exchanging'].includes(result.status)){message('auth-message',result.status==='expired'?'授权请求已过期，请重新连接。':'授权未完成，请重新连接。');return;}
    authTimer=setTimeout(()=>pollAuth(id,interval),interval*1000);
  }catch(e){message('page-error',e.message);}
}
function renderPreview(r) {
  selectedRecord=r;$('preview-title').textContent=r.title;$('preview-target').textContent=`${r.target.sheet_name || r.target.sheet_id} · ${r.target.reader}`;
  $('preview-state').textContent=(STATUS[r.status]||r.status)+(r.target_row===null?'':` · 第 ${r.target_row+1} 行`);
  message('preview-error',r.error);
  $('preview-progress').textContent=progressText(r);message('preview-quality',(r.quality_notes||[]).join('\n'));
  $('preview-delivery').textContent=r.automatic?'原方式：整理完成后自动上传（受页面自动填报开关控制）':'原方式：整理完成后确认上传';
  const history=r.failure_history||[];$('failure-history').hidden=!history.length;
  $('failure-items').replaceChildren(...history.slice(-6).reverse().map(h=>el('p',`${new Date(h.time*1000).toLocaleString('zh-CN')} · ${h.phase==='prepare'?'内容整理':'腾讯文档'}：${h.error}`)));
  const complete=r.headers.length>0&&r.cells.length===r.headers.length;
  const editable=['ready','failed'].includes(r.status)&&r.target_row===null;
  const visibleCells=r.cells.length?r.cells:(r.partial_cells||[]);
  $('preview-fields').replaceChildren(...visibleCells.map(c=>{const i=c.column;
    const field=el('div',undefined,'preview-field'),label=el('label',`${i+1}. ${c.header || '无表头'}`),input=el('textarea');
    input.id=`cell-${i}`;label.htmlFor=input.id;input.value=c.value;input.readOnly=!complete || !editable || ['姓名','阅读人','汇报人','填报人','学生姓名'].includes(c.header.replace(/\s/g,''));input.maxLength=12000;input.rows=Math.min(12,Math.max(2,Math.ceil(c.value.length/65)));input.dataset.column=i;
    field.append(label,input);
    if(c.quotes?.length){const details=el('details');details.append(el('summary','核对报告依据'));for(const q of c.quotes)details.append(el('blockquote',q));field.append(details);}
    return field;
  }));
  if(!visibleCells.length)$('preview-fields').append(el('p',STATUS[r.status] || '尚无填报内容','muted'));
  $('publish').hidden=!(['ready','failed','uncertain'].includes(r.status)&&complete);
  $('resume').hidden=!(r.status==='failed'&&r.target_row===null&&!complete);
  $('pause-retry').hidden=r.status!=='retry_wait';
  $('publish').textContent=r.target_row===null?'追加到腾讯文档':'核对原行并重试';
  $('regenerate').hidden=!(['ready','failed'].includes(r.status)&&r.target_row===null);
}
function progressText(r){
  if(r.status==='retry_wait'){
    if(r.automatic&&state&&!state.config.enabled)return '自动填报已暂停；重新开启后继续恢复';
    return `${r.stage||'等待恢复'} · 约 ${Math.max(0,Math.ceil((r.next_retry_at||0)-Date.now()/1000))} 秒后继续`;
  }
  if(['upload_queued','checking','uploading','synced'].includes(r.status))return r.stage||'腾讯文档同步';
  if(!r.total_columns)return r.stage||'';
  let text=`已核对 ${r.completed_columns||0}/${r.total_columns} 列`;
  if(r.status==='preparing'){text+=' · '+(r.stage||'整理中');if(r.response_chars)text+=` · 已接收 ${r.response_chars} 字`;else if(r.reasoning_chars)text+=' · 模型正在思考';}
  return text;
}
async function openPreview(id) {pendingPublish=false;renderPreview((await api('/records/'+encodeURIComponent(id))).record);if(!$('preview').open)$('preview').showModal();}
$('settings-form').addEventListener('submit',e=>{e.preventDefault();action(e.submitter,async()=>{await api('/settings',settingsData());message('settings-state','设置已保存');await refresh();});});
$('inspect').addEventListener('click',e=>action(e.currentTarget,inspect));
$('connect').addEventListener('click',e=>action(e.currentTarget,async()=>{
  clearTimeout(authTimer);const auth=await api('/connect',{});$('auth-pending').hidden=false;$('auth-message').textContent='请在新打开的腾讯文档页面完成授权，完成后会回到这里';$('auth-code').textContent=auth.user_code||'';$('auth-code').hidden=!auth.user_code;$('auth-link').href=auth.verification_url;
  window.open(auth.verification_url,'_blank','noopener');
  authTimer=setTimeout(()=>pollAuth(auth.id,auth.interval),auth.interval*1000);
}));
$('disconnect').addEventListener('click',e=>action(e.currentTarget,async()=>{await api('/disconnect',{});clearTimeout(authTimer);$('auth-pending').hidden=true;$('enabled').checked=false;await refresh();}));
$('prepare-form').addEventListener('submit',e=>{e.preventDefault();action(e.submitter,async()=>{const result=await api('/prepare',{job_id:$('job-select').value});await openPreview(result.record.id);await refresh();});});
$('close-preview').addEventListener('click',()=>$('preview').close());
$('publish').addEventListener('click',e=>action(e.currentTarget,async()=>{
  const values=[...$('preview-fields').querySelectorAll('textarea')].map(t=>t.value);
  pendingPublish=true;
  try{
    const result=await api(`/records/${selectedRecord.id}/publish`,{revision:selectedRecord.revision,values});renderPreview(result.record);pendingPublish=false;await refresh();
  }catch(error){
    try{
      const latest=(await api(`/records/${selectedRecord.id}`)).record;
      if(['upload_queued','checking','uploading','synced','duplicate','retry_wait'].includes(latest.status)&&JSON.stringify(latest.cells.map(c=>c.value))===JSON.stringify(values)){
        renderPreview(latest);pendingPublish=false;await refresh();return;
      }
    }catch(ignore){}
    throw error;
  }
},'preview-error'));
$('regenerate').addEventListener('click',e=>action(e.currentTarget,async()=>{const result=await api(`/records/${selectedRecord.id}/regenerate`,{});renderPreview(result.record);await refresh();},'preview-error'));
$('resume').addEventListener('click',e=>action(e.currentTarget,async()=>{const result=await api(`/records/${selectedRecord.id}/resume`,{revision:selectedRecord.revision});renderPreview(result.record);await refresh();},'preview-error'));
$('pause-retry').addEventListener('click',e=>action(e.currentTarget,async()=>{const result=await api(`/records/${selectedRecord.id}/pause-retry`,{});renderPreview(result.record);await refresh();},'preview-error'));
async function tick() {
  if(polling || document.hidden)return;polling=true;
  try {
    await refresh();
    if(pollFailed){message('page-error');pollFailed=false;}
    if($('preview').open && selectedRecord && (pendingPublish||['queued','preparing','upload_queued','checking','uploading','retry_wait'].includes(selectedRecord.status))){
      const latest=(await api('/records/'+selectedRecord.id)).record;
      if(!pendingPublish||!['ready','failed'].includes(latest.status)){renderPreview(latest);pendingPublish=false;}
    }
  }catch(e){pollFailed=true;message('page-error','暂时无法连接工作台；后台进度已保存。'+e.message);}finally{polling=false;}
}
if(ENTRY.get('tencent')==='connected'){const t=$('toast');t.textContent='腾讯文档已连接';t.hidden=false;setTimeout(()=>{t.hidden=true;},3800);}
else if(ENTRY.get('tencent')==='error')message('page-error',ENTRY.get('message')||'腾讯文档授权未完成，请重新连接。');
$('notebook-menu').addEventListener('click',()=>$('sidebar').classList.toggle('open'));
tick();setInterval(tick,5000);
