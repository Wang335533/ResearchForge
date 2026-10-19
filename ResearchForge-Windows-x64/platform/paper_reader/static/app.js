'use strict';
const $ = id => document.getElementById(id);
const appBase = document.querySelector('meta[name="app-base"]')?.content || '';
const appUrl = path => appBase + path;
const state = {settings:null, jobs:[], document:null, job:null, profile:null, prompts:{}, promptTab:'report', busy:false};
const labels = {queued:'排队中', running:'分析中', stopping:'正在停止', completed:'已完成', failed:'未完成', interrupted:'已中断', stopped:'已停止'};
const live = s => ['queued','running','stopping'].includes(s);
let toastTimer, pollTimer;
let readerPicker;
readerPicker = new RFModelPicker($('reader-model-picker'), 'rf-reading-model', () => {if(state.settings)modelChanged();});
const escapeHTML = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = n => Number(n || 0).toLocaleString('zh-CN');
const day = t => new Date(t * 1000).toLocaleDateString('zh-CN');

async function api(path, options={}) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), options.body instanceof FormData ? 120000 : 20000);
  try {
  const headers = {'X-PaperLens':'1', ...options.headers};
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const signal = options.signal ? AbortSignal.any([options.signal, controller.signal]) : controller.signal;
  const response = await fetch(appUrl(path), {...options, headers, signal});
  let data;
  try { data = await response.json(); } catch (error) { if(error.name === 'AbortError')throw error; throw new Error('服务未返回有效响应，请重新打开工作台。'); }
  if (!response.ok) throw new Error(data.error || '操作未完成。');
  return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('请求超时，请刷新记录后重试；精读提交重试不会重复创建任务。');
    throw error;
  } finally {clearTimeout(timer);}
}
const post = (path, body={}) => api(path,{method:'POST', body:JSON.stringify(body)});
function toast(message,action=null){ const el=$('toast'); el.textContent=message; if(action){const b=document.createElement('button');b.type='button';b.textContent=action.label;b.onclick=()=>{el.hidden=true;action.run();};el.append(b);} el.hidden=true; void el.offsetWidth; el.hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>{el.hidden=true;},action?7000:3800); }
let booted=false;
// Swapping between composing and reading animates only after start-up and only on a real change.
function swapView(showReading,apply){ const change=$('reading').hidden===showReading; if(booted&&change&&window.RF?.transition) window.RF.transition(apply); else apply(); }
function error(message){ $('page-error').textContent=message; $('page-error').hidden=!message; }
// Polling re-applies the same state every few seconds; touching only what changed keeps the page still
// (no flicker, no lost hover or selection, no restarted animations).
const put=(id,text)=>{const el=typeof id==='string'?$(id):id;text=String(text??'');if(el.textContent!==text)el.textContent=text;};
const showIf=(id,on)=>{const el=typeof id==='string'?$(id):id;if(el.hidden===!!on)el.hidden=!on;};
const setAttr=(el,name,value)=>{if(el.getAttribute(name)!==value)el.setAttribute(name,value);};
function inline(id,message,bad=false){ $(id).textContent=message; $(id).classList.toggle('error',bad); }
function closeSidebar(){ $('sidebar').classList.remove('open'); }
function showDialog(id){ closeSidebar(); $(id).showModal(); }

