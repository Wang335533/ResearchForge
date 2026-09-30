
const totals=JSON.parse(document.getElementById('report-counts').textContent);
const cards=[...document.querySelectorAll('.searchable')], search=document.getElementById('search'), selected=document.getElementById('selected-only'), status=document.getElementById('search-status');
const indexed=cards.map(card=>({card,text:card.textContent.toLocaleLowerCase()}));
function filter(){
  const q=search.value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const visible={idea:0,proposal:0,paper:0};
  for(const {card,text} of indexed){
    const match=q.every(s=>text.includes(s))&&(!selected.checked||card.dataset.kind==='proposal'||card.dataset.selected==='true');
    card.hidden=!match;
    if(match)visible[card.dataset.kind]++;
    const toc=document.querySelector(`[data-toc="${card.id}"]`);
    if(toc)toc.hidden=!match;
  }
  status.textContent=`当前显示 ${visible.idea} / ${totals.ideas} 个构想 · ${visible.paper} / ${totals.papers} 篇文献 · ${visible.proposal} / ${totals.proposals} 份提案`;
  document.getElementById('no-results').hidden=Object.values(visible).some(n=>n>0);
}
function resetFilter(){search.value='';selected.checked=false;filter();}
search.addEventListener('input',filter);selected.addEventListener('change',filter);
document.getElementById('reset').addEventListener('click',resetFilter);
document.getElementById('print').addEventListener('click',()=>window.print());
const printDetails=[document.getElementById('context'),...document.querySelectorAll('.print-expand')];
window.addEventListener('beforeprint',()=>{for(const detail of printDetails){detail.dataset.wasOpen=String(detail.open);detail.open=true;}});
window.addEventListener('afterprint',()=>{for(const detail of printDetails)detail.open=detail.dataset.wasOpen==='true';});
function revealTarget(hash){
  const target=document.getElementById(hash.replace(/^#/,''));
  if(!target)return;
  if(target.hidden)resetFilter();
  if(target.tagName==='DETAILS')target.open=true;
}
document.addEventListener('click',event=>{const link=event.target.closest('a[href^="#"]');if(link)revealTarget(link.getAttribute('href'));});
window.addEventListener('hashchange',()=>revealTarget(window.location.hash));
function progress(){const d=document.documentElement;document.querySelector('.reading-progress').style.width=`${d.scrollHeight>d.clientHeight?100*d.scrollTop/(d.scrollHeight-d.clientHeight):0}%`;}
window.addEventListener('scroll',progress,{passive:true});filter();revealTarget(window.location.hash);progress();
