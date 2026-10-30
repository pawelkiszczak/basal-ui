// Playground response: a Decision's answers, safety badges, probabilities and source spans; sending.
'use strict';
function resultHeadline(name, answer, requestQuestion) {
  const type=answer.type || requestQuestion?.type || 'unknown';
  if(type==='choice') { const key=answer.choice ?? answer.answer ?? ''; const criteria=requestQuestion?.criteria; const desc=criteria && !Array.isArray(criteria) ? criteria[key] : null; const text=withoutKey(desc==null?'':typeof desc==='string'?desc:JSON.stringify(desc),key); return `${key}${text?` · ${text}`:''}`; }
  if(type==='noul') { const p=Number(answer.noul ?? answer.probabilities?.true); return Number.isFinite(p)?t('P(yes) {value}',{value:`${(p*100).toFixed(1)}%`}):t('P(yes) —'); }
  if(type==='score') { const legend=answer.legend; const score=Number(answer.score ?? answer.expected_score); const top=legend&&Number.isFinite(score)?legend[String(Math.round(score))]:null; return `${t('Expected level {value}',{value:Number.isFinite(score)?score.toFixed(2):'—'})}${top?` · ${t('nearest: {value}',{value:top})}`:''}`; }
  return null;
}
// decision safety: deferral, automation against the served model's certified thresholds, calibration validity
const modelEntry=id=>(statusCache?.models||[]).find(m=>m.id===id)?.entry||null;
const uncalibrated=entry=>Boolean(entry?.temperature_per_prim)&&!Object.keys(entry.temperature_per_prim).length;
function decisionBase(answer,q) {
  const type=answer.type||q.type;
  if(type==='tool')return 'choice';  // a single tool decision is deferred against the choice threshold
  if(type!=='act')return type;
  if(q.base)return q.base;
  const keys=q.criteria&&!Array.isArray(q.criteria)?Object.keys(q.criteria):[];
  return keys.length&&keys.every(k=>k==='true'||k==='false')?'noul':'choice';
}
// why the model's certified thresholds do not hold for this answer (null: they do). The thresholds are certified on
// text decisions with the served option orders; a media answer ("text"), one fitted on other option orders or an act
// question shape the calibration was not validated for ("unvalidated") and uncalibrated probabilities get no claim.
function certificateGap(answer, entry) {
  if(entry?.calibration_warning)return `calibration warning: ${entry.calibration_warning}`;
  if(answer.calibration==='text')return t('the thresholds are certified on text decisions, not on media');
  if(answer.calibration==='unvalidated')return t(answer.type==='act'?'the calibration is not validated for this question shape':'the calibration was fitted on other option orders');
  if(answer.calibration!=null&&answer.calibration!=='validated')return `calibration: ${answer.calibration}`;
  if(uncalibrated(entry))return t('the probabilities are not calibrated');
  return null;
}
// the certified 1% / 5% thresholds of the answer's type (null: no claim for it); the chip says the status in words with
// the semantic colours: success = clears a threshold, warning = below them or deferred (review), neutral = no claim
function certifiedFor(answer,q,entry) {
  const type=decisionBase(answer,q);
  if(!entry||!['choice','noul','score'].includes(type)||answer.confidence==null)return null;
  const cert=entry.certified||{}, t1=cert['0.01']?.[type], t5=cert['0.05']?.[type];
  return t1==null&&t5==null?null:{type,t1:t1==null?null:Number(t1),t5:t5==null?null:Number(t5),c:Number(answer.confidence)};
}
function automationStatus(answer,q,entry) {
  const gap=certificateGap(answer,entry);
  if(answer.deferred===true)return {tone:'warning',text:t('Deferred to a person — review'),why:t('The answer was deferred: its confidence did not reach the requested max_error, so a person should decide.')};
  if(typeof answer.max_error==='string'||gap)return {tone:'neutral',text:t('Not certified for this answer — review'),why:gap||t('this answer was deferred or its requested max_error is uncertified')};
  const cert=certifiedFor(answer,q,entry);
  if(!cert){
    if(!entry||!['choice','noul','score'].includes(decisionBase(answer,q))||answer.confidence==null)return null;
    return {tone:'neutral',text:t('No certificate for this type'),why:t('The calibration in use certifies no thresholds for {type} decisions, so it makes no error claim for this answer.',{type:decisionBase(answer,q)})};
  }
  const {type,t1,t5,c}=cert;
  if(t1!=null&&c>=t1)return {tone:'success',text:t('Automatable at 1% error'),why:t('The confidence clears the threshold certified for 1% error on {type} decisions: on the calibration data, answers at least this confident were wrong at most 1% of the time.',{type}),cert};
  if(t5!=null&&c>=t5)return {tone:'success',text:t('Automatable at 5% error'),why:t('The confidence clears the threshold certified for 5% error on {type} decisions (not the stricter 1% one): on the calibration data, answers at least this confident were wrong at most 5% of the time.',{type}),cert};
  return {tone:'warning',text:t('Below the certified thresholds — review'),why:t('The confidence is below every certified threshold for {type} decisions, so the calibration makes no error claim: have a person review it, or send max_error to defer such answers.',{type}),cert};
}
const TONE_BADGE={success:'fit-fits',warning:'fit-tight',neutral:'fit-unknown'};
// confidence on a track with the certified marks; the fill takes the status colour
function confidenceGauge(cert,tone) {
  // a label centres under its mark (at an end it turns inwards); marks under 30% apart, which a narrow results column
  // cannot fit side by side, put the lower one's label on a second row
  const close=cert.t1!=null&&cert.t5!=null&&Math.abs(cert.t1-cert.t5)<0.3, lower=Math.min(cert.t1??1,cert.t5??1);
  const side=v=>(clamp01(v)>0.9?' before':clamp01(v)<0.1?' after':'')+(close&&v===lower?' low':'');
  const mark=(v,label)=>v==null?'':`<span class="meter-mark" style="left:${(clamp01(v)*100).toFixed(2)}%" aria-hidden="true"></span><span class="gauge-label${side(v)}" style="left:${(clamp01(v)*100).toFixed(2)}%" aria-hidden="true">${label}</span>`;
  const shown=v=>v==null?'—':pct(v);
  const text=t('Confidence {value}; 1% error threshold {one}, 5% error threshold {five}',{value:pct(cert.c),one:shown(cert.t1),five:shown(cert.t5)});
  return `<div class="gauge${close?' stacked':''}"><div class="meter${tone==='warning'?' warning':''}" role="meter" aria-label="${t('Confidence')}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(cert.c*100)}" aria-valuetext="${esc(text)}"><span class="meter-fill" style="width:${(clamp01(cert.c)*100).toFixed(2)}%"></span>${mark(cert.t5,t('≤{n}% error',{n:5}))}${mark(cert.t1,t('≤{n}% error',{n:1}))}</div></div>`;
}
function automationBadge(answer,q,entry,model) {
  const status=automationStatus(answer,q,entry);
  if(!status)return '';
  const shown=v=>v==null?'—':Number(v).toFixed(4);
  const thresholds=status.cert?`<p>${t('certified {type} thresholds: 1% error ≥ {one}, 5% error ≥ {five}',{type:status.cert.type,one:shown(status.cert.t1),five:shown(status.cert.t5)})}</p>`:'';
  return `<span class="badge ${TONE_BADGE[status.tone]}">${status.text}</span><details class="why" data-model="${esc(model||'')}"><summary>${t('Why?')}</summary><div class="why-body"><p>${esc(status.why)}</p>${thresholds}<p class="why-calibration">${t('Calibration in use: loading…')}</p></div></details>`;
}
// Shipped vs Selected calibration of a model, for the Why? disclosure (GET /api/calibrations, once per model)
const calibrationInfo={};
async function explainCalibration(details) {
  const model=details.dataset.model, line=$('.why-calibration',details); if(!line)return;
  const running=(statusCache?.models||[]).find(m=>m.id===model);
  try {
    calibrationInfo[model]??=await api(`/api/calibrations?id=${encodeURIComponent(model)}`);
    const d=calibrationInfo[model];
    let text=d.selected?t('Selected calibration: {file}, chosen in the Library in place of the shipped one.',{file:shortName(d.selected)}):t('Shipped calibration: the file supplied with the model for this execution configuration.');
    if(running?.restart_required)text+=' '+t('Another calibration or server setting is saved: it applies from the model\'s next start.');
    line.textContent=text;
  } catch { line.textContent=t('The calibration in use could not be read.'); }
}
document.addEventListener('toggle',e=>{if(e.target.matches?.('details.why')&&e.target.open)explainCalibration(e.target);},true);
function calibrationChip(answer,entry) {
  const c=answer.calibration;
  if(c==='validated')return `<span class="badge fit-fits">${t('calibrated')}</span>`;
  if(c==='unvalidated')return `<span class="badge fit-tight">${t(answer.type==='act'?'calibration not validated for this question shape':'unvalidated — fitted on other option orders')}</span>`;
  if(c==='text')return `<span class="badge fit-tight">${t('fitted on text (media request)')}</span>`;
  if(c==='per decision')return `<span class="badge fit-unknown">${t('per decision')}</span>`;
  if(c==null&&uncalibrated(entry))return `<span class="badge fit-tight">${t('uncalibrated probabilities')}</span>`;
  return '';
}
// a task answer's `automated`: {max_error: true (use as is) | false (review) | null (no certificate for the task)}
function automatedChips(automated) {
  if(!automated||typeof automated!=='object')return '';
  return Object.entries(automated).map(([risk,v])=>{
    const at=`${(Number(risk)*100).toFixed(0)}%`;
    return v===true?`<span class="badge fit-fits" title="max_error ${esc(risk)}">${at}: ${t('automated')}</span>`:v===false?`<span class="badge fit-tight" title="max_error ${esc(risk)}">${at}: ${t('review')}</span>`:`<span class="badge fit-unknown" title="max_error ${esc(risk)}">${at}: ${t('no certificate')}</span>`;
  }).join(' ');
}
function safetyStrip(answer,q,entry,model) {
  const parts=[];
  if(answer.deferred===true&&answer.refused!=null)parts.push(`<span class="muted"><code>refused</code>: ${esc(answer.refused)}</span>`);
  parts.push(automationBadge(answer,q,entry,model),calibrationChip(answer,entry),automatedChips(answer.automated));
  const html=parts.filter(Boolean).join(' '), status=automationStatus(answer,q,entry);
  const gauge=status?.cert&&answer.deferred!==true?confidenceGauge(status.cert,status.tone):'';
  const reason=typeof answer.max_error==='string'?`<span class="reason warning">${esc(answer.max_error)}</span>`:'';
  return html||reason?`<div class="chips safety">${html}</div>${gauge}${reason}`:'';
}
function resultBanners(entry,u) {
  let html=entry?.calibration_warning?`<div class="error-box">${esc(entry.calibration_warning)}</div>`:'';
  const truncated=u?.truncated;
  if(truncated)html+=`<div class="error-box">${t('State truncated to the context: dropped {dropped} of {total} tokens (kept {head} from the start, {tail} from the end)',{dropped:esc(truncated.dropped_tokens),total:esc(truncated.state_tokens),head:esc(truncated.head_tokens),tail:esc(truncated.tail_tokens)})}</div>`;
  return html;
}
const clamp01=v=>Math.max(0,Math.min(1,Number(v)||0));
// an option's description without what only repeats its key: "88412": "88412" → none, "4": "[4] Payment status" →
// "Payment status" (marked elements carry their badge number for the model)
const withoutKey=(text,k)=>{const tag=`[${k}]`;return text===String(k)||text===tag?'':text.startsWith(`${tag} `)?text.slice(tag.length+1):text;};
function labelOf(q,k) {
  const c=q.labels??q.criteria;
  return c&&typeof c==='object'&&!Array.isArray(c)&&c[k]!=null?withoutKey(textOf(c[k]),k)||null:null;
}
// probability bars, most probable first; `marked` = the keys drawn as chosen (default: the most probable)
function probBars(probs, q={}, marked=null) {
  const entries=Object.entries(probs||{}).sort((a,b)=>Number(b[1])-Number(a[1]));
  const chosen=marked??new Set(entries.length?[entries[0][0]]:[]);
  return entries.map(([k,v])=>{const p=clamp01(v), d=labelOf(q,k);return `<div class="prob ${chosen.has(k)?'winner':''}"><span title="${esc(d?`${k} · ${d}`:k)}">${esc(k)}${d?` · ${esc(d)}`:''}</span><div class="bar" aria-hidden="true"><span style="width:${p*100}%"></span></div><strong>${(p*100).toFixed(1)}%${chosen.has(k)?`<span class="sr-only"> · ${t('chosen')}</span>`:''}</strong></div>`;}).join('');
}
const metaLine=text=>`<div class="answer-meta">${text}</div>`;
const confidenceLine=answer=>metaLine(`${t('Confidence')}: ${answer.confidence==null?'—':pct(answer.confidence)}`);
const chipList=(items,empty)=>items.length?`<div class="chips">${items.map(x=>`<span class="chip">${esc(x)}</span>`).join('')}</div>`:`<div class="muted">${esc(t(empty))}</div>`;
const headlineDiv=text=>`<div class="headline">${text}</div>`;
const RESULT_VIEWS={
  multi(a,q){
    const sel=a.selected||[], objective=a.objective&&a.objective!=='threshold';
    const constraints=Object.entries(a.constraints_applied||{}).map(([k,v])=>`<span class="chip muted">${esc(k)}: ${esc(textOf(v))}</span>`).join('');
    return chipList(sel.map(k=>labelOf(q,k)?`${k} · ${labelOf(q,k)}`:k),'none selected')+metaLine(`${objective?`${t('expected {objective} score {score}',{objective:esc(a.objective),score:num(a.expected_score,3)})} · `:''}${t('set confidence {confidence}',{confidence:pct(a.set_confidence)})}${a.threshold!=null?` · ${t('threshold {value}',{value:esc(a.threshold)})}`:''}`)+(constraints?`<div class="chips">${constraints}</div>`:'')+probBars(a.probabilities,q,new Set(sel));
  },
  act(a,q){
    const costs=Object.entries(a.expected_costs||{}).sort((x,y)=>x[1]-y[1]);
    const table=costs.length?`<table class="mini-table"><thead><tr><th>${t('Action')}</th><th>${t('Expected cost')}</th></tr></thead><tbody>${costs.map(([k,v])=>`<tr${k===a.action?' class="chosen"':''}><td>${esc(k)}</td><td>${num(v,3)}</td></tr>`).join('')}</tbody></table>`:'';
    return headlineDiv(`${esc(a.action)}${a.deferred?` <span class="badge fit-too_large">${t('deferred')}</span>`:''}`)+confidenceLine(a)+table+metaLine(t('Most probable outcome: {answer}',{answer:esc(a.answer)}))+probBars(a.probabilities,q,new Set([a.answer]));
  },
  tool(a){
    const head=a.multi?chipList(a.tools||[],'no tool'):headlineDiv(esc(a.tool??t('no tool')));
    const ranking=a.ranking?`<ol class="ranking">${a.ranking.map(r=>`<li><code>${esc(r.tool)}</code> · ${pct(r.probability)}</li>`).join('')}</ol>`:'';
    return head+`<div class="chips"><span class="chip">${t('call')}: ${a.call?t('true'):t('false')}</span></div>`+metaLine(a.multi?t('set confidence {confidence}',{confidence:pct(a.set_confidence)}):`${t('Confidence')}: ${pct(a.confidence)}`)+ranking+probBars(a.probabilities,{},new Set(a.multi?a.tools||[]:[a.tool]));
  },
  classify(a,q){
    const multi=Array.isArray(a.labels);
    const head=multi?chipList(a.labels,'no label'):headlineDiv(`${esc(a.label)}${labelOf(q,a.label)?` · ${esc(labelOf(q,a.label))}`:''}`);
    return head+metaLine(multi?t('set confidence {confidence}',{confidence:pct(a.set_confidence)}):`${t('Confidence')}: ${pct(a.confidence)}`)+probBars(a.scores,q,new Set(multi?a.labels:[a.label]));
  },
  verify(a,q){
    const claims=q.claims, text=k=>Array.isArray(claims)?claims[Number(k)]:claims?.[k];
    const verdict=v=>`<span class="badge ${v==='supported'?'fit-fits':v==='contradicted'?'fit-too_large':'fit-tight'}">${esc(t(v))}</span>`;
    const rows=Object.entries(a.claims||{}).map(([k,c])=>`<tr><td><code>${esc(k)}</code> ${esc(textOf(text(k)??''))}</td><td>${verdict(c.verdict)}</td><td>${pct(c.confidence)}</td><td>${automatedChips(c.automated)}</td></tr>`).join('');
    return `<div class="table-wrap"><table class="mini-table"><thead><tr><th>${t('Claim')}</th><th>${t('Verdict')}</th><th>${t('Confidence')}</th><th>${t('Automated')}</th></tr></thead><tbody>${rows}</tbody></table></div>`+metaLine(t('All supported: {answer}',{answer:a.all_supported?t('yes'):t('no')})+((a.not_supported||[]).length?` · ${t('not supported: {items}',{items:esc(a.not_supported.join(', '))})}`:''));
  },
  compress(a){
    return `<pre class="compressed">${esc(a.compressed)}</pre>`+metaLine(t('kept {kept} of {total} {unit}s · {compressed}/{original} chars ({ratio})',{kept:(a.kept||[]).length,total:(a.units||[]).length,unit:esc(a.unit),compressed:esc(a.compressed_chars),original:esc(a.original_chars),ratio:esc(a.ratio)}));
  },
  extract(a){
    const rows=Object.entries(a.fields||{}).map(([path,f])=>`<tr><td><code>${esc(path)}</code></td><td>${esc(f.value==null?'—':textOf(f.value))}${f.error?`<span class="reason">${esc(f.error)}</span>`:''}</td><td>${pct(f.confidence)}</td><td>${esc(f.source)}</td><td>${automatedChips(f.automated)}</td></tr>`).join('');
    return `<div class="table-wrap"><table class="mini-table"><thead><tr><th>${t('Field')}</th><th>${t('Value')}</th><th>${t('Confidence')}</th><th>${t('Source')}</th><th>${t('Automated')}</th></tr></thead><tbody>${rows}</tbody></table></div><details><summary>${t('data')}</summary><pre>${esc(JSON.stringify(a.data,null,2))}</pre></details>`;
  },
};
function decisionView(name, a, q) {
  const headline=resultHeadline(name,a,q);
  return (headline?headlineDiv(esc(headline)):'')+confidenceLine(a)+probBars(a.probabilities,q);
}
// the request's state with spans marked: evidence of decisions, kept / dropped compress units, extracted fields
function sourceSpans(answers, request) {
  const spans=[];
  for(const [name,a] of Object.entries(answers||{})) {
    if(!a||typeof a!=='object')continue;
    (Array.isArray(a.evidence)?a.evidence:[]).forEach(e=>spans.push({start:e.start,end:e.end,kind:'evidence',label:name,probability:e.probability}));
    if(a.type==='compress'){const kept=new Set(a.kept||[]);(a.units||[]).forEach((u,i)=>spans.push({...u,kind:kept.has(i)?'kept':'dropped',label:`${name} · ${a.unit} ${i}`}));}
    if(a.type==='extract')Object.entries(a.fields||{}).forEach(([path,f])=>{if(Number.isInteger(f.start)&&Number.isInteger(f.end))spans.push({start:f.start,end:f.end,kind:'field',label:`${name} · ${path}`,probability:f.confidence});});
  }
  return spans.filter(s=>Number.isInteger(s.start)&&Number.isInteger(s.end)&&s.end>s.start);
}
function renderSource(state, spans) {
  const accepted=[];
  for(const s of [...spans].sort((a,b)=>(Number(b.probability)||0)-(Number(a.probability)||0)))  // overlaps: the more probable span
    if(!accepted.some(x=>s.start<x.end&&x.start<s.end))accepted.push(s);
  accepted.sort((a,b)=>a.start-b.start);
  let html='', at=0;
  for(const s of accepted) {
    const title=`${s.label}${s.probability==null?'':` · ${pct(s.probability)}`}`, text=esc(state.slice(s.start,s.end));
    html+=esc(state.slice(at,s.start))+`<mark class="${s.kind}" title="${esc(title)}">${s.kind==='dropped'?`<s>${text}</s>`:text}</mark>`; at=s.end;
  }
  html+=esc(state.slice(at));
  const kinds=[...new Set(accepted.map(s=>s.kind))];
  return `<div class="source-view"><div class="section-head"><strong>${t('Source')}</strong><span class="chips">${kinds.map(k=>`<mark class="${k}">${esc(t(k))}</mark>`).join(' ')}</span></div><pre class="source">${html}</pre></div>`;
}
// the probabilities of a choice over the boxes of its options in an image (bundled examples carry the boxes); the
// colours are fixed (app.css .image-source): the image is the background in both themes
function imageSources(answers, request, mediaSnapshot=selectedMedia) {
  return mediaSnapshot.map((item,i)=>({item,i}))
    .filter(({item,i})=>item?.kind==='image'&&validElementGeometry(item.elements)&&request.media?.[i]?.data===item.data&&answers?.[item.elements.question]?.probabilities)
    .map(({item})=>{
      const {question,boxes,size:[w,h]}=item.elements, a=answers[question], probs=a.probabilities, chosen=String(a.choice??a.answer??'');
      const badge=Math.max(20,Math.min(32,w/28));  // the painted badge's size (elements.js): labels start after it
      const rects=Object.entries(boxes).map(([k,[x,y,bw,bh]])=>{
        const p=Number(probs[k])||0, win=k===chosen;
        return `<rect class="${win?'chosen':''}" x="${x}" y="${y}" width="${bw}" height="${bh}" fill-opacity="${(0.08+0.5*p).toFixed(3)}"><title>${esc(k)} · ${pct(p)}</title></rect><text x="${x+badge+6}" y="${Math.max(16,y-4)}">${esc(k)} · ${pct(p)}</text>`;
      }).join('');
      return `<div class="source-view image-source"><div class="section-head"><strong>${t('Image · {question}',{question:esc(question)})}</strong><span class="chips"><span class="chip">${t('element probabilities')}</span></span></div><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${t('Element probabilities over {name}',{name:esc(item.file.name)})}"><image href="data:${esc(item.file.type)};base64,${esc(item.data)}" width="${w}" height="${h}"/>${rects}</svg></div>`;  // not the blob URL: it is revoked when the media change
    }).join('');
}
// thinking (basal/think.py): readout 2 after the reasoning, with the answer it replaced
function thinkBlock(a) {
  const think=a.think, used=a.meta?.think;
  if(think?.skipped)return `<div class="chips"><span class="chip muted">${t('thinking skipped ({reason})',{reason:esc(think.skipped)})}</span></div>`;
  if(!think||think.tokens==null)return used?`<div class="chips"><span class="chip muted">${t('no reasoning')}</span></div>`:'';
  return `<div class="think-block"><span class="chip">${t('reasoned')}</span> ${esc(think.tokens)} ${t('tokens')} · ${num(think.ms,0)} ms · ${t('calibration')} ${esc(t(think.calibration??'—'))}; ${t('without thinking')}: ${pct(think.no_think?.confidence)}${think.text?`<details><summary>${t('Reasoning')}</summary><pre>${esc(think.text)}</pre></details>`:''}</div>`;
}
function renderResults(data, request, mediaSnapshot=selectedMedia) {
  const answers=data?.answers || data || {}, entry=modelEntry(request.model); let html='';
  for(const [name,answer] of Object.entries(answers)) {
    const q=request.questions?.[name]||{};
    if(!answer||typeof answer!=='object'){html+=`<article class="answer"><h3>${esc(name)}</h3><pre>${esc(JSON.stringify(answer,null,2))}</pre></article>`;continue;}
    const type=answer.type||q.type||'unknown', view=RESULT_VIEWS[type];
    const body=view?view(answer,q):answer.probabilities&&typeof answer.probabilities==='object'?decisionView(name,answer,q):`<pre>${esc(JSON.stringify(answer,null,2))}</pre>`;
    html+=`<article class="answer"><h3><span>${esc(name)}</span><span class="chip">${esc(type)}</span>${answer.experimental?`<span class="badge experimental">${t('experimental')}</span>`:''}</h3>${safetyStrip(answer,q,entry,request.model)}${thinkBlock(answer)}${body}</article>`;
  }
  const spans=typeof request.state==='string'?sourceSpans(answers,request):[];
  const u=data?.usage, mediaUsage=u?.media;
  const mediaSummary=mediaUsage?` · ${t('Media')}: ${mediaUsage.images??0} ${t('images')}, ${mediaUsage.audio??0} ${t('audio')}, ${mediaUsage.media_tokens??0} ${t('tokens')}`:'';
  const thinkUsage=u?.think_questions!=null?` · ${t('Thought on')}: ${u.think_questions} · ${t('Output tokens')}: ${u.output_tokens??0}`:'';
  const extraUsage=[['branches',u?.branches],['second_order_branches',u?.second_order_branches],['lane',u?.lane]].filter(([,v])=>v!=null).map(([k,v])=>`${k}: ${esc(v)}`).join(' · ');
  const usage=u?`<div class="answer-meta">${t('Latency')}: ${u.latency_ms??'—'} ms · ${t('Input tokens')}: ${u.input_tokens??'—'} · ${t('Questions')}: ${u.questions??'—'}${extraUsage?` · ${extraUsage}`:''}${mediaSummary}${thinkUsage}</div>`:'';
  const policy=contextPolicy(request.model);
  const meter=u?`<div class="answer-meta">${u.longest_prompt_tokens!=null?`${t('Longest prompt')} ${esc(u.longest_prompt_tokens)} / ${esc(policy.limit??'—')} ${t('tokens')} · `:''}${esc(policy.text)}</div>`:'';
  $('#results').innerHTML=`<div class="section-head"><strong>${esc(request.model||'')}</strong>${html&&!request.media?`<button type="button" id="inspectDecision">${t('Inspect this decision')}</button>`:''}</div>`+resultBanners(entry,u)+(html ? usage+meter+html+(spans.length?renderSource(request.state,spans):'')+imageSources(answers,request,mediaSnapshot) : `<div class="empty">${t('No answers returned.')}</div>`);
  const inspectButton=$('#inspectDecision'); if(inspectButton)inspectButton.onclick=()=>inspectDecision(request);
  $('#rawResponse').textContent=JSON.stringify(data,null,2); $('#rawDetails').hidden=false;
}
// the active context policy of a loaded model: basal-serve's entry.context when it reports one, else the load options
// (context_overflow, max_len, truncate_head; the defaults of basal_ui/options.py)
function contextPolicy(id) {
  const m=(statusCache?.models||[]).find(x=>x.id===id), o=m?.options||{}, c=m?.entry?.context||{};
  const overflow=c.overflow||o.context_overflow||'truncate', limit=c.limit??o.max_len??null;
  const head=Number(o.truncate_head??1/3), share=Math.abs(head-1/3)<1e-6?'1/3':`${Math.round(head*100)}%`;
  const over=overflow==='refuse'?t('a longer prompt is refused'):t('a longer state loses its middle (keeps {share} from the start)',{share});
  return {overflow,limit,text:limit!=null?t('Context {limit} tokens: {policy}',{limit,policy:over}):t('Context: the model\'s own; {policy}',{policy:over})};
}
function renderContextPolicy() {
  const box=$('#contextPolicy'); if(!box)return;
  box.textContent=selectedModel&&readyModels().some(m=>m.id===selectedModel)?contextPolicy(selectedModel).text:'';
}
// the stacked layout (<= 1100 px) puts the answer below the long request form: bring the outcome into view
function revealOutcome(el, always=false) {
  if(el&&(always||matchMedia('(max-width: 1100px)').matches))el.scrollIntoView({block:el.id==='decideError'?'nearest':'start'});
}
function showDecideError(message) { $('#decideError').textContent=message; $('#decideError').hidden=false; revealOutcome($('#decideError')); }
// Send and Compare stay focusable while a request runs (aria-disabled, not disabled), so keyboard focus stays put
let decisionBusy=false;
function setDecisionBusy(busy) {
  decisionBusy=busy;
  $('#sendButton').setAttribute('aria-disabled',String(busy));
  $('#compareButton').setAttribute('aria-disabled',String(busy||!$('#compareHint').hidden));  // or no second model
  $('#results').setAttribute('aria-busy',String(busy));
}
// the request in the form, checked as Send checks it; null after showing the problem
function validatedRequest() {
  $('#decideError').hidden=true;
  let request;const jsonMode=$('#editJsonToggle').checked;
  try { request=jsonMode?JSON.parse($('#requestJson').value):buildRequest(); }
  catch(e) { showDecideError(jsonMode?t('Invalid JSON: {message}',{message:e.message}):e.message);if(!jsonMode)$('.q-name:invalid')?.reportValidity();return null; }
  if(!request||typeof request!=='object'||Array.isArray(request)) { showDecideError(t('The request must be a JSON object.')); return null; }
  if(!jsonMode){
    const invalid=[...document.querySelectorAll('.question')].filter(card=>card._error);
    if(invalid.length){showDecideError(invalid.map(card=>`${$('.q-name',card).value||t('question')}: ${card._error}`).join('\n'));return null;}
  }
  request.model=selectedModel;
  const unsupported=[...new Set((Array.isArray(request.media)?request.media:[]).map(m=>m?.type))].filter(k=>['image','audio'].includes(k)&&!loadedMedia.includes(k));
  if(unsupported.length){showDecideError(t('{model} does not take {media}. Remove the media, or load a basal-omni model with "Load with media" and select it.',{model:shortName(selectedModel||t('This model')),media:mediaTitle(unsupported)}));return null;}
  const withEvidence=Array.isArray(request.media)&&request.media.length?Object.entries(request.questions||{}).filter(([,q])=>q?.evidence).map(([name])=>name):[];
  if(withEvidence.length){showDecideError(t('Evidence spans are read from the state\'s text, so a request with media cannot have them: turn off "Evidence spans" on {questions}, or remove the media.',{questions:withEvidence.join(', ')}));return null;}
  return request;
}
async function sendDecision() {
  if(decisionBusy)return;
  const request=validatedRequest(); if(!request)return;
  $('#results').innerHTML=''; $('#playgroundForm').classList.remove('comparing');
  if($('#editJsonToggle').checked)$('#requestJson').value=JSON.stringify(request,null,2);
  setDecisionBusy(true);
  const mediaSnapshot=(Array.isArray(request.media)?request.media:[]).map(media=>{const item=selectedMedia.find(current=>current.data===media?.data);return item?{kind:item.kind,data:item.data,file:item.file,elements:item.elements}:null;});
  try {
    const response=await api('/api/decide',{method:'POST',body:JSON.stringify(request)}); renderResults(response,request,mediaSnapshot);
    store('basal-ui.tried','1'); revealOutcome($('.response-panel'));  // the first-run step: a Decision was read
  }
  catch(e) { showDecideError(e.message); toast(e.message,'error'); }
  finally { setDecisionBusy(false); }
}
$('#addQuestion').addEventListener('click',()=>addQuestion());
$('#stateInput').addEventListener('input',()=>{$('#stateInput')._requestState=undefined;syncRequest();});
$('#editJsonToggle').addEventListener('change',()=>{
  if($('#editJsonToggle').checked){syncRequest(true);}
  else{
    let request;
    try{request=JSON.parse($('#requestJson').value);}
    catch(e){showMediaError(t('Invalid request JSON: {message}',{message:e.message}));$('#editJsonToggle').checked=true;return;}
    if(!request||typeof request!=='object'||Array.isArray(request)){showMediaError(t('request JSON must be an object'));$('#editJsonToggle').checked=true;return;}
    showMediaError();
    const meta=elementPresetData(),media=Array.isArray(request.media)?request.media:[];
    const names=media.map(item=>selectedMedia.find(current=>current.data===item?.data)?.file.name);
    const marks=meta.marked.flatMap(record=>{const index=media.findIndex(item=>item?.data===record.markedData);return index<0?[]:[{...record,index}];});
    if(!setRequest(request,names))restorePresetMediaFallback(request,names);
    restoreElementMarks(marks);
  }
  $('#editJsonArea').hidden=!$('#editJsonToggle').checked;$('#builder').hidden=$('#editJsonToggle').checked;
});
$('#mediaInput').addEventListener('change',event=>selectMedia(Array.from(event.target.files||[])));
$('#mediaDetail').addEventListener('change',()=>syncRequest());
$('#factsToggle').addEventListener('change',()=>syncRequest(false,REQUEST_OPTION_KEYS));
$('#langSelect').addEventListener('change',()=>syncRequest(false,REQUEST_OPTION_KEYS));
['#thinkSelect','#thinkTextToggle','#earlyExitSelect'].forEach(s=>$(s).addEventListener('change',()=>syncRequest(false,REQUEST_OPTION_KEYS)));
$('#mediaLightboxClose').addEventListener('click',()=>$('#mediaLightbox').close());
$('#mediaLightbox').addEventListener('click',e=>{if(e.target===$('#mediaLightbox'))$('#mediaLightbox').close();});
window.addEventListener('beforeunload',()=>selectedMedia.forEach(item=>URL.revokeObjectURL(item.url)));
$('#sendButton').addEventListener('click',sendDecision);
$('#copyRaw').addEventListener('click',()=>copyText($('#rawResponse').textContent,t('Response JSON copied')));
