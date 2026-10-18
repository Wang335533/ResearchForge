/* Workspace overview on the research home: read-only counts from /api/workspace/overview. */
(function () {
  'use strict';
  const root = document.getElementById('workspace-overview');
  if (!root) return;
  const count = (key, value) => {
    const el = root.querySelector(`[data-count="${key}"]`);
    if (!el) return;
    if (window.RFMotion) window.RFMotion.countTo(el, value); else el.textContent = Number(value).toLocaleString('zh-CN');
  };
  const sub = (key, text) => { const el = root.querySelector(`[data-sub="${key}"]`); if (el) el.textContent = text; };
  async function refresh() {
    try {
      const response = await fetch('/api/workspace/overview', {cache: 'no-store'});
      if (!response.ok) return;
      const data = await response.json();
      const research = data.research || {}, reading = data.reading || {}, memory = data.memory || {}, prompts = data.prompts || {};
      root.hidden = false;
      count('research', research.total || 0);
      count('reading', reading.total || 0);
      count('memory', memory.papers || 0);
      count('prompts', prompts.total || 0);
      sub('research', research.active ? `${research.active} 项进行中` : research.completed ? `${research.completed} 项已完成` : '从上方开始第一项');
      sub('reading', reading.active ? `${reading.active} 份分析中` : reading.completed ? `${reading.completed} 份已完成` : '上传或从全文库载入');
      sub('memory', memory.papers ? `${memory.fields || 0} 个领域 · ${memory.confirmed_edges || 0} 条已核实关联` : '精读后自动归位');
      sub('prompts', prompts.favorites ? `${prompts.favorites} 份收藏` : '收藏好用的写法');
    } catch { /* The overview is decorative; the composer works without it. */ }
  }
  refresh();
  window.addEventListener('focus', () => { if (!document.getElementById('sec-config')?.classList.contains('hide')) refresh(); });
})();