async function refreshSettings(keepSelection=true){
  const selected = keepSelection ? $('provider').value : '';
  state.settings = await api('/api/settings');
  $('provider').replaceChildren(...state.settings.providers.map(p=>new Option(p.name,p.id)));
  $('provider').value=state.settings.providers.some(p=>p.id===selected)?selected:state.settings.default_provider;
  modelChanged();
}
function modelChanged(){
  const p=state.settings?.providers.find(p=>p.id===$('provider').value);
  const oauth=p?.auth_type==='chatgpt_oauth';
  $('reader-model-picker').hidden=!oauth;
  $('model-label').hidden=oauth;
  // With a single interface there is nothing to choose; show only what will be used.
  $('provider').closest('.model-picker').classList.toggle('single',(state.settings?.providers.length||0)<=1);
  $('model-label').textContent=p ? p.model+(p.auth_type==='chatgpt_oauth'?' · '+(p.reasoning_effort||'默认')+' 思考':p.thinking==='enabled'?' · 深度思考已启用':'') : '';
  $('api-shortcut').textContent=p ? p.name+(p.has_key?'':p.auth_type==='chatgpt_oauth'?' · 待登录':' · 缺少密钥') : '设置接口';$('api-shortcut').classList.toggle('warn',!!p&&!p.has_key);
  $('run').disabled=!state.document || state.busy || !p?.has_key || (oauth&&!readerPicker?.valid);
  $('run-note').textContent=p&&!p.has_key?(p.auth_type==='chatgpt_oauth'?'先在 API 设置里登录 ChatGPT。':'这个接口还没有密钥，请先在 API 设置里填写。'):!state.document?'先上传或选择一篇论文。':'正文会发送给所选接口生成报告；完成后自动归入文献地图（多一次调用）。';
}
function setArchivedView(on){
  state.showArchived=on;$('history-archived').setAttribute('aria-pressed',String(on));
  $('history-label').textContent=on?'已移出的记录':'精读记录';
}
// Readings belong to a research project; the history shows the current one (boot.js / shell.js).
const projectQuery=(extra='')=>{const q=[extra,window.RFProject?.param()].filter(Boolean).join('&');return q?'?'+q:'';};
async function refreshJobs(){
  let data=await api('/api/jobs'+projectQuery(state.showArchived?'archived=1':''));
  // Once the last removed record is restored, return to the history instead of an empty view.
  if(state.showArchived&&!data.archived_count){setArchivedView(false);data=await api('/api/jobs'+projectQuery());}
  state.jobs=data.jobs;state.archivedCount=data.archived_count||0;
  showIf('history-archived',state.archivedCount||state.showArchived);put('archived-count',state.archivedCount);
  renderHistory();
}
function renderHistory(){
  const needle=$('search').value.trim().toLowerCase();
  const jobs=state.jobs.filter(j=>j.title.toLowerCase().includes(needle));
  put('history-count',state.jobs.length);
  window.ReadingViews?.sync(state.jobs);
  const signature=JSON.stringify([needle,state.showArchived,state.job?.id,jobs.map(j=>[j.id,j.title,j.status,j.created])]);
  if($('history').dataset.signature===signature)return;
  $('history').dataset.signature=signature;
  const mixed=!window.RFProject?.current();
  $('history').innerHTML=jobs.length?jobs.map(j=>`<button class="history-item ${state.job?.id===j.id?'active':''}" data-job="${escapeHTML(j.id)}"><strong>${escapeHTML(j.title)}</strong><small><span class="status-dot ${escapeHTML(j.status)}"></span><span>${labels[j.status]||j.status}</span>${mixed?`<span class="history-project">${escapeHTML(state.projectNames?.[j.project_id]||'')}</span>`:''}<time>${day(j.created)}</time></small></button>`).join(''):`<p class="muted empty-history">${needle?'没有匹配的精读记录。':state.showArchived?'没有移出的记录。':'还没有精读记录。<br>上传第一篇论文就能开始。'}</p>`;
}
function showListView(view){
  state.job=null;window.RFMotion?.progress(false);error('');closeSidebar();
  $('compose').hidden=true;$('reading').hidden=true;window.ReadingViews.show(view);
  $('crumb').textContent=view==='queue'?'待读清单':'已读目录';renderHistory();window.scrollTo({top:0});
}
function newReading(){
  window.ReadingViews?.show(null);
  state.job=null; swapView(false,()=>{$('compose').hidden=false; $('reading').hidden=true;}); $('crumb').textContent='新建精读';
  if(state.projects)fillResearch(false);
  window.RFMotion?.progress(false);
  error(''); closeSidebar(); renderHistory();
}
function resetReading(){
  if(state.busy)return;
  state.document=null; $('research').value='';
  $('document-card').hidden=true; $('drop-zone').hidden=false;
  $('upload-title').textContent='拖入论文，或点击选择'; $('upload-subtitle').textContent='PDF / Markdown / TXT';
  try{sessionStorage.removeItem('paperlens-pending');}catch{}
  location.hash='new'; newReading(); saveDraft(); modelChanged();
  requestAnimationFrame(()=>$('drop-zone').focus({preventScroll:true}));
}
async function selectJob(id){
  try{
    const j=await api('/api/jobs/'+id);
    if(location.hash.slice(1)!==id) return;
    if(state.job?.id!==id)$('show-evidence').checked=false;
    state.job=j; error(''); closeSidebar(); window.ReadingViews?.show(null);
    swapView(true,()=>{$('compose').hidden=true; $('reading').hidden=false; renderJob();}); renderHistory();
  }catch(e){error(e.message);}
}
function renderJob(){
  const j=state.job;if(!j)return;
  window.RFMotion?.progress(true);
  put('crumb',j.title);put('report-title',j.title);
  put('report-date',day(j.created)+' · 论文精读');
  put('report-status',labels[j.status]||j.status);setAttr($('report-status'),'class','badge '+(j.status==='failed'?'failed':''));
  put('report-model',j.snapshot.provider.name+' / '+j.model+(j.snapshot.provider.reasoning_effort?' · '+readerPicker.label(j.snapshot.provider.reasoning_effort):''));
  put('report-basis',j.analysis_basis||'准备读取正文');
  showIf('stop',live(j.status)); $('stop').disabled=j.status==='stopping';
  showIf('archive-job',!live(j.status)); put($('archive-job').querySelector('span'),j.archived?'恢复到记录':'移出记录'); showIf('delete-job',!live(j.status));
  showIf('resume',['failed','interrupted','stopped'].includes(j.status));
  showIf('progress-card',j.status!=='completed');setAttr($('progress-card'),'data-status',j.status);
  put('stage',j.stage);if($('progress').value!==j.progress)$('progress').value=j.progress;put('progress-value',j.progress+'%');
  put('progress-message',j.status==='failed'&&!j.workflow_version&&/输出达到模型额度上限/.test(j.error||'')
    ?'这是旧版本留下的报错。现在超出输出上限时会保存写完的小节并接着写，点“继续分析”即可，不必重新上传。':j.message);
  put('generation-count',live(j.status)?(j.generated_chars ? '已返回 '+num(j.generated_chars)+' 字符':j.reasoning_chars?'模型正在分析…':'等待模型响应…'):'');
  put('report-error',j.error||'');showIf('report-error',j.error);
  const notes=[...(j.warnings||[]),...(j.checks||[])];
  if($('report-warnings').dataset.signature!==JSON.stringify(notes)){
    $('report-warnings').dataset.signature=JSON.stringify(notes);
    $('report-warnings').replaceChildren(...notes.map(s=>{const p=document.createElement('p');p.textContent=s;return p;}));
  }
  const tokens=(j.usage||[]).reduce((n,u)=>n+(u.total_tokens||0),0);
  const interrupted=(j.usage||[]).filter(u=>u._status&&u._status!=='completed').length;
  put('token-usage',tokens?'用量 '+num(tokens)+' tokens'+(interrupted?'（含 '+interrupted+' 次中断或截断的请求）':''):'');
  const available=!!(j.report||j.draft);
  showIf('report-incomplete',available&&j.status!=='completed');
  showIf(document.querySelector('.export-actions'),available);
  setAttr($('export-md'),'href',appUrl('/api/jobs/'+j.id+'/export/md'));setAttr($('export-html'),'href',appUrl('/api/jobs/'+j.id+'/export/html'));
  setAttr($('sync-job'),'href',appUrl('/reading-sync?job='+encodeURIComponent(j.id)));showIf('sync-job',j.status==='completed');
  $('show-evidence').disabled=!j.audit_html;showIf($('show-evidence').closest('label'),j.audit_html);
  setAttr($('memory-card-link'),'href',appUrl('/memory?job='+j.id));showIf('memory-card-link',j.status==='completed');
  showIf('next-steps',j.status==='completed');
  const origin=j.source_research;showIf('origin-link',origin?.task_id&&appBase);
  if(origin?.task_id)setAttr($('origin-link'),'href','/?task='+encodeURIComponent(origin.task_id));
  const content=($('show-evidence').checked&&j.audit_html||j.html) || (!live(j.status)?'<p class="muted">尚未生成报告正文。已完成的分段证据仍保留，可继续分析。</p>':'<div class="waiting"><span class="waiting-orb"><svg class="icon" aria-hidden="true"><use href="/ui/icons.svg#i-book-open-text"/></svg></span>报告生成中，可关闭页面，后台继续。</div>');
  updateReport(content,live(j.status));
}
// The report grows while it is written. Unchanged leading blocks stay in place (with their math already
// rendered and any selection intact); only changed or new blocks are replaced, and they fade in gently.
let reportBlocks=[],reportRaw=null,tocEntries=[];
function updateReport(content,streaming){
  const report=$('report');
  if(reportRaw===content)return;
  const template=document.createElement('template');template.innerHTML=content;
  const nodes=[...template.content.childNodes];
  const blocks=nodes.filter(n=>n.nodeType===1),sources=blocks.map(n=>n.outerHTML);
  let same=0;
  if(!nodes.some(n=>n.nodeType===3&&n.textContent.trim())&&report.children.length===reportBlocks.length)
    while(same<sources.length&&same<reportBlocks.length&&sources[same]===reportBlocks[same])same++;
  else report.replaceChildren();
  while(report.children.length>same)report.lastElementChild.remove();
  const added=same?blocks.slice(same):nodes;
  report.append(...added);reportBlocks=sources;reportRaw=content;
  for(const node of added){
    if(node.nodeType!==1)continue;
    if(streaming&&same)node.classList.add('stream-in');
    if(window.renderMathInElement)window.renderMathInElement(node,{throwOnError:false,trust:false,strict:'ignore'});
    (node.matches('a')?[node]:[...node.querySelectorAll('a')]).forEach(a=>{a.target='_blank';a.rel='noopener noreferrer';});
  }
  updateToc();
}
// The table of contents changes only when a heading is added, removed or renamed.
function updateToc(){
  const headings=[...$('report').querySelectorAll('h1,h2')];
  headings.forEach((h,i)=>{if(h.id!=='section-'+i)h.id='section-'+i;});
  const entries=headings.map(h=>h.tagName+h.textContent);
  if(entries.join('\n')===tocEntries.join('\n'))return;
  const link=(h,i)=>{const a=document.createElement('a');a.textContent=h.textContent;a.href='#section-'+i;a.className=h.tagName==='H1'?'toc-h1':'toc-h2';a.addEventListener('click',e=>{e.preventDefault();document.getElementById('section-'+i)?.scrollIntoView({behavior:'smooth',block:'start'});});return a;};
  // While a report is written, headings only accumulate: add the new links and keep the rest.
  const grows=tocEntries.length&&entries.slice(0,tocEntries.length).join('\n')===tocEntries.join('\n');
  if(!grows){
    $('toc').replaceChildren();
    if(headings.length){const title=document.createElement('strong');title.textContent='阅读目录';$('toc').append(title);}
  }
  headings.forEach((h,i)=>{if(!grows||i>=tocEntries.length)$('toc').append(link(h,i));});
  tocEntries=entries;
  watchToc(headings);
}
let tocObserver;
// Highlight the section being read so long reports keep their bearings.
function watchToc(headings){
  tocObserver?.disconnect();
  if(!('IntersectionObserver' in window)||!headings.length)return;
  const links=new Map([...$('toc').querySelectorAll('a')].map(a=>[a.getAttribute('href').slice(1),a]));
  tocObserver=new IntersectionObserver(entries=>{
    const hit=entries.filter(e=>e.isIntersecting).sort((a,b)=>a.boundingClientRect.top-b.boundingClientRect.top)[0];
    if(!hit)return;
    links.forEach(a=>a.classList.toggle('active',a===links.get(hit.target.id)));
  },{rootMargin:'-80px 0px -65% 0px'});
  headings.forEach(h=>tocObserver.observe(h));
}
async function route(){
  const id=location.hash.slice(1);
  if(id==='queue'||id==='directory')showListView(id);
  else if(!id||id==='new')newReading();
  else if(/^[a-f0-9]{32}$/.test(id)) await selectJob(id);
}
async function poll(){
  clearTimeout(pollTimer);
  if(document.hidden){pollTimer=setTimeout(poll,15000);return;}
  try{
    if(state.job && live(state.job.status)){
      const id=state.job.id;const j=await api('/api/jobs/'+id);
      if(state.job?.id===id){state.job=j;renderJob();}
    }
    await refreshJobs();put('connection','已连接');
  }catch(e){put('connection','连接中断');}
  pollTimer=setTimeout(poll,state.jobs.some(j=>live(j.status))?2500:10000);
}
async function upload(file){
  if(!file||state.busy)return;
  if(!/\.(pdf|md|markdown|txt)$/i.test(file.name)){error('请选择 PDF、MD 或 TXT 文件。');return;}
  if(file.size>40*1024*1024){error('文件超过 40 MB，请拆分后上传。');return;}
  const previous=state.document;
  state.busy=true;modelChanged();error('');$('drop-zone').disabled=true;
  $('drop-zone').hidden=false;$('document-card').hidden=true;$('upload-title').textContent='正在提取论文正文…';$('upload-subtitle').textContent=file.name;
  try{
    const body=new FormData();body.append('file',file);
    state.document=await api('/api/documents',{method:'POST',body});
    showDocument();toast('正文已提取，可以开始精读');requestAnimationFrame(()=>$('research').focus({preventScroll:true}));
  }catch(e){state.document=previous;error(e.message);$('drop-zone').hidden=false;$('upload-title').textContent='拖入论文，或点击选择';$('upload-subtitle').textContent='PDF / Markdown / TXT';}
  finally{if(state.document)showDocument();state.busy=false;$('drop-zone').disabled=false;$('file-input').value='';modelChanged();}
}
function showDocument(){
  const d=state.document;if(!d)return;
  $('drop-zone').hidden=true;$('document-card').hidden=false;$('filename').textContent=d.filename;
  $('file-meta').textContent=(d.pages?d.pages+' 页 · ':d.lines?num(d.lines)+' 行 · ':'')+num(d.chars)+' 字符'+(d.source_queue?' · 来自待读清单':d.source_library?' · 来自本地论文库':'');
  $('file-warnings').textContent=(d.warnings||[]).join(' ');
  const origin=d.source_research;const box=$('research-origin');box.replaceChildren();box.hidden=!origin;
  if(origin){const label=document.createElement('span');label.textContent='来自构想：'+origin.idea_title+' · ';box.append(label);const a=document.createElement('a');a.textContent='返回 ↗';a.href='/?task='+encodeURIComponent(origin.task_id);a.target='_blank';a.rel='noopener noreferrer';box.append(a);}
  saveDraft();
}
async function previewDocument(id){
  try{
    const d=await api('/api/documents/'+id);
    $('source-meta').textContent=d.filename+' · '+num(d.chars)+' 字符'+(d.text.length>200000?' · 预览前 200,000 字符':'');
    $('source-text').textContent=d.text.slice(0,200000);$('source-download').href=appUrl('/api/documents/'+id+'/source');showDialog('source-dialog');
  }catch(e){error(e.message);}
}
async function startAnalysis(){
  if(!state.document||state.busy)return;state.busy=true;modelChanged();error('');
  try{
    const body={document_id:state.document.id,project_id:$('reading-project')?.value||window.RFProject.concrete(),provider_id:$('provider').value,research:$('research').value,...(state.settings.providers.find(p=>p.id===$('provider').value)?.auth_type==='chatgpt_oauth'?readerPicker.value():{})};
    let pending;try{pending=JSON.parse(sessionStorage.getItem('paperlens-pending')||'null');}catch{}
    const signature=JSON.stringify(body);
    const request_id=pending?.signature===signature?pending.request_id:crypto.randomUUID();
    try{sessionStorage.setItem('paperlens-pending',JSON.stringify({signature,request_id}));}catch{}
    const j=await post('/api/jobs',{...body,request_id});
    try{sessionStorage.removeItem('paperlens-pending');}catch{}
    location.hash=j.id;await refreshJobs();clearTimeout(pollTimer);pollTimer=setTimeout(poll,1500);
  }catch(e){error(e.message);}finally{state.busy=false;modelChanged();}
}
function openSettings(){
  $('profile-select').replaceChildren(...state.settings.providers.map(p=>new Option(p.name,p.id)));
  $('profile-select').value=$('provider').value||state.settings.default_provider;
  fillProfile($('profile-select').value);showDialog('settings-dialog');
}
function fillProfile(id){
  state.profile=id||null;
  const p=state.settings.providers.find(p=>p.id===id)||{name:'',model:'',endpoint:'',thinking:'auto',max_tokens:24000,context_chars:90000,direct:false};
  const oauth=p.auth_type==='chatgpt_oauth';
  $('chatgpt-panel').hidden=!oauth;
  $('connection-fields').hidden=oauth;
  $('p-direct').closest('label').hidden=oauth;
  for(const field of ['p-name','p-model','p-endpoint','p-key','p-thinking','p-tokens','p-context','p-direct'])$(field).disabled=oauth;
  if(oauth)refreshChatGPT().catch(e=>inline('chatgpt-status',e.message,true));
  for(const [field,key] of [['p-name','name'],['p-model','model'],['p-endpoint','endpoint'],['p-thinking','thinking'],['p-tokens','max_tokens'],['p-context','context_chars']])$(field).value=p[key];
  $('p-key').value='';$('p-key').placeholder=p.has_key?'已配置 · 留空保留现有密钥':'填入 API Key';
  $('key-status').textContent=p.has_key?'已保存（不回显），填写新值可替换':'仅保存在本机，不回显';
  if(oauth){$('p-key').placeholder='通过 ChatGPT 登录取得授权';$('key-status').textContent='已授权时使用套餐额度，登录令牌由本机加密保存。';}
  $('p-default').checked=id===state.settings.default_provider;$('p-direct').checked=!!p.direct;
  $('test-provider').disabled=!id||!p.has_key;inline('settings-msg','');
}

