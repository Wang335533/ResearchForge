function referenceLabel(reference){
  let label;
  if(reference.reference_status==='matched_local_record' && reference.source_id){
    label=`已匹配检索记录${reference.reference_label?' ['+reference.reference_label+']':''} · ${reference.source_id}`;
    if(reference.reference_corrected_fields?.length) label+=' · 已回填原始书目信息';
  }else{
    const labels={unmatched_local_record:'未匹配本次检索文献 · 未核对',
      external_unverified:'未匹配本次检索文献 · 未核对',
      ambiguous_local_record:'对应记录不唯一 · 待核对',
      conflicting_local_record:'引用信息存在冲突 · 待核对'};
    label=labels[reference.reference_status]||'来源尚未核对';
    if(reference.source_id) label+=` · 原引用编号：${reference.source_id}`;
  }
  return reference.reference_note?`${label} · ${reference.reference_note}`:label;
}


function literatureCheckOf(id) { return (S.literatureChecks || S.task?.literature_checks || {})[String(id)]; }
function literatureDecisionLabel(check) {
  if (!check) return '历史结果未记录文献判断';
  if (check.decision === 'pass') return '通过文献判断';
  return check.evidence_status === 'insufficient' || /证据不足|insufficient evidence/i.test(check.reason || '') ? '暂缓：证据不足' : '未通过文献判断';
}
function literatureCheckPapers(id, check) {
  const papers = S.lit[String(id)] || [];
  return (check.source_ids || []).map((label,index) => ({label, paper: papers.find((p,i) =>
    check.resolved_source_ids?.[index] ? (p.source_id || p.ut) === check.resolved_source_ids[index] : (p.reference_label || 'P'+(i+1)) === label)}));
}
function literatureCheckHtml(id) {
  const check = literatureCheckOf(id);
  if (!check) return '';
  return `<section class="literature-check"><h3>${esc(literatureDecisionLabel(check))}</h3><p class="prose">${esc(check.reason)}</p>
    ${check.evidence_status === 'insufficient' ? '<p class="hint">当前证据不足以支持继续优化，不代表这个构想没有研究价值。</p>' : ''}
    ${literatureCheckPapers(id,check).map(({label,paper}) => `<details><summary>[${esc(label)}] ${esc(paper?.title || '来源记录缺失')}</summary>
      <p>${esc(paper?.authors)} · ${esc(paper?.year)} · ${esc(paper?.journal)}</p><p>${esc(paper?.source_id || paper?.ut)}</p><p class="prose">${esc(paper?.abstract || '未提供摘要')}</p></details>`).join('')}
    <p class="hint">判断基于本次检索摘要；已保存原始构想和判断依据。</p></section>`;
}
function literatureCheckMd(id) {
  const check = literatureCheckOf(id);
  if (!check) return '';
  return ['### 文献判断 · '+literatureDecisionLabel(check),'',check.reason,'',
    ...literatureCheckPapers(id,check).map(({label,paper}) => `- [${label}] ${paper?.title || '来源记录缺失'} · ${paper?.authors || ''} (${paper?.year || '—'}, ${paper?.journal || ''}) · ${paper?.source_id || paper?.ut || ''}`),''].join('\n');
}

