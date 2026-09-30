/* Layer 5 only renders saved content locally; no model requests or translation. */
const reportView = {id:null, revision:0, state:null, timer:null, serial:0, submitting:false, downloadWhenReady:false};

function resetReportExport() {
  clearTimeout(reportView.timer);
  Object.assign(reportView,{id:null,revision:0,state:null,submitting:false,downloadWhenReady:false,serial:reportView.serial+1});
  $('report-preview').classList.add('hide'); $('report-download').classList.add('hide');
  $('report-progress').classList.add('hide'); $('report-error').textContent='';
  $('report-progress-text').textContent=''; $('report-issues').classList.add('hide');
  $('report-issues-list').replaceChildren();
  $('report-message').textContent='正在读取导出状态…';
  setStep(5,'',''); setStage(5,'idle','尚未导出');
}

function paintReportExport() {
  if (!S.task || reportView.id !== S.taskId) return;
  const state=reportView.state || {status:'idle'}, busy=['queued','running'].includes(state.status);
  const active=TASK_ACTIVE.has(S.task.status), ready=state.status==='completed';
  const literature=Object.values(S.task.lit||{}), matches=literature.reduce((sum,rows)=>sum+rows.length,0);
  $('report-scope').textContent=`包含 ${S.ideas.length} 个构想 · ${literature.length} 组文献（${matches} 条）· ${Object.keys(S.literatureChecks || {}).length} 条判断 · ${S.proposals.length} 份提案`;
  $('btn-generate-report').disabled=reportView.submitting||busy||active;
  $('btn-report-html').disabled=reportView.submitting||busy||active;
  $('btn-generate-report').textContent=reportView.submitting?'正在提交…':busy?'正在生成…':ready?'下载 HTML':['failed','interrupted'].includes(state.status)?'重新导出':'导出 HTML';
  $('report-message').textContent=active?'研究进行中，完成或停止后可导出。':state.message||'尚未导出';
  $('report-error').textContent=state.error||'';
  const issues=$('report-issues'), list=$('report-issues-list');
  list.replaceChildren();
  for (const issue of state.issues||[]) {
    const item=document.createElement('li');
    item.textContent=`${issue.reason}。原文：${issue.source}`;
    list.appendChild(item);
  }
  issues.classList.toggle('hide',!(state.issues||[]).length);
  $('report-progress').classList.toggle('hide',!(busy&&state.total>0));
  $('report-progress').value=state.total?100*(state.completed||0)/state.total:0;
  $('report-progress-text').textContent=ready?'报告已生成':busy?'正在本地排版':'';
  const labels={idle:'尚未导出',queued:'等待导出',running:'正在排版',completed:'已生成',failed:'导出失败',interrupted:'导出中断'};
  const visual=ready?'done':busy?'running':['failed','interrupted'].includes(state.status)?'error':'idle';
  setStage(5,visual,labels[state.status]||'尚未导出');
  setStep(5,visual,ready?'已生成':busy?'排版中':'');
  for (const id of ['report-preview','report-download']) $(id).classList.toggle('hide',!ready);
  if (ready) {
    $('report-preview').href=state.file_url;
    $('report-download').href=state.file_url+'&download=1';
  }
}

function downloadReport(state) {
  const link=document.createElement('a');
  link.href=state.file_url+'&download=1'; link.download='';
  document.body.appendChild(link); link.click(); link.remove();
}

function acceptReportState(state) {
  reportView.state=state; paintReportExport();
  if (state.status==='completed' && reportView.downloadWhenReady) {
    reportView.downloadWhenReady=false; downloadReport(state); toast('报告已生成，开始下载','ok');
  }
  if (['failed','interrupted'].includes(state.status)) reportView.downloadWhenReady=false;
}

async function pollReportExport() {
  const id=reportView.id, serial=reportView.serial;
  if (!id || S.taskId!==id) return;
  clearTimeout(reportView.timer);
  try {
    const state=await taskRequest(`/api/tasks/${encodeURIComponent(id)}/report`);
    if (serial!==reportView.serial || id!==S.taskId) return;
    acceptReportState(state);
    if (['queued','running'].includes(state.status)) reportView.timer=setTimeout(pollReportExport,2000);
  } catch(error) {
    if (serial!==reportView.serial || id!==S.taskId) return;
    $('report-error').textContent='暂时无法读取导出进度：'+error.message+'。正在重新连接，已保存的研究结果不受影响。';
    reportView.timer=setTimeout(pollReportExport,5000);
  }
}

function renderReportExport(task) {
  if (task.id!==reportView.id) {
    resetReportExport(); reportView.id=task.id;
  }
  paintReportExport();
  if (task.revision!==reportView.revision) {
    reportView.revision=task.revision;
    if (!reportView.submitting) pollReportExport();
  }
}

async function startReportExport() {
  if (!S.task || reportView.submitting || TASK_ACTIVE.has(S.task.status)) return;
  if (reportView.id!==S.taskId) renderReportExport(S.task);
  selectStage(5); $('export-menu').open=false;
  if (reportView.state?.status==='completed') {downloadReport(reportView.state);return;}
  if (['queued','running'].includes(reportView.state?.status)) return;
  const id=S.taskId, serial=++reportView.serial;
  clearTimeout(reportView.timer);
  reportView.submitting=true; reportView.downloadWhenReady=true; paintReportExport();
  try {
    const state=await taskRequest(`/api/tasks/${encodeURIComponent(id)}/report`,{});
    if (serial!==reportView.serial || id!==S.taskId) return;
    acceptReportState(state);
  } catch(error) {
    if (serial!==reportView.serial || id!==S.taskId) return;
    // A timed-out POST may have succeeded. Only GET is repeated automatically.
    reportView.downloadWhenReady=false;
    reportView.state={status:'idle',error:error.message,message:'正在核对服务器上的导出状态…'};
    toast('导出请求未确认：'+error.message,'err');
  } finally {
    if (serial===reportView.serial && id===S.taskId) {
      reportView.submitting=false; paintReportExport(); pollReportExport();
    }
  }
}

$('btn-generate-report').onclick=startReportExport;
$('btn-report-html').onclick=startReportExport;
// Another window can continue the same export; refocusing only refreshes status.
function refreshReportOnReturn() {
  if (reportView.id && !reportView.submitting && document.visibilityState==='visible') pollReportExport();
}
window.addEventListener('focus',refreshReportOnReturn);
document.addEventListener('visibilitychange',refreshReportOnReturn);
if (S.task) renderReportExport(S.task);
