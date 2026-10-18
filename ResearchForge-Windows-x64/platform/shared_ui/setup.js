/* 首次配置指南: live readiness checks (read-only local APIs) and a copyable indexing command. */
(function () {
  'use strict';
  function set(name, ok, text) {
    var item = document.querySelector('[data-check="' + name + '"]');
    if (!item) return;
    item.dataset.state = ok ? 'ok' : 'warn';
    item.querySelector('em').textContent = text;
  }
  function json(url) {
    return fetch(url, { cache: 'no-store' }).then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });
  }
  json('/api/workspace/overview').then(function (data) {
    var provider = data.provider || {}, retrieval = data.retrieval || {};
    set('provider', provider.ready, provider.ready ? (provider.name || '默认接口') + ' · 已连接' : provider.name ? provider.name + ' · 待授权或配置密钥' : '尚未配置');
    set('corpus', retrieval.ready, retrieval.ready ? '已就绪 · ' + Number(retrieval.papers || 0).toLocaleString('zh-CN') + ' 篇' : retrieval.error ? '准备失败，可在研究构想中重试' : '尚未准备（精读不需要）');
  }).catch(function () {
    set('provider', false, '暂时无法读取');
    set('corpus', false, '暂时无法读取');
  });
  json('/papers/api/library/status').then(function (s) {
    set('library', s.ready, s.ready ? Number(s.count || 0).toLocaleString('zh-CN') + ' 个文件已建索引' : '尚未建立索引（可选）');
  }).catch(function () { set('library', false, '暂时无法读取'); });

  var button = document.getElementById('copy-command');
  if (button) button.addEventListener('click', function () {
    var text = document.getElementById('index-command').textContent;
    var done = function () {
      if (window.RFMotion) window.RFMotion.stampAt(button, '已复制');
      var toast = document.getElementById('toast');
      toast.textContent = '命令已复制，在本文件夹打开命令提示符后粘贴运行';
      toast.hidden = true; void toast.offsetWidth; toast.hidden = false;
      clearTimeout(done.timer); done.timer = setTimeout(function () { toast.hidden = true; }, 4200);
    };
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, function () {});
  });
})();
