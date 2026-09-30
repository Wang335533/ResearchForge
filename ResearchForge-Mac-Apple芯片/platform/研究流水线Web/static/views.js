/* Result readers keep existing DOM while task checkpoints arrive. */
const DIMS = [['importance','理论重要性',20],['insight','反直觉洞见',20],['mechanism','机制推导',20],
  ['dialogue','文献对话',15],['testability','可检验性',10],['policy','政策含义',10],['expression','表达密度',5]];

function emptyStage(n, title, description) {
  const body = $('s' + n + '-body');
  if (body.children.length) return;
  body.innerHTML = `<div class="empty-state">${icon('book')}<h3>${esc(title)}</h3><p>${esc(description)}</p></div>`;
}
function renderIdeas() {
  const root = $('s1-body');
  if (!S.ideas.length) { emptyStage(1, S.count === 0 ? '空研究' : '构想生成中', S.count === 0 ? '构想数量为 0，未调用模型。' : '生成后显示在这里'); return; }
  if (!root.querySelector('.idea-list')) root.innerHTML = '<div class="idea-list"></div>';
  const list = root.querySelector('.idea-list');
  let added = 0;
  S.ideas.forEach((idea, index) => {
    if (list.children[index]) return;
    const key = 'idea-' + idea.id, opened = !!viewState().expanded[key];
    const article = document.createElement('article'); article.className = 'idea-card rf-reveal';
    article.style.setProperty('--i', Math.min(added++, 12));
    article.innerHTML = `<span class="rank">构想 ${String(index + 1).padStart(2,'0')}</span><h3>${esc(idea.title)}</h3>
      <div id="idea-abstract-${index}" class="abs${opened ? ' open' : ''}">${esc(idea.abstract)}</div>
      <button class="text-btn" aria-expanded="${opened}" aria-controls="idea-abstract-${index}">${opened ? '收起摘要' : '展开摘要'}</button>`;
    article.querySelector('button').onclick = event => {
      const open = article.querySelector('.abs').classList.toggle('open');
      event.currentTarget.textContent = open ? '收起摘要' : '展开摘要'; event.currentTarget.setAttribute('aria-expanded', String(open));
      viewState().expanded[key] = open; saveView();
    };
    list.appendChild(article);
  });
}
function reviewedIdeaCount(task = S.task) {
  const ids = new Set((task?.ideas || []).map(idea => String(idea.id)));
  return new Set((task?.judge_batches || []).flatMap(batch => batch.idea_ids || [])
    .map(String).filter(id => ids.has(id))).size;
}
function renderTop5() {
  const root = $('s2-body');
  if (!S.top5.length) {
    const target = selectionTarget(), task = S.task, reviewed = reviewedIdeaCount(task);
    const batches = task?.judge_batches?.length || 0;
    const started = target > 0 && (reviewed > 0 || task?.stage === 2);
    const active = started && ['running','queued','stopping'].includes(task?.status);
    const title = target === 0 ? '已跳过评审' : started ? (active ? '正在评审' : '评审已暂停，结果已保留') : '等待评审';
    const detail = target === 0 ? '本次未安排筛选。' : started ?
      `已评审 ${reviewed} / ${S.count} 个（${batches} 批），完成后展示前 ${target} 名。` :
      '构想完成后开始评审';
    const message = started ? task?.message || '' : '';
    const signature = JSON.stringify(['review-progress',title,detail,message]);
    if (root.dataset.signature !== signature) {
      root.dataset.signature = signature;
      root.innerHTML = `<div class="empty-state" role="status" aria-live="polite">${icon('book')}<h3>${esc(title)}</h3><p>${esc(detail)}</p>${message ? `<p>${esc(message)}</p>` : ''}</div>`;
    }
    return;
  }
  const signature = JSON.stringify(S.top5);
  if (root.dataset.signature === signature) return;
  root.dataset.signature = signature;
  root.innerHTML = S.top5.map((item, index) => {
    const idea = findIdea(item) || {title: '构想 ' + item.id};
    const scores = DIMS.map(([key, label, full]) => {
      if (item.rubric_version === 'gen_prompt_2') label = ({dialogue:'理论贡献', policy:'现实解释力'})[key] || label;
      const score = Number(item.scores?.[key] ?? 0);
      return `<div class="sbar"><div class="t"><span>${label}</span><span>${score}/${full}</span></div><div class="track"><div class="fillb" style="width:${Math.max(0,Math.min(100,score/full*100))}%"></div></div></div>`;
    }).join('');
    return `<article class="top-item"><div class="top-head"><span class="medal">${String(item.rank || index+1).padStart(2,'0')}</span><h3>${esc(idea.title)}</h3><span class="total">${Number(item.total || 0)} <small>/ 100</small></span></div>
      ${item.hook ? `<p class="hook">${esc(item.hook)}</p>` : ''}
      <details class="score-details" data-view-key="score-${index}"><summary>评分与评语</summary><div class="score-bars">${scores}</div><p class="reason">${esc(item.reason || '')}</p></details></article>`;
  }).join('');
  bindExpansions(root);
}
function resultPicker(root, kind, items, titleOf, onChange) {
  const view = viewState(), key = kind + 'Id';
  if (!items.some(item => String(item.id) === String(view[key]))) view[key] = String(items[0].id);
  let picker = root.querySelector('.result-picker');
  if (!picker) {
    root.replaceChildren(); picker = document.createElement('div'); picker.className = 'result-picker';
    picker.innerHTML = `<label for="${kind}-select">${kind === 'lit' ? '构想' : '结果'}</label><select id="${kind}-select" aria-label="选择${kind === 'lit' ? '文献对应构想' : '文献判断与提案'}"></select>
      <span class="picker-position"></span><button class="icon-btn picker-prev" aria-label="上一${kind === 'lit' ? '组文献' : '项结果'}">‹</button><button class="icon-btn picker-next" aria-label="下一${kind === 'lit' ? '组文献' : '项结果'}">›</button>`;
    root.appendChild(picker);
    const content = document.createElement('div'); content.id = kind + '-content'; root.appendChild(content);
  }
  const select = picker.querySelector('select');
  // Updating options only when their identities change keeps keyboard focus stable.
  const optionSignature = JSON.stringify(items.map(item => [item.id, titleOf(item)]));
  if (select.dataset.signature !== optionSignature) {
    select.replaceChildren(...items.map((item,index) => {
      const option = document.createElement('option'); option.value = String(item.id);
      option.textContent = `${String(index+1).padStart(2,'0')} · ${titleOf(item)}`; return option;
    }));
    select.dataset.signature = optionSignature;
  }
  select.value = view[key];
  const selected = items.findIndex(item => String(item.id) === view[key]);
  picker.querySelector('.picker-position').textContent = `${selected+1} / ${items.length}`;
  const choose = index => {
    view[key] = String(items[index].id); saveView(); onChange();
  };
  select.onchange = () => choose(items.findIndex(item => String(item.id) === select.value));
  const prev = picker.querySelector('.picker-prev'), next = picker.querySelector('.picker-next');
  prev.disabled = selected === 0; next.disabled = selected === items.length-1;
  prev.onclick = () => choose(selected-1); next.onclick = () => choose(selected+1);
  return {item: items[selected], content: $(kind + '-content')};
}
function renderLiterature() {
  const root = $('s3-body');
  const available = S.top5.filter(item => Object.hasOwn(S.lit, String(item.id)));
  if (!available.length) { emptyStage(3, selectionTarget() === 0 ? '已跳过文献检索' : '等待文献检索', selectionTarget() === 0 ? '本次未安排筛选。' : '评审完成后检索文献'); return; }
  const {item, content} = resultPicker(root, 'lit', available, item => findIdea(item)?.title || '构想 ' + item.id, renderLiterature);
  const papers = S.lit[String(item.id)] || [], signature = JSON.stringify([item.id, papers]);
  if (content.dataset.signature === signature) return;
  content.dataset.signature = signature;
  const recent = papers.filter(paper => paper.selection_pool === 'recent').length;
  const zh = papers.filter(paper => paper.language === 'zh' || (!paper.language && (paper.ut || paper.source_id || '').startsWith('CNKI:'))).length;
  const en = papers.filter(paper => paper.language === 'en' || (!paper.language && (paper.ut || paper.source_id || '').startsWith('WOS:'))).length;
  content.innerHTML = `<p class="lit-heading">${papers.length} 篇文献 · 中文 ${zh} 篇 · 英文 ${en} 篇 · 近期 ${recent} 篇 · 全库补充 ${papers.length-recent} 篇</p>` + papers.map((paper,index) => {
    const doi = paper.doi ? ` · <a href="https://doi.org/${encodeURIComponent(paper.doi)}" target="_blank" rel="noopener noreferrer">DOI ↗</a>` : '';
    const semantic = Number.isFinite(paper.semantic_similarity) ? paper.semantic_similarity.toFixed(3) : '—';
    const score = Number.isFinite(paper.retrieval_score) ? paper.retrieval_score.toFixed(5) : '—';
    return `<article class="paper-row"><span class="yr">${esc(paper.year || '—')}</span><div class="pt">
      <h3 class="paper-title"><span class="source-tag">${esc(paper.reference_label || 'P'+(index+1))}</span>${esc(paper.title)}</h3>
      <p class="paper-authors">${esc(paper.authors || '未提供作者')}</p><p class="paper-journal">${esc(paper.journal)}${doi}</p>
      <details class="paper-evidence" data-view-key="paper-${esc(item.id)}-${index}"><summary>摘要与检索信息</summary><p>${esc(paper.abstract || '未提供摘要')}</p>
      <div class="retrieval-metrics">${esc(paper.retrieval_reason || '')} · ${esc(paper.selection_pool === 'recent' ? '近期检索' : '全库补充')}<br>RRF ${score} · 语义相似度 ${semantic}<br>${esc(paper.document_types || '')} · ${esc(paper.source_id || '')}</div></details>${readingBridgeSlot(index)}</div></article>`;
  }).join('') + (!papers.length ? '<div class="empty-state"><h3>未找到相关文献</h3></div>' : '');
  bindExpansions(content);
  connectResearchPapers(content,item.id,'literature',papers);
}
function referenceIdentity(ref) {
  return JSON.stringify([ref.reference_status, ref.source_id || '', ref.source_id ? '' : [ref.title,ref.authors,ref.year,ref.journal]]);
}
function shortReferenceStatus(ref) {
  return ({matched_local_record:'已匹配书目', unmatched_local_record:'待核对', external_unverified:'待核对',
    ambiguous_local_record:'存在多个匹配', conflicting_local_record:'书目信息冲突'})[ref.reference_status] || '待核对';
}
function renderProposals() {
  const root = $('s4-body');
  const items = S.top5.map(judge => ({id:judge.id, judge, check:literatureCheckOf(judge.id),
    ...S.proposals.find(record => record.idea.id === judge.id)})).filter(item => item.proposal || item.check?.decision === 'drop');
  if (!items.length) { emptyStage(4, selectionTarget() === 0 ? '已跳过提案' : '等待判断与提案', selectionTarget() === 0 ? '本次未安排筛选。' : '文献判断通过后生成提案'); return; }
  const {item, content} = resultPicker(root, 'proposal', items, item => `${item.check ? literatureDecisionLabel(item.check)+' · ' : ''}${item.proposal?.title_zh || findIdea(item)?.title || '研究构想'}`, renderProposals);
  const signature = JSON.stringify(item);
  if (content.dataset.signature === signature) return;
  content.dataset.signature = signature;
  if (!item.proposal) {
    content.innerHTML = `<article class="proposal-article"><p class="proposal-kicker">原始构想 ${esc(item.id)} · 初筛第 ${esc(item.judge.rank)} 名</p><h2 class="proposal-title">${esc(findIdea(item)?.title)}</h2>${literatureCheckHtml(item.id)}<p class="hint">判断已完成，未进入提案。</p></article>`;
    return;
  }
  const proposal = item.proposal, papers = S.lit[String(item.id)] || [];
  const refs = [...(proposal.key_references || [])];
  for (const hyp of proposal.hypotheses || []) {
    const ref = hyp.contribution || {};
    if (!refs.some(existing => referenceIdentity(existing) === referenceIdentity(ref))) refs.push(ref);
  }
  const referenceCards = refs.map((ref,index) => {
    const paper = ref.reference_status === 'matched_local_record' ? papers.find(p => p.source_id === ref.source_id) : null;
    return `<div class="reference-card" id="reference-${index}" tabindex="-1"><span class="reference-status${paper ? ' matched' : ''}">${esc(shortReferenceStatus(ref))}</span>
      <p class="reference-title">${esc(ref.title || [ref.authors,ref.year].filter(Boolean).join(' · '))}</p>
      <p class="reference-byline">${esc(ref.authors || '未提供作者')} (${esc(ref.year || '—')})<br>${esc(ref.journal || '')}</p>
      <details class="reference-details" data-view-key="reference-${esc(item.id)}-${index}"><summary>${paper ? '来源与摘要' : '来源详情'}</summary><p>${esc(referenceLabel(ref))}</p>
      ${ref.doi ? `<p><a href="https://doi.org/${encodeURIComponent(ref.doi)}" target="_blank" rel="noopener noreferrer">查看 DOI ↗</a></p>` : ''}
      ${paper ? `<p class="evidence-text">${esc(paper.abstract || '未提供摘要')}</p>` : ''}</details>${readingBridgeSlot(index)}</div>`;
  }).join('');
  const hypotheses = (proposal.hypotheses || []).map(hyp => {
    const contribution = hyp.contribution || {};
    const index = refs.findIndex(ref => referenceIdentity(ref) === referenceIdentity(contribution));
    return `<section class="hyp"><div class="hyp-top"><span class="hbadge">${esc(hyp.id || 'H')}</span><p>${esc(hyp.hypothesis)}</p></div>
      <div class="contribution"><p class="theory-line">目标理论 · <strong>${esc(contribution.target_theory || '—')}</strong></p><p class="prose">${esc(contribution.how || '—')}</p>
      <button class="citation-link" data-reference="${index}" aria-label="查看 ${esc(contribution.authors || '')} 的来源">${esc(contribution.authors || '来源')} · ${esc(contribution.year || '—')}<span> ↗</span></button></div></section>`;
  }).join('');
  content.innerHTML = `<div class="reader-grid"><article class="proposal-article"><p class="proposal-kicker">初筛第 ${String(item.judge.rank || 1).padStart(2,'0')} 名 · 优化提案</p>
    <h2 class="proposal-title">${esc(proposal.title_zh || proposal.title_en || '')}</h2>
    ${literatureCheckHtml(item.id) || '<p class="hint">历史提案未记录单独的文献判断。</p>'}
    <section class="article-section"><h3>中文摘要</h3><p class="prose">${esc(proposal.abstract_zh || proposal.abstract_en || '')}</p>
    ${proposal.title_en || proposal.abstract_en ? `<details class="translation" data-view-key="english-${esc(item.id)}"><summary>历史外文原稿 · 展开核对</summary><p class="prose" lang="en">${esc(proposal.title_en || '')}</p><p class="prose" lang="en">${esc(proposal.abstract_en || '')}</p></details>` : ''}</section>
    ${proposal.theoretical_foundation ? `<section class="article-section"><h3>对应理论</h3><p class="prose">${esc(proposal.theoretical_foundation)}</p></section>` : ''}
    <section class="article-section"><h3>研究假说与理论贡献</h3>${hypotheses}</section>
    ${proposal.improvement_notes ? `<details class="improvement" data-view-key="improvements-${esc(item.id)}"><summary>相对原构想的改进</summary><p class="prose">${esc(proposal.improvement_notes)}</p></details>` : ''}
    <div class="article-actions"><button class="btn btn-ghost p-export">${icon('download')}导出此提案</button><button class="text-btn show-original">查看原构想</button></div></article>
    <aside class="references-panel" aria-label="提案参考文献"><h3>参考文献 <span class="muted">${refs.length}</span></h3><p class="hint">书目已匹配，内容仍需核验。</p>${referenceCards || '<p class="muted">暂无参考文献</p>'}</aside></div>`;
  content.querySelector('.p-export').onclick = () => download(`proposal_top${item.judge.rank || 1}.md`, proposalMd(item.judge, proposal));
  content.querySelector('.show-original').onclick = () => {
    selectStage(1);
    requestAnimationFrame(() => {
      const index = S.ideas.findIndex(idea => String(idea.id) === String(item.id));
      const card = $('s1-body').querySelectorAll('.idea-card')[index];
      if (card) { card.scrollIntoView({block:'center',behavior:'instant'}); card.querySelector('button').focus({preventScroll:true}); }
    });
  };
  content.querySelectorAll('[data-reference]').forEach(button => button.onclick = () => {
    const card = $('reference-' + button.dataset.reference);
    if (card) {
      card.querySelector('details').open = true;
      card.scrollIntoView({block:'nearest',behavior:'instant'}); card.focus({preventScroll:true});
    }
  });
  bindExpansions(content);
  connectResearchPapers(content,item.id,'references',refs);
}
function renderResearchContents() { renderIdeas(); renderTop5(); renderLiterature(); renderProposals(); }