function proposalMd(t,prop){
  const L=['# '+(prop.title_zh||prop.title_en||''),'',
    '## 中文摘要',prop.abstract_zh||prop.abstract_en||'',''];
  if(prop.title_en || prop.abstract_en) L.push('<details><summary>历史外文原稿 · 展开核对</summary>','',
    prop.title_en||'', '',prop.abstract_en||'','','</details>','');
  L.push(literatureCheckMd(t.id));
  if(prop.theoretical_foundation) L.push('## 对应理论',prop.theoretical_foundation,'');
  L.push('## 核心研究假说','');
  (prop.hypotheses||[]).forEach(h=>{ const c=h.contribution||{};
    L.push(`### ${h.id} ${h.hypothesis}`,'',
      `- **目标理论**：${c.target_theory||'—'}`,
      `- **代表作者**：${c.authors||'—'}`,
      `- **文献出处**：${c.authors||''} (${c.year||'—'}, ${c.journal||'—'})`,
      `- **来源核对**：${referenceLabel(c)}`,
      `- **贡献方式**：${c.how||'—'}`,''); });
  L.push('## 关键改进',(prop.improvement_notes||'').replace(/；/g,'；\n'),'','## 关键文献','',
    '引用核对仅说明书目信息与本次检索记录的对应，不代表论文内容解读已核验。','');
  (prop.key_references||[]).forEach(r=>L.push(`- ${r.authors||''} (${r.year||'—'}). ${r.title||''}. *${r.journal||''}*${r.doi?' DOI: '+r.doi:''} — ${referenceLabel(r)}`));
  return L.join('\n');
}
function buildMarkdown(){
  const L=['# ResearchForge · 自动化研究构思流水线报告','',
    `- 生成时间：${new Date().toLocaleString()}`,
    `- 研究主题：${S.topic}`,
    `- 构想数量：${S.ideas.length}（生成）/ ${S.proposals.length}（最终提案）`,'',
    '---','','## 一、初步构想（全部 '+S.ideas.length+' 个）',''];
  S.ideas.forEach((it,i)=>L.push(`### ${i+1}. ${it.title}`,'',it.abstract,''));
  L.push('---','','## 二、主编评审（入选 '+S.top5.length+' 个）','');
  S.top5.forEach(t=>{ const idea=S.ideas.find(x=>String(x.id)===String(t.id))||{};
    L.push(`### 第 ${t.rank} 名（${t.total} 分）· ${idea.title||''}`,'',
      `- 最反直觉的点：${t.hook||''}`,'',`- 评语：${t.reason||''}`,''); });
  L.push('---','','## 三、向量检索文献（每个构想）','');
  Object.entries(S.lit).forEach(([id,ps])=>{
    const idea=S.ideas.find(x=>String(x.id)===String(id))||{};
    L.push(`### ${idea.title||id}`,'');
    ps.forEach(p=>L.push(`### [${p.reference_label}] ${p.title}`,'',`- 作者：${p.authors}`,`- 期刊／年份：${p.journal} / ${p.year}`,`- DOI：${p.doi||'未提供'}`,`- 来源编号：${p.source_id}`,`- 检索依据：${p.retrieval_reason}`,'','完整摘要：',p.abstract,''));
    L.push(''); });
  L.push('---','','## 四、文献判断与最终研究提案','');
  S.top5.filter(t => literatureCheckOf(t.id)?.decision === 'drop').forEach(t => L.push(`## 构想 ${t.id}`,literatureCheckMd(t.id)));
  S.proposals.forEach(({judge,proposal})=>L.push(proposalMd(judge,proposal),'','---',''));
  return L.join('\n');
}

function ideaExportState(idea) {
  const selected = S.top5.find(item => String(item.id) === String(idea.id));
  const optimized = S.proposals.some(item => String(item.idea.id) === String(idea.id));
  if (optimized) return `已完成优化提案 · 入选第 ${selected?.rank || '—'} 名`;
  const check = literatureCheckOf(idea.id);
  if (check?.decision === 'drop') return `${literatureDecisionLabel(check)} · 已完成判断，未生成提案`;
  if (selected) return `已入选第 ${selected.rank} 名 · 提案尚未完成`;
  if (S.top5.length) return '未入选初筛 · 保留完整原始构想';
  return S.selectionCount === 0 ? '未进行筛选' : '尚无筛选结果';
}

function buildIdeasMarkdown() {
  const lines = ['# 全部研究构想', '', `研究主题：${S.topic}`, '',
    `任务编号：${S.task?.id || S.taskId || '—'}`, '',
    `本文件收录 ${S.ideas.length} 个原始构想的完整标题与摘要。已保存优化提案 ${S.proposals.length} 份。`, ''];
  S.ideas.forEach((idea, index) => lines.push(`## ${index + 1}. ${idea.title}`, '',
    `构想编号：${idea.id} · ${ideaExportState(idea)}`, '', idea.abstract, '', literatureCheckMd(idea.id)));
  return lines.join('\n');
}

