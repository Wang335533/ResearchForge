'use strict';
(() => {
  const libraryState = {results:[], page:1, selected:null, ready:false, searchSeq:0, previewSeq:0, controller:null, previewController:null};
  function message(text,bad=false){$('library-message').textContent=text;$('library-message').classList.toggle('error',bad);}
  async function loadStatus(){
    try{
      const s=await api('/api/library/status');libraryState.ready=s.ready;
      $('library-status').textContent=s.ready?num(s.count)+' 篇 · 更新于 '+(s.indexed_at||'未知').slice(0,10)+' · 本地检索':s.message;
      $('library-submit').disabled=!s.ready;
    }catch(e){libraryState.ready=false;$('library-status').textContent=e.message;$('library-submit').disabled=true;}
  }
  async function openLibrary(){
    if(state.busy){toast('请等待当前文件处理完成');return;}
    showDialog('library-dialog');$('library-query').focus();await loadStatus();
  }
  function resetPreview(){
    libraryState.previewSeq++;libraryState.previewController?.abort();libraryState.selected=null;
    $('library-import').disabled=true;$('library-detail').hidden=true;$('library-preview-empty').hidden=false;
    $('library-preview-empty').innerHTML='<svg class="icon" aria-hidden="true"><use href="/ui/icons.svg#i-file-text"/></svg><p>选择一篇论文预览</p>';
  }
  async function searchLibrary(page=1){
    if(!libraryState.ready)return;
    const q=$('library-query').value.trim();
    if(q.length<2){message('请输入至少 2 个字符的题名或关键词。');return;}
    const seq=++libraryState.searchSeq;libraryState.controller?.abort();libraryState.controller=new AbortController();
    resetPreview();$('library-submit').disabled=true;$('library-pages').hidden=true;
    $('library-results').innerHTML='<div class="library-empty"><p>正在搜索…</p></div>';message('正在检索…');
    const params=new URLSearchParams({q,scope:$('library-scope').value,language:$('library-language').value,year_from:$('library-year-from').value,year_to:$('library-year-to').value,page});
    try{
      const r=await api('/api/library/search?'+params,{signal:libraryState.controller.signal});
      if(seq!==libraryState.searchSeq)return;
      libraryState.results=r.results;libraryState.page=r.page;
      message((r.results.length?'第 '+r.page+' 页 · '+r.results.length+' 篇'+(r.has_more?'，还有更多结果':'')+' · '+r.strategy+' · '+(r.elapsed_ms/1000).toFixed(2)+' 秒':'无匹配结果，试试更少的关键词或换一种语言。')+(r.note?' '+r.note:''));
      renderResults();$('library-pages').hidden=page===1&&!r.has_more;$('library-prev').disabled=page===1;$('library-next').disabled=!r.has_more;$('library-page-label').textContent='第 '+page+' 页';
    }catch(e){if(seq!==libraryState.searchSeq||libraryState.controller.signal.aborted)return;message(e.message,true);$('library-results').innerHTML='<div class="library-empty"><p>请调整检索词后重试。</p></div>';}
    finally{if(seq===libraryState.searchSeq)$('library-submit').disabled=!libraryState.ready;}
  }
  function renderResults(){
    $('library-results').innerHTML=libraryState.results.length?libraryState.results.map(p=>`<button type="button" class="library-result ${p.uid===libraryState.selected?'selected':''}" data-uid="${escapeHTML(p.uid)}"><span class="library-result-meta">${escapeHTML([p.year||'年份待核实',p.journal||'期刊待核实',p.language==='zh'?'中文':'英文'].join(' · '))}</span><strong>${escapeHTML(p.title)}</strong><span class="library-excerpt">${escapeHTML(p.excerpt||'索引未取得摘要，可查看正文。')}</span><span class="library-result-action">预览论文 <svg class="icon" aria-hidden="true"><use href="/ui/icons.svg#i-arrow-right"/></svg></span></button>`).join(''):'<div class="library-empty"><svg class="icon" aria-hidden="true"><use href="/ui/icons.svg#i-search"/></svg><p>没有匹配结果。</p></div>';
  }
  async function previewPaper(uid){
    const seq=++libraryState.previewSeq;libraryState.previewController?.abort();libraryState.previewController=new AbortController();
    libraryState.selected=uid;renderResults();$('library-import').disabled=true;$('library-detail').hidden=true;$('library-preview-empty').hidden=false;
    $('library-preview-empty').innerHTML='<p>正在读取论文原文…</p>';
    try{
      const r=await api('/api/library/papers/'+encodeURIComponent(uid),{signal:libraryState.previewController.signal});
      if(seq!==libraryState.previewSeq)return;
      $('library-paper-title').textContent=r.paper.title;$('library-paper-meta').textContent=[r.paper.year||'年份待核实',r.paper.journal||'期刊待核实',num(r.chars)+' 字符'].join(' · ');
      $('library-abstract').textContent=r.paper.abstract||'索引中无摘要';
      $('library-paper-note').textContent=(r.paper.changed_since_index?'原文件已更新，将使用当前正文。 ':'')+(r.warnings||[]).join(' ');
      $('library-path').textContent=r.paper.path;$('library-source').textContent=r.preview;$('library-preview-note').textContent=r.preview_limited?'预览前 60,000 字符，精读读取全文。':'完整提取文本';
      $('library-detail').hidden=false;$('library-preview-empty').hidden=true;$('library-import').disabled=false;
    }catch(e){if(seq!==libraryState.previewSeq||libraryState.previewController.signal.aborted)return;$('library-preview-empty').textContent=e.message;}
  }
  async function importPaper(){
    if(!libraryState.selected||state.busy)return;
    state.busy=true;modelChanged();$('library-import').disabled=true;
    const uid=libraryState.selected;
    try{
      const doc=await post('/api/library/import',{uid});state.document=doc;
      location.hash='new';newReading();showDocument();$('library-dialog').close();toast('论文已载入');
    }catch(e){message(e.message,true);}
    finally{state.busy=false;modelChanged();$('library-import').disabled=!libraryState.selected||$('library-detail').hidden;}
  }
  $('library-nav').addEventListener('click',openLibrary);$('library-open').addEventListener('click',openLibrary);
  $('library-form').addEventListener('submit',e=>{e.preventDefault();searchLibrary(1);});
  $('library-results').addEventListener('click',e=>{const b=e.target.closest('[data-uid]');if(b)previewPaper(b.dataset.uid);});
  $('library-prev').addEventListener('click',()=>searchLibrary(libraryState.page-1));$('library-next').addEventListener('click',()=>searchLibrary(libraryState.page+1));
  $('library-refresh').addEventListener('click',async()=>{await loadStatus();if($('library-query').value.trim().length>=2)await searchLibrary(1);});
  $('library-import').addEventListener('click',importPaper);
})();