let chatgptPoll, chatgptState;
async function refreshChatGPT(){
  const previous=chatgptState;
  const s=await api('/api/chatgpt/status');chatgptState=s;
  const picker=$('chatgpt-accounts');
  picker.replaceChildren(...s.accounts.map(a=>new Option(a.label,a.client_id)),new Option('添加其他账户或工作区',''));
  picker.value=s.active||'';
  inline('chatgpt-status',s.pending?s.message:(s.connected?'ChatGPT 套餐已授权':(s.message||'尚未授权使用 ChatGPT 套餐。')));
  $('chatgpt-login').disabled=s.pending;$('chatgpt-logout').disabled=!s.connected;
  if(previous&&(previous.active!==s.active||previous.connected!==s.connected))await readerPicker.load(true);
  clearTimeout(chatgptPoll);
  if(s.pending)chatgptPoll=setTimeout(async()=>{try{await refreshChatGPT();await refreshSettings();$('test-provider').disabled=!chatgptState.connected;}catch(e){inline('chatgpt-status',e.message,true);}},2000);
}
$('chatgpt-login').addEventListener('click',async()=>{
  try{await post('/api/chatgpt/login',{client_id:$('chatgpt-accounts').value||null,reconsent:!!chatgptState?.active&&!chatgptState?.connected});await refreshChatGPT();}
  catch(e){inline('chatgpt-status',e.message,true);}
});
$('chatgpt-accounts').addEventListener('change',async()=>{
  if(!$('chatgpt-accounts').value)return;
  try{await post('/api/chatgpt/select',{client_id:$('chatgpt-accounts').value});await refreshChatGPT();await refreshSettings();}
  catch(e){inline('chatgpt-status',e.message,true);}
});
$('chatgpt-logout').addEventListener('click',async()=>{
  try{const r=await post('/api/chatgpt/logout');await refreshChatGPT();await refreshSettings();inline('chatgpt-status',r.message);}
  catch(e){inline('chatgpt-status',e.message,true);}
});
async function saveProfile(e){
  e.preventDefault();const button=e.submitter;button.disabled=true;
  try{
    const oauth=state.settings.providers.find(p=>p.id===state.profile)?.auth_type==='chatgpt_oauth';
    const data={id:state.profile,name:$('p-name').value,model:$('p-model').value,endpoint:$('p-endpoint').value,api_key:$('p-key').value,thinking:$('p-thinking').value,max_tokens:Number($('p-tokens').value),context_chars:Number($('p-context').value),is_default:$('p-default').checked,direct:$('p-direct').checked};
    const result=await post('/api/providers',data);await refreshSettings(false);
    $('profile-select').replaceChildren(...state.settings.providers.map(p=>new Option(p.name,p.id)));$('profile-select').value=result.provider.id;
    fillProfile(result.provider.id);inline('settings-msg','已保存，新任务生效');
  }catch(e){inline('settings-msg',e.message,true);}finally{button.disabled=false;}
}
function syncPrompt(){state.prompts[state.promptTab]=$('prompt-editor').value;}
function showPromptTab(tab){
  state.promptTab=tab;$('prompt-editor').value=state.prompts[tab];
  $('prompt-label').textContent={report:'报告分析提示词',extract:'长文证据提取提示词',memory:'文献记忆定位提示词'}[tab];
  ['report','extract','memory'].forEach(k=>$(k+'-prompt-tab').setAttribute('aria-selected',tab===k));
  $('prompt-count').textContent=num($('prompt-editor').value.length)+' 字符';
}
function openPrompts(){state.prompts={...state.settings.prompts};inline('prompts-msg','');$('prompt-applied').hidden=true;showPromptTab('report');showDialog('prompts-dialog');}

