'use strict';
(() => {
  const libraryState = {results:[], page:1, selected:null, ready:false, online:false, searchSeq:0, previewSeq:0, controller:null, previewController:null};
  const EMPTY_PREVIEW = '<i class="rf-vignette" data-v="reading" aria-hidden="true"></i><p>选择一篇论文预览</p>';
  function message(text,bad=false){$('library-message').textContent=text;$('library-message').classList.toggle('error',bad);}
  function people(authors,limit=3){
    const list=String(authors||'').split(/\s*[;；]\s*/).filter(Boolean);
    if(!list.length)return '';
    const zh=/[一-鿿]/.test(list[0]);
    return list.slice(0,limit).join(zh?'、':', ')+(list.length>limit?(zh?' 等':' et al.'):'');
  }
  function citation(p){
    const issue=[p.volume&&('第 '+p.volume+' 卷'),p.issue&&('第 '+p.issue+' 期'),p.pages&&('页 '+p.pages)].filter(Boolean).join('，');
    return [p.journal,p.year,issue].filter(Boolean).join(' · ');
  }
  async function loadStatus(){
    try{
      const s=await api('/api/library/status');libraryState.ready=s.ready;libraryState.online=!!s.online;
      $('library-status').textContent=s.ready?(s.online?'文献网站书目 · 本机 '+num(s.count)+' 个全文文件':'本机 '+num(s.count)+' 个全文文件 · 索引更新于 '+(s.indexed_at||'未知').slice(0,10)):s.message;
      $('library-local-only-wrap').hidden=!s.online;
      for(const id of ['library-year-from','library-year-to']){
        const off=!s.online&&s.has_years===false;
        $(id).disabled=off;$(id).title=off?'现有文件索引没有可靠年份，请在原文中核对':'';if(off)$(id).value='';
      }
      if(s.ready)message(s.message+' 空格分隔关键词，短语可加引号。');
      $('library-submit').disabled=!s.ready;
    }catch(e){libraryState.ready=false;$('library-status').textContent=e.message;$('library-submit').disabled=true;}
  }
  async function openLibrary(){
    if(state.busy){toast('请等待当前文件处理完成');return;}
    showDialog('library-dialog');$('library-query').focus();await loadStatus();
  }
  function resetPreview(){
    libraryState.previewSeq++;libraryState.previewController?.abort();libraryState.selected=null;
    $('library-import').disabled=true;$('library-queue').disabled=true;$('library-detail').hidden=true;$('library-preview-empty').hidden=false;
    $('library-preview-empty').innerHTML=EMPTY_PREVIEW;
  }
  async function searchLibrary(page=1){
    if(!libraryState.ready)return;
    const q=$('library-query').value.trim();
    if(q.length<2){message('请输入至少 2 个字符的题名、作者或关键词。');return;}
    const seq=++libraryState.searchSeq;libraryState.controller?.abort();libraryState.controller=new AbortController();
    resetPreview();$('library-submit').disabled=true;$('library-pages').hidden=true;
    $('library-results').innerHTML='<div class="library-empty"><p>正在检索…</p></div>';message(libraryState.online?'正在检索文献网站书目…':'正在检索本机索引…');
    const params=new URLSearchParams({q,scope:$('library-scope').value,language:$('library-language').value,year_from:$('library-year-from').value,
      year_to:$('library-year-to').value,page,local_only:$('library-local-only').checked?'1':'0'});
    try{
      const r=await api('/api/library/search?'+params,{signal:libraryState.controller.signal});
      if(seq!==libraryState.searchSeq)return;
      libraryState.results=r.results;libraryState.page=r.page;
      const count=r.total!=null?'共 '+num(r.total)+' 篇 · ':'';
      message((r.results.length?r.strategy+' · '+count+'第 '+r.page+' 页 · 用时 '+(r.elapsed_ms/1000).toFixed(1)+' 秒':'没有结果，试试更少的关键词、换一种语言或放宽年份。')+(r.note?' '+r.note:''));
      renderResults();$('library-pages').hidden=page===1&&!r.has_more;$('library-prev').disabled=page===1;$('library-next').disabled=!r.has_more;$('library-page-label').textContent='第 '+page+' 页';
    }catch(e){if(seq!==libraryState.searchSeq||libraryState.controller.signal.aborted)return;message(e.message,true);$('library-results').innerHTML='<div class="library-empty"><p>请调整检索词后重试。</p></div>';}
    finally{if(seq===libraryState.searchSeq)$('library-submit').disabled=!libraryState.ready;}
  }
  function badge(p){
    const found=p.local?.status==='found';
    // How the result matched, when it is more specific than a keyword hit (similar titles need checking).
    const match=p.match_label&&!['keyword','author'].includes(p.match_kind)?`<span class="library-match ${escapeHTML(p.match_kind)}">${escapeHTML(p.match_label)}</span>`:'';
    return `<span class="library-tags">${match}<span class="library-local ${found?'found':'missing'}">${found?'本机有全文':'本机未找到原文'}</span></span>`;
  }
  function renderResults(){
    $('library-results').innerHTML=libraryState.results.length?libraryState.results.map(p=>`<button type="button" class="library-result ${p.uid===libraryState.selected?'selected':''}" data-uid="${escapeHTML(p.uid)}">
      <span class="library-result-meta">${escapeHTML([p.journal||'期刊待核实',p.year||'年份待核实',p.language==='zh'?'中文':'英文'].join(' · '))}</span>
      <strong>${escapeHTML(p.title)}</strong>
      ${p.authors?`<span class="library-authors">${escapeHTML(people(p.authors))}</span>`:''}
      <span class="library-excerpt">${escapeHTML(p.excerpt||(p.local?.status==='found'?'没有摘要，可预览正文。':'没有摘要。'))}</span>
      <span class="library-result-foot">${badge(p)}<span class="library-result-action">预览 <svg class="icon" aria-hidden="true"><use href="/ui/icons.svg#i-arrow-right"/></svg></span></span>
    </button>`).join(''):'<div class="library-empty"><svg class="icon" aria-hidden="true"><use href="/ui/icons.svg#i-search"/></svg><p>没有匹配结果。</p></div>';
  }
  async function previewPaper(uid){
    const seq=++libraryState.previewSeq;libraryState.previewController?.abort();libraryState.previewController=new AbortController();
    libraryState.selected=uid;renderResults();$('library-import').disabled=true;$('library-detail').hidden=true;$('library-preview-empty').hidden=false;
    $('library-preview-empty').innerHTML='<p>'+(uid.startsWith('api:')?'正在读取书目并查找本机原文…':'正在读取论文原文…')+'</p>';
    try{
      const r=await api('/api/library/papers/'+encodeURIComponent(uid),{signal:libraryState.previewController.signal});
      if(seq!==libraryState.previewSeq)return;
      const p=r.paper;
      $('library-paper-source').textContent=p.source==='online'?'文献网站书目':'本机索引';
      $('library-paper-title').textContent=p.title;
      $('library-paper-authors').textContent=p.authors?people(p.authors,12):'作者待核实';
      $('library-paper-meta').textContent=citation(p)||'期刊与年份待核实';
      $('library-paper-doi').textContent=p.doi?'DOI '+p.doi:'';
      $('library-paper-note').textContent=[p.identity_note,p.changed_since_index?'原文件已更新，将使用当前正文。':'',...(r.warnings||[])].filter(Boolean).join(' ');
      $('library-abstract').textContent=p.abstract||(r.available?'没有摘要，可展开下方原文查看。':'没有摘要。');
      $('library-keywords').textContent=p.keywords?'关键词：'+p.keywords:'';
      $('library-source-detail').hidden=!r.available;
      $('library-missing').hidden=!!r.available;
      libraryState.fileUid=r.available?(p.file_uid||p.uid):'';
      if(r.available){
        $('library-path').textContent=p.path;$('library-source').textContent=r.preview;
        $('library-download').href=appUrl('/api/library/download?uid='+encodeURIComponent(libraryState.fileUid));
        $('library-preview-note').textContent=(r.chars?num(r.chars)+' 字符 · ':'')+(r.preview_limited?'预览前 60,000 字符，精读读取全文。':'完整提取文本');
      }
      $('library-detail').hidden=false;$('library-preview-empty').hidden=true;$('library-import').disabled=!r.available;
      // A website record without a local file can still be saved to the reading list as a reference.
      $('library-queue').disabled=!(r.available||p.document_id);
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
    finally{state.busy=false;modelChanged();$('library-import').disabled=!libraryState.selected||$('library-detail').hidden||!$('library-missing').hidden;}
  }
  // Other pages open the library with a ready-made search (?panel=library&q=…&scope=…).
  window.PaperLibrary={async open(query='',scope=''){await openLibrary();if(scope&&[...$('library-scope').options].some(o=>o.value===scope))$('library-scope').value=scope;
    if(query){$('library-query').value=query;await searchLibrary(1);}}};
  $('library-nav').addEventListener('click',openLibrary);$('library-open').addEventListener('click',openLibrary);
  $('library-form').addEventListener('submit',e=>{e.preventDefault();searchLibrary(1);});
  $('library-results').addEventListener('click',e=>{const b=e.target.closest('[data-uid]');if(b)previewPaper(b.dataset.uid);});
  // Double-click a result to load it straight away once its preview is ready.
  $('library-results').addEventListener('dblclick',async e=>{const b=e.target.closest('[data-uid]');if(!b)return;if(libraryState.selected!==b.dataset.uid||$('library-import').disabled)await previewPaper(b.dataset.uid);if(!$('library-import').disabled)importPaper();});
  $('library-prev').addEventListener('click',()=>searchLibrary(libraryState.page-1));$('library-next').addEventListener('click',()=>searchLibrary(libraryState.page+1));
  $('library-refresh').addEventListener('click',async()=>{await loadStatus();if($('library-query').value.trim().length>=2)await searchLibrary(1);});
  $('library-local-only').addEventListener('change',()=>{if($('library-query').value.trim().length>=2)searchLibrary(1);});
  $('library-import').addEventListener('click',importPaper);
  $('library-queue').addEventListener('click',async()=>{
    const uid=libraryState.selected;if(!uid)return;const b=$('library-queue');b.disabled=true;
    try{const r=await post('/api/reading-queue',{uid,project_id:window.RFProject.concrete()});window.ReadingViews?.refreshCount();
      message(r.created?'已加入待读清单'+(r.item.has_source?'':'（只有书目，原文可稍后补上）')+'。':'这篇论文已在待读清单中。');
      toast(r.created?'已加入待读清单':'已在待读清单中',{label:'查看清单',run:()=>{$('library-dialog').close();location.hash='queue';}});}
    catch(e){message(e.message,true);}finally{b.disabled=false;}
  });
  $('library-reveal').addEventListener('click',async()=>{
    try{const r=await post('/api/library/reveal',{uid:libraryState.fileUid});message(r.message);}catch(e){message(e.message,true);}
  });
  $('library-copy-path').addEventListener('click',async()=>{
    try{await navigator.clipboard.writeText($('library-path').textContent);toast('路径已复制');}catch{message('浏览器未允许复制，请手动选中路径。',true);}
  });
})();
