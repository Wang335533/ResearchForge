'use strict';
const $ = id => document.getElementById(id);
const appBase = document.querySelector('meta[name="app-base"]')?.content || '';
const appUrl = path => appBase + path;
const state = {settings:null, jobs:[], document:null, job:null, profile:null, prompts:{}, promptTab:'report', busy:false};
const labels = {queued:'排队中', running:'分析中', stopping:'正在停止', completed:'已完成', failed:'未完成', interrupted:'已中断', stopped:'已停止'};
const live = s => ['queued','running','stopping'].includes(s);
let toastTimer, pollTimer;
const escapeHTML = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = n => Number(n || 0).toLocaleString('zh-CN');
const day = t => new Date(t * 1000).toLocaleDateString('zh-CN');

async function api(path, options={}) {
  const headers = {'X-PaperLens':'1', ...options.headers};
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const response = await fetch(appUrl(path), {...options, headers});
  let data;
  try { data = await response.json(); } catch { throw new Error('服务未返回有效响应，请重新打开工作台。'); }
  if (!response.ok) throw new Error(data.error || '操作未完成。');
  return data;
}
const post = (path, body={}) => api(path,{method:'POST', body:JSON.stringify(body)});
function toast(message){ const el=$('toast'); el.textContent=message; el.hidden=true; void el.offsetWidth; el.hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>{el.hidden=true;},3800); }
let booted=false;
// Swapping between composing and reading animates only after start-up and only on a real change.
function swapView(showReading,apply){ const change=$('reading').hidden===showReading; if(booted&&change&&window.RF?.transition) window.RF.transition(apply); else apply(); }
function error(message){ $('page-error').textContent=message; $('page-error').hidden=!message; }
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
  $('model-label').textContent=p ? p.model+(p.thinking==='enabled'?' · 深度思考已启用':'') : '';
  $('api-shortcut').textContent=p ? p.name+(p.has_key?'':' · 未配置密钥') : '设置 API';$('api-shortcut').classList.toggle('warn',!!p&&!p.has_key);
  $('run').disabled=!state.document || state.busy || !p?.has_key;
  $('run-note').textContent=p&&!p.has_key?'该接口未配置密钥。':'一次生成完整精读报告，正文将发送至所选 API；入库时另调用一次整理文献地图。';
}
async function refreshJobs(){
  state.jobs=(await api('/api/jobs')).jobs;
  renderHistory();
}
function renderHistory(){
  const needle=$('search').value.trim().toLowerCase();
  const jobs=state.jobs.filter(j=>j.title.toLowerCase().includes(needle));
  $('history-count').textContent=state.jobs.length;
  $('history').innerHTML=jobs.length?jobs.map(j=>`<button class="history-item ${state.job?.id===j.id?'active':''}" data-job="${escapeHTML(j.id)}"><strong>${escapeHTML(j.title)}</strong><small><span>${labels[j.status]||j.status}</span><time>${day(j.created)}</time></small></button>`).join(''):`<p class="muted empty-history">${needle?'没有匹配的阅读记录。':'还没有阅读记录。<br>上传第一篇论文，开始精读。'}</p>`;
}
function newReading(){
  state.job=null; swapView(false,()=>{$('compose').hidden=false; $('reading').hidden=true;}); $('crumb').textContent='新建精读';
  error(''); closeSidebar(); renderHistory();
}
async function selectJob(id){
  try{
    const j=await api('/api/jobs/'+id);
    if(location.hash.slice(1)!==id) return;
    state.job=j; error(''); closeSidebar();
    swapView(true,()=>{$('compose').hidden=true; $('reading').hidden=false; renderJob();}); renderJob(); renderHistory();
  }catch(e){error(e.message);}
}
function renderJob(){
  const j=state.job;if(!j)return;
  $('crumb').textContent=j.title;
  $('report-title').textContent=j.title;
  $('report-date').textContent=day(j.created)+' · 论文精读';
  $('report-status').textContent=labels[j.status]||j.status;
  $('report-status').className='badge '+(j.status==='failed'?'failed':'');
  $('report-model').textContent=j.snapshot.provider.name+' / '+j.model;
  $('report-basis').textContent=j.analysis_basis||'准备读取正文';
  $('stop').hidden=!live(j.status); $('stop').disabled=j.status==='stopping';
  $('resume').hidden=!['failed','interrupted','stopped'].includes(j.status);
  $('progress-card').hidden=j.status==='completed';
  $('stage').textContent=j.stage;$('progress').value=j.progress;$('progress-value').textContent=j.progress+'%';
  $('progress-message').textContent=j.message;
  $('generation-count').textContent=live(j.status)?(j.generated_chars ? '已返回 '+num(j.generated_chars)+' 字符':j.reasoning_chars?'模型正在分析…':'等待模型响应…'):'';
  $('report-error').textContent=j.error||'';$('report-error').hidden=!j.error;
  $('report-warnings').replaceChildren(...[...(j.warnings||[]),...(j.checks||[])].map(s=>{const p=document.createElement('p');p.textContent=s;return p;}));
  const tokens=(j.usage||[]).reduce((n,u)=>n+(u.total_tokens||0),0);
  $('token-usage').textContent=tokens?'用量 '+num(tokens)+' tokens':'';
  const available=!!(j.report||j.draft);
  $('report-incomplete').hidden=!available||j.status==='completed';
  document.querySelector('.export-actions').hidden=!available;
  $('export-md').href=appUrl('/api/jobs/'+j.id+'/export/md');$('export-html').href=appUrl('/api/jobs/'+j.id+'/export/html');
  $('memory-card-link').href=appUrl('/memory?job='+j.id);$('memory-card-link').hidden=j.status!=='completed';
  $('next-steps').hidden=j.status!=='completed';
  const origin=j.source_research;$('origin-link').hidden=!(origin?.task_id&&appBase);
  if(origin?.task_id)$('origin-link').href='/?task='+encodeURIComponent(origin.task_id);
  const content=j.html || '<div class="waiting"><span class="waiting-orb"><svg class="icon" aria-hidden="true"><use href="/ui/icons.svg#i-book-open-text"/></svg></span>报告生成中，可关闭页面，后台继续。</div>';
  // Only server-sanitized Markdown is inserted as HTML.
  if($('report').dataset.raw!==content){
    $('report').innerHTML=content;$('report').dataset.raw=content;
    if(window.renderMathInElement) window.renderMathInElement($('report'),{throwOnError:false,trust:false,strict:'ignore'});
    const headings=[...$('report').querySelectorAll('h1,h2')];
    $('toc').replaceChildren();
    if(headings.length){const title=document.createElement('strong');title.textContent='阅读目录';$('toc').append(title);}
    headings.forEach((h,i)=>{h.id='section-'+i;const a=document.createElement('a');a.textContent=h.textContent;a.href='#section-'+i;a.className=h.tagName==='H1'?'toc-h1':'toc-h2';a.addEventListener('click',e=>{e.preventDefault();h.scrollIntoView({behavior:'smooth',block:'start'});});$('toc').append(a);});
    watchToc(headings);
    $('report').querySelectorAll('a').forEach(a=>{a.target='_blank';a.rel='noopener noreferrer';});
  }
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
  if(!id||id==='new')newReading();
  else if(/^[a-f0-9]{32}$/.test(id)) await selectJob(id);
}
async function poll(){
  try{
    if(state.job && live(state.job.status)){
      const id=state.job.id;const j=await api('/api/jobs/'+id);
      if(state.job?.id===id){state.job=j;renderJob();}
    }
    await refreshJobs();$('connection').textContent='已连接';
  }catch(e){$('connection').textContent='连接中断';}
  pollTimer=setTimeout(poll,state.jobs.some(j=>live(j.status))?2500:10000);
}
async function upload(file){
  if(!file||state.busy)return;
  if(!/\.(pdf|md|markdown|txt)$/i.test(file.name)){error('请选择 PDF、MD 或 TXT 文件。');return;}
  if(file.size>40*1024*1024){error('文件超过 40 MB，请拆分后上传。');return;}
  state.busy=true;modelChanged();error('');$('drop-zone').disabled=true;
  $('drop-zone').hidden=false;$('document-card').hidden=true;$('upload-title').textContent='正在提取论文正文…';$('upload-subtitle').textContent=file.name;
  try{
    const body=new FormData();body.append('file',file);
    state.document=await api('/api/documents',{method:'POST',body});
    showDocument();toast('正文已提取');
  }catch(e){state.document=null;error(e.message);$('drop-zone').hidden=false;$('upload-title').textContent='拖入论文，或点击选择';$('upload-subtitle').textContent='PDF / Markdown / TXT';}
  finally{state.busy=false;$('drop-zone').disabled=false;$('file-input').value='';modelChanged();}
}
function showDocument(){
  const d=state.document;if(!d)return;
  $('drop-zone').hidden=true;$('document-card').hidden=false;$('filename').textContent=d.filename;
  $('file-meta').textContent=(d.pages?d.pages+' 页 · ':d.lines?num(d.lines)+' 行 · ':'')+num(d.chars)+' 字符'+(d.source_library?' · 来自本地论文库':'');
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
    const j=await post('/api/jobs',{document_id:state.document.id,provider_id:$('provider').value,research:$('research').value});
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
  for(const [field,key] of [['p-name','name'],['p-model','model'],['p-endpoint','endpoint'],['p-thinking','thinking'],['p-tokens','max_tokens'],['p-context','context_chars']])$(field).value=p[key];
  $('p-key').value='';$('p-key').placeholder=p.has_key?'已配置 · 留空保留现有密钥':'填入 API Key';
  $('key-status').textContent=p.has_key?'已保存（不回显），填写新值可替换':'仅保存在本机，不回显';
  $('p-default').checked=id===state.settings.default_provider;$('p-direct').checked=!!p.direct;
  $('test-provider').disabled=!id||!p.has_key;inline('settings-msg','');
}
async function saveProfile(e){
  e.preventDefault();const button=e.submitter;button.disabled=true;
  try{
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

$('new').addEventListener('click',()=>{location.hash='new';newReading();});
$('next-reading').addEventListener('click',()=>{location.hash='new';newReading();window.scrollTo({top:0});});
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
$('reanalyze').addEventListener('click',async()=>{try{const j=state.job;state.document=await api('/api/documents/'+j.document_id);$('research').value=j.research;location.hash='new';newReading();showDocument();const pid=j.snapshot.provider.id;if(state.settings.providers.some(p=>p.id===pid))$('provider').value=pid;modelChanged();toast('已载入原论文，可调整后重新分析');}catch(e){error(e.message);}});
$('copy').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(state.job.report||state.job.draft);toast('已复制');}catch{toast('无法复制，请用导出 MD');}});
$('print').addEventListener('click',()=>window.print());
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
function saveDraft(){
  try{sessionStorage.setItem('paperlens-draft',JSON.stringify({document_id:state.document?.id,research:$('research').value,provider:$('provider').value}));}catch{}
}
async function restoreDraft(){
  let draft;try{draft=JSON.parse(sessionStorage.getItem('paperlens-draft')||'null');}catch{}
  if(!draft)return;
  $('research').value=typeof draft.research==='string'?draft.research:'';
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
  toast('已载入原文与对应构想');
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
  else if(panel==='library')$('library-nav').click();
  else if(panel==='prompts'){if(params.get('apply'))await applyLibraryPrompt(params.get('apply'),params.get('target'));else openPrompts();}
}
async function init(){try{await Promise.all([refreshSettings(false),refreshJobs()]);await restoreDraft();await loadResearchImport();await route();await openPanelFromUrl();booted=true;pollTimer=setTimeout(poll,2000);}catch(e){booted=true;error(e.message);}}
init();
