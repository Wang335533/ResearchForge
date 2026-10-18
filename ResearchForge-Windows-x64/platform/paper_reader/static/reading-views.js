'use strict';
/* 待读清单 and 已读目录: two views beside the compose and report views. Neither calls a model. */
(() => {
  const $ = id => document.getElementById(id);
  const base = document.querySelector('meta[name="app-base"]')?.content || '';
  const url = path => base + path;
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icon = name => `<svg class="icon" aria-hidden="true"><use href="/ui/icons.svg#i-${name}"/></svg>`;
  const date = t => new Date(t * 1000).toLocaleDateString('zh-CN');
  const QUEUE = {unread:'未开始', reading:'阅读中', read:'已读', removed:'已移出'};
  const JOB = {completed:'已完成', running:'分析中', queued:'排队中', stopping:'正在停止', interrupted:'已中断', failed:'未完成', stopped:'已停止'};
  const SOURCE = {online:'文献网站书目', library:'本地论文库', upload:'上传'};

  async function api(path, options = {}) {
    const headers = {'X-PaperLens': '1', ...(options.body && !(options.body instanceof FormData) ? {'Content-Type': 'application/json'} : {})};
    const response = await fetch(url(path), {...options, headers});
    let data;
    try { data = await response.json(); } catch { throw new Error('服务未返回有效响应，请刷新页面。'); }
    if (!response.ok) throw new Error(data.error || '操作未完成，请重试。');
    return data;
  }
  const send = (path, method, body) => api(path, {method, body: body instanceof FormData ? body : JSON.stringify(body || {})});
  function say(id, text, bad = false) { $(id).textContent = text; $(id).classList.toggle('error', bad); }

  /* ---------------------------------------------------------------- 待读清单 */
  const queue = {status: 'active', page: 1, items: [], seq: 0, editing: null, attachTo: null, busy: false};
  function queueCard(i) {
    const id = esc(i.id), meta = [i.authors.length ? i.authors.join('；') : '作者待补充', i.year || '年份待补充', i.journal].filter(Boolean).join(' · ');
    const primary = i.status === 'removed'
      ? `<button class="btn btn-primary btn-sm" data-act="restore" data-id="${id}">恢复到清单</button>`
      : i.job_id ? `<a class="btn btn-primary btn-sm" href="${esc(url('/#' + i.job_id))}">${icon('book-open-text')}查看报告</a>`
      : i.has_source ? `<button class="btn btn-primary btn-sm" data-act="read" data-id="${id}">${icon('arrow-right')}开始精读</button>`
      : `<button class="btn btn-primary btn-sm" data-act="attach" data-id="${id}">${icon('upload')}补充全文</button>`;
    const extra = [
      i.job_id && i.has_source && i.status !== 'removed' ? `<button data-act="read" data-id="${id}">${icon('rotate-ccw')}再读一次</button>` : '',
      i.has_source ? `<a href="${esc(url('/api/reading-queue/' + encodeURIComponent(i.id) + '/source'))}" download>${icon('download')}下载原文件</a>` : '',
      i.library_uid ? `<button data-act="reveal" data-id="${id}">${icon('folder-open')}在资源管理器中定位</button>` : '',
      i.remote?.url ? `<a href="${esc(i.remote.url)}" target="_blank" rel="noopener noreferrer">${icon('external-link')}文献网站详情</a>` : '',
      `<button data-act="edit" data-id="${id}">${icon('pencil')}编辑信息与备注</button>`,
      i.status !== 'removed' ? `<button data-act="remove" data-id="${id}">${icon('trash-2')}移出清单</button>` : '',
    ].filter(Boolean).join('');
    return `<article class="queue-item${i.priority ? ' priority' : ''}" data-rf-key="${id}">
      <div class="queue-main"><div class="queue-badges"><span class="status ${i.status === 'read' ? 'done' : i.status === 'reading' ? 'running' : i.status === 'removed' ? 'stopped' : ''}">${QUEUE[i.status]}</span>${i.priority ? '<span class="queue-flag">优先</span>' : ''}${i.has_source ? '' : '<span class="queue-flag warn">待补全文</span>'}<span class="queue-source">${SOURCE[i.source_kind] || '上传'}</span></div>
        <h3><button data-act="edit" data-id="${id}">${esc(i.title)}</button></h3><p class="queue-meta">${esc(meta)}</p>${i.notes ? `<p class="queue-note">${esc(i.notes)}</p>` : ''}</div>
      <div class="queue-actions">${primary}<details class="export-menu queue-more"><summary class="btn btn-quiet btn-sm btn-icon" aria-label="更多操作">${icon('chevron-down')}</summary><div class="menu-items">${extra}</div></details></div></article>`;
  }
  function renderQueue() {
    const q = $('queue-query').value.trim();
    $('queue-list').innerHTML = queue.items.length ? queue.items.map(queueCard).join('')
      : `<div class="empty-state">${q ? icon('search') : '<i class="rf-vignette" data-v="reading" aria-hidden="true"></i>'}<strong>${q ? '没有匹配的论文' : queue.status === 'active' ? '清单还空着' : '这一栏还没有论文'}</strong><p>${q ? '换个词，或切换上方的阅读状态。' : '在全文论文库里点“加入待读”，或直接上传想读的论文。'}</p></div>`;
  }
  async function loadQueue(page = 1) {
    const seq = ++queue.seq;
    $('queue-list').setAttribute('aria-busy', 'true');
    try {
      const d = await api('/api/reading-queue?' + new URLSearchParams({q: $('queue-query').value, status: queue.status, page, project: window.RFProject.current()}));
      if (seq !== queue.seq) return;
      queue.items = d.items; queue.page = d.page; renderQueue();
      const active = d.counts.unread + d.counts.reading;
      document.querySelectorAll('#reading-queue [data-count]').forEach(b => { b.textContent = b.dataset.count === 'active' ? active : d.counts[b.dataset.count]; });
      document.querySelectorAll('#reading-queue [data-status]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.status === queue.status)));
      setNavCount(active);
      $('queue-pages').hidden = page === 1 && !d.has_more; $('queue-prev').disabled = page === 1; $('queue-next').disabled = !d.has_more;
      $('queue-page-label').textContent = '第 ' + page + ' 页';
    } catch (e) { if (seq === queue.seq) say('queue-message', e.message, true); }
    finally { if (seq === queue.seq) $('queue-list').removeAttribute('aria-busy'); }
  }
  function setNavCount(n) { const badge = $('queue-nav-count'); if (badge.textContent !== String(n)) badge.textContent = n; if (badge.hidden !== !n) badge.hidden = !n; }
  async function refreshCount() {
    try { const d = await api('/api/reading-queue?status=active&page=1' + window.RFProject.param('&')); setNavCount(d.counts.unread + d.counts.reading); } catch { /* the badge is optional */ }
  }
  async function uploadToQueue(files) {
    if (queue.busy || !files.length) return;
    queue.busy = true; $('queue-upload').disabled = true;
    let added = 0, duplicate = 0; const failed = [];
    try {
      for (let n = 0; n < files.length; n++) {
        const file = files[n]; say('queue-message', `正在保存 ${n + 1} / ${files.length}：${file.name}`);
        try {
          if (file.size > 40 * 1024 * 1024) throw new Error('超过 40 MB');
          const body = new FormData(); body.append('file', file); body.append('project_id', window.RFProject.concrete());
          const d = await send('/api/reading-queue/upload', 'POST', body);
          d.created ? added++ : duplicate++;
        } catch (e) { failed.push(file.name + '：' + e.message); }
      }
      queue.status = 'active'; $('queue-query').value = ''; await loadQueue();
      say('queue-message', `已加入 ${added} 篇` + (duplicate ? `，${duplicate} 篇已在清单中` : '') + '。' + (failed.length ? ' 未保存：' + failed.join('；') : ''), !!failed.length);
    } finally { queue.busy = false; $('queue-upload').disabled = false; $('queue-files').value = ''; }
  }
  function editQueue(item) {
    queue.editing = item;
    $('edit-title').value = item.title; $('edit-authors').value = item.authors.join('\n'); $('edit-year').value = item.year || '';
    $('edit-journal').value = item.journal || ''; $('edit-status').value = item.status; $('edit-priority').checked = item.priority;
    $('edit-notes').value = item.notes || ''; say('editor-error', '');
    if (!$('queue-editor').open) $('queue-editor').showModal();
  }
  $('queue-list').addEventListener('click', async e => {
    const b = e.target.closest('[data-act]'); if (!b) return;
    const item = queue.items.find(i => i.id === b.dataset.id); if (!item) return;
    b.closest('details')?.removeAttribute('open');
    const act = b.dataset.act;
    if (act === 'edit') return editQueue(item);
    if (act === 'attach') { queue.attachTo = item.id; $('attach-source').click(); return; }
    b.disabled = true;
    try {
      if (act === 'read') {
        const d = await send('/api/reading-queue/' + encodeURIComponent(item.id) + '/read', 'POST');
        location.href = url('/?document=' + encodeURIComponent(d.document_id) + '#new'); return;
      }
      if (act === 'reveal') { const d = await send('/api/library/reveal', 'POST', {uid: item.library_uid}); say('queue-message', d.message); return; }
      await send('/api/reading-queue/' + encodeURIComponent(item.id), 'PATCH', {status: act === 'restore' ? 'unread' : 'removed'});
      await loadQueue(queue.page);
      say('queue-message', act === 'restore' ? '已恢复到待读清单。' : '已移出清单，可在“已移出”中恢复。');
    } catch (err) { say('queue-message', err.message, true); }
    finally { b.disabled = false; }
  });
  $('queue-form').addEventListener('submit', async e => {
    e.preventDefault(); const item = queue.editing; if (!item) return;
    $('queue-save').disabled = true;
    try {
      await send('/api/reading-queue/' + encodeURIComponent(item.id), 'PATCH', {title: $('edit-title').value, authors: $('edit-authors').value.split('\n'),
        year: $('edit-year').value.trim(), journal: $('edit-journal').value, status: $('edit-status').value, priority: $('edit-priority').checked, notes: $('edit-notes').value});
      $('queue-editor').close(); await loadQueue(queue.page); say('queue-message', '已保存。');
    } catch (err) { say('editor-error', err.message, true); }
    finally { $('queue-save').disabled = false; }
  });
  $('attach-source').addEventListener('change', async () => {
    const id = queue.attachTo, file = $('attach-source').files[0]; if (!id || !file) return;
    try { const body = new FormData(); body.append('file', file); await send('/api/reading-queue/' + encodeURIComponent(id) + '/source', 'POST', body); await loadQueue(queue.page); say('queue-message', '全文已补上，可以开始精读。'); }
    catch (e) { say('queue-message', e.message, true); }
    finally { $('attach-source').value = ''; queue.attachTo = null; }
  });
  $('queue-upload').addEventListener('click', () => $('queue-files').click());
  $('queue-files').addEventListener('change', () => uploadToQueue([...$('queue-files').files]));
  $('queue-find').addEventListener('click', () => $('library-nav').click());
  document.querySelectorAll('#reading-queue [data-status]').forEach(b => b.addEventListener('click', () => { queue.status = b.dataset.status; loadQueue(); }));
  let queueTimer; $('queue-query').addEventListener('input', () => { clearTimeout(queueTimer); queueTimer = setTimeout(() => loadQueue(), 220); });
  $('queue-prev').addEventListener('click', () => loadQueue(queue.page - 1)); $('queue-next').addEventListener('click', () => loadQueue(queue.page + 1));
  /* Several papers can be dropped onto the list view at once. */
  $('reading-queue').addEventListener('dragover', e => { if ([...(e.dataTransfer?.types || [])].includes('Files')) { e.preventDefault(); $('reading-queue').classList.add('dragging'); } });
  $('reading-queue').addEventListener('dragleave', e => { if (!$('reading-queue').contains(e.relatedTarget)) $('reading-queue').classList.remove('dragging'); });
  $('reading-queue').addEventListener('drop', e => { e.preventDefault(); e.stopPropagation(); $('reading-queue').classList.remove('dragging'); uploadToQueue([...e.dataTransfer.files]); });

  /* ---------------------------------------------------------------- 已读目录 */
  const catalog = {page: 1, seq: 0, signature: ''};
  const reportLink = (p, v) => v.archived && p.paper_id ? url('/memory#' + encodeURIComponent(p.paper_id)) : url('/#' + encodeURIComponent(v.id));
  function fill(id, values, label) {
    const el = $(id), prior = el.value;
    el.replaceChildren(new Option(label, ''), ...values.map(v => new Option(v, v)));
    if (values.some(v => String(v) === prior)) el.value = prior;
  }
  function catalogRow(p) {
    const latest = p.versions[0], best = p.versions.find(v => v.status === 'completed') || latest;
    const authors = p.authors.length ? p.authors.slice(0, 6).join('；') + (p.authors.length > 6 ? ' 等' : '') : '作者待补充';
    return `<article class="catalog-item" data-rf-key="${esc(p.id)}">
      <div class="catalog-main"><h3><a href="${esc(reportLink(p, best))}">${esc(p.title)}</a></h3>
        <p class="queue-meta">${esc([authors, p.year || '年份待核实', p.journal].filter(Boolean).join(' · '))}</p>
        <div class="catalog-fields">${p.fields.map(f => `<span>${esc(f)}</span>`).join('')}</div>
        <details class="catalog-versions"><summary>${p.versions.length} 份分析记录 · 最近 ${esc(date(p.updated))}</summary>${p.versions.map(v => `<a href="${esc(reportLink(p, v))}"><span class="status-dot ${esc(v.status)}"></span>${esc(date(v.created))} · ${esc(JOB[v.status] || v.status)} · ${esc(v.model || '模型未记录')}${v.archived ? ' · 已移出记录' : ''}</a>`).join('')}</details></div>
      <div class="catalog-actions"><a class="btn btn-secondary btn-sm" href="${esc(reportLink(p, best))}">${p.completed ? '查看报告' : '继续'}</a>${p.paper_id ? `<a class="text-btn" href="${esc(url('/memory#' + encodeURIComponent(p.paper_id)))}">文献地图</a><a class="text-btn" href="${esc(url('/knowledge?paper=' + encodeURIComponent(p.paper_id)))}">变量记录</a>` : ''}</div></article>`;
  }
  async function loadCatalog(page = 1) {
    const seq = ++catalog.seq;
    $('catalog-list').setAttribute('aria-busy', 'true');
    try {
      const d = await api('/api/reading-catalog?' + new URLSearchParams({q: $('catalog-query').value, field: $('catalog-field').value, year: $('catalog-year').value, status: $('catalog-status').value, page, project: window.RFProject.current()}));
      if (seq !== catalog.seq) return;
      catalog.page = d.page; fill('catalog-field', d.fields, '全部领域'); fill('catalog-year', d.years, '全部年份');
      $('catalog-summary').textContent = `${d.counts.papers} 篇论文 · ${d.counts.completed} 篇有完整报告 · ${d.counts.reports} 份分析记录。同一篇论文的多次精读归在一起。`;
      say('catalog-message', $('catalog-query').value || $('catalog-field').value || $('catalog-year').value || $('catalog-status').value !== 'all' ? `找到 ${d.total} 篇` : '');
      $('catalog-list').innerHTML = d.items.length ? d.items.map(catalogRow).join('')
        : `<div class="empty-state"><i class="rf-vignette" data-v="reading" aria-hidden="true"></i><strong>这个范围还没有阅读记录</strong><p>调整筛选，或开始一篇新的精读；完成的报告会自动整理到这里。</p></div>`;
      $('catalog-pages').hidden = page === 1 && !d.has_more; $('catalog-prev').disabled = page === 1; $('catalog-next').disabled = !d.has_more;
      $('catalog-page-label').textContent = '第 ' + page + ' 页';
    } catch (e) { if (seq === catalog.seq) say('catalog-message', e.message, true); }
    finally { if (seq === catalog.seq) $('catalog-list').removeAttribute('aria-busy'); }
  }
  $('catalog-form').addEventListener('submit', e => { e.preventDefault(); loadCatalog(); });
  ['catalog-field', 'catalog-year', 'catalog-status'].forEach(id => $(id).addEventListener('change', () => loadCatalog()));
  let catalogTimer; $('catalog-query').addEventListener('input', () => { clearTimeout(catalogTimer); catalogTimer = setTimeout(() => loadCatalog(), 250); });
  $('catalog-prev').addEventListener('click', () => loadCatalog(catalog.page - 1)); $('catalog-next').addEventListener('click', () => loadCatalog(catalog.page + 1));

  /* ---------------------------------------------------------------- view switching */
  const VIEWS = {queue: 'reading-queue', directory: 'reading-directory'};
  function show(view) {
    for (const [name, id] of Object.entries(VIEWS)) $(id).hidden = view !== name;
    document.querySelectorAll('[data-reading-view]').forEach(a => a.classList.toggle('active', a.dataset.readingView === view));
    if (view === 'queue') loadQueue(queue.page);
    if (view === 'directory') loadCatalog(catalog.page);
  }
  window.ReadingViews = {
    show,
    current: () => Object.keys(VIEWS).find(name => !$(VIEWS[name]).hidden) || null,
    // Called after each history poll: refresh the open directory only when the records changed.
    sync(jobs) {
      const signature = jobs.map(j => j.id + ':' + j.status + ':' + j.updated).join('|');
      if (signature === catalog.signature) return;
      const first = !catalog.signature; catalog.signature = signature;
      if (!first && !$('reading-directory').hidden) loadCatalog(catalog.page);
      if (!first) refreshCount();
    },
    refreshCount,
    editQueue: async id => { try { editQueue(await api('/api/reading-queue/' + encodeURIComponent(id))); } catch (e) { say('queue-message', e.message, true); } },
  };
  refreshCount();
})();
