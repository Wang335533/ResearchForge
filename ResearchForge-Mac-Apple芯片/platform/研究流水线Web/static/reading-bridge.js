/* Full-text handoff is local and explicit; it never starts model analysis. */
const RB={cache:new Map(),dialog:null,selected:null,previewSeq:0,searchSeq:0};
async function bridgeAPI(path,options={}){const r=await fetch('/papers/api'+path,{...options,headers:{'X-PaperLens':'1',...(options.body?{'Content-Type':'application/json'}:{}),...options.headers}});const d=await r.json();if(!r.ok)throw Error(d.error||'连接未完成，请重试。');return d;}
function readingBridgeSlot(index){return `<div class="reading-bridge-slot" data-reading-index="${index}"><span class="bridge-status">正在匹配本地原文…</span></div>`;}
async function connectResearchPapers(root,ideaId,kind,refs){
 const task=S.taskId;if(!task)return;const key=JSON.stringify([task,String(ideaId),kind,refs]);root.dataset.bridgeKey=key;
 const load=async()=>{const p=new URLSearchParams({task_id:task,idea_id:String(ideaId),kind});return bridgeAPI('/bridge/references?'+p);};
 try{let cached=RB.cache.get(key);if(!cached||Date.now()-cached.at>60000){cached={at:Date.now(),promise:load()};RB.cache.set(key,cached);if(RB.cache.size>50)RB.cache.delete(RB.cache.keys().next().value);}
  const data=await cached.promise;if(root.dataset.bridgeKey!==key||S.taskId!==task||!root.isConnected)return;
  root.querySelectorAll('[data-reading-index]').forEach(slot=>{const item=data.items.find(x=>x.index===Number(slot.dataset.readingIndex));if(!item)return;const m=item.match,c=m.candidates.find(x=>x.uid===m.uid),descriptor={task_id:task,idea_id:String(ideaId),kind,index:item.index};
   slot.replaceChildren();const status=document.createElement('span');status.className='bridge-status '+(m.status==='matched'?'available':'');status.textContent=m.status==='matched'?'本地原文已匹配':m.status==='review'?'原文候选待核对':m.status==='missing'?'本地原文未匹配':'原文连接暂不可用';status.title=m.message;slot.append(status);
   const button=(text,action)=>{const b=document.createElement('button');b.className='text-btn bridge-action';b.textContent=text;b.onclick=action;slot.append(b);return b;};
   if(c){button('载入精读 ↗',e=>handoffPaper(descriptor,c.uid,false,e.currentTarget));button('预览原文',()=>openReadingBridge(item,descriptor));if(c.memory_paper_id){const a=document.createElement('a');a.className='text-btn';a.textContent='已读 · 文献地图';a.href='/papers/memory#'+encodeURIComponent(c.memory_paper_id);a.target='_blank';a.rel='noopener noreferrer';slot.append(a);}}
   else button(m.candidates.length?'核对候选原文':'搜索本地原文',()=>openReadingBridge(item,descriptor));
  });
 }catch(e){RB.cache.delete(key);if(root.dataset.bridgeKey!==key)return;root.querySelectorAll('[data-reading-index]').forEach(slot=>{slot.replaceChildren();const note=document.createElement('span');note.className='bridge-status';note.textContent='连接本地原文失败：'+e.message;const b=document.createElement('button');b.className='text-btn';b.textContent='重新匹配';b.onclick=()=>connectResearchPapers(root,ideaId,kind,refs);slot.append(note,b);});}
}
async function handoffPaper(descriptor,uid,confirmed,button){
 button.disabled=true;const prior=button.textContent;button.textContent='正在载入…';
 let tab;try{tab=window.open('about:blank','_blank');if(tab)tab.opener=null;}catch{}
 try{const data=await bridgeAPI('/bridge/import',{method:'POST',body:JSON.stringify({...descriptor,uid,confirmed})});
  if(button.nextElementSibling?.dataset.bridgeOpen)button.nextElementSibling.remove();
  const a=document.createElement('a');a.href=data.reading_url;a.className='text-btn';a.dataset.bridgeOpen='1';a.textContent='已载入，打开精读 ↗';button.after(a);
  // Some embedded browsers return a popup handle without opening a visible tab.
  // Keep an explicit link even when automatic navigation appears to succeed.
  try{if(tab&&!tab.closed)tab.location.replace(data.reading_url);}catch{}
  toast(data.created?'已载入，请在精读页开始分析':'此前已载入，未重复导入');
 }catch(e){if(tab&&!tab.closed)tab.close();toast(e.message,'error');if($('reading-bridge-dialog').open)$('bridge-error').textContent=e.message;}
 finally{button.disabled=false;button.textContent=prior;}
}
function clearBridgePreview(){RB.selected=null;RB.previewSeq++;$('reading-bridge-dialog').querySelector('[data-bridge-open]')?.remove();$('bridge-import').disabled=true;$('bridge-confirm').checked=false;$('bridge-confirm-label').hidden=true;$('bridge-paper-title').textContent='选择一份原文';$('bridge-paper-meta').textContent='';$('bridge-paper-path').textContent='';$('bridge-paper-text').textContent='选择候选文件后，可以核对论文正文。';}
function renderBridgeCandidates(candidates){$('bridge-candidates').replaceChildren();for(const c of candidates){const b=document.createElement('button');b.className='bridge-candidate';b.disabled=c.available===false;const title=document.createElement('strong');title.textContent=c.title;const meta=document.createElement('span');meta.textContent=[c.year||'年份待核实',c.journal,c.match_reason||'关键词候选 · 需核对',c.available===false?'原文件不可用':''].filter(Boolean).join(' · ');b.append(title,meta);b.onclick=()=>previewBridgePaper(c);$('bridge-candidates').append(b);}if(!candidates.length)$('bridge-candidates').textContent='暂未找到准确原文。可调整上方题名或关键词继续搜索；不代表这篇论文不存在。';}
function openReadingBridge(item,descriptor){RB.dialog={item,descriptor};RB.searchSeq++;clearBridgePreview();$('bridge-title').textContent='连接本地原文';$('bridge-reference').textContent=[item.reference.title,item.reference.authors,item.reference.year].filter(Boolean).join(' · ');$('bridge-query').value=(item.reference.title||'').slice(0,400);$('bridge-notice').textContent=item.match.message;$('bridge-error').textContent='';$('bridge-search').disabled=false;renderBridgeCandidates(item.match.candidates);$('reading-bridge-dialog').showModal();if(item.match.status==='matched')previewBridgePaper(item.match.candidates[0]);}
async function previewBridgePaper(c){const seq=++RB.previewSeq;RB.selected=null;$('reading-bridge-dialog').querySelector('[data-bridge-open]')?.remove();$('bridge-import').disabled=true;$('bridge-error').textContent='';$('bridge-paper-title').textContent=c.title;$('bridge-paper-text').textContent='正在读取原文…';$('bridge-confirm').checked=false;
 try{const result=await bridgeAPI('/library/papers/'+encodeURIComponent(c.uid));if(seq!==RB.previewSeq||!$('reading-bridge-dialog').open)return;RB.selected=c;const exact=RB.dialog.item.match.status==='matched'&&RB.dialog.item.match.uid===c.uid;$('bridge-confirm-label').hidden=exact;$('bridge-paper-title').textContent=result.paper.title;$('bridge-paper-meta').textContent=[result.paper.year||'年份待核实',result.paper.journal,result.chars.toLocaleString()+' 字符',...(result.warnings||[])].join(' · ');$('bridge-paper-path').textContent=result.paper.path;$('bridge-paper-text').textContent=result.preview+ (result.preview_limited?'\n\n（此处显示前 60,000 字符，载入精读会读取完整正文。）':'');$('bridge-import').disabled=!exact;
 }catch(e){if(seq!==RB.previewSeq)return;$('bridge-error').textContent=e.message;$('bridge-paper-text').textContent='未能读取这份原文。';}
}
$('bridge-close').onclick=()=>$('reading-bridge-dialog').close();$('reading-bridge-dialog').addEventListener('close',()=>{RB.previewSeq++;RB.searchSeq++;RB.selected=null;});
$('bridge-search-form').onsubmit=async e=>{e.preventDefault();const seq=++RB.searchSeq;clearBridgePreview();$('bridge-search').disabled=true;$('bridge-error').textContent='';try{const result=await bridgeAPI('/library/search?'+new URLSearchParams({q:$('bridge-query').value,scope:'title'}));if(seq!==RB.searchSeq)return;renderBridgeCandidates(result.results);$('bridge-notice').textContent=result.has_more?'显示前 20 个题名候选，请增加关键词缩小范围。':'题名关键词候选，请核对后选择。';}catch(err){if(seq===RB.searchSeq)$('bridge-error').textContent=err.message;}finally{if(seq===RB.searchSeq)$('bridge-search').disabled=false;}};
$('bridge-confirm').onchange=()=>{$('bridge-import').disabled=!RB.selected||!$('bridge-confirm').checked;};
$('bridge-import').onclick=e=>{if(RB.selected){const exact=RB.dialog.item.match.status==='matched'&&RB.dialog.item.match.uid===RB.selected.uid;handoffPaper(RB.dialog.descriptor,RB.selected.uid,!exact&&$('bridge-confirm').checked,e.currentTarget);}};