$('new').addEventListener('click',resetReading);
$('next-reading').addEventListener('click',()=>{resetReading();window.scrollTo({top:0});});
$('menu').addEventListener('click',()=>$('sidebar').classList.toggle('open'));
$('main').addEventListener('click',closeSidebar);
$('search').addEventListener('input',renderHistory);
$('history').addEventListener('click',e=>{const button=e.target.closest('[data-job]');if(button)location.hash=button.dataset.job;});
$('provider').addEventListener('change',modelChanged);
$('settings').addEventListener('click',openSettings);$('api-shortcut').addEventListener('click',openSettings);
$('prompts').addEventListener('click',openPrompts);$('guide-prompts').addEventListener('click',openPrompts);
document.querySelectorAll('.close-dialog').forEach(b=>b.addEventListener('click',()=>b.closest('dialog').close()));
document.querySelectorAll('dialog').forEach(d=>d.addEventListener('click',e=>{if(e.target===d){const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close();}}));
$('drop-zone').addEventListener('click',()=>$('file-input').click());$('replace').addEventListener('click',()=>$('file-input').click());
$('file-input').addEventListener('change',()=>upload($('file-input').files[0]));
['dragenter','dragover'].forEach(type=>$('drop-zone').addEventListener(type,e=>{e.preventDefault();$('drop-zone').classList.add('dragging');}));
['dragleave','drop'].forEach(type=>$('drop-zone').addEventListener(type,e=>{e.preventDefault();$('drop-zone').classList.remove('dragging');if(type==='drop')upload(e.dataTransfer.files[0]);}));
$('preview').addEventListener('click',()=>previewDocument(state.document.id));
$('run').addEventListener('click',startAnalysis);
$('read-source').addEventListener('click',()=>previewDocument(state.job.document_id));
$('stop').addEventListener('click',async()=>{try{await post('/api/jobs/'+state.job.id+'/stop');await selectJob(state.job.id);}catch(e){error(e.message);}});
$('resume').addEventListener('click',async()=>{const b=$('resume');b.disabled=true;try{await post('/api/jobs/'+state.job.id+'/resume');await selectJob(state.job.id);clearTimeout(pollTimer);pollTimer=setTimeout(poll,1000);}catch(e){error(e.message);}finally{b.disabled=false;}});
$('reanalyze').addEventListener('click',async()=>{try{const j=state.job;state.document=await api('/api/documents/'+j.document_id);$('research').value=j.research;location.hash='new';newReading();showDocument();const pid=j.snapshot.provider.id;if(state.settings.providers.some(p=>p.id===pid))$('provider').value=pid;readerPicker.set(j.snapshot.provider);modelChanged();toast('已载入原论文，可调整后重新分析');}catch(e){error(e.message);}});
$('show-evidence').addEventListener('change',renderJob);
$('copy').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('show-evidence').checked?(state.job.report||state.job.draft):(state.job.display_report||state.job.report||state.job.draft));toast('已复制');window.RFMotion?.stampAt($('copy'),'已复制');}catch{toast('无法复制，请用导出 MD');}});
$('print').addEventListener('click',()=>{if($('show-evidence').checked){$('show-evidence').checked=false;renderJob();}window.print();});
$('history-archived').addEventListener('click',async()=>{
  setArchivedView(!state.showArchived);
  try{await refreshJobs();}catch(e){toast(e.message);}
});
$('archive-job').addEventListener('click',async()=>{
  const j=state.job;if(!j||live(j.status))return;
  const archived=!j.archived;
  try{
    await post('/api/jobs/'+j.id+'/archive',{archived});
    if(archived){
      location.hash='new';await refreshJobs();
      toast('已移出「'+j.title+'」，文献地图中的卡片保留',{label:'撤销',run:async()=>{try{await post('/api/jobs/'+j.id+'/archive',{archived:false});await refreshJobs();location.hash=j.id;}catch(e){toast(e.message);}}});
    }else{await refreshJobs();await selectJob(j.id);toast('已恢复到精读记录');}
  }catch(e){error(e.message);}
});
// Deleting a reading is permanent (its card goes too when it was the card's only reading); originals go to the recycle bin.
$('delete-job').addEventListener('click',async()=>{
  const j=state.job;if(!j||live(j.status))return;
  try{
    const {plan}=await api('/api/jobs/'+j.id+'/delete-plan');const items=['这份精读报告与它的写作进度'];
    if(plan.cards.length)items.push('文献卡片「'+plan.cards[0]+'」（这是它唯一的精读）'+(plan.knowledge?'，以及 '+plan.knowledge+' 条变量与术语':''));
    for(const [title,left] of Object.entries(plan.versions_left||{}))items.push('卡片「'+title+'」保留，还剩 '+left+' 份精读');
    if(plan.documents)items.push('原文文件（移入系统回收站）');
    if(!await window.RF.confirm({title:'删除「'+j.title+'」？',message:'删除后无法撤销：',items,note:'只想从列表里隐藏，可以改用“移出记录”。'}))return;
    await api('/api/jobs/'+j.id,{method:'DELETE'});
    location.hash='new';await refreshJobs();toast('已删除「'+j.title+'」');
  }catch(e){error(e.message);}
});
/* Drop a paper anywhere on the compose view, not only on the small tray. */
(()=>{
  let depth=0;const overlay=$('drop-overlay');
  const hasFile=e=>[...(e.dataTransfer?.types||[])].includes('Files');
  const show=on=>{overlay.hidden=!on;document.body.classList.toggle('is-dropping',on);};
  window.addEventListener('dragenter',e=>{if(!hasFile(e)||$('compose').hidden||document.querySelector('dialog[open]'))return;depth++;show(true);});
  window.addEventListener('dragover',e=>{if(!overlay.hidden){e.preventDefault();e.dataTransfer.dropEffect='copy';}});
  window.addEventListener('dragleave',()=>{if(overlay.hidden)return;depth=Math.max(0,depth-1);if(!depth)show(false);});
  window.addEventListener('drop',e=>{if(overlay.hidden)return;e.preventDefault();depth=0;show(false);const f=e.dataTransfer.files[0];if(f)upload(f);});
})();
$('profile-select').addEventListener('change',()=>fillProfile($('profile-select').value));
$('add-provider').addEventListener('click',()=>{$('profile-select').value='';fillProfile(null);$('p-name').focus();});
$('settings-form').addEventListener('submit',saveProfile);
$('test-provider').addEventListener('click',async()=>{const b=$('test-provider');b.disabled=true;inline('settings-msg','测试中（一次小额调用）…');try{const r=await post('/api/providers/'+state.profile+'/test');inline('settings-msg','连接成功 · '+r.seconds+' 秒');}catch(e){inline('settings-msg',e.message,true);}finally{b.disabled=false;}});
$('prompt-editor').addEventListener('input',()=>{$('prompt-count').textContent=num($('prompt-editor').value.length)+' 字符';});
$('report-prompt-tab').addEventListener('click',()=>{syncPrompt();showPromptTab('report');});$('extract-prompt-tab').addEventListener('click',()=>{syncPrompt();showPromptTab('extract');});
$('memory-prompt-tab').addEventListener('click',()=>{syncPrompt();showPromptTab('memory');});
$('restore-prompts').addEventListener('click',async()=>{try{state.prompts=await api('/api/prompts/defaults');showPromptTab(state.promptTab);inline('prompts-msg','已载入默认版本，保存后生效');}catch(e){inline('prompts-msg',e.message,true);}});
$('save-prompts').addEventListener('click',async()=>{syncPrompt();const b=$('save-prompts');b.disabled=true;try{await post('/api/prompts',state.prompts);await refreshSettings();inline('prompts-msg','已保存，下次分析生效');}catch(e){inline('prompts-msg',e.message,true);}finally{b.disabled=false;}});
window.addEventListener('hashchange',route);
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&booted)poll();});
function saveDraft(){
  try{sessionStorage.setItem('paperlens-draft',JSON.stringify({document_id:state.document?.id,research:$('research').value,provider:$('provider').value}));}catch{}
}
async function restoreDraft(){
  let draft;try{draft=JSON.parse(sessionStorage.getItem('paperlens-draft')||'null');}catch{}
  if(!draft)return;
  if(typeof draft.research==='string'&&draft.research)$('research').value=draft.research;
  if(state.settings.providers.some(p=>p.id===draft.provider))$('provider').value=draft.provider;
  if(draft.document_id){try{state.document=await api('/api/documents/'+draft.document_id);showDocument();}catch{toast('上次的论文已不可用');}}
  modelChanged();
}
$('research').addEventListener('input',saveDraft);$('provider').addEventListener('change',saveDraft);
window.addEventListener('pagehide',saveDraft);
async function loadResearchImport(){
  const params=new URLSearchParams(location.search),id=params.get('document');if(!id)return;
  const d=await api('/api/documents/'+encodeURIComponent(id));state.document=d;
  if(d.source_research?.research)$('research').value=d.source_research.research;
  history.replaceState(null,'',location.pathname+'#new');newReading();showDocument();modelChanged();
  toast(d.source_queue?'已载入待读论文，可以开始精读':'已载入原文与对应构想');
}
async function applyLibraryPrompt(id,target){
  const labels={report:'报告分析',extract:'长文证据提取',memory:'文献地图定位'};
  if(!labels[target])target='report';
  try{const r=await api('/api/prompt-library/'+encodeURIComponent(id));
    openPrompts();state.prompts[target]=r.content;showPromptTab(target);
    const note=$('prompt-applied');note.textContent='已载入「'+r.title+'」→ '+labels[target]+'，保存后生效。';note.hidden=false;
  }catch(e){error(e.message);}
}
async function openPanelFromUrl(){
  const params=new URLSearchParams(location.search),panel=params.get('panel');
  if(!panel)return;
  history.replaceState(null,'',location.pathname+location.hash);
  if(panel==='settings')openSettings();
  else if(panel==='library')await window.PaperLibrary.open(params.get('q')||'',params.get('scope')||'');
  else if(panel==='prompts'){if(params.get('apply'))await applyLibraryPrompt(params.get('apply'),params.get('target'));else openPrompts();}
}
function viewFromUrl(){
  const params=new URLSearchParams(location.search),view=params.get('view');
  if(view!=='queue'&&view!=='directory')return;
  params.delete('view');const rest=params.toString();
  history.replaceState(null,'',location.pathname+(rest?'?'+rest:'')+'#'+view);
}
// The new-reading form names the project the reading goes into and starts "我的研究" from its research description.
async function loadProjects(){
  try{
    const d=await api('/api/projects');state.projects=d.projects.filter(p=>!p.archived);
    state.projectNames=Object.fromEntries(d.projects.map(p=>[p.id,p.name]));
    const select=$('reading-project'),chosen=window.RFProject.concrete();
    select.replaceChildren(...state.projects.map(p=>new Option(p.name,p.id)));
    select.value=state.projects.some(p=>p.id===chosen)?chosen:'default';
    fillResearch(false);
  }catch{/* The form still works with the default project. */}
}
function fillResearch(replace){
  const p=state.projects?.find(x=>x.id===$('reading-project').value);
  const note=$('research-from-project');
  if(p?.description&&(replace||!$('research').value.trim())){$('research').value=p.description;saveDraft();}
  note.hidden=!p?.description;note.textContent=p?.description?'已按课题「'+p.name+'」的研究说明填写，可以修改。':'';
}
$('reading-project').addEventListener('change',()=>{const before=state.projects?.find(x=>x.description===$('research').value.trim());fillResearch(!$('research').value.trim()||!!before);});
async function init(){viewFromUrl();try{await Promise.all([refreshSettings(false),refreshJobs(),loadProjects()]);await restoreDraft();await loadResearchImport();await route();await openPanelFromUrl();booted=true;pollTimer=setTimeout(poll,2000);}catch(e){booted=true;error(e.message);}}
init();
