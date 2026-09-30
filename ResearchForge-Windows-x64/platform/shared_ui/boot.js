/* Runs synchronously in <head>, before first paint: apply the saved appearance and
   give cross-page transitions a direction that follows the module order. */
(function () {
  var root = document.documentElement;
  try {
    var saved = localStorage.getItem('rf-theme');
    if (saved === 'light' || saved === 'dark') root.setAttribute('data-theme', saved);
  } catch (e) { /* Storage is optional; the system appearance still applies. */ }

  var ORDER = { research: 0, reading: 1, memory: 2, prompts: 3 };
  function moduleOf(path) {
    if (/\/memory\/?$/.test(path)) return 'memory';
    if (/\/prompt-library\/?$/.test(path)) return 'prompts';
    if (/^\/papers(\/|$)/.test(path)) return 'reading';
    return 'research';
  }
  window.addEventListener('pageswap', function () {
    try { sessionStorage.setItem('rf-from-module', moduleOf(location.pathname)); } catch (e) {}
  });
  window.addEventListener('pagereveal', function (event) {
    var from = null;
    try { from = sessionStorage.getItem('rf-from-module'); sessionStorage.removeItem('rf-from-module'); } catch (e) {}
    if (!event.viewTransition || !from || !event.viewTransition.types) return;
    var to = moduleOf(location.pathname);
    if (from === to) return;
    event.viewTransition.types.add(ORDER[to] > ORDER[from] ? 'rf-forward' : 'rf-backward');
  });
})();
