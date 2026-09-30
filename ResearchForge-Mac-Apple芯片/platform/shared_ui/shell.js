/* Workspace shell behaviour shared by every module: appearance switching, the mobile
   drawer shade, in-page view transitions and the Ctrl/⌘+K command palette.
   It only reads existing local APIs and never starts a model call. */
(function () {
  'use strict';
  var ICONS = '/ui/icons.svg';
  var meta = document.querySelector('meta[name="app-base"]');
  // Research pages carry no app-base; reader pages carry "" (standalone) or "/papers".
  var READER = meta ? meta.content : '/papers';
  var INTEGRATED = !meta || meta.content !== '';
  var IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  var reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function icon(name) { return '<svg class="icon" aria-hidden="true"><use href="' + ICONS + '#i-' + name + '"/></svg>'; }

  /* ---------- In-page transitions ---------- */
  // type "rf-inpage" animates the whole content column; "rf-stage" only the element named rf-panel.
  function transition(update, type) {
    if (!document.startViewTransition || reducedMotion.matches || document.visibilityState !== 'visible') { update(); return; }
    try { document.startViewTransition({ update: update, types: [type || 'rf-inpage'] }); }
    catch (error) { document.startViewTransition(update); }
  }

  /* ---------- Appearance ---------- */
  var THEMES = ['system', 'light', 'dark'];
  var THEME_LABEL = { system: '跟随系统', light: '浅色', dark: '深色' };
  var THEME_ICON = { system: 'monitor', light: 'sun', dark: 'moon' };
  function currentTheme() {
    try { var t = localStorage.getItem('rf-theme'); return THEMES.indexOf(t) >= 0 ? t : 'system'; } catch (e) { return 'system'; }
  }
  function applyTheme(theme) {
    var root = document.documentElement;
    if (theme === 'system') root.removeAttribute('data-theme'); else root.setAttribute('data-theme', theme);
    try { if (theme === 'system') localStorage.removeItem('rf-theme'); else localStorage.setItem('rf-theme', theme); } catch (e) {}
    paintThemeButtons();
  }
  function cycleTheme() {
    var next = THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length];
    transition(function () { applyTheme(next); });
    return next;
  }
  function paintThemeButtons() {
    var theme = currentTheme();
    document.querySelectorAll('[data-rf-theme-toggle]').forEach(function (button) {
      button.innerHTML = icon(THEME_ICON[theme]);
      button.title = '外观：' + THEME_LABEL[theme] + '（点击切换）';
      button.setAttribute('aria-label', '切换外观，当前' + THEME_LABEL[theme]);
    });
  }

  /* ---------- Mobile drawer shade for pages that toggle .sidebar.open ---------- */
  function setupDrawer() {
    var sidebar = document.querySelector('.sidebar');
    if (!sidebar || document.getElementById('sidebar-shade')) return; // The research page manages its own.
    var shade = document.createElement('div');
    shade.className = 'rf-shade';
    shade.setAttribute('aria-hidden', 'true');
    document.body.appendChild(shade);
    var sync = function () { shade.classList.toggle('on', sidebar.classList.contains('open')); };
    new MutationObserver(sync).observe(sidebar, { attributes: true, attributeFilter: ['class'] });
    shade.addEventListener('click', function () { sidebar.classList.remove('open'); });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && sidebar.classList.contains('open') && !document.querySelector('dialog[open]')) sidebar.classList.remove('open');
    });
  }

  /* ---------- Command palette ---------- */
  var STATUS = { queued: '排队中', running: '进行中', stopping: '正在停止', stopped: '已停止', failed: '未完成', interrupted: '已中断', completed: '已完成' };
  function shortDate(seconds) {
    if (!seconds) return '';
    return new Date(seconds * 1000).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
  }
  function compact(text, n) {
    var chars = Array.from(String(text || '').replace(/\s+/g, ' ').trim());
    return chars.length > n ? chars.slice(0, n).join('') + '…' : chars.join('');
  }
  function actions() {
    var list = [
      { title: '新建研究', sub: '研究构想', icon: 'plus', href: '/?new=1', click: '#btn-new', keys: 'new research xinjian yanjiu', only: INTEGRATED },
      { title: '新建精读', sub: '论文精读', icon: 'plus', href: READER + '/#new', click: '#new', keys: 'new reading jingdu' },
      { title: '搜索本地论文库', sub: '论文精读', icon: 'search', href: READER + '/?panel=library', click: '#library-nav', keys: 'library search lunwenku' },
      { title: '共享 API 设置', sub: '论文精读 · 研究构想共用', icon: 'key-round', href: READER + '/?panel=settings', click: '#settings', keys: 'api key settings shezhi miyao' },
      { title: '新建提示词', sub: '提示词库', icon: 'notebook-pen', href: READER + '/prompt-library?new=1', click: '#new-prompt', keys: 'prompt new tishici' },
      { title: '精读运行提示词', sub: '论文精读', icon: 'scroll-text', href: READER + '/?panel=prompts', click: '#prompts', keys: 'prompt reader tishici' },
      { title: '研究运行提示词', sub: '研究构想', icon: 'scroll-text', href: '/?prompts=1', click: '#btn-prompts-nav', keys: 'prompt research tishici', only: INTEGRATED },
      { title: '切换外观', sub: '当前：' + THEME_LABEL[currentTheme()], icon: THEME_ICON[currentTheme()], run: function () { cycleTheme(); }, keep: true, keys: 'theme dark light waiguan shense qianse' },
      { title: '使用指南', sub: '配置与数据位置', icon: 'circle-help', href: '/setup', keys: 'help guide zhinan', only: INTEGRATED },
      { title: '架构说明', sub: '模块与接口', icon: 'layers', href: '/architecture', keys: 'architecture jiagou', only: INTEGRATED }
    ];
    return list.filter(function (item) { return item.only !== false; });
  }
  function modules() {
    var list = [
      { title: '研究构想', sub: '构想 → 评审 → 文献 → 提案', icon: 'lightbulb', href: '/', only: INTEGRATED },
      { title: '论文精读', sub: '精读报告与研究对比', icon: 'book-open-text', href: READER + '/' },
      { title: '文献地图', sub: '领域、主题与关联', icon: 'waypoints', href: READER + '/memory' },
      { title: '提示词库', sub: '收藏与复用', icon: 'library-big', href: READER + '/prompt-library' }
    ];
    return list.filter(function (item) { return item.only !== false; });
  }
  var SOURCES = [
    { key: 'tasks', group: '研究', only: INTEGRATED, url: function () { return '/api/tasks'; },
      map: function (d) { return (d.tasks || []).map(function (t) { return { title: compact(t.topic, 60), sub: (STATUS[t.status] || t.status) + ' · ' + shortDate(t.created_at), icon: 'lightbulb', href: '/?task=' + encodeURIComponent(t.id), keys: t.topic }; }); } },
    { key: 'jobs', group: '精读记录', url: function () { return READER + '/api/jobs'; },
      map: function (d) { return (d.jobs || []).map(function (j) { return { title: j.title, sub: (STATUS[j.status] || j.status) + ' · ' + shortDate(j.created), icon: 'book-open-text', href: READER + '/#' + j.id }; }); } },
    { key: 'memory', group: '文献地图', url: function () { return READER + '/api/memory'; },
      map: function (d) { return (d.papers || []).map(function (p) { return { title: p.title, sub: [(p.authors || []).slice(0, 2).join(' · '), p.year, (p.locations && p.locations[0] ? p.locations[0].field : '')].filter(Boolean).join(' · '), icon: 'waypoints', href: READER + '/memory#' + p.id, keys: (p.tags || []).join(' ') }; }); } },
    { key: 'prompts', group: '提示词', url: function (q) { return READER + '/api/prompt-library?' + new URLSearchParams({ view: 'all', page: '1', q: q || '' }); },
      live: true,
      map: function (d) { return (d.items || []).map(function (r) { return { title: r.title, sub: r.category, icon: 'library-big', href: READER + '/prompt-library#' + r.id, keys: (r.tags || []).join(' ') + ' ' + (r.excerpt || '') }; }); } }
  ].filter(function (s) { return s.only !== false; });

  var K = { dialog: null, input: null, list: null, status: null, items: [], active: 0, cache: {}, loading: 0, liveTimer: null, liveQuery: '', returnFocus: null };

  function fetchJSON(url) {
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, 5000);
    return fetch(url, { cache: 'no-store', signal: controller.signal })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .finally(function () { clearTimeout(timer); });
  }
  function loadSource(source, query) {
    var id = source.key + (source.live ? '|' + (query || '') : '');
    var hit = K.cache[id];
    if (hit && Date.now() - hit.at < 20000) return Promise.resolve(hit.items);
    K.loading++; paintStatus();
    return fetchJSON(source.url(query)).then(function (data) {
      var items = source.map(data).map(function (item) { item.group = source.group; return item; });
      K.cache[id] = { at: Date.now(), items: items };
      return items;
    }).catch(function () { return (K.cache[id] && K.cache[id].items) || []; })
      .finally(function () { K.loading--; paintStatus(); });
  }
  function paintStatus() { if (K.status) K.status.textContent = K.loading ? '正在读取…' : ''; }

  function buildPalette() {
    var dialog = document.createElement('dialog');
    dialog.className = 'rf-kbar';
    dialog.setAttribute('aria-label', '命令面板');
    dialog.innerHTML =
      '<div class="rf-kbar-head">' + icon('search') +
      '<input class="rf-kbar-input" type="text" role="combobox" aria-expanded="true" aria-controls="rf-kbar-list" aria-autocomplete="list" autocomplete="off" spellcheck="false" placeholder="搜索研究、论文、提示词或操作">' +
      '<kbd>Esc</kbd></div>' +
      '<div class="rf-kbar-list" id="rf-kbar-list" role="listbox" aria-label="搜索结果"></div>' +
      '<div class="rf-kbar-foot"><span><kbd>↑</kbd><kbd>↓</kbd>选择</span><span><kbd>↵</kbd>打开</span><span><kbd>Esc</kbd>关闭</span><span class="rf-kbar-status" role="status"></span></div>';
    document.body.appendChild(dialog);
    K.dialog = dialog;
    K.input = dialog.querySelector('.rf-kbar-input');
    K.list = dialog.querySelector('.rf-kbar-list');
    K.status = dialog.querySelector('.rf-kbar-status');
    K.input.addEventListener('input', function () { K.active = 0; render(); scheduleLive(); });
    K.input.addEventListener('keydown', onKey);
    K.list.addEventListener('mousemove', function (event) {
      var row = event.target.closest('.rf-kbar-item');
      if (row && Number(row.dataset.index) !== K.active) { K.active = Number(row.dataset.index); paintActive(false); }
    });
    K.list.addEventListener('click', function (event) {
      var row = event.target.closest('.rf-kbar-item');
      if (row) choose(K.items[Number(row.dataset.index)]);
    });
    dialog.addEventListener('click', function (event) { if (event.target === dialog) dialog.close(); });
    dialog.addEventListener('close', function () {
      if (K.returnFocus && document.contains(K.returnFocus)) K.returnFocus.focus({ preventScroll: true });
    });
  }
  function openPalette() {
    if (!K.dialog) buildPalette();
    if (K.dialog.open) { K.input.select(); return; }
    K.returnFocus = document.activeElement;
    K.input.value = '';
    K.active = 0;
    K.dialog.showModal();
    render();
    SOURCES.forEach(function (source) { loadSource(source, '').then(render); });
  }
  function scheduleLive() {
    clearTimeout(K.liveTimer);
    var q = K.input.value.trim();
    K.liveTimer = setTimeout(function () {
      SOURCES.filter(function (s) { return s.live && q; }).forEach(function (s) {
        K.liveQuery = q;
        loadSource(s, q).then(function () { if (K.input.value.trim() === q) render(); });
      });
    }, 180);
  }
  function tokensOf(q) { return q.toLowerCase().split(/\s+/).filter(Boolean); }
  function matches(item, tokens) {
    if (!tokens.length) return true;
    var hay = (item.title + ' ' + (item.sub || '') + ' ' + (item.keys || '') + ' ' + (item.group || '')).toLowerCase();
    return tokens.every(function (t) { return hay.indexOf(t) >= 0; });
  }
  function score(item, q) {
    var title = item.title.toLowerCase();
    if (!q) return 0;
    return title.indexOf(q) === 0 ? 0 : title.indexOf(q) > 0 ? 1 : 2;
  }
  function highlight(title, q) {
    var tokens = tokensOf(q).map(function (t) { return t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); });
    if (!tokens.length) return esc(title);
    return title.split(new RegExp('(' + tokens.join('|') + ')', 'ig')).map(function (part, i) {
      return i % 2 ? '<mark>' + esc(part) + '</mark>' : esc(part);
    }).join('');
  }
  function sourceItems(source, q) {
    var id = source.key + (source.live ? '|' + (q || '') : '');
    var hit = K.cache[id] || (source.live ? K.cache[source.key + '|'] : null);
    return hit ? hit.items : [];
  }
  function render() {
    if (!K.dialog) return;
    var raw = K.input.value.trim(), q = raw.toLowerCase(), tokens = tokensOf(raw);
    var groups = [];
    var acts = actions().filter(function (a) { return matches(a, tokens); });
    var mods = modules().filter(function (m) { return matches(m, tokens); });
    if (!q) {
      groups.push(['快速操作', acts.slice(0, 6)]);
      groups.push(['切换模块', mods]);
    }
    SOURCES.forEach(function (source) {
      var list = sourceItems(source, raw).filter(function (item) { return source.live && q ? true : matches(item, tokens); });
      list = list.slice().sort(function (a, b) { return score(a, q) - score(b, q); });
      groups.push([q ? source.group : '最近' + source.group.replace(/记录$/, ''), list.slice(0, q ? 8 : 4)]);
    });
    if (q) { groups.push(['切换模块', mods]); groups.push(['操作', acts]); }
    K.items = [];
    var html = '';
    groups.forEach(function (group) {
      if (!group[1].length) return;
      html += '<div class="rf-kbar-group" role="presentation">' + esc(group[0]) + '</div>';
      group[1].forEach(function (item) {
        var index = K.items.push(item) - 1;
        html += '<div class="rf-kbar-item" role="option" id="rf-kbar-opt-' + index + '" data-index="' + index + '" aria-selected="false">' +
          '<span class="rf-kbar-icon">' + icon(item.icon) + '</span>' +
          '<span class="rf-kbar-text"><span class="rf-kbar-title">' + highlight(item.title, raw) + '</span>' +
          (item.sub ? '<span class="rf-kbar-sub">' + esc(item.sub) + '</span>' : '') + '</span>' +
          '<span class="rf-kbar-enter">' + icon(item.run ? 'corner-down-left' : 'arrow-right') + '</span></div>';
      });
    });
    if (!K.items.length) html = '<div class="rf-kbar-empty">没有找到「' + esc(raw) + '」</div>';
    K.list.innerHTML = html;
    if (K.active >= K.items.length) K.active = Math.max(0, K.items.length - 1);
    paintActive(true);
  }
  function paintActive(scroll) {
    var rows = K.list.querySelectorAll('.rf-kbar-item');
    rows.forEach(function (row, i) { row.setAttribute('aria-selected', String(i === K.active)); });
    var current = rows[K.active];
    if (current) {
      K.input.setAttribute('aria-activedescendant', current.id);
      if (scroll) current.scrollIntoView({ block: 'nearest' });
    } else K.input.removeAttribute('aria-activedescendant');
  }
  function onKey(event) {
    if (event.isComposing) return;
    var n = K.items.length;
    if (event.key === 'ArrowDown') { event.preventDefault(); if (n) { K.active = (K.active + 1) % n; paintActive(true); } }
    else if (event.key === 'ArrowUp') { event.preventDefault(); if (n) { K.active = (K.active - 1 + n) % n; paintActive(true); } }
    else if (event.key === 'Home' && !K.input.value) { event.preventDefault(); K.active = 0; paintActive(true); }
    else if (event.key === 'End' && !K.input.value) { event.preventDefault(); K.active = Math.max(0, n - 1); paintActive(true); }
    else if (event.key === 'Enter') { event.preventDefault(); if (K.items[K.active]) choose(K.items[K.active]); }
  }
  function choose(item) {
    if (item.run) {
      item.run();
      if (item.keep) { render(); return; }
      K.dialog.close();
      return;
    }
    // The chosen destination decides focus; do not pull it back to the trigger.
    K.returnFocus = null;
    K.dialog.close();
    var target = item.click ? document.querySelector(item.click) : null;
    if (target && !target.disabled) { target.click(); return; }
    var url = new URL(item.href, location.origin);
    if (url.pathname === location.pathname) {
      var task = url.searchParams.get('task'), id = url.hash.slice(1);
      if (task && typeof window.selectTask === 'function') { window.selectTask(task); return; }
      if (id && typeof window.openPaper === 'function') { window.openPaper(id); return; }
      if (id && typeof window.selectPrompt === 'function') { window.selectPrompt(id); return; }
      if (id && url.search === location.search) { location.hash = id; return; }
    }
    location.href = url.href;
  }

  /* ---------- Wiring ---------- */
  document.addEventListener('keydown', function (event) {
    if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && (event.key === 'k' || event.key === 'K')) {
      event.preventDefault();
      if (K.dialog && K.dialog.open) K.dialog.close(); else openPalette();
    }
  });
  function wire() {
    document.querySelectorAll('[data-rf-kbar]').forEach(function (button) { button.addEventListener('click', openPalette); });
    document.querySelectorAll('[data-rf-kbd]').forEach(function (kbd) { kbd.textContent = IS_MAC ? '⌘K' : 'Ctrl K'; });
    document.querySelectorAll('[data-rf-theme-toggle]').forEach(function (button) { button.addEventListener('click', cycleTheme); });
    paintThemeButtons();
    setupDrawer();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wire); else wire();

  window.RF = { transition: transition, openPalette: openPalette, cycleTheme: cycleTheme, icon: icon, readerBase: READER, integrated: INTEGRATED };
})();
