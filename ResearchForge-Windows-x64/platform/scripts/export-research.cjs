/* Export one saved task with the exact same renderers used by the web UI. */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;',
  }[char]));
}

function renderTask(task) {
  const buttons = new Map();
  const context = vm.createContext({
    S: {task, taskId: task.id, topic: task.topic, count: task.count,
      selectionCount: task.selection_count ?? 5, ideas: task.ideas,
      top5: task.top5, lit: task.lit, proposals: task.proposals},
    esc: escapeHtml,
    $: id => {if (!buttons.has(id)) buttons.set(id, {}); return buttons.get(id);},
  });
  vm.runInContext(fs.readFileSync(path.join(root, '研究流水线Web/static/exports.js'), 'utf8'), context);
  return {
    ideas: vm.runInContext('buildIdeasMarkdown()', context),
    html: vm.runInContext('buildReadingHtml()', context),
    markdown: vm.runInContext('buildMarkdown()', context),
    json: JSON.stringify(task, null, 2),
  };
}

function validate(task, rendered) {
  const ids = task.ideas.map(idea => String(idea.id));
  if (new Set(ids).size !== ids.length) throw new Error('Duplicate idea IDs in saved task');
  if (task.status === 'completed' && task.ideas.length !== task.count) throw new Error('Completed task has an unexpected idea count');
  for (const idea of task.ideas) {
    for (const value of [idea.title, idea.abstract]) {
      if (!value || !rendered.ideas.includes(value) || !rendered.markdown.includes(value) || !rendered.html.includes(escapeHtml(value))) {
        throw new Error(`Missing full title/abstract for idea ${idea.id}`);
      }
    }
  }
  for (const item of task.proposals) {
    if (!ids.includes(String(item.idea.id))) throw new Error('Proposal refers to an unknown idea');
    for (const key of ['title_en', 'title_zh', 'abstract_en', 'abstract_zh', 'theoretical_foundation']) {
      const value = item.proposal[key];
      if (value && (!rendered.html.includes(escapeHtml(value)) || !rendered.markdown.includes(value))) throw new Error(`Missing proposal field ${key}`);
    }
  }
  for (const papers of Object.values(task.lit)) {
    for (const paper of papers) {
      if (paper.abstract && (!rendered.html.includes(escapeHtml(paper.abstract)) || !rendered.markdown.includes(paper.abstract))) throw new Error('Missing full paper abstract');
    }
  }
  if (JSON.stringify(JSON.parse(rendered.json)) !== JSON.stringify(task)) throw new Error('JSON snapshot does not match the saved task');
}

module.exports = {renderTask, validate, escapeHtml};
