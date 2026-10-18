(() => {
  'use strict';
  const $=id=>document.getElementById(id), base=document.querySelector('meta[name="app-base"]')?.content||'', scholars=document.body.dataset.notebook==='scholars';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const referenceFields=[['measurement_references','测量参考文献'],['reference_usage','借鉴与调整'],['reference_evidence','引用依据'],['reference_status','来源核对']];
  const referenceKeys=new Set(referenceFields.map(([key])=>key));
  const fields=[['original','原文名称'],['role','变量角色'],['concept','经济概念'],['theory','经济理论'],['definition','操作定义'],['formula','公式'],['numerator','分子'],['denominator','分母'],['unit','单位'],['window','时间窗口'],['level','观察层级'],['data_source','数据来源'],['access','获取性质'],['processing','样本与处理'],['measure_variants','口径变体'],...referenceFields,['risk','测量风险'],['evidence','原文依据']];
  const preview=text=>String(text||'').replace(/\\\[[\s\S]*?\\\]|\$\$[\s\S]*?\$\$/g,'（公式见详情）').replace(/\\\([\s\S]*?\\\)/g,'（变量符号见详情）').replace(/\*\*|`/g,'');
  // Measurement fields keep the report's inline $…$ formulas, so single dollars are math here.
  const math=el=>window.renderMathInElement?.(el,{throwOnError:false,trust:false,strict:'ignore',delimiters:[{left:'$$',right:'$$',display:true},{left:'\\[',right:'\\]',display:true},{left:'\\(',right:'\\)',display:false},{left:'$',right:'$',display:false}]});
  const statuses={pending:'待核对',reviewed:'已核对',dismissed:'已排除'}, kinds={variable:'变量与测量',term:'A 变量用词',concept:'B 经济概念',theory:'C 经济理论'};
  const LANGS={en:'英文文献',zh:'中文文献'};
  let kind='variable', lang='', page=1, seq=0, timer, controller, items=[], detailSeq=0;
  try{lang=['en','zh'].includes(localStorage.getItem('rf-knowledge-lang'))?localStorage.getItem('rf-knowledge-lang'):'';}catch{}
  const selected=new Map(), initial=new URLSearchParams(location.search);
  // The notebook opens on one folder per paper; ?paper=<id> is the open folder (also linked from 学者统计 and 阅读目录).
  let paper=scholars?'':initial.get('paper')||'', folders=[], listPage=1, listScroll=0, restoreScroll=null, morph=null, unfold=true, toHead=false;
  const icon=name=>window.RF?.icon?.(name)||'';
  const folderUrl=id=>location.pathname+(id?'?paper='+encodeURIComponent(id):'');
  document.querySelectorAll('[data-base]').forEach(a=>a.href=base+a.dataset.base);
  if(initial.has('q'))$('notebook-query').value=initial.get('q');
  const message=(text,error=false)=>{$('notebook-message').textContent=text;$('notebook-message').dataset.error=error;};
  async function api(path,body,signal){const r=await fetch(base+path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json','X-PaperLens':'1'}:{},body:body?JSON.stringify(body):undefined,signal});const d=await r.json();if(!r.ok)throw Error(d.error||'读取失败，请重试');return d;}
  function options(id,values,label){const el=$(id);if(!el)return;const key=JSON.stringify(values);if(el.dataset.key===key)return;el.dataset.key=key;const prior=el.value;el.replaceChildren(new Option(label,''),...values.map(v=>typeof v==='string'?new Option(v,v):new Option(v.title,v.id)));el.value=prior;}
  function stats(pairs){$('notebook-stats').innerHTML=pairs.map(([n,t])=>`<div class="notebook-stat"><strong>${esc(n)}</strong><span>${esc(t)}</span></div>`).join('');}
  function paperLink(p){return base+'/memory#'+encodeURIComponent(p.paper_id||p.id);}
  async function load(next=1){
    const own=++seq;controller?.abort();controller=new AbortController();$('notebook-results').setAttribute('aria-busy','true');message('正在读取本机阅读积累…');
    try{
      // Only the current research project's papers and records (boot.js); '' shows every project.
      const params={q:$('notebook-query').value,page:next,project:window.RFProject.current()},older=$('notebook-history')?.checked?'1':'';
      let path='/api/scholars?';
      if(scholars)Object.assign(params,{field:$('notebook-field').value,year:$('notebook-year').value});
      else if(paper){path='/api/knowledge?';Object.assign(params,{paper,kind,status:$('notebook-status').value,group:$('notebook-group').value,history:older});}
      else{path='/api/knowledge/papers?';Object.assign(params,{lang,field:$('notebook-field').value,history:older});}
      const d=await api(path+new URLSearchParams(params),null,controller.signal);if(own!==seq)return;
      options('notebook-field',d.fields,'全部领域');
      if(scholars){
        options('notebook-year',[...d.years,{id:'unknown',title:'年份待核实'}],'全部年份');
        stats([[d.counts.scholars,'已读学者'],[d.counts.papers,'独立已读论文'],[d.counts.missing_authors,'作者待补充'],[d.fields.filter(f=>f!=='待分类').length,'已定位领域']]);
        $('scholar-note').textContent=d.note;
        $('missing-authors').hidden=!d.missing.length;
        $('missing-author-list').innerHTML=d.missing.map(p=>`<p><a href="${esc(paperLink(p))}">${esc(p.title)}</a></p>`).join('');
        items=d.items;page=Math.min(next,Math.max(1,Math.ceil(items.length/24)));renderScholars(items.slice((page-1)*24,page*24));
        $('notebook-next').disabled=page*24>=items.length;
      }else{
        stats([[d.counts.papers,'篇文献已积累'],[d.counts.variable,'条测量记录（含历史摘录）'],[d.counts.term+d.counts.concept+d.counts.theory,'条三层用词'],[d.counts.reviewed+' / '+d.counts.total,'条已核对']]);
        // Language counts are papers (folders); English and Chinese literature keep separate shelves.
        document.querySelectorAll('[data-lang]').forEach(b=>{const n=b.dataset.lang?d.counts['papers_'+b.dataset.lang]:d.counts.papers;let c=b.querySelector('b');if(!c){c=document.createElement('b');b.append(c);}c.textContent=n;});
        refineState(d);
        paint(()=>{level();if(paper)showRecords(d);else showFolders(d);});
        $('notebook-next').disabled=!d.has_more;
      }
      message(scholars?'找到 '+d.total+' 位学者':paper?'找到 '+d.total+' 条记录 · '+kinds[kind]:$('notebook-query').value.trim()?d.total+' 篇文献中有 '+d.records+' 条匹配记录':'共 '+d.total+' 篇文献 · '+d.records+' 条记录');
      $('notebook-pages').hidden=d.total<=24;$('notebook-prev').disabled=page===1;$('notebook-page').textContent='第 '+page+' 页';
    }catch(e){if(e.name!=='AbortError'&&own===seq)message(e.message,true);}
    finally{if(own===seq)$('notebook-results').removeAttribute('aria-busy');}
  }
  function renderScholars(rows){
    $('notebook-results').innerHTML=rows.length?`<div class="catalog-table-wrap"><table class="catalog-table"><thead><tr><th>学者</th><th>已读论文</th><th>第一作者</th><th>领域</th><th>合作最多的学者</th></tr></thead><tbody>${rows.map(a=>`<tr><td><button class="scholar-name" data-scholar="${esc(a.id)}">${esc(a.name)}</button></td><td>${a.count} 篇</td><td>${a.first_author} 篇</td><td>${Object.keys(a.fields).map(f=>`<span class="catalog-field">${esc(f)}</span>`).join('')}</td><td>${a.coauthors.slice(0,3).map(c=>esc(c.name)+' · '+c.count).join('<br>')||'本范围无合作者'}</td></tr>`).join('')}</tbody></table></div>`:'<div class="notebook-empty"><strong>这个范围还没有学者记录</strong>调整姓名、年份或领域筛选。作者缺失的论文可在文献地图补充。</div>';
    document.querySelectorAll('[data-scholar]').forEach(b=>b.onclick=()=>openScholar(items.find(a=>a.id===b.dataset.scholar)));
  }
  function openScholar(a){
    if(!a)return;
    $('detail-title').textContent=a.name;
    $('detail-body').innerHTML=`<p class="notebook-note">${a.count} 篇已读论文 · ${a.first_author} 篇第一作者。以下合作次数仅覆盖当前已读范围。</p><div class="scholar-coauthors">${a.coauthors.map(c=>`<button type="button" data-coauthor="${esc(c.name)}">${esc(c.name)} · ${c.count} 篇</button>`).join('')}</div><ul class="scholar-papers">${a.papers.map(p=>`<li><strong>${esc(p.year||'年份待核实')}</strong> · <a href="${esc(paperLink(p))}">${esc(p.title)}</a><div>${esc(p.authors.join('；'))}</div><div>${esc(p.fields.join(' / '))} · <a href="${base}/knowledge?paper=${encodeURIComponent(p.id)}">查看变量记录</a> · <a href="${esc(paperLink(p))}">核对作者与年份</a></div></li>`).join('')}</ul><p class="notebook-note">姓名按全文姓名、大小写与标点归并；缩写、别名及同名身份需要核对，不自动推断为同一人。</p>`;
    $('detail-footer').innerHTML='<button type="button" class="btn btn-secondary" data-close="notebook-detail">关闭</button>';
    $('detail-body').querySelectorAll('[data-coauthor]').forEach(b=>b.onclick=()=>{$('notebook-detail').close();$('notebook-query').value=b.dataset.coauthor;load();});
    bindClose();if(!$('notebook-detail').open)$('notebook-detail').showModal();
  }
  /* ---------------------------------------------------------------- Paper folders */
  // Opening a folder morphs its card into the folder header (and back) as one shared element.
  function paint(update){
    const m=morph;morph=null;
    if(!m||!window.RF?.transition){update();return;}
    if(m.from?.isConnected)m.from.style.viewTransitionName='rf-folder';
    window.RF.transition(()=>{
      if(m.from)m.from.style.viewTransitionName='';
      update();
      const to=m.to();if(to){to.style.viewTransitionName='rf-folder';setTimeout(()=>{to.style.viewTransitionName='';},1200);}
    },'rf-folder');
  }
  function level(){
    const inside=!!paper;
    document.querySelector('.notebook-main').dataset.level=inside?'records':'papers';
    $('notebook-stats').hidden=inside;$('folder-crumbs').hidden=!inside;$('folder-head').hidden=!inside;
    document.querySelector('.knowledge-kinds').hidden=!inside;document.querySelector('.knowledge-langs').hidden=inside;
    $('notebook-field').hidden=inside;$('notebook-status').hidden=!inside;$('notebook-group').hidden=!inside;
    $('notebook-query').placeholder=inside?'在这篇文献中搜索变量、公式或引用':'搜索论文、作者、变量或公式';
  }
  function progress(f){return `<span class="folder-progress" role="img" aria-label="已核对 ${f.reviewed} / ${f.total}" title="已核对 ${f.reviewed} / ${f.total}"><i style="--p:${f.total?(f.reviewed/f.total).toFixed(3):0}"></i></span>`;}
  function folderCard(f){
    const counts=[['variable','变量'],['concept','概念'],['theory','理论'],['term','用词']].filter(([k])=>f.counts[k]).map(([k,label])=>`<span><b>${f.counts[k]}</b>${label}</span>`).join('');
    const busy=f.refine&&['queued','running'].includes(f.refine.state);
    const flag=busy?'<span class="folder-flag is-busy">整理中</span>':f.outdated?'<span class="folder-flag" title="这篇的记录按旧规则生成，可用“按新规范重整”更新">可重整</span>':'';
    const authors=f.authors.length>3?f.authors.slice(0,3).join(' · ')+' 等':f.authors.join(' · ');
    const tongue=f.language==='en'?'en':'zh-CN';
    return `<a class="paper-folder lang-${f.language}" href="${esc(folderUrl(f.id))}" data-folder="${esc(f.id)}" data-paper="${esc(f.id)}"><span class="folder-back" aria-hidden="true"></span><span class="folder-tab">${LANGS[f.language]}</span><span class="folder-sheets" aria-hidden="true"><i></i><i></i></span>
      <span class="folder-front"><span class="folder-meta"><span>${esc(f.year||'年份待核实')}${f.fields.length?' · '+esc(f.fields.join(' / ')):''}</span>${flag}</span><strong class="folder-title" lang="${tongue}">${esc(f.title)}</strong><span class="folder-authors">${esc(authors||'作者待补充')}</span>
      <span class="folder-index" lang="${tongue}">${f.names.map(n=>`<span class="folder-name kind-${n.kind}">${esc(n.name)}</span>`).join('')}${f.total>f.names.length?`<span class="folder-more">共 ${f.total} 条</span>`:''}</span>
      <span class="folder-foot"><span class="folder-counts">${counts}</span>${progress(f)}</span></span></a>`;
  }
  function showFolders(d){
    folders=d.items;page=d.page;
    const searching=$('notebook-query').value.trim();
    const grid=list=>`<div class="folder-grid"${unfold?' data-rf-animate="always"':''}>${list.map(folderCard).join('')}</div>`;
    const shelf=l=>{const list=folders.filter(f=>f.language===l);return list.length?`<section class="folder-shelf lang-${l}" aria-label="${LANGS[l]}"><h2 class="folder-shelf-title"><i aria-hidden="true"></i>${LANGS[l]}<small>${d.languages[l]} 篇</small></h2>${grid(list)}</section>`:'';};
    $('notebook-results').innerHTML=!folders.length?`<div class="notebook-empty"><strong>${searching?'没有文献含有匹配的变量或术语':'这个范围还没有'+(lang?esc(LANGS[lang]):'')+'文献文件夹'}</strong>${searching?'换一个关键词，或清空搜索查看全部文献。':'完成精读后，每篇文献会自动建一个文件夹收纳它的变量与术语；也可点击“同步已读报告”。'}</div>`:lang?shelf(lang):shelf('en')+shelf('zh');
    math($('notebook-results'));unfold=false;
    if(restoreScroll!==null){window.scrollTo(0,restoreScroll);restoreScroll=null;}
  }
  function folderHead(f){
    $('folder-crumb-lang').textContent=f?LANGS[f.language]:'';
    $('folder-crumb-title').textContent=f?f.title:'未找到这篇文献';
    const head=$('folder-head');
    if(!f){head.className='folder-head';head.innerHTML='<div class="folder-head-body"><p class="notebook-note">未找到这篇文献，可能已被合并或删除。请返回全部文献。</p></div>';return;}
    const tongue=f.language==='en'?'en':'zh-CN';
    const counts=[['variable','变量与测量'],['concept','经济概念'],['theory','经济理论'],['term','变量用词']].filter(([k])=>k!=='term'||f.counts.term);
    head.className='folder-head lang-'+f.language;
    head.innerHTML=`<span class="folder-head-tab">${LANGS[f.language]}<span aria-hidden="true"> · </span>文献文件夹</span><div class="folder-head-body">
      <div class="folder-head-text"><p class="folder-meta">${esc(f.year||'年份待核实')}${f.fields.length?' · '+esc(f.fields.join(' / ')):''}</p><h2 id="folder-head-title" lang="${tongue}">${esc(f.title)}</h2><p class="folder-authors">${esc(f.authors.join('；')||'作者待补充')}</p></div>
      <div class="folder-head-side"><dl class="folder-head-counts">${counts.map(([k,label])=>`<div><dt>${label}</dt><dd>${f.counts[k]}</dd></div>`).join('')}</dl><div class="folder-head-review"><span>已核对 ${f.reviewed} / ${f.total}</span>${progress(f)}</div><a class="btn btn-secondary btn-sm" href="${esc(paperLink(f))}">${icon('book-open-text')}查看来源论文</a></div></div>`;
    math(head);
  }
  function showRecords(d){
    items=d.items;page=d.page;
    options('notebook-group',d.groups,'全部分组');
    document.querySelectorAll('[data-kind]').forEach(b=>{const n=d.kind_counts?.[b.dataset.kind];let c=b.querySelector('b');if(!c){c=document.createElement('b');b.append(c);}c.textContent=n??'';if(b.dataset.kind==='term')b.hidden=!n&&kind!=='term';});
    folderHead(d.folder);renderKnowledge(items);math($('notebook-results'));unfold=false;
    if(toHead){toHead=false;const bar=parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--rf-topbar-h'))||56,top=$('folder-crumbs').getBoundingClientRect().top+scrollY-bar-12;if(scrollY>top)window.scrollTo(0,Math.max(0,top));}
  }
  function markKind(){document.querySelectorAll('[data-kind]').forEach(t=>t.setAttribute('aria-current',String(t.dataset.kind===kind)));}
  function openFolder(id,from,push){
    if(scholars||!id||paper===id)return;
    const f=folders.find(x=>x.id===id);
    if(!paper){listPage=page;listScroll=scrollY;}
    paper=id;$('notebook-group').value='';
    // A folder opens on its measurements, or on the first layer the search matched (it may match only concepts).
    kind=f&&['variable','concept','theory','term'].find(k=>f.counts[k])||'variable';
    markKind();
    if(push)history.pushState({rfFolder:id},'',folderUrl(id));
    morph={from:from||document.querySelector(`.paper-folder[data-folder="${CSS.escape(id)}"]`),to:()=>$('folder-head')};unfold=true;toHead=true;
    load(1);
  }
  function closeFolder(){
    const id=paper;if(!id)return;paper='';
    morph={from:$('folder-head'),to:()=>document.querySelector(`.paper-folder[data-folder="${CSS.escape(id)}"]`)};unfold=false;restoreScroll=listScroll;
    load(listPage);
  }
  function renderKnowledge(rows){
    const searching=$('notebook-query').value.trim();
    $('notebook-results').innerHTML=rows.length?`<div class="notebook-grid"${unfold?' data-rf-animate="always"':''}>${rows.map(e=>`<article class="knowledge-card"><div class="knowledge-card-head"><h3 lang="${e.language==='en'?'en':'zh-CN'}"><button data-entry="${e.id}">${esc(e.display_name||e.name)}</button></h3><label title="加入比较"><input type="checkbox" data-compare="${e.id}" aria-label="比较 ${esc(e.display_name||e.name)}" ${selected.has(e.id)?'checked':''}></label></div><div class="knowledge-badges"><span class="knowledge-badge ${e.status==='reviewed'?'reviewed':''}">${statuses[e.status]}</span><span class="knowledge-badge">${e.extraction==='legacy'?'历史测量摘录':e.extraction==='manual'?'人工拆分卡片':e.extraction==='refined'?'按新规范整理':'结构化卡片'}</span>${e.superseded?'<span class="knowledge-badge">已被重整替代</span>':''}${e.current?'':'<span class="knowledge-badge">旧报告</span>'}${e.group?`<span class="knowledge-badge">${esc(e.group)}</span>`:''}</div><div class="knowledge-preview">${esc(preview(e.definition||e.raw))}</div><div class="knowledge-card-actions"><button type="button" class="knowledge-open" data-entry="${e.id}">查看详情<span aria-hidden="true"> →</span></button></div></article>`).join('')}</div>`:`<div class="notebook-empty"><strong>${searching?'这篇文献中没有匹配的'+esc(kinds[kind])+'记录':'这篇文献还没有'+esc(kinds[kind])+'记录'}</strong>${searching?'换一个关键词，或切换上方的知识层次。':kind==='variable'?'完成精读后自动积累，也可点击“同步已读报告”。':'新精读报告的惯用词表会自动归入这里。旧报告没有三层词表的，保留其测量摘录，不自动补造理论关系。'}</div>`;
    document.querySelectorAll('[data-entry]').forEach(b=>b.onclick=()=>openEntry(b.dataset.entry));
    document.querySelectorAll('[data-compare]').forEach(b=>b.onchange=()=>{if(b.checked){if(selected.size>=4){b.checked=false;message('一次最多比较 4 条记录；先取消一条再添加。',true);return;}selected.set(b.dataset.compare,items.find(e=>e.id===b.dataset.compare));}else selected.delete(b.dataset.compare);updateCompare();});
  }
  function updateCompare(){$('compare-selected').textContent='比较已选（'+selected.size+'/4）';$('compare-selected').disabled=selected.size<2;}
  function fieldInput(e,[key,label]){
    const citation=referenceKeys.has(key);
    const hint=citation?'尚未提取，请结合本文的变量定义与参考文献核对':'待核实 / 尚未从历史摘录拆分';
    return `<label class="${citation||['definition','formula','risk','evidence','processing','mapping'].includes(key)?'wide':''}">${label}<textarea name="${key}" rows="${key==='definition'||key==='measurement_references'?4:2}" maxlength="16000" placeholder="${hint}">${esc(e[key])}</textarea></label>`;
  }
  function referenceEditor(e){
    const hasSources=referenceFields.some(([key])=>e[key]);
    return `<details class="wide variable-references"><summary>测量文献来源 · ${hasSources?'查看与编辑':'待补充'}</summary><p class="notebook-note">逐个变量记录参考了谁、沿用了什么、本文如何调整。引用关系需由变量定义及参考文献共同支持；旧记录未提取不代表本文没有引用。</p><div class="form-grid">${referenceFields.map(field=>fieldInput(e,field)).join('')}</div></details>`;
  }
  function sourceExcerpt(e){return e.raw_html||`<div class="knowledge-excerpt">${esc(e.raw)}</div>`;}
  function readingSection(e,title,keys){
    const values=keys.map(key=>fields.find(([k])=>k===key)||[key,({mapping:'对应变量、概念或理论',variants:'慎用变体与原因',notes:'阅读笔记',alternate_name:'中文名称'})[key]]).filter(([key])=>String(e[key]||'').trim());
    if(!values.length)return '';
    return `<section class="knowledge-read-section"><h3>${esc(title)}</h3><dl>${values.map(([key,label])=>`<div><dt>${esc(label)}</dt><dd>${esc(e[key])}</dd></div>`).join('')}</dl></section>`;
  }
  function showEntry(e){
    const legacy=e.extraction==='legacy',isVariable=e.kind==='variable';
    $('notebook-detail').dataset.mode='entry';
    $('detail-title').textContent=e.display_name||e.name;$('detail-title').lang=e.language==='en'?'en':'zh-CN';math($('detail-title'));
    const missing=fields.filter(([key])=>!referenceKeys.has(key)&&!String(e[key]||'').trim()).map(([,label])=>label);
    $('detail-body').innerHTML=`<article class="knowledge-reading"><div class="knowledge-read-source"><a href="${esc(paperLink(e))}">${esc(e.title)}</a><p>${esc((e.authors||[]).join('；'))}${e.authors?.length?' · ':''}${esc(e.year||'年份待核实')}</p><div class="knowledge-badges"><span class="knowledge-badge">${statuses[e.status]}</span><span class="knowledge-badge">${legacy?'历史测量摘录':e.extraction==='manual'?'人工整理卡片':'结构化卡片'}</span>${e.group?`<span class="knowledge-badge">${esc(e.group)}</span>`:''}</div></div>
      ${legacy?`<section class="knowledge-read-section"><h3>变量详细说明</h3><div class="report knowledge-source-report">${sourceExcerpt(e)}</div><p class="notebook-note">这是已读报告保留的完整测量摘录，可能包含多个指标。尚未拆成单独字段的信息，可直接在上文阅读。</p></section>`:''}
      ${readingSection(e,isVariable?'变量含义':'用词与定义',[...(e.alternate_name?['alternate_name']:[]),'original','role','concept','theory',...(legacy&&e.definition===e.raw?[]:['definition']),'mapping','variants'])}
      ${isVariable?readingSection(e,'测量口径',['formula','numerator','denominator','unit','window','level','measure_variants']):''}
      ${isVariable?readingSection(e,'数据与处理',['data_source','access','processing','risk']):''}
      ${isVariable?(readingSection(e,'测量文献来源',referenceFields.map(([key])=>key))||'<section class="knowledge-read-section"><h3>测量文献来源</h3><p class="notebook-note">这条记录尚未单独整理引用文献。已有引用可能保留在详细说明中；需结合变量定义与参考文献核对后补充。</p></section>'):''}
      ${readingSection(e,'依据与笔记',['evidence','notes'])}
      ${isVariable&&missing.length?`<details><summary>尚未单独整理的字段（${missing.length} 项）</summary><p class="notebook-note">${esc(missing.join('、'))}。空字段表示尚未提取或拆分，不代表论文没有报告。</p></details>`:''}
      ${legacy?'':`<details><summary>查看报告原始摘录</summary><div class="report knowledge-source-report">${sourceExcerpt(e)}</div></details>`}
      <details><summary>来源报告与修订记录</summary>${e.sources.map((s,i)=>`<p><a href="${base}/api/memory/versions/${encodeURIComponent(s)}/export/html">下载来源报告 ${i+1}</a></p>`).join('')}<p class="notebook-note">${e.history.length} 次人工修订。进入编辑可查看修订前的内容。</p></details></article>`;
    $('detail-footer').innerHTML=`<a class="btn btn-secondary" href="${esc(paperLink(e))}">查看来源论文</a><button type="button" class="btn btn-primary" id="edit-entry">编辑与补充</button>`;
    $('edit-entry').onclick=()=>openEntry(e.id,false,true);
    $('detail-body').scrollTop=0;math($('detail-body'));
  }
  async function openEntry(id,split=false,editing=false){
    const n=++detailSeq;$('notebook-detail').dataset.mode='entry';
    const listed=items.find(e=>e.id===id);$('detail-title').textContent=listed?.display_name||listed?.name||'详情';
    $('detail-body').innerHTML='<p class="notebook-note" role="status">正在读取详细信息…</p>';
    $('detail-footer').replaceChildren();$('detail-body').scrollTop=0;
    if(!$('notebook-detail').open)$('notebook-detail').showModal();
    try{const {entry:e}=await api('/api/knowledge/'+encodeURIComponent(id));if(n!==detailSeq||!$('notebook-detail').open)return;
      if(!split&&!editing){showEntry(e);return;}
      $('detail-title').textContent=split?'从摘录拆分一个变量':e.name;if(split){e.name='';for(const [key] of fields)if(key!=='evidence')e[key]='';e.status='pending';e.group='';e.notes='';}
      const editFields=e.kind==='variable'?fields:[['original','原文用词'],['definition','定义与边界'],['mapping','对应变量、概念或理论'],['variants','慎用变体与原因'],['evidence','原文依据']];
      $('detail-body').innerHTML=`<p class="notebook-note">来源：<a href="${esc(paperLink(e))}">${esc(e.title)}</a> · ${esc(e.year||'年份待核实')}<br>${esc(e.section)} · ${e.extraction==='legacy'?'历史摘录可能包含多个指标；字段未拆分的请结合原文填写。':'根据报告字段提取，尚需对照原文核对。'}</p><details open><summary>保留的报告原始摘录</summary><div class="report knowledge-source-report">${e.raw_html||esc(e.raw)}</div></details><details><summary>来源版本与编辑记录</summary>${e.sources.map((s,i)=>`<p><a href="${base}/api/memory/versions/${encodeURIComponent(s)}/export/html">下载来源报告 ${i+1}</a></p>`).join('')}<p class="notebook-note">${e.history.length} 次人工修订。自动同步保留人工修改，完整历史随文献备份保存。</p>${e.history.slice(-5).reverse().map(h=>`<details><summary>修订前版本 ${h.revision} · ${esc(new Date(h.updated*1000).toLocaleString('zh-CN'))}</summary><pre class="knowledge-excerpt">${esc(JSON.stringify(h,null,2))}</pre></details>`).join('')}</details><form id="entry-form" class="form-grid"><label class="wide">记录名称<input name="name" value="${esc(e.name)}" required maxlength="300"></label><label>自定分组<input name="group" value="${esc(e.group)}" maxlength="300" placeholder="如：创新产出 / 专利引用"></label><label>核对状态<select name="status">${Object.entries(statuses).map(([k,v])=>`<option value="${k}" ${e.status===k?'selected':''}>${v}</option>`).join('')}</select></label>${editFields.filter(([key])=>!referenceKeys.has(key)).map(field=>fieldInput(e,field)).join('')}${e.kind==='variable'?referenceEditor(e):''}<label class="wide">阅读笔记与核对依据<textarea name="notes" rows="3" maxlength="16000">${esc(e.notes)}</textarea></label><p id="entry-message" class="notebook-note wide" role="status"></p></form>`;
      $('detail-footer').innerHTML='<span class="notebook-note">'+(split?'新卡片关联同一原始摘录，原记录继续保留。':'只修订这条来源记录；原始摘录与报告保留。')+'</span>'+(!split&&e.kind==='variable'?'<button type="button" class="btn btn-secondary" id="split-entry">拆分为变量卡片</button>':'')+'<button class="btn btn-primary" form="entry-form" type="submit">保存卡片</button>';
      const cancel=document.createElement('button');cancel.type='button';cancel.className='btn btn-secondary';cancel.id='cancel-entry-edit';cancel.textContent='取消编辑';cancel.onclick=()=>openEntry(id);$('detail-footer').append(cancel);
      if($('split-entry'))$('split-entry').onclick=()=>openEntry(id,true);
      $('entry-form').onsubmit=async event=>{event.preventDefault();const button=$('detail-footer').querySelector('[type="submit"]');button.disabled=true;try{const data=Object.fromEntries(new FormData($('entry-form')));data.revision=e.revision;const saved=await api('/api/knowledge/'+e.id+(split?'/split':''),data);if(!split&&selected.has(e.id))selected.set(e.id,saved.entry);$('notebook-detail').close();await load(page);message('卡片已保存。人工修订不会被自动同步覆盖。');}catch(error){$('entry-message').textContent=error.message;}finally{button.disabled=false;}};
      $('detail-body').scrollTop=0;math($('detail-body'));
    }catch(error){if(n!==detailSeq||!$('notebook-detail').open)return;$('detail-body').innerHTML=`<p class="notebook-note" role="alert">${esc(error.message)}</p><button type="button" class="btn btn-secondary" id="retry-entry">重试加载详情</button>`;$('retry-entry').onclick=()=>openEntry(id,split,editing);}
  }
  function compare(){
    const entries=[...selected.values()];
    const allRows=[['title','来源论文'],['year','年份'],['status','核对状态'],['definition','操作定义 / 历史摘录'],...fields.filter(([key])=>key!=='definition'),['group','自定分组'],['mapping','三层对应'],['variants','慎用变体'],['notes','阅读笔记']];
    const rows=allRows.filter(([key])=>entries.some(e=>e[key])),empty=allRows.filter(([key])=>!entries.some(e=>e[key])).map(([,label])=>label);
    $('compare-body').innerHTML='<p class="notebook-note">先比较定义、分母、窗口、层级、处理与样本，再核对测量参考文献及本文调整，判断是否可比。同引一篇文献不保证口径相同。空字段表示未取得或未拆分，不能视为相同。</p><table class="comparison-table"><thead><tr><th>比较项目</th>'+entries.map(e=>`<th>${esc(e.display_name||e.name)}</th>`).join('')+'</tr></thead><tbody>'+rows.map(([key,label])=>`<tr><th>${label}</th>${entries.map(e=>`<td>${esc(key==='status'?statuses[e[key]]:e[key]||'待核实 / 未拆分')}</td>`).join('')}</tr>`).join('')+'</tbody></table>'+(empty.length?'<p class="notebook-note">所选记录均未取得或尚未拆分的字段：'+esc(empty.join('、'))+'。这些字段尚不能比较。</p>':'');
    $('notebook-compare').showModal();math($('compare-body'));
  }
  let refineData={candidates:[],refining:{}},refineTimer=null;
  function refineState(d){
    refineData={candidates:d.refine_candidates||[],refining:d.refining||{}};
    const active=Object.values(refineData.refining).filter(r=>['queued','running'].includes(r.state)).length;
    const button=$('notebook-refine');
    button.hidden=!refineData.candidates.length&&!active;
    button.querySelector('span').textContent=active?`正在重整（${active}）`:`按新规范重整（${refineData.candidates.length}）`;
    clearTimeout(refineTimer);
    if(active)refineTimer=setTimeout(pollRefine,3000);
  }
  async function pollRefine(){
    try{
      const d=await api('/api/knowledge/refine');
      const before=refineData.refining;refineData.refining=d.refining;
      const finished=Object.entries(d.refining).filter(([pid,r])=>['done','failed'].includes(r.state)&&before[pid]?.state!==r.state);
      if(finished.length){await load(page);const failed=finished.filter(([,r])=>r.state==='failed');
        message(failed.length?'部分论文未完成重整：'+failed.map(([,r])=>r.message).join('；'):'重整完成：'+finished.map(([,r])=>r.message).join('；'),!!failed.length);return;}
      if($('notebook-detail').open&&$('notebook-detail').dataset.mode==='refine')renderRefine();
      refineState({refine_candidates:refineData.candidates,refining:d.refining});
    }catch(e){message(e.message,true);}
  }
  function renderRefine(){
    const states={queued:'等待中',running:'整理中',done:'已完成',failed:'未完成'};
    const rows=refineData.candidates.map(p=>{const r=refineData.refining[p.id];return `<label class="refine-row"><input type="checkbox" value="${esc(p.id)}" ${r&&['queued','running'].includes(r.state)?'disabled':'checked'}><span>${esc(p.title)}</span>${r?`<small class="refine-state ${esc(r.state)}">${esc(states[r.state]||r.state)}${r.message?' · '+esc(r.message):''}</small>`:''}</label>`;}).join('');
    $('detail-body').innerHTML=`<p class="notebook-note">早期报告按旧规则生成：同一指标的对数、标准化、移动平均等变换被拆成多张卡，常规控制变量也单独成卡，术语表混入了变量和分组标签。重整会按现行规则（一个构念一张卡、术语只收概念与理论）重写这些论文的变量卡与术语表。</p>
      <ul class="refine-rules"><li>每篇调用一次精读所用的模型，只读取已保存的报告，不重新读原文。</li><li>原有自动提取的记录保留为“已被重整替代”，勾选“包含旧报告版本”可查看；你手动拆分或编辑过的卡片不受影响。</li><li>重整结果仍需对照原文核对。</li></ul>
      <div class="refine-list">${rows||'<p class="notebook-note">没有需要重整的论文。</p>'}</div>`;
  }
  function openRefine(){
    $('notebook-detail').dataset.mode='refine';
    $('detail-title').textContent='按新规范重整变量与术语';
    renderRefine();
    $('detail-footer').innerHTML='<button type="button" class="btn btn-secondary" data-close="notebook-detail">取消</button><button type="button" class="btn btn-primary" id="start-refine">开始重整</button>';
    bindClose();
    $('start-refine').onclick=async()=>{
      const papers=[...$('detail-body').querySelectorAll('.refine-list input:checked')].map(i=>i.value);
      if(!papers.length){message('请先勾选要重整的论文。',true);return;}
      $('start-refine').disabled=true;
      try{await api('/api/knowledge/refine',{papers});$('notebook-detail').close();message(`已开始重整 ${papers.length} 篇论文，完成后自动刷新。`);await pollRefine();}
      catch(e){message(e.message,true);$('start-refine').disabled=false;}
    };
    if(!$('notebook-detail').open)$('notebook-detail').showModal();
  }
  function bindClose(){document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>{$(b.dataset.close).close();detailSeq++;});}
  bindClose();$('notebook-detail').addEventListener('cancel',()=>detailSeq++);$('notebook-menu').onclick=()=>document.querySelector('.sidebar').classList.toggle('open');
  $('notebook-search').onsubmit=e=>{e.preventDefault();load();};$('notebook-query').oninput=()=>{clearTimeout(timer);timer=setTimeout(()=>load(),250);};
  for(const id of ['notebook-field','notebook-year','notebook-status','notebook-group','notebook-history'])if($(id))$(id).onchange=()=>load();
  $('notebook-prev').onclick=()=>load(page-1);$('notebook-next').onclick=()=>load(page+1);
  if(!scholars){
    document.querySelectorAll('[data-kind]').forEach(b=>b.onclick=()=>{kind=b.dataset.kind;markKind();unfold=true;load();});
    // English-language and Chinese-language literature keep separate shelves; the choice is remembered.
    const markLang=()=>document.querySelectorAll('[data-lang]').forEach(t=>t.setAttribute('aria-current',String(t.dataset.lang===lang)));markLang();
    document.querySelectorAll('[data-lang]').forEach(b=>b.onclick=()=>{lang=b.dataset.lang;markLang();try{localStorage.setItem('rf-knowledge-lang',lang);}catch{}unfold=true;load();});
    // Folders are real links (open in a new tab works); a plain click opens them in place.
    $('notebook-results').addEventListener('click',e=>{const a=e.target.closest('.paper-folder');if(!a||e.button!==0||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;e.preventDefault();openFolder(a.dataset.folder,a,true);});
    $('folder-back').onclick=()=>{if(history.state?.rfFolder===paper)history.back();else{history.pushState(null,'',folderUrl(''));closeFolder();}};
    addEventListener('popstate',()=>{const id=new URLSearchParams(location.search).get('paper')||'';if(id===paper)return;if(id)openFolder(id,null,false);else closeFolder();});
    level();
    $('compare-selected').onclick=compare;
    $('notebook-refine').onclick=openRefine;
    $('notebook-rebuild').onclick=async()=>{const b=$('notebook-rebuild');b.disabled=true;try{const d=await api('/api/knowledge/rebuild',{});await load();message('同步完成：新增 '+d.added+' 条来源记录，补齐 '+d.authors_repaired+' 篇作者信息。');}catch(e){message(e.message,true);}finally{b.disabled=false;}};
  }
  load().then(()=>{if(!scholars&&/^[a-f0-9]{32}$/.test(location.hash.slice(1)))openEntry(location.hash.slice(1));});
})();
