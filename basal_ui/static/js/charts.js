// Charts as inline SVG, no library: meter, bars, histogram, sparkline, reliability diagram and threshold strip.
// Colours come from the --chart-* tokens (colour-blind safe, both themes). Every chart has an accessible name
// (role=img / role=meter with aria-label) and the numbers it draws in a visually hidden table.
'use strict';
const CHART_SERIES=['var(--chart-1)','var(--chart-2)','var(--chart-3)','var(--chart-4)'];
const chartColor=i=>CHART_SERIES[i%CHART_SERIES.length];
const finite=v=>v!=null&&Number.isFinite(Number(v));
// the hidden table (in a clipped box: a table ignores the 1px width of .sr-only): caption, header cells and rows of already formatted cells
function chartTable(caption, head, rows) {
  return html`<div class="sr-only"><table><caption>${caption}</caption><thead><tr>${head.map(h=>html`<th scope="col">${h}</th>`)}</tr></thead><tbody>${rows.map(r=>html`<tr>${r.map(c=>html`<td>${c}</td>`)}</tr>`)}</tbody></table></div>`;
}
// meter: segments [{value, label}] stacked on a 0..max track, marks [{at, label}]; tone '' | 'warning' | 'danger'
function chartMeter({label, max, segments=[], marks=[], tone='', valueText='', format=v=>v}) {
  const total=segments.reduce((sum,s)=>sum+(Number(s.value)||0),0), share=v=>Math.max(0,Math.min(100,(Number(v)||0)/(max||1)*100));
  const fills=segments.map((s,i)=>html`<span class="viz-meter-fill" style="width:${share(s.value).toFixed(2)}%;background:${raw(segments.length>1?chartColor(i):tone?`var(--${tone})`:'var(--accent)')}" title="${s.label} · ${format(s.value)}"></span>`);
  const ticks=marks.map(m=>html`<span class="viz-meter-mark" style="left:${share(m.at).toFixed(2)}%" title="${m.label}"></span>`);
  const legend=segments.length>1?html`<ul class="viz-legend" aria-hidden="true">${segments.map((s,i)=>html`<li><span class="viz-swatch" style="background:${raw(chartColor(i))}"></span>${s.label} · ${format(s.value)}</li>`)}</ul>`:'';
  return html`<div class="viz viz-meter${tone?` ${tone}`:''}"><div class="viz-meter-track" role="meter" aria-label="${label}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(share(total))}" aria-valuetext="${valueText||`${format(total)} / ${format(max)}`}">${fills}${ticks}</div>${legend}${segments.length>1||marks.length?chartTable(label,[t('Part'),t('Value')],[...segments.map(s=>[s.label,format(s.value)]),...marks.map(m=>[m.label,format(m.at)])]):''}</div>`.text;
}
// horizontal bars: items [{label, value, note?}] on 0..max (default: the largest value); series colour by index
function chartBars({label, items, max=null, format=v=>v, series=0}) {
  const top=max??Math.max(0,...items.map(x=>Number(x.value)||0)), width=v=>top?Math.max(0,Math.min(100,(Number(v)||0)/top*100)):0;
  return html`<figure class="viz viz-bars" role="img" aria-label="${label}"><div aria-hidden="true">${items.map(x=>html`<div class="viz-bar-row"><span class="viz-bar-label" title="${x.label}">${x.label}</span><svg class="viz-bar-track" viewBox="0 0 100 10" preserveAspectRatio="none" focusable="false"><rect x="0" y="0" width="100" height="10" class="viz-track"/>${finite(x.value)?raw(`<rect x="0" y="0" width="${width(x.value).toFixed(2)}" height="10" fill="${chartColor(x.series??series)}"/>`):''}</svg><span class="viz-bar-value">${finite(x.value)?format(x.value):'—'}${x.note?html` <small>${x.note}</small>`:''}</span></div>`)}</div></figure>${chartTable(label,[t('Item'),t('Value')],items.map(x=>[x.label,finite(x.value)?format(x.value):'—']))}`.text;
}
// histogram of values in `bins` equal-width bins between their min and max; the caption names the range
function chartHistogram({label, values, bins=12, format=v=>v}) {
  const xs=values.filter(finite).map(Number); if(!xs.length)return '';
  const lo=Math.min(...xs), hi=Math.max(...xs);
  if(hi===lo)bins=1;  // every value equal: one bin [lo, hi] holds them all (no made-up range)
  const step=(hi-lo)/bins, counts=Array(bins).fill(0);
  xs.forEach(v=>{counts[step?Math.min(bins-1,Math.floor((v-lo)/step)):0]++;});
  const top=Math.max(...counts), w=100/bins;
  const bars=counts.map((n,i)=>`<rect x="${(i*w+w*0.1).toFixed(2)}" y="${(40-n/top*38).toFixed(2)}" width="${(w*0.8).toFixed(2)}" height="${(n/top*38).toFixed(2)}" fill="var(--chart-1)"/>`).join('');
  const edge=i=>format(i===bins?hi:lo+i*step);  // the last edge is the maximum itself, not lo + bins × step
  return html`<figure class="viz viz-histogram" role="img" aria-label="${label}"><svg viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true" focusable="false"><line x1="0" y1="40" x2="100" y2="40" class="viz-axis"/>${raw(bars)}</svg><figcaption class="viz-axis-labels" aria-hidden="true"><span>${edge(0)}</span><span>${tp(xs.length,'{n} sample','{n} samples')}</span><span>${format(hi)}</span></figcaption></figure>${chartTable(label,[t('From'),t('To'),t('Count')],counts.map((n,i)=>[edge(i),edge(i+1),n]))}`.text;
}
// sparkline: points [{x: label, y}] in order; the last value is printed beside the line
function chartSparkline({label, points, format=v=>v, series=0}) {
  const ys=points.filter(p=>finite(p.y)); if(ys.length<2)return '';
  const lo=Math.min(...ys.map(p=>Number(p.y))), hi=Math.max(...ys.map(p=>Number(p.y))), span=hi-lo||1;
  const path=ys.map((p,i)=>`${(i/(ys.length-1)*100).toFixed(2)},${(22-(Number(p.y)-lo)/span*20).toFixed(2)}`).join(' ');
  return html`<figure class="viz viz-sparkline" role="img" aria-label="${label}"><svg viewBox="0 0 100 24" preserveAspectRatio="none" aria-hidden="true" focusable="false"><polyline points="${path}" fill="none" stroke="${raw(chartColor(series))}" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg><span class="viz-spark-value" aria-hidden="true">${format(ys.at(-1).y)}</span></figure>${chartTable(label,[t('Point'),t('Value')],ys.map(p=>[p.x,format(p.y)]))}`.text;
}
// reliability diagram: [confidence, correct] pairs in `bins` confidence bins; the diagonal is perfect calibration
function reliabilityBins(pairs, bins=10) {
  const out=Array.from({length:bins},(_,i)=>({lo:i/bins,hi:(i+1)/bins,n:0,conf:0,ok:0}));
  pairs.forEach(([c,ok])=>{const b=out[Math.min(bins-1,Math.floor(Math.max(0,Math.min(1,c))*bins))];b.n++;b.conf+=c;b.ok+=ok?1:0;});
  return out.filter(b=>b.n).map(b=>({...b,confidence:b.conf/b.n,accuracy:b.ok/b.n}));
}
function chartReliability({label, pairs, bins=10}) {
  const data=reliabilityBins(pairs,bins); if(!data.length)return '';
  const n=pairs.length, ece=data.reduce((sum,b)=>sum+b.n/n*Math.abs(b.accuracy-b.confidence),0);
  const xy=(c,a)=>[(8+c*88).toFixed(2),(92-a*88).toFixed(2)];
  const gaps=data.map(b=>{const [x,y]=xy(b.confidence,b.accuracy),[,yd]=xy(b.confidence,b.confidence);return `<line x1="${x}" y1="${yd}" x2="${x}" y2="${y}" class="viz-gap"/>`;}).join('');
  const dots=data.map(b=>{const [x,y]=xy(b.confidence,b.accuracy);return `<circle cx="${x}" cy="${y}" r="${(1.6+Math.sqrt(b.n/n)*3).toFixed(2)}" fill="var(--chart-1)"/>`;}).join('');
  const grid=[0,0.5,1].map(v=>{const [x]=xy(v,0),[,y]=xy(0,v);return `<line x1="8" y1="${y}" x2="96" y2="${y}" class="viz-grid"/><line x1="${x}" y1="4" x2="${x}" y2="92" class="viz-grid"/><text x="${x}" y="99" text-anchor="middle">${v*100}%</text><text x="6" y="${(Number(y)+1.5).toFixed(2)}" text-anchor="end">${v*100}%</text>`;}).join('');
  return html`<figure class="viz viz-reliability" role="img" aria-label="${label}"><svg viewBox="-6 0 104 102" aria-hidden="true" focusable="false">${raw(grid)}<line x1="8" y1="92" x2="96" y2="4" class="viz-diagonal"/>${raw(gaps+dots)}</svg><figcaption class="muted">${t('Confidence (across) against the share correct (up); the diagonal is perfect calibration. ECE {ece} over {n} answers.',{ece:pct(ece),n})}</figcaption></figure>${chartTable(label,[t('Confidence bin'),t('Answers'),t('Mean confidence'),t('Share correct')],data.map(b=>[`${pct(b.lo)}–${pct(b.hi)}`,b.n,pct(b.confidence),pct(b.accuracy)]))}`.text;
}
// threshold strip: per row a 0..1 confidence axis with the certified 1% (solid) and 5% (hollow) error thresholds
function chartThresholds({label, rows}) {
  if(!rows.length)return '';
  const pos=v=>(Math.max(0,Math.min(1,Number(v)))*100).toFixed(2);
  const mark=(v,cls,name)=>finite(v)?html`<span class="viz-threshold ${cls}" style="left:${pos(v)}%" title="${name} ${num(v,3)}"></span>`:'';
  return html`<figure class="viz viz-thresholds" role="img" aria-label="${label}"><div aria-hidden="true">${rows.map(r=>html`<div class="viz-bar-row"><span class="viz-bar-label">${r.label}</span><span class="viz-threshold-track">${mark(r.five,'five',t('5% error'))}${mark(r.one,'one',t('1% error'))}</span><span class="viz-bar-value">${finite(r.one)?num(r.one,3):'—'} · ${finite(r.five)?num(r.five,3):'—'}</span></div>`)}<div class="viz-legend"><span><span class="viz-threshold one"></span>${t('1% error')}</span><span><span class="viz-threshold five"></span>${t('5% error')}</span><span>${t('confidence 0 → 1')}</span></div></div></figure>${chartTable(label,[t('Question type'),t('1% error'),t('5% error')],rows.map(r=>[r.label,finite(r.one)?num(r.one,3):'—',finite(r.five)?num(r.five,3):'—']))}`.text;
}
