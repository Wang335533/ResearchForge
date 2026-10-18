/* Runs synchronously in <head>, before first paint: apply the saved appearance and motion
   preference, and give cross-page transitions a direction that follows the module order. */
(function () {
  var root = document.documentElement;

  // The default appearance follows the clock: light by day, dark after dusk, with a cooler dawn and a
  // warmer dusk in between. Sunrise and sunset shift with the season (an approximation for mid-latitude
  // local time); no location is read.
  function daypart(now) {
    now = now || new Date();
    var day = Math.floor((now - new Date(now.getFullYear(), 0, 0)) / 864e5);
    var season = Math.cos(2 * Math.PI * (day - 172) / 365);  // 1 at the June solstice, -1 in December
    var rise = 6 - 1.1 * season, set = 18.2 + 1.2 * season, t = now.getHours() + now.getMinutes() / 60;
    var lightFrom = rise - 0.25, darkFrom = set + 0.5;
    var part = t < lightFrom || t >= darkFrom ? 'night' : t < rise + 1.5 ? 'dawn' : t >= set - 1.5 ? 'dusk' : 'day';
    return { part: part, dark: part === 'night', lightFrom: lightFrom, darkFrom: darkFrom };
  }
  function clock(hours) { var m = Math.round(hours * 60); return Math.floor(m / 60) + ':' + ('0' + m % 60).slice(-2); }
  window.RFClock = { daypart: daypart, clock: clock };

  var saved = 'time';
  try {
    saved = localStorage.getItem('rf-theme') || 'time';
    // Following the clock became the default; an earlier fixed choice is moved to it once.
    if (localStorage.getItem('rf-theme-clock') !== '1') {
      localStorage.setItem('rf-theme-clock', '1');
      localStorage.setItem('rf-theme', 'time');
      saved = 'time';
    }
    var motion = localStorage.getItem('rf-motion');
    if (motion === 'full' || motion === 'calm' || motion === 'off') root.setAttribute('data-rf-motion', motion);
  } catch (e) { /* Storage is optional; the clock still applies. */ }
  if (saved === 'light' || saved === 'dark') root.setAttribute('data-theme', saved);
  else if (saved !== 'system') {
    var now = daypart();
    root.setAttribute('data-theme', now.dark ? 'dark' : 'light');
    root.setAttribute('data-rf-tone', now.part);
  }

  // The research project (课题) the reading pages show; '' means every project. A link may carry
  // ?project=<id>. Page scripts read this before they first load data; shell.js draws the switcher.
  (function () {
    var KEY = 'rf-project';
    try {
      var linked = new URLSearchParams(location.search).get('project');
      if (linked !== null && /^(?:default|[a-f0-9]{32}|all)?$/.test(linked)) localStorage.setItem(KEY, linked === 'all' ? '' : linked);
    } catch (e) { /* Storage is optional. */ }
    window.RFProject = {
      current: function () { try { var v = localStorage.getItem(KEY); return v === null ? 'default' : v; } catch (e) { return 'default'; } },
      // A concrete project for new readings and queue items, even while "every project" is shown.
      concrete: function () { var v = this.current(); if (v) return v; try { return localStorage.getItem(KEY + '-last') || 'default'; } catch (e) { return 'default'; } },
      param: function (separator) { var v = this.current(); return v ? (separator || '') + 'project=' + encodeURIComponent(v) : ''; },
      set: function (id) { try { localStorage.setItem(KEY, id || ''); if (id) localStorage.setItem(KEY + '-last', id); } catch (e) {} }
    };
  })();

  var ORDER = { research: 0, reading: 1, memory: 2, knowledge: 3, prompts: 4, sync: 5 };
  function moduleOf(path) {
    if (/\/memory\/?$/.test(path)) return 'memory';
    if (/\/knowledge\/?$/.test(path)) return 'knowledge';
    if (/\/prompt-library\/?$/.test(path)) return 'prompts';
    if (/\/reading-sync\/?$/.test(path)) return 'sync';
    if (/^\/papers(\/|$)/.test(path)) return 'reading';
    return 'research';
  }
  root.setAttribute('data-rf-module', moduleOf(location.pathname));
  // Arriving from another workspace page: the scenery, heading and sections are shown in place instead of
  // replaying their entrance (shell.css .rf-entered). A first visit still gets the full entrance.
  try { if (sessionStorage.getItem('rf-from-module')) root.classList.add('rf-entered'); } catch (e) {}
  // Motion "off" (or a reduced-motion system without an explicit "full") skips page transitions entirely.
  function still() {
    var chosen = root.getAttribute('data-rf-motion');
    return chosen === 'off' || (chosen !== 'full' && window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }
  // A skipped transition (motion off, a quick second navigation, a hidden window) rejects its
  // promises; that is expected, so it must not surface as an uncaught error.
  function quiet(transition) {
    if (transition) [transition.ready, transition.finished, transition.updateCallbackDone].forEach(function (p) {
      if (p) p.catch(function () {});
    });
  }
  window.addEventListener('pageswap', function (event) {
    quiet(event.viewTransition);
    if (still() && event.viewTransition) event.viewTransition.skipTransition();
    try { sessionStorage.setItem('rf-from-module', moduleOf(location.pathname)); } catch (e) {}
  });
  window.addEventListener('pagereveal', function (event) {
    quiet(event.viewTransition);
    if (still() && event.viewTransition) { event.viewTransition.skipTransition(); return; }
    var from = null;
    try { from = sessionStorage.getItem('rf-from-module'); sessionStorage.removeItem('rf-from-module'); } catch (e) {}
    // A page reached through a view transition skips its own long entrance choreography.
    if (event.viewTransition) root.classList.add('rf-arrived');
    if (!event.viewTransition || !from || !event.viewTransition.types) return;
    var to = moduleOf(location.pathname);
    if (from === to) return;
    event.viewTransition.types.add(ORDER[to] > ORDER[from] ? 'rf-forward' : 'rf-backward');
  });
})();