function buildReadingHtml() {
  const text = value => `<p class="body-text">${esc(value)}</p>`;
  const fact = (label, value) => `<p class="fact"><strong>${esc(label)}：</strong>${esc(value || '—')}</p>`;
  const references = items => (items || []).map(ref => `<li>${text(`${ref.authors || ''} (${ref.year || '—'}). ${ref.title || ''}. ${ref.journal || ''}`)}
    ${ref.doi ? fact('DOI', ref.doi) : ''}${fact('来源核对', referenceLabel(ref))}</li>`).join('');
  const ideas = S.ideas.map((idea, index) => `<article id="idea-${index + 1}" class="card idea-card">
    <div class="eyebrow">IDEA ${String(index + 1).padStart(2, '0')} · 原始编号 ${esc(idea.id)}</div>
    <h3>${esc(idea.title)}</h3><p class="status">${esc(ideaExportState(idea))}</p>${text(idea.abstract)}${literatureCheckHtml(idea.id)}</article>`).join('');
  const proposals = S.proposals.map(({idea, judge, proposal: p}, index) => `<article id="proposal-${index + 1}" class="card proposal-card">
    <div class="eyebrow">提案 ${index + 1} · 对应原始 IDEA ${esc(idea.id)} · 评审第 ${esc(judge.rank)} 名</div>
    <h3>${esc(p.title_zh||p.title_en)}</h3>${literatureCheckHtml(idea.id)}
    <h4>中文摘要</h4>${text(p.abstract_zh||p.abstract_en)}
    ${p.title_en || p.abstract_en ? `<details><summary>历史外文原稿 · 展开核对</summary>${text(p.title_en)}${text(p.abstract_en)}</details>` : ''}
    <h4>对应理论</h4>${text(p.theoretical_foundation)}<h4>研究假说与理论贡献</h4>
    ${(p.hypotheses || []).map(h => {const c = h.contribution || {}; return `<section class="hypothesis">
      <h5>${esc(h.id)} · ${esc(h.hypothesis)}</h5>${fact('目标理论', c.target_theory)}
      ${fact('代表文献', `${c.authors || ''} (${c.year || '—'}, ${c.journal || '—'})`)}
      ${fact('来源核对', referenceLabel(c))}${text(c.how)}</section>`;}).join('')}
    <h4>相对原构想的改进</h4>${text(p.improvement_notes)}
    <h4>关键参考文献</h4><ol class="references">${references(p.key_references)}</ol></article>`).join('');
  const judges = S.top5.map(item => `<article class="card"><h3>第 ${esc(item.rank)} 名 · IDEA ${esc(item.id)} · ${esc(item.total)} 分</h3>
    ${fact('核心亮点', item.hook)}${text(item.reason)}</article>`).join('');
  const literature = Object.entries(S.lit).map(([id, papers]) => `<details class="card"><summary>IDEA ${esc(id)} · ${papers.length} 篇检索文献（含完整摘要）</summary>
    ${papers.map(p => `<article class="paper"><h4>[${esc(p.reference_label)}] ${esc(p.title)}</h4>
      ${fact('作者', p.authors)}${fact('期刊 / 年份', `${p.journal || ''} / ${p.year || '—'}`)}
      ${fact('DOI', p.doi)}${fact('来源编号', p.source_id)}${fact('检索依据', p.retrieval_reason)}${text(p.abstract)}</article>`).join('')}</details>`).join('');
  return `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>全部 ${S.ideas.length} 个构想 · ResearchForge</title><style>
    *{box-sizing:border-box}body{margin:0;background:#f4f5f0;color:#25342e;font:16px/1.8 system-ui,-apple-system,"Microsoft YaHei",sans-serif}
    header,main{max-width:980px;margin:auto;padding:36px 28px}header{padding-bottom:12px}h1{font-size:32px;line-height:1.35}h2{margin:44px 0 20px;font-size:25px}
    h3{font-size:21px;line-height:1.55;margin:10px 0}h4{font-size:18px;margin:26px 0 8px}h5{font-size:16px;margin:0 0 12px}
    .card{background:white;border:1px solid #dce3da;border-radius:12px;padding:28px;margin:18px 0;overflow-wrap:anywhere}
    .body-text{white-space:pre-wrap;margin:12px 0}.fact{margin:8px 0;white-space:pre-wrap}.eyebrow,.status{color:#597263;font-size:14px}
    .english-title{color:#52695b;font-size:18px}.hypothesis{border-left:3px solid #a5b8a4;padding:8px 20px;margin:20px 0}
    .references li{margin-bottom:22px}.paper{border-top:1px solid #e2e7df;padding:14px 0}.note{color:#607267}.toc{padding:20px 28px;background:#e8ede3;border-radius:12px}
    .toc a{color:#345f4a;text-decoration:none}.toc li{margin:5px 0}summary{cursor:pointer;font-weight:600}footer{padding:32px;text-align:center;color:#66776a}
    @media(max-width:640px){header,main{padding:22px 16px}.card{padding:20px}h1{font-size:26px}}
    @media print{body{background:white;font-size:11pt}.card{border:0;padding:0;margin:24px 0}header,main{padding:0}.toc{background:white}h2,h3,h4,h5{break-after:avoid}a{color:inherit}}
    </style></head><body><header><div class="eyebrow">RESEARCHFORGE · 批量阅读与导出</div>
    <h1>全部 ${S.ideas.length} 个构想与 ${S.proposals.length} 份提案</h1>${text(S.topic)}
    <p class="note">任务编号：${esc(S.task?.id || S.taskId || '—')}<br>原始构想与优化提案分别保留；未入选构想没有被补写为提案。所有构想及提案正文均已展开。</p>
    </header><main><nav class="toc"><strong>阅读目录</strong><p><a href="#ideas">全部构想</a> · <a href="#judges">初筛评审</a> · <a href="#proposals">全部优化提案</a> · <a href="#literature">检索文献</a></p><ol>
    ${S.ideas.map((idea, index) => `<li><a href="#idea-${index + 1}">${esc(idea.title)}</a></li>`).join('')}</ol></nav>
    <section id="ideas"><h2>一、全部原始构想（${S.ideas.length} 个）</h2>${ideas}</section>
    <section id="judges"><h2>二、已保存的初筛评审（${S.top5.length} 个入选）</h2>${judges}</section>
    <section id="proposals"><h2>三、全部优化提案（${S.proposals.length} 份）</h2>${proposals}</section>
    <section id="literature"><h2>四、检索文献与完整摘要</h2><p class="note">展开下方分组查看。引用书目匹配不代表论文内容解读或研究质量已经核验。</p>${literature}</section>
    </main><footer>ResearchForge · 本文件可离线阅读，使用浏览器打印可另存为 PDF。</footer></body></html>`;
}

function exportName(suffix) {
  return `researchforge_${S.task?.id || S.taskId || 'research'}_${suffix}`;
}
function download(name, text, type='text/markdown;charset=utf-8'){
  const a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob([text],{type})); a.download=name; a.click();
  setTimeout(()=>URL.revokeObjectURL(a.href),3000);
}
$('btn-ideas-md').onclick=()=>{ download(exportName('all_ideas.md'), buildIdeasMarkdown()); toast(`已导出全部 ${S.ideas.length} 个构想`,'ok'); };
$('btn-reading-html').onclick=()=>{ download(exportName('all_ideas_and_proposals.html'), buildReadingHtml(), 'text/html;charset=utf-8'); toast('全部构想与提案已合并导出','ok'); };
$('btn-md').onclick=()=>{ download(exportName('report.md'), buildMarkdown()); toast('Markdown 报告已导出','ok'); };
$('btn-json').onclick=()=>{ download(exportName('data.json'),
  JSON.stringify(S.task || {topic:S.topic, count:S.count, selection_count:S.selectionCount, ideas:S.ideas, top5:S.top5, lit:S.lit, proposals:S.proposals, literature_checks:S.literatureChecks},null,2),
  'application/json'); toast('JSON 数据已导出','ok'); };
