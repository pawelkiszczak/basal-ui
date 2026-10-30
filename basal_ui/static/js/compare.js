// Playground Compare: the same request on two loaded models (POST /api/decide for each, at the same time), the answers
// side by side and, per question, each option's probability on both with the difference.
'use strict';
let compareSignature='';
function renderCompareModels(ready) {
  const others=ready.filter(m=>m.id!==selectedModel), select=$('#compareModel'), current=select.value;
  const signature=JSON.stringify([selectedModel,others.map(m=>m.id)]);
  // with one model Compare stays in place, unavailable (aria-disabled), and the hint next to it says what it needs
  select.hidden=!others.length; $('#compareHint').hidden=Boolean(others.length);
  $('#compareButton').setAttribute('aria-disabled',String(decisionBusy||!others.length));
  if(signature===compareSignature)return;
  compareSignature=signature;
  select.innerHTML=others.map(m=>html`<option value="${m.id}">${shortName(m.id)}</option>`.text).join('');
  if(others.some(m=>m.id===current))select.value=current;
}
// an answer's option probabilities as {key: p}; a yes/no answer as {yes, no}
function optionProbabilities(a) {
  if(a?.probabilities&&typeof a.probabilities==='object'&&!Array.isArray(a.probabilities))return a.probabilities;
  if(typeof a?.noul==='number')return {yes:a.noul,no:1-a.noul};
  return null;
}
function compareColumn(id, result, request) {
  if(result.status==='rejected')return html`<section><h3>${shortName(id)}</h3><div class="error-box" role="alert">${result.reason?.message||String(result.reason)}</div></section>`;
  const data=result.value, answers=data?.answers||{}, u=data?.usage||{};
  const items=Object.entries(answers).map(([name,a])=>{
    const q=request.questions?.[name]||{}, probs=optionProbabilities(a), head=a&&typeof a==='object'?resultHeadline(name,a,q):null;
    return html`<article class="answer"><h4><span>${name}</span> <span class="chip">${a?.type||q.type||'?'}</span></h4>${head?html`<div class="headline">${head}</div>`:''}${a?.confidence!=null?html`<div class="answer-meta">${t('Confidence')}: ${pct(a.confidence)}</div>`:''}${probs?raw(probBars(probs,q)):html`<pre>${JSON.stringify(a,null,2)}</pre>`}</article>`;
  });
  return html`<section aria-label="${shortName(id)}"><h3 title="${id}">${shortName(id)}</h3><div class="answer-meta">${t('Latency')}: ${u.latency_ms??'—'} ms · ${t('Input tokens')}: ${u.input_tokens??'—'}</div>${items}</section>`;
}
// per question: every option's probability on A and B and B − A; the chosen answers same or different
function compareDiff(ids, results, request) {
  const [a,b]=results.map(r=>r.status==='fulfilled'?r.value?.answers||{}:null);
  if(!a||!b)return '';
  const top=p=>p?Object.entries(p).sort((x,y)=>y[1]-x[1])[0]?.[0]:null;
  const tables=Object.keys(request.questions||a).map(name=>{
    const pa=optionProbabilities(a[name]), pb=optionProbabilities(b[name]);
    if(!pa||!pb)return '';
    const keys=[...new Set([...Object.keys(pa),...Object.keys(pb)])], same=top(pa)===top(pb);
    const rows=keys.map(k=>{
      const d=(Number(pb[k])||0)-(Number(pa[k])||0), sign=Math.abs(d)<0.0005?'':d>0?'up':'down';
      return html`<tr><th scope="row">${k}</th><td>${pct(pa[k])}</td><td>${pct(pb[k])}</td><td class="${sign}">${d>0?'+':d<0?'−':'±'}${(Math.abs(d)*100).toFixed(1)} pp</td></tr>`;
    });
    return html`<div class="compare-diff"><div class="section-head"><h4>${name}</h4><span class="badge ${same?'fit-fits':'fit-tight'}">${same?t('Same answer'):t('Different answers')}</span></div><div class="table-wrap" tabindex="0" role="region" aria-label="${t('Option probabilities of {question} on both models',{question:name})}"><table class="mini-table"><caption class="sr-only">${t('Option probabilities of {question} on both models',{question:name})}</caption><thead><tr><th scope="col">${t('Option')}</th><th scope="col">${shortName(ids[0])}</th><th scope="col">${shortName(ids[1])}</th><th scope="col">${t('Difference')}</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
  });
  return html`<h3 class="compare-diff-title">${t('Probability difference ({b} − {a})',{a:shortName(ids[0]),b:shortName(ids[1])})}</h3>${tables}`;
}
async function compareDecision() {
  if(decisionBusy)return;
  const ids=[selectedModel,$('#compareModel').value];
  if(!ids[1]||ids[0]===ids[1]){announce(t('Load a second model to compare.'));return;}  // the hint says it too
  const request=validatedRequest(); if(!request)return;
  setDecisionBusy(true);
  $('#playgroundForm').classList.add('comparing');  // two columns and the difference table take the full width
  $('#results').innerHTML=html`<p class="muted">${t('Comparing {a} and {b}…',{a:shortName(ids[0]),b:shortName(ids[1])})}</p>`.text;
  try{
    const results=await Promise.allSettled(ids.map(model=>api('/api/decide',{method:'POST',body:JSON.stringify({...request,model})})));
    $('#results').innerHTML=html`<div class="section-head"><strong>${t('Compare')}</strong></div><div class="compare-grid">${ids.map((id,i)=>compareColumn(id,results[i],request))}</div>${compareDiff(ids,results,request)}`.text;
    $('#rawResponse').textContent=JSON.stringify(Object.fromEntries(ids.map((id,i)=>[id,results[i].status==='fulfilled'?results[i].value:{error:String(results[i].reason?.message)}])),null,2);
    $('#rawDetails').hidden=false;
    announce(t('Comparison ready')); revealOutcome($('.response-panel'),true);
    if(results.some(r=>r.status==='fulfilled'))store('basal-ui.tried','1');
  }catch(e){showDecideError(e.message);}
  finally{setDecisionBusy(false);}
}
$('#compareButton').addEventListener('click',compareDecision);
$('#playgroundModel').addEventListener('change',()=>renderCompareModels(readyModels()));
