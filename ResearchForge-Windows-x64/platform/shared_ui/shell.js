/* Workspace shell behaviour shared by every module: appearance and motion switching, the mobile
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
    var chosen = document.documentElement && document.documentElement.getAttribute('data-rf-motion');
    if (!document.startViewTransition || chosen === 'off' || (reducedMotion.matches && chosen !== 'full') || document.visibilityState !== 'visible') { update(); return; }
    var applied = false, animation;
    var apply = function () { if (!applied) { applied = true; return update(); } };
    try { animation = document.startViewTransition({ update: apply, types: [type || 'rf-inpage'] }); }
    catch (error) {
      try { animation = document.startViewTransition(apply); }
      catch (unsupported) { apply(); return; }
    }
    // An unavailable/interrupted animation must not become an unhandled page error.
    animation.ready.catch(function () {});
    animation.finished.catch(function () {});
    animation.updateCallbackDone.catch(function (error) { console.error('界面更新失败', error); });
  }

  /* ---------- Appearance ---------- */
  var THEMES = ['time', 'light', 'dark', 'system'];
  var THEME_LABEL = { time: '跟随时间', system: '跟随系统', light: '浅色', dark: '深色' };
  var THEME_ICON = { time: 'sun-moon', system: 'monitor', light: 'sun', dark: 'moon' };
  var PART_LABEL = { dawn: '清晨', day: '白天', dusk: '傍晚', night: '夜间' };
  function currentTheme() {
    try { var t = localStorage.getItem('rf-theme'); return THEMES.indexOf(t) >= 0 ? t : 'time'; } catch (e) { return 'time'; }
  }
  function clockTheme() {
    var root = document.documentElement, now = window.RFClock.daypart();
    root.setAttribute('data-theme', now.dark ? 'dark' : 'light');
    root.setAttribute('data-rf-tone', now.part);
  }
  function applyTheme(theme) {
    var root = document.documentElement;
    root.removeAttribute('data-rf-tone');
    if (theme === 'time') clockTheme();
    else if (theme === 'system') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', theme);
    try { localStorage.setItem('rf-theme', theme); } catch (e) {}
    paintThemeButtons();
  }
  // Following the clock: check every minute and on returning to the page. Switching to dark in the
  // evening (or back at dawn) fades in slowly; dawn and dusk tints only ease the background washes.
  function followClock() {
    if (currentTheme() !== 'time' || !window.RFClock) return;
    var root = document.documentElement, now = window.RFClock.daypart();
    if (root.getAttribute('data-rf-tone') === now.part) return;
    var flip = root.getAttribute('data-theme') !== (now.dark ? 'dark' : 'light');
    var update = function () { clockTheme(); paintThemeButtons(); };
    if (flip) transition(update, 'rf-dusk'); else update();
  }
  setInterval(followClock, 60000);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) followClock(); });
  // Another tab changed the appearance: follow it here too.
  window.addEventListener('storage', function (event) { if (event.key === 'rf-theme') applyTheme(currentTheme()); });
  function themeHint(theme) {
    if (theme !== 'time' || !window.RFClock) return THEME_LABEL[theme];
    var now = window.RFClock.daypart(), clock = window.RFClock.clock;
    return '跟随时间，现在' + PART_LABEL[now.part] + (now.dark ? '，' + clock(now.lightFrom) + ' 转浅色' : '，' + clock(now.darkFrom) + ' 转深色');
  }
  function cycleTheme(event) {
    var next = THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length];
    var root = document.documentElement;
    // The new appearance washes in as a circle from wherever the switch was pressed.
    var source = event && event.currentTarget && event.currentTarget.getBoundingClientRect ? event.currentTarget.getBoundingClientRect() : null;
    var x = source ? source.left + source.width / 2 : window.innerWidth - 40, y = source ? source.top + source.height / 2 : 40;
    root.style.setProperty('--rf-theme-x', x + 'px');
    root.style.setProperty('--rf-theme-y', y + 'px');
    root.style.setProperty('--rf-theme-r', Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y)) + 'px');
    transition(function () { applyTheme(next); }, 'rf-theme');
    return next;
  }
  function paintThemeButtons() {
    var theme = currentTheme();
    document.querySelectorAll('[data-rf-theme-toggle]').forEach(function (button) {
      button.innerHTML = icon(THEME_ICON[theme]);
      button.title = '外观：' + themeHint(theme) + '（点击切换）';
      button.setAttribute('aria-label', '切换外观，当前' + THEME_LABEL[theme]);
    });
  }

  /* ---------- Motion preference ---------- */
  var MOTIONS = ['full', 'calm', 'off'];
  var MOTION_LABEL = { full: '完整动效', calm: '简洁动效', off: '关闭动效' };
  var MOTION_ICON = { full: 'sparkles', calm: 'wind', off: 'pause' };
  function currentMotion() {
    var saved = document.documentElement.getAttribute('data-rf-motion');
    if (MOTIONS.indexOf(saved) >= 0) return saved;
    return reducedMotion.matches ? 'off' : 'full';
  }
  function cycleMotion() {
    var next = MOTIONS[(MOTIONS.indexOf(currentMotion()) + 1) % MOTIONS.length];
    document.documentElement.setAttribute('data-rf-motion', next);
    try { localStorage.setItem('rf-motion', next); } catch (e) {}
    paintMotionButtons();
    return next;
  }
  function paintMotionButtons() {
    var motion = currentMotion();
    document.querySelectorAll('[data-rf-motion-toggle]').forEach(function (button) {
      button.innerHTML = icon(MOTION_ICON[motion]);
      button.title = MOTION_LABEL[motion] + '（点击切换：完整 / 简洁 / 关闭）';
      button.setAttribute('aria-label', '切换动效，当前' + MOTION_LABEL[motion]);
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
    var mobile = window.matchMedia('(max-width: 860px)');
    var workspace = document.querySelector('.workspace');
    var toggle = document.querySelector('#menu, #memory-menu, #prompt-menu');
    var wasOpen = false;
    var sync = function () {
      var open = mobile.matches && sidebar.classList.contains('open');
      shade.classList.toggle('on', open);
      if (!open && sidebar.contains(document.activeElement) && !document.querySelector('dialog[open]')) toggle?.focus();
      sidebar.toggleAttribute('inert', mobile.matches && !open);
      sidebar.setAttribute('aria-hidden', String(mobile.matches && !open));
      workspace?.toggleAttribute('inert', open);
      toggle?.setAttribute('aria-expanded', String(open));
      if (open && !wasOpen) sidebar.querySelector('a[href], button:not(:disabled), input:not(:disabled)')?.focus();
      wasOpen = open;
    };
    new MutationObserver(sync).observe(sidebar, { attributes: true, attributeFilter: ['class'] });
    mobile.addEventListener('change', function () { sidebar.classList.remove('open'); sync(); });
    shade.addEventListener('click', function () { sidebar.classList.remove('open'); });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && sidebar.classList.contains('open') && !document.querySelector('dialog[open]')) sidebar.classList.remove('open');
    });
    sync();
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
      { title: '待读清单', sub: '论文精读', icon: 'bookmark', href: READER + '/?view=queue', keys: 'queue toread daidu qingdan' },
      { title: '已读目录', sub: '论文精读', icon: 'list', href: READER + '/?view=directory', keys: 'catalog directory yidu mulu' },
      { title: '学者统计', sub: '论文精读', icon: 'users', href: READER + '/scholars', keys: 'scholars authors xuezhe tongji' },
      { title: '共享 API 设置', sub: '论文精读 · 研究构想共用', icon: 'key-round', href: READER + '/?panel=settings', click: '#settings', keys: 'api key settings shezhi miyao' },
      { title: '新建提示词', sub: '提示词库', icon: 'notebook-pen', href: READER + '/prompt-library?new=1', click: '#new-prompt', keys: 'prompt new tishici' },
      { title: '精读运行提示词', sub: '论文精读', icon: 'scroll-text', href: READER + '/?panel=prompts', click: '#prompts', keys: 'prompt reader tishici' },
      { title: '研究运行提示词', sub: '研究构想', icon: 'scroll-text', href: '/?prompts=1', click: '#btn-prompts-nav', keys: 'prompt research tishici', only: INTEGRATED },
      { title: '切换外观', sub: '当前：' + THEME_LABEL[currentTheme()], icon: THEME_ICON[currentTheme()], run: function () { cycleTheme(); }, keep: true, keys: 'theme dark light waiguan shense qianse' },
      { title: '切换动效', sub: '当前：' + MOTION_LABEL[currentMotion()], icon: MOTION_ICON[currentMotion()], run: function () { cycleMotion(); }, keep: true, keys: 'motion animation dongxiao donghua' },
      { title: '使用指南', sub: '配置与数据位置', icon: 'circle-help', href: '/setup', keys: 'help guide zhinan', only: INTEGRATED },
      { title: '架构说明', sub: '模块与接口', icon: 'layers', href: '/architecture', keys: 'architecture jiagou', only: INTEGRATED }
    ];
    return list.filter(function (item) { return item.only !== false; });
  }
  function modules() {
    var list = [
      { title: '研究构想', sub: '构想 → 评审 → 文献 → 提案', icon: 'lightbulb', href: '/?new=1', only: INTEGRATED },
      { title: '论文精读', sub: '精读报告与研究对比', icon: 'book-open-text', href: READER + '/' },
      { title: '文献地图', sub: '领域、主题与关联', icon: 'waypoints', href: READER + '/memory' },
      { title: '变量与术语', sub: '测量卡片与三层映射', icon: 'ruler', href: READER + '/knowledge' },
      { title: '提示词库', sub: '收藏与复用', icon: 'library-big', href: READER + '/prompt-library' },
      { title: '组会填报', sub: '精读报告写入腾讯文档表格', icon: 'table-2', href: READER + '/reading-sync' }
    ];
    return list.filter(function (item) { return item.only !== false; });
  }
  var SOURCES = [
    { key: 'tasks', group: '研究', only: INTEGRATED, url: function () { return '/api/tasks'; },
      map: function (d) { return (d.tasks || []).map(function (t) { return { title: compact(t.topic, 60), sub: (STATUS[t.status] || t.status) + ' · ' + shortDate(t.created_at), icon: 'lightbulb', href: '/?task=' + encodeURIComponent(t.id), keys: t.topic }; }); } },
    { key: 'jobs', group: '精读记录', url: function () { return READER + '/api/jobs' + (window.RFProject ? window.RFProject.param('?') : ''); },
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
        html += '<div class="rf-kbar-item" role="option" id="rf-kbar-opt-' + index + '" data-index="' + index + '" aria-selected="false" style="--i:' + index + '">' +
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

  /* ---------- Disclosure menus ---------- */
  // Export and apply menus close on an outside click, on Escape and after an item is chosen.
  var MENUS = 'details.export-menu[open], details.apply-menu[open]';
  document.addEventListener('click', function (event) {
    document.querySelectorAll(MENUS).forEach(function (menu) {
      var item = event.target.closest && event.target.closest('.menu-items button, .menu-items a, .apply-items a');
      if (!menu.contains(event.target) || (item && menu.contains(item))) menu.open = false;
    });
  });
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    document.querySelectorAll(MENUS).forEach(function (menu) { menu.open = false; menu.querySelector('summary').focus(); });
  });

  /* ---------- Confirmation for destructive actions ---------- */
  // RF.confirm({title, message, items, confirm, typed}) → Promise<boolean>. Lists exactly what goes; with
  // `typed`, the button unlocks only after that name is typed (for deleting a whole project).
  function confirmAction(options) {
    var dialog = document.createElement('dialog');
    dialog.className = 'dialog rf-confirm';
    dialog.setAttribute('aria-labelledby', 'rf-confirm-title');
    dialog.innerHTML = '<form method="dialog"><div class="dialog-head"><div><h2 id="rf-confirm-title">' + esc(options.title) + '</h2>' +
      (options.message ? '<p>' + esc(options.message) + '</p>' : '') + '</div></div><div class="dialog-body">' +
      (options.items && options.items.length ? '<ul class="rf-confirm-items">' + options.items.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>' : '') +
      (options.note ? '<p class="rf-confirm-note">' + esc(options.note) + '</p>' : '') +
      (options.typed ? '<label class="rf-confirm-typed">输入「' + esc(options.typed) + '」以确认<input autocomplete="off" spellcheck="false"></label>' : '') +
      '</div><div class="dialog-footer"><button class="btn btn-secondary" value="cancel" type="submit">取消</button>' +
      '<button class="btn btn-danger" value="ok" type="submit"' + (options.typed ? ' disabled' : '') + '>' + esc(options.confirm || '删除') + '</button></div></form>';
    document.body.appendChild(dialog);
    var input = dialog.querySelector('.rf-confirm-typed input'), ok = dialog.querySelector('[value="ok"]');
    if (input) input.addEventListener('input', function () { ok.disabled = input.value.trim() !== options.typed; });
    return new Promise(function (resolve) {
      var done = function (ok) { if (done.called) return; done.called = true; resolve(ok); setTimeout(function () { dialog.remove(); }, 300); };
      // Answer from the button itself; Esc or closing otherwise means no.
      dialog.querySelector('form').addEventListener('submit', function (event) { done(!!event.submitter && event.submitter.value === 'ok'); });
      dialog.addEventListener('close', function () { done(dialog.returnValue === 'ok'); });
      dialog.showModal();
      (input || dialog.querySelector('[value="cancel"]')).focus();
    });
  }

  /* ---------- Research projects (课题) ---------- */
  // Reading pages show one project at a time. The switcher sits under the brand; the manager dialog
  // creates, renames, describes, archives and deletes projects. Switching reloads the page.
  var P = { list: [], box: null, dialog: null };
  function projectApi(path, options) {
    options = options || {};
    var headers = { 'X-PaperLens': '1' };
    if (options.body) headers['Content-Type'] = 'application/json';
    return fetch(READER + path, { method: options.method || 'GET', headers: headers, cache: 'no-store',
                                  body: options.body ? JSON.stringify(options.body) : undefined })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok) throw new Error(d.error || '操作未完成，请重试。'); return d; }); });
  }
  function projectCount(p) { return (p.counts.papers || 0) + ' 篇文献' + (p.counts.queue ? ' · ' + p.counts.queue + ' 篇待读' : ''); }
  function switchProject(id) {
    window.RFProject.set(id);
    var url = new URL(location.href);
    url.searchParams.delete('project'); url.searchParams.delete('paper'); url.searchParams.delete('job'); url.hash = '';
    location.href = url.pathname + url.search;
  }
  function paintProjects() {
    if (!P.box) return;
    var current = window.RFProject.current(), shown = P.list.filter(function (p) { return !p.archived || p.id === current; });
    var active = P.list.find(function (p) { return p.id === current; });
    if (current && !active && P.list.length) { switchProject('default'); return; }  // the project was deleted elsewhere
    P.box.querySelector('[data-rf-project-name]').textContent = active ? active.name : '全部课题';
    P.box.querySelector('[data-rf-project-meta]').textContent = active ? projectCount(active) : P.list.length + ' 个课题，内容混合显示';
    P.box.querySelector('.rf-project-menu').innerHTML =
      '<div class="rf-project-menu-label">切换课题</div>' +
      shown.map(function (p) {
        return '<button type="button" role="menuitemradio" aria-checked="' + (p.id === current) + '" data-rf-project-id="' + esc(p.id) + '"><span>' + esc(p.name) + (p.archived ? '（已归档）' : '') + '</span><small>' + esc(projectCount(p)) + '</small></button>';
      }).join('') +
      '<button type="button" role="menuitemradio" aria-checked="' + (!current) + '" data-rf-project-id=""><span>全部课题</span><small>混合显示，仅供浏览</small></button>' +
      '<div class="rf-project-menu-foot"><button type="button" data-rf-project-new>' + icon('plus') + '新建课题</button><button type="button" data-rf-project-manage>' + icon('settings') + '管理课题</button></div>';
  }
  function loadProjects() {
    return projectApi('/api/projects').then(function (d) { P.list = d.projects; paintProjects(); return P.list; });
  }
  function toggleProjectMenu(open) {
    var menu = P.box.querySelector('.rf-project-menu'), trigger = P.box.querySelector('.rf-project-current');
    open = open === undefined ? menu.hidden : open;
    menu.hidden = !open;
    trigger.setAttribute('aria-expanded', String(open));
    if (open) (menu.querySelector('[aria-checked="true"]') || menu.querySelector('button')).focus();
  }
  function projectRow(p) {
    var empty = !p.counts.papers && !p.counts.readings && !p.counts.queue, fixed = p.id === 'default';
    return '<form class="rf-project-row' + (p.archived ? ' is-archived' : '') + '" data-rf-project-row="' + esc(p.id) + '">' +
      '<div class="rf-project-row-head"><input name="name" value="' + esc(p.name) + '" maxlength="80" required aria-label="课题名称"><span class="rf-project-row-count">' +
      esc(projectCount(p)) + ' · ' + (p.counts.readings || 0) + ' 份精读' + (p.archived ? ' · 已归档' : '') + '</span></div>' +
      '<label>研究说明<textarea name="description" rows="3" maxlength="20000" placeholder="研究问题、数据与初步设计。在这个课题里新建精读时，会自动填入“我的研究”。">' + esc(p.description || '') + '</textarea></label>' +
      '<div class="rf-project-row-actions"><button class="btn btn-secondary btn-sm" type="submit">保存</button>' +
      (fixed ? '' : '<button class="btn btn-secondary btn-sm" type="button" data-rf-project-archive>' + (p.archived ? '恢复' : '归档') + '</button>') +
      '<button class="btn btn-secondary btn-sm rf-danger" type="button" data-rf-project-delete' + (fixed && empty ? ' disabled title="默认课题已经是空的"' : '') + '>' + (fixed ? '清空' : '删除') + '</button>' +
      '<span class="rf-project-row-note" role="status"></span></div></form>';
  }
  function openProjectManager(focusNew) {
    if (!P.dialog) {
      P.dialog = document.createElement('dialog');
      P.dialog.className = 'dialog rf-project-dialog';
      P.dialog.setAttribute('aria-labelledby', 'rf-project-title');
      P.dialog.innerHTML = '<div class="dialog-head"><div><h2 id="rf-project-title">研究课题</h2><p>每个课题的精读记录、文献地图、文献网络、变量与术语和待读清单分开保存，互不混合。</p></div>' +
        '<button type="button" class="icon-btn" data-rf-project-close aria-label="关闭课题管理">' + icon('x') + '</button></div>' +
        '<div class="dialog-body"><form class="rf-project-create"><label class="sr-only" for="rf-project-new-name">新课题名称</label><input id="rf-project-new-name" name="name" maxlength="80" required placeholder="新课题名称，如：标准化与企业竞争"><button class="btn btn-primary" type="submit">' + icon('plus') + '新建课题</button></form>' +
        '<p class="rf-project-error" role="alert"></p><div class="rf-project-rows"></div></div>';
      document.body.appendChild(P.dialog);
      P.dialog.addEventListener('click', function (event) {
        if (event.target.closest('[data-rf-project-close]')) P.dialog.close();
        var row = event.target.closest('[data-rf-project-row]');
        if (!row) return;
        var id = row.dataset.rfProjectRow, note = row.querySelector('.rf-project-row-note');
        if (event.target.closest('[data-rf-project-archive]')) {
          var p = P.list.find(function (x) { return x.id === id; });
          projectApi('/api/projects/' + id, { method: 'POST', body: { archived: !p.archived } }).then(function () {
            return loadProjects().then(paintManager);
          }).catch(function (e) { note.textContent = e.message; });
        }
        if (event.target.closest('[data-rf-project-delete]')) deleteProject(id, note);
      });
      P.dialog.addEventListener('submit', function (event) {
        event.preventDefault();
        var form = event.target, error = P.dialog.querySelector('.rf-project-error');
        error.textContent = '';
        if (form.classList.contains('rf-project-create')) {
          projectApi('/api/projects', { method: 'POST', body: { name: form.name.value } }).then(function (d) {
            form.reset();
            // A new project becomes the current one: the page reloads into it.
            switchProject(d.project.id);
          }).catch(function (e) { error.textContent = e.message; });
          return;
        }
        var note = form.querySelector('.rf-project-row-note');
        projectApi('/api/projects/' + form.dataset.rfProjectRow, { method: 'POST', body: { name: form.name.value, description: form.description.value } })
          .then(function () { note.textContent = '已保存'; return loadProjects(); })
          .catch(function (e) { note.textContent = e.message; });
      });
    }
    paintManager();
    if (!P.dialog.open) P.dialog.showModal();
    if (focusNew) P.dialog.querySelector('#rf-project-new-name').focus();
  }
  // Deleting a project removes its readings, cards and queued papers; a non-empty one needs its name typed.
  function deleteProject(id, note) {
    var p = P.list.find(function (x) { return x.id === id; }), fixed = id === 'default';
    projectApi('/api/projects/' + id + '/delete-plan').then(function (d) {
      var plan = d.plan, items = [];
      if (plan.cards.length) items.push(plan.cards.length + ' 张文献卡片（含变量与术语 ' + plan.knowledge + ' 条）');
      if (plan.readings) items.push(plan.readings + ' 份精读记录');
      if (plan.queue) items.push(plan.queue + ' 篇待读论文');
      if (plan.documents) items.push(plan.documents + ' 份原文文件（移入系统回收站）');
      var empty = !items.length;
      return confirmAction({ title: fixed ? '清空「' + p.name + '」？' : '删除课题「' + p.name + '」？',
        message: empty ? '这个课题是空的。' : (fixed ? '默认课题保留，里面的内容全部删除，无法撤销：' : '课题和里面的内容全部删除，无法撤销：'),
        items: items, note: empty ? '' : '需要保留的文献，可以先在文献卡片里改“所属课题”移走。',
        typed: empty ? '' : p.name, confirm: fixed ? '清空' : '删除' });
    }).then(function (ok) {
      if (!ok) return;
      return projectApi('/api/projects/' + id, { method: 'DELETE' }).then(function () {
        if (window.RFProject.current() === id && !fixed) { switchProject('default'); return; }
        if (window.RFProject.current() === id) { location.reload(); return; }
        return loadProjects().then(paintManager);
      });
    }).catch(function (e) { note.textContent = e.message; });
  }
  function paintManager() {
    if (P.dialog) P.dialog.querySelector('.rf-project-rows').innerHTML = P.list.map(projectRow).join('');
  }
  function setupProjects() {
    // The server reserves the switcher's place on pages organised by project (no layout shift on load).
    P.box = document.querySelector('[data-rf-project-slot]');
    if (!P.box) return;
    P.box.querySelector('.rf-project-current').addEventListener('click', function () { toggleProjectMenu(); });
    P.box.addEventListener('click', function (event) {
      var pick = event.target.closest('[data-rf-project-id]');
      if (pick) { toggleProjectMenu(false); if (pick.dataset.rfProjectId !== window.RFProject.current()) switchProject(pick.dataset.rfProjectId); }
      if (event.target.closest('[data-rf-project-new]')) { toggleProjectMenu(false); openProjectManager(true); }
      if (event.target.closest('[data-rf-project-manage]')) { toggleProjectMenu(false); openProjectManager(false); }
    });
    document.addEventListener('click', function (event) { if (P.box && !P.box.contains(event.target)) P.box.querySelector('.rf-project-menu').hidden = true; });
    P.box.addEventListener('keydown', function (event) { if (event.key === 'Escape') { toggleProjectMenu(false); P.box.querySelector('.rf-project-current').focus(); } });
    loadProjects().catch(function () { P.box.querySelector('[data-rf-project-name]').textContent = '课题读取失败'; });
  }
  window.RFProjects = { open: openProjectManager, reload: loadProjects, list: function () { return P.list; } };

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
    document.querySelectorAll('[data-rf-motion-toggle]').forEach(function (button) { button.addEventListener('click', cycleMotion); });
    paintThemeButtons();
    paintMotionButtons();
    reducedMotion.addEventListener('change', paintMotionButtons);
    setupDrawer();
    setupProjects();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wire); else wire();

  window.RF = { transition: transition, openPalette: openPalette, cycleTheme: cycleTheme, cycleMotion: cycleMotion, icon: icon, confirm: confirmAction, readerBase: READER, integrated: INTEGRATED };
})();
