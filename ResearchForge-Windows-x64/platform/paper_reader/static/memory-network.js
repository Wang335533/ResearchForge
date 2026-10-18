/* 文献网络: the whole project's read papers, or one paper's local network.

   Overview: one card per read paper, grouped into islands by its primary field; only links between
   read papers are drawn. Focus (click a paper): the paper in the centre, read papers linked to it on an
   inner ring, and its own cited works on an outer ring grouped by the role they play in the report.
   A side panel lists the same items. Pan by dragging, zoom with the wheel, Esc returns. Patterns from
   Connected Papers (graph + list + detail), ResearchRabbit (navigate paper by paper) and Obsidian
   (global vs local graph). */
(() => {
  'use strict';
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const icon = n => window.RF?.icon?.(n) || '';
  const KIND = {  // relation kinds between read papers: class and label
    '引用': ['cite', '引用'], '理论关联': ['theory', '理论关联'], '方法借鉴': ['method', '方法借鉴'],
    '问题相近': ['near', '问题相近'], '支持': ['support', '支持'], '存在分歧': ['dispute', '存在分歧'],
  };
  const ROLE = {dialogue: '文献对话', theory: '理论来源', contribution: '贡献对照', measure: '测量来源', method: '方法借鉴', evidence: '结果比较', mention: '其他提及'};
  const ROLE_ORDER = Object.keys(ROLE);
  const PALETTE = 6;
  const S = {root: null, papers: [], edges: [], focus: null, trail: [], pick: null, role: '', hidden: new Set(), view: null, opts: {}, sig: ''};

  const hue = name => { let h = 0; for (const ch of String(name)) h = (h * 31 + ch.codePointAt(0)) >>> 0; return 'f' + (h % PALETTE); };
  const primary = p => p.locations?.[0]?.field || '待分类';
  const short = c => (c.authors?.[0] ? c.authors[0] + (c.authors.length > 2 || c.more ? ' 等' : c.authors.length === 2 ? ' 和 ' + c.authors[1] : '') + ' ' : '') + (c.year || '');
  const mainRole = c => ROLE_ORDER.find(r => c.roles?.includes(r)) || 'mention';
  const byline = p => (p.authors?.length ? p.authors.slice(0, 3).join(' · ') + (p.authors.length > 3 ? ' 等' : '') : '作者待核实') + ' · ' + (p.year || '年份待核实');
  const visibleEdges = () => S.edges.filter(e => !S.hidden.has(e.kind) && S.byId.has(e.source) && S.byId.has(e.target));
  const neighbours = id => visibleEdges().filter(e => e.source === id || e.target === id);

  /* ---------------------------------------------------------------- Layouts */
  function overviewLayout() {
    const groups = new Map();
    for (const p of S.papers) { const f = primary(p); if (!groups.has(f)) groups.set(f, []); groups.get(f).push(p); }
    const order = [...groups].sort((a, b) => (a[0] === '待分类') - (b[0] === '待分类') || b[1].length - a[1].length || a[0].localeCompare(b[0], 'zh-CN'));
    const W = 1120, CW = 228, CH = 104, nodes = new Map(), islands = [];
    let x = 0, y = 0, rowH = 0;
    for (const [field, list] of order) {
      list.sort((a, b) => (a.year || 9999) - (b.year || 9999) || a.title.localeCompare(b.title));
      const cols = Math.min(3, Math.max(1, Math.ceil(Math.sqrt(list.length * 1.4)))), rows = Math.ceil(list.length / cols);
      const w = cols * CW + 36, h = rows * CH + 74;
      if (x && x + w > W) { x = 0; y += rowH + 28; rowH = 0; }
      islands.push({field, x, y, w, h, n: list.length, tone: field === '待分类' ? 'fx' : hue(field)});
      list.forEach((p, i) => nodes.set(p.id, {x: x + 18 + (i % cols) * CW, y: y + 58 + Math.floor(i / cols) * CH, w: CW - 20, h: CH - 18}));
      x += w + 28; rowH = Math.max(rowH, h);
    }
    return {nodes, islands, width: Math.max(0, ...islands.map(i => i.x + i.w)), height: y + rowH};
  }
  function focusLayout(p) {
    const links = neighbours(p.id), others = [...new Set(links.map(e => e.source === p.id ? e.target : e.source))].map(id => S.byId.get(id));
    const cited = (p.cited || []).filter(c => !c.matched).slice(0, 48);
    const R1 = others.length ? Math.max(250, others.length * 34) : 0, R2 = Math.max(R1 + 170, 330, cited.length * 13);
    const nodes = new Map([[p.id, {x: -150, y: -52, w: 300, h: 104, center: true}]]);
    others.forEach((o, i) => {
      const a = -Math.PI / 2 + (others.length === 1 ? 0 : (i / others.length) * Math.PI * 2);
      nodes.set(o.id, {x: Math.cos(a) * R1 - 110, y: Math.sin(a) * R1 * .78 - 40, w: 220, h: 80});
    });
    // Outer ring: one sector per role, sized by its share of the cited works.
    const sectors = [], pills = [];
    const byRole = ROLE_ORDER.map(r => [r, cited.filter(c => mainRole(c) === r)]).filter(([, l]) => l.length);
    let start = -Math.PI / 2 + .12;
    const span = Math.PI * 2 - .24 * Math.max(1, byRole.length);
    for (const [role, list] of byRole) {
      const width = span * list.length / cited.length;
      list.forEach((c, i) => {
        const a = start + width * (list.length === 1 ? .5 : i / (list.length - 1)), r = R2 + (i % 2) * 46;
        pills.push({c, role, x: Math.cos(a) * r, y: Math.sin(a) * r * .82, a});
      });
      const mid = start + width / 2;
      // Role names sit just inside the ring of pills, where only the thin spokes pass.
      sectors.push({role, n: list.length, x: Math.cos(mid) * (R2 - 62), y: Math.sin(mid) * (R2 - 62) * .82});
      start += width + .24;
    }
    const extent = Math.max(R2 + 250, R1 + 200);
    return {nodes, links, others, pills, sectors, extent, hiddenCited: Math.max(0, (p.cited || []).filter(c => !c.matched).length - cited.length)};
  }

  /* ---------------------------------------------------------------- Drawing */
  // Several links between the same two papers fan out side by side instead of drawing over each other.
  function lanes(edges) {
    const groups = new Map();
    for (const e of edges) { const k = [e.source, e.target].sort().join('|'); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(e); }
    const lane = new Map();
    for (const list of groups.values()) list.forEach((e, i) => lane.set(e.id, list.length === 1 ? .14 : (i - (list.length - 1) / 2) * .34));
    return lane;
  }
  function edgePath(a, b, bend, flip) {
    const ax = a.x + a.w / 2, ay = a.y + a.h / 2, bx = b.x + b.w / 2, by = b.y + b.h / 2;
    // The bend is measured from a fixed orientation of the pair, so reversed links keep their lane.
    const sgn = flip ? -1 : 1, mx = (ax + bx) / 2, my = (ay + by) / 2, dx = (bx - ax) * sgn, dy = (by - ay) * sgn;
    return {d: `M${ax},${ay} Q${mx - dy * bend},${my + dx * bend} ${bx},${by}`, lx: mx - dy * bend / 2, ly: my + dx * bend / 2};
  }
  function edgeMarkup(e, pos, labelled, lane, text) {
    const a = pos.get(e.source), b = pos.get(e.target);
    if (!a || !b) return '';
    const [cls, label] = KIND[e.kind] || ['near', e.kind], path = edgePath(a, b, lane?.get(e.id) ?? .14, e.source > e.target);
    const tip = e.kind + (e.status === 'confirmed' ? ' · 已核实' : ' · 待核实建议') + '\n' + e.reason;
    return `<g class="nx-edge k-${cls} ${e.status === 'confirmed' ? 'is-confirmed' : ''}" data-edge="${esc(e.id)}" data-a="${esc(e.source)}" data-b="${esc(e.target)}"><path d="${path.d}"${cls === 'cite' ? ' marker-end="url(#nx-arrow)"' : ''}/><path class="nx-hit" d="${path.d}"><title>${esc(tip)}</title></path>${labelled ? `<text x="${path.lx}" y="${path.ly}">${esc(text || label)}</text>` : ''}</g>`;
  }
  function cardMarkup(p, n, extra = '') {
    const cited = (p.cited || []).length, links = neighbours(p.id).length, second = p.locations?.[1]?.field;
    return `<foreignObject x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" class="nx-node${n.center ? ' is-center' : ''}" data-node="${esc(p.id)}"><div xmlns="http://www.w3.org/1999/xhtml" class="nx-card ${hue(primary(p))}${extra}" tabindex="0" role="button" aria-label="${esc((n.center ? '当前论文：' : '查看') + p.title)}" title="${esc(p.title + '\n' + byline(p) + (second ? '\n跨领域：' + second : ''))}"><strong>${esc(p.title)}</strong><small><span>${esc(byline(p))}</span></small><em>${cited ? `<b>引 ${cited}</b>` : ''}${links ? `<b>关联 ${links}</b>` : ''}${second ? `<i title="跨领域：${esc(second)}" class="${hue(second)}"></i>` : ''}</em></div></foreignObject>`;
  }
  function overviewMarkup() {
    const L = overviewLayout(), edges = visibleEdges(), lane = lanes(edges);
    const islands = L.islands.map((g, i) => `<g class="nx-island ${g.tone}" style="--i:${i}"><rect x="${g.x}" y="${g.y}" width="${g.w}" height="${g.h}" rx="22"/><text class="nx-island-name" x="${g.x + 20}" y="${g.y + 32}">${esc(g.field)}</text><text class="nx-island-count" x="${g.x + g.w - 18}" y="${g.y + 32}" text-anchor="end">${g.n} 篇</text></g>`).join('');
    return {box: {x: -40, y: -40, w: L.width + 80, h: Math.max(380, L.height + 80)},
            svg: islands + `<g class="nx-edges">${edges.map(e => edgeMarkup(e, L.nodes, false, lane)).join('')}</g>` + S.papers.map(p => cardMarkup(p, L.nodes.get(p.id))).join('')};
  }
  function focusMarkup(p) {
    const F = focusLayout(p), c = F.nodes.get(p.id), cx = c.x + c.w / 2, cy = c.y + c.h / 2, lane = lanes(F.links);
    // One label per pair of papers, naming every kind of link between them, on the middle lane.
    const labels = new Map(), pairs = new Map();
    for (const e of F.links) { const k = [e.source, e.target].sort().join('|'); if (!pairs.has(k)) pairs.set(k, []); pairs.get(k).push(e); }
    for (const list of pairs.values()) labels.set(list[Math.floor((list.length - 1) / 2)].id, list.map(e => e.kind).join(' · '));
    const ring = F.pills.length ? `<ellipse class="nx-ring" cx="0" cy="0" rx="${F.extent - 250}" ry="${(F.extent - 250) * .82}"/>` : '';
    const spokes = F.pills.map(t => `<path class="nx-spoke r-${t.role}" data-pill="${esc(t.c.key)}" d="M${cx},${cy} L${t.x},${t.y}"/>`).join('');
    const pills = F.pills.map((t, i) => {
      const w = Math.min(176, 30 + short(t.c).length * 7.2), left = Math.cos(t.a) < -.2, x = left ? t.x - w : Math.cos(t.a) > .2 ? t.x : t.x - w / 2;
      return `<foreignObject x="${x}" y="${t.y - 15}" width="${w}" height="30" class="nx-pill-wrap" style="--i:${i}"><button xmlns="http://www.w3.org/1999/xhtml" type="button" class="nx-pill r-${t.role}${S.pick === t.c.key ? ' is-picked' : ''}" data-pill="${esc(t.c.key)}" title="${esc(t.c.label + '\n' + t.c.roles.map(r => ROLE[r]).join('、'))}">${esc(short(t.c))}</button></foreignObject>`;
    }).join('');
    const sectors = F.sectors.map(s => `<text class="nx-sector r-${s.role}" x="${s.x}" y="${s.y}" text-anchor="middle">${ROLE[s.role]} · ${s.n}</text>`).join('');
    const box = {x: -F.extent, y: -F.extent * .82 - 40, w: F.extent * 2, h: F.extent * 1.64 + 80};
    return {box, focus: F, svg: ring + spokes + sectors + `<g class="nx-edges">${F.links.map(e => edgeMarkup(e, F.nodes, labels.has(e.id), lane, labels.get(e.id))).join('')}</g>` + pills +
      F.others.map(o => cardMarkup(o, F.nodes.get(o.id))).join('') + cardMarkup(p, c)};
  }

  /* ---------------------------------------------------------------- Side panel */
  function panelOverview() {
    const groups = new Map();
    for (const p of S.papers) { const f = primary(p); if (!groups.has(f)) groups.set(f, []); groups.get(f).push(p); }
    const links = visibleEdges().length;
    return `<header class="nx-panel-head"><h3>本课题的文献网络</h3><p>${S.papers.length} 篇已读论文 · ${links} 条关联 · 按主领域分组。点一篇论文，查看它与其他已读论文的关系，以及它引用的文献。</p></header>` +
      [...groups].map(([f, list]) => `<section class="nx-group"><h4><i class="${f === '待分类' ? 'fx' : hue(f)}"></i>${esc(f)}<small>${list.length}</small></h4>${list.map(p => `<button type="button" class="nx-row" data-focus="${esc(p.id)}"><strong>${esc(p.title)}</strong><small>${esc(byline(p))}${(p.cited || []).length ? ' · 引 ' + p.cited.length : ''}${neighbours(p.id).length ? ' · 关联 ' + neighbours(p.id).length : ''}</small></button>`).join('')}</section>`).join('');
  }
  function panelFocus(p, F) {
    const fields = (p.locations || []).map((l, i) => `<span class="nx-field ${hue(l.field)}" title="${esc(l.topic + ' → ' + l.question)}">${i ? '跨领域 · ' : ''}${esc(l.field)}</span>`).join('');
    const rel = F.links.map(e => { const o = S.byId.get(e.source === p.id ? e.target : e.source), [cls] = KIND[e.kind] || ['near'];
      return `<button type="button" class="nx-rel" data-focus="${esc(o.id)}"><span class="nx-kind k-${cls}">${esc(e.kind)}${e.kind === '引用' ? (e.source === p.id ? ' →' : ' ←') : ''}</span><strong>${esc(o.title)}</strong><small>${esc(e.reason)}</small></button>`; }).join('');
    const cited = (p.cited || []).filter(c => !c.matched), counts = ROLE_ORDER.map(r => [r, cited.filter(c => mainRole(c) === r).length]).filter(([, n]) => n);
    const shown = cited.filter(c => !S.role || mainRole(c) === S.role);
    const picked = cited.find(c => c.key === S.pick);
    return `<header class="nx-panel-head is-focus"><div class="nx-fields">${fields}</div><h3>${esc(p.title)}</h3><p>${esc(byline(p))}</p><div class="nx-panel-actions"><button type="button" class="btn btn-primary btn-sm" data-open="${esc(p.id)}">${icon('book-open-text')}打开文献卡片</button><button type="button" class="btn btn-secondary btn-sm" data-back>${icon('arrow-left')}${S.trail.length ? '上一篇' : '全部论文'}</button></div></header>` +
      `<section class="nx-section"><h4>已读论文中的关联<small>${F.links.length}</small></h4>${rel || '<p class="nx-empty">还没有与其他已读论文的关联。读过它引用的文献后，会自动连线。</p>'}</section>` +
      `<section class="nx-section"><h4>它引用的文献<small>${cited.length}</small></h4>${picked ? pickedMarkup(picked) : ''}` +
      (counts.length > 1 ? `<div class="nx-roles"><button type="button" data-role="" aria-pressed="${!S.role}">全部</button>${counts.map(([r, n]) => `<button type="button" class="r-${r}" data-role="${r}" aria-pressed="${S.role === r}">${ROLE[r]} ${n}</button>`).join('')}</div>` : '') +
      (shown.length ? `<ol class="nx-cited">${shown.map(c => `<li><button type="button" class="nx-cite r-${mainRole(c)}${S.pick === c.key ? ' is-picked' : ''}" data-pill="${esc(c.key)}"><strong>${esc(short(c))}</strong><span>${esc(c.title || '题名待核实')}</span></button></li>`).join('')}</ol>` : '<p class="nx-empty">这份报告里没有识别到引用文献。</p>') +
      (F.hiddenCited ? `<p class="nx-empty">图中显示作用最重要的 48 篇，列表包含全部。</p>` : '') + `</section>`;
  }
  function pickedMarkup(c) {
    const lib = S.opts.libraryUrl ? S.opts.libraryUrl(c) : '';
    return `<div class="nx-picked r-${mainRole(c)}"><strong>${esc(c.label)}</strong><p>${c.roles.map(r => `<span class="nx-role r-${r}">${ROLE[r]}</span>`).join('')}</p>${(c.contexts || []).slice(0, 2).map(x => `<blockquote>${esc(x.text)}</blockquote>`).join('')}<div class="nx-picked-actions">${lib ? `<a href="${esc(lib)}" target="_blank" rel="noopener">${icon('search')}在全文库中查找</a>` : ''}${c.doi ? `<span>DOI：${esc(c.doi)}</span>` : ''}</div></div>`;
  }

  /* ---------------------------------------------------------------- Pan and zoom (viewBox) */
  function applyView() {
    const svg = S.root.querySelector('.nx-svg'), v = S.view;
    svg.setAttribute('viewBox', `${v.x} ${v.y} ${v.w} ${v.h}`);
    S.root.querySelector('[data-zoom-label]').textContent = Math.round(100 * S.fit.w / v.w) + '%';
  }
  function zoom(factor, cx, cy) {
    const v = S.view, w = Math.min(S.fit.w * 3, Math.max(S.fit.w / 4, v.w / factor)), k = w / v.w;
    cx ??= v.x + v.w / 2; cy ??= v.y + v.h / 2;
    S.view = {x: cx - (cx - v.x) * k, y: cy - (cy - v.y) * k, w, h: v.h * k};
    applyView();
  }
  function fit(box) {
    const stage = S.root.querySelector('.nx-stage'), sw = stage.clientWidth || 1100, sh = stage.clientHeight || 640, ratio = sw / sh;
    let {x, y, w, h} = box;
    // A small network is shown at natural size (at most 115%) instead of being blown up to fill the canvas.
    const minW = sw / 1.15, minH = sh / 1.15;
    if (w < minW) { x -= (minW - w) / 2; w = minW; }
    if (h < minH) { y -= (minH - h) / 2; h = minH; }
    if (w / h > ratio) { const nh = w / ratio; y -= (nh - h) / 2; h = nh; } else { const nw = h * ratio; x -= (nw - w) / 2; w = nw; }
    S.fit = {x, y, w, h}; S.view = {...S.fit}; applyView();
  }
  function bindStage() {
    const stage = S.root.querySelector('.nx-stage'), svg = S.root.querySelector('.nx-svg');
    const point = ev => { const r = svg.getBoundingClientRect(); return [S.view.x + (ev.clientX - r.left) / r.width * S.view.w, S.view.y + (ev.clientY - r.top) / r.height * S.view.h]; };
    stage.addEventListener('wheel', ev => { ev.preventDefault(); const [x, y] = point(ev); zoom(ev.deltaY < 0 ? 1.15 : 1 / 1.15, x, y); }, {passive: false});
    let drag = null;
    stage.addEventListener('pointerdown', ev => { if (ev.button || ev.target.closest('.nx-card, .nx-pill, .nx-hit')) return; drag = {x: ev.clientX, y: ev.clientY, v: {...S.view}}; stage.setPointerCapture(ev.pointerId); stage.classList.add('is-panning'); });
    stage.addEventListener('pointermove', ev => { if (!drag) return; const r = svg.getBoundingClientRect(); S.view = {...drag.v, x: drag.v.x - (ev.clientX - drag.x) / r.width * drag.v.w, y: drag.v.y - (ev.clientY - drag.y) / r.height * drag.v.h}; applyView(); });
    const end = () => { drag = null; stage.classList.remove('is-panning'); };
    stage.addEventListener('pointerup', end); stage.addEventListener('pointercancel', end);
    stage.addEventListener('dblclick', ev => { if (!ev.target.closest('.nx-card, .nx-pill')) { S.view = {...S.fit}; applyView(); } });
  }

  /* ---------------------------------------------------------------- Interaction */
  function hover(id) {
    const svg = S.root.querySelector('.nx-svg');
    svg.classList.toggle('is-hovering', !!id);
    if (!id) { svg.querySelectorAll('.is-lit').forEach(el => el.classList.remove('is-lit')); return; }
    const near = new Set([id]);
    svg.querySelectorAll('.nx-edge').forEach(g => { const on = g.dataset.a === id || g.dataset.b === id; g.classList.toggle('is-lit', on); if (on) { near.add(g.dataset.a); near.add(g.dataset.b); } });
    svg.querySelectorAll('.nx-node').forEach(n => n.classList.toggle('is-lit', near.has(n.dataset.node)));
  }
  function lightPill(key) {
    S.root.querySelectorAll('.nx-svg [data-pill]').forEach(el => el.classList.toggle('is-lit', el.dataset.pill === key));
    S.root.querySelector('.nx-svg').classList.toggle('is-tracing', !!key);
  }
  function setFocus(id, push = true) {
    if (id === S.focus) return;
    if (push && S.focus) S.trail.push(S.focus);
    if (!id) S.trail = [];
    S.focus = id; S.pick = null; S.role = '';
    draw(true);
  }
  function back() { setFocus(S.trail.pop() || null, false); }

  function bindRoot() {
    S.root.addEventListener('click', ev => {
      const t = ev.target;
      const node = t.closest('.nx-node'); if (node) { if (node.dataset.node === S.focus) S.opts.open?.(S.focus); else setFocus(node.dataset.node); return; }
      const focus = t.closest('[data-focus]'); if (focus) { setFocus(focus.dataset.focus); return; }
      const pill = t.closest('[data-pill]'); if (pill) { S.pick = S.pick === pill.dataset.pill ? null : pill.dataset.pill; draw(false); return; }
      if (t.closest('[data-back]')) { back(); return; }
      const open = t.closest('[data-open]'); if (open) { S.opts.open?.(open.dataset.open); return; }
      const role = t.closest('[data-role]'); if (role) { S.role = role.dataset.role; draw(false); return; }
      const kind = t.closest('[data-kind-toggle]'); if (kind) { const k = kind.dataset.kindToggle; S.hidden.has(k) ? S.hidden.delete(k) : S.hidden.add(k); draw(true); return; }
      const z = t.closest('[data-zoom]'); if (z) { z.dataset.zoom === 'fit' ? (S.view = {...S.fit}, applyView()) : zoom(z.dataset.zoom === 'in' ? 1.25 : .8); }
    });
    S.root.addEventListener('keydown', ev => {
      if (ev.key === 'Escape' && S.focus) { ev.preventDefault(); back(); }
      if ((ev.key === 'Enter' || ev.key === ' ') && ev.target.closest('.nx-card')) { ev.preventDefault(); ev.target.closest('.nx-node')?.dispatchEvent(new MouseEvent('click', {bubbles: true})); }
    });
    S.root.addEventListener('pointerover', ev => { const n = ev.target.closest('.nx-node'); if (n) hover(n.dataset.node); const p = ev.target.closest('[data-pill]'); if (p) lightPill(p.dataset.pill); });
    S.root.addEventListener('pointerout', ev => { if (ev.target.closest('.nx-node') && !ev.relatedTarget?.closest?.('.nx-node')) hover(null); if (ev.target.closest('[data-pill]') && !ev.relatedTarget?.closest?.('[data-pill]')) lightPill(null); });
  }

  function legend() {
    const kinds = [...new Set(S.edges.map(e => e.kind))];
    return kinds.map(k => { const [cls, label] = KIND[k] || ['near', k]; return `<button type="button" class="nx-legend k-${cls}" data-kind-toggle="${esc(k)}" aria-pressed="${!S.hidden.has(k)}"><i></i>${esc(label)}</button>`; }).join('') || '<span class="nx-legend-none">还没有关联</span>';
  }
  function draw(refit) {
    const p = S.focus && S.byId.get(S.focus);
    if (S.focus && !p) { S.focus = null; S.trail = []; }
    const M = p ? focusMarkup(p) : overviewMarkup();
    S.root.querySelector('.nx-crumbs').innerHTML = p ? `<button type="button" data-back>${icon('arrow-left')}${S.trail.length ? '上一篇' : '全部论文'}</button><span>/</span><strong title="${esc(p.title)}">${esc(p.title)}</strong>` : `<strong>全部论文</strong><span class="nx-tip">滚轮缩放 · 拖动空白处平移 · 双击复位 · 点论文看它的局部网络</span>`;
    S.root.querySelector('.nx-legends').innerHTML = legend();
    const svg = S.root.querySelector('.nx-svg');
    svg.classList.toggle('is-focus', !!p);
    svg.innerHTML = `<defs><marker id="nx-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z"/></marker></defs>` + M.svg;
    S.root.querySelector('.nx-panel').innerHTML = p ? panelFocus(p, M.focus) : panelOverview();
    if (refit || !S.view) fit(M.box); else applyView();
    if (S.pick) lightPill(S.pick);
    window.renderMathInElement?.(S.root.querySelector('.nx-panel'), {throwOnError: false, trust: false, strict: 'ignore'});
  }

  function render(container, {papers, edges, open, libraryUrl}) {
    S.opts = {open, libraryUrl};
    S.papers = papers; S.edges = edges.filter(e => e.status !== 'rejected'); S.byId = new Map(papers.map(p => [p.id, p]));
    const sig = JSON.stringify([papers.map(p => [p.id, p.updated]), edges.map(e => [e.id, e.status, e.kind])]);
    if (!container.querySelector('.nx') || !S.root?.isConnected) {
      container.innerHTML = `<div class="nx"><div class="nx-bar"><nav class="nx-crumbs" aria-label="网络位置"></nav><div class="nx-legends" aria-label="按关联类型显示"></div><div class="nx-zoom"><button type="button" data-zoom="out" aria-label="缩小">${icon('zoom-out')}</button><span data-zoom-label>100%</span><button type="button" data-zoom="in" aria-label="放大">${icon('zoom-in')}</button><button type="button" data-zoom="fit" aria-label="适合窗口" title="适合窗口">${icon('layers')}</button></div></div><div class="nx-body"><div class="nx-stage" tabindex="0" aria-label="文献网络画布"><svg class="nx-svg" role="img" aria-label="文献网络"></svg></div><aside class="nx-panel" aria-label="论文与引用"></aside></div></div>`;
      S.root = container.querySelector('.nx'); S.view = null;
      bindRoot(); bindStage();
    } else if (sig === S.sig) return;
    S.sig = sig;
    draw(!S.view);
  }
  const reset = () => { S.focus = null; S.trail = []; S.pick = null; S.view = null; };
  window.MemoryNetwork = {render, reset, focus: id => { if (S.byId?.has(id)) setFocus(id); }};
})();
