// Shared helpers every view uses: DOM lookup, escaping, the API client, toasts, dialogs, theme, shortcuts.
'use strict';
const $ = (s, root=document) => root.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// html`<b>${value}</b>` escapes every interpolated value; raw(markup) or an array of html`` results is kept as is
class SafeHtml { constructor(text) { this.text = text; } toString() { return this.text; } }
const raw = text => new SafeHtml(String(text ?? ''));
const htmlValue = v => v instanceof SafeHtml ? v.text : Array.isArray(v) ? v.map(htmlValue).join('') : esc(v);
const html = (strings, ...values) => raw(strings.reduce((out, s, i) => out + htmlValue(values[i - 1]) + s));
const gb = n => n == null ? '—' : `${(Number(n) / 1e9).toFixed(1)} GB`;
const pct=v=>v==null?'—':`${(Number(v)*100).toFixed(1)}%`;
const num=(v,digits=1)=>v==null?'—':Number(v).toFixed(digits);
const shortName=id=>String(id).split('/').pop();
const clock=ts=>new Date(ts*1000).toLocaleTimeString([], {hour12:false});
// browser storage that is blocked (some private or embedded contexts) or full reads as unset and drops the write:
// it never throws into the code that renders
const stored=key=>{try{return localStorage.getItem(key);}catch{return null;}};
const store=(key,value)=>{try{localStorage.setItem(key,value);}catch{}};
async function act(fn){try{await fn();}catch(e){toast(e.message,'error');}}
// a trigger whose dialog waits for a fetch: busy (and announced) until it opens or fails
async function busyWhile(button, fn){
  button.setAttribute('aria-busy','true'); announce(t('Loading…'));
  try{await fn();}finally{button.removeAttribute('aria-busy');}
}
let modelData = [], hw = null, examples = [], pollTimer = null, loadedMedia = [], selectedMedia = [], mediaSelection = 0, statusSequence = 0;
function httpFailure(status,message) {
  const hint={422:t('Invalid request: revise the input.'),503:t('Service unavailable: if GPU memory is exhausted, shorten the input or unload another model before retrying manually.'),500:t('Internal server error: inspect model logs and request ID.')}[status];
  return `${status?`HTTP ${status}. `:''}${hint?hint+' ':''}${message}`;
}
async function api(path, options={}) {
  const response = await fetch(path, {headers:{'Content-Type':'application/json',...(options.headers||{})}, ...options});
  const text = await response.text(); let data;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!response.ok) {  // basal-ui: {"detail": msg}; basal-serve 422: {"error": msg, "detail": [{"msg"}]}
    const detail = typeof data?.detail === 'string' ? data.detail : typeof data?.error === 'string' ? data.error : Array.isArray(data?.detail) ? data.detail.map(d => d?.msg).filter(Boolean).join('; ') : null;
    const message=detail||data?.error?.message||(typeof data==='string'?data:JSON.stringify(data,null,2))||response.statusText;
    const id=response.headers.get('x-request-id');
    // status, and a basal-ui route's message code and field ({"detail", "code", "field"}): what the UI maps on
    throw Object.assign(new Error(`${httpFailure(response.status,message)}${id?` [request ${id}]`:''}`),
      {status:response.status,code:typeof data?.code==='string'?data.code:null,field:typeof data?.field==='string'?data.field:null});
  }
  return data;
}
// a polite screen-reader announcement (#srStatus); messages closer than 1.5 s apart wait their turn
let announceQueue=[], announceTimer=null, announcedAt=0;
function announce(message) {
  if(!message||announceQueue.includes(message))return;
  announceQueue.push(message);
  const next=()=>{
    announceTimer=null; const text=announceQueue.shift(); if(text==null)return;
    const region=$('#srStatus'); region.textContent=''; requestAnimationFrame(()=>{region.textContent=text;});
    announcedAt=Date.now(); if(announceQueue.length)announceTimer=setTimeout(next,1500);
  };
  if(!announceTimer)announceTimer=setTimeout(next,Math.max(0,announcedAt+1500-Date.now()));
}
// errors are alerts that stay until dismissed; other toasts leave after 6 s, but not while hovered or focused.
// An error equal to one still shown (the same text, its request ID aside) is not stacked again: that toast counts the
// repeat (×n, not announced again). key: one toast per key, a new message replaces the old one's text in place, and
// dismissToast(key) removes it (the status poll's error, once the server answers again). At most TOAST_ERRORS errors
// stay; the oldest gives way.
const TOAST_ERRORS=3, toastIdentity=text=>String(text??'').replace(/\s*\[request [^\]]*\]$/,'');
let toastReturn=null;  // the control that had the focus before Tab reached the toasts
$('#toastStack').addEventListener('focusin',e=>{if(e.relatedTarget&&!$('#toastStack').contains(e.relatedTarget))toastReturn=e.relatedTarget;});
// a toast removed under keyboard focus hands it to the next toast's (or the previous one's) close button, else back to
// where it came from while that is still shown, else to the view's title
function removeToast(node) {
  if(!node?.isConnected)return;
  const hadFocus=node.contains(document.activeElement), toasts=[...$('#toastStack').children], i=toasts.indexOf(node);
  const neighbour=toasts[i+1]||toasts[i-1];
  node.remove();
  if(!hadFocus)return;
  const back=toastReturn?.isConnected&&toastReturn.getClientRects().length?toastReturn:null;
  ((neighbour&&$('.toast-close',neighbour))||back||$('#pageTitle')).focus();
}
const dismissToast=key=>removeToast([...$('#toastStack').children].find(n=>n.dataset.key===key));
function toast(message, type='success', {key=''}={}) {
  const stack=$('#toastStack'), error=type==='error', identity=toastIdentity(message);
  const same=[...stack.children].find(n=>(key&&n.dataset.key===key)||(error&&n.classList.contains('error')&&n.dataset.identity===identity));
  if(same&&same.className===`toast ${type}`){
    if(key)same.dataset.key=key;
    if(same.dataset.identity===identity){  // a repeat: counted, not announced again
      const count=$('.toast-count',same); same.dataset.count=String(Number(same.dataset.count||1)+1);
      count.textContent=`×${same.dataset.count}`; count.title=t('Last at {time}',{time:new Date().toLocaleTimeString([],{hour12:false})}); count.hidden=false;
    }else{  // the same subject, new text: replaced in place (an alert announces the change)
      same.dataset.identity=identity; same.dataset.count='1'; $('.toast-count',same).hidden=true; $('.toast-text',same).textContent=message;
      if(!error)announce(message);
    }
    return same;
  }
  if(same)removeToast(same);  // a key that changed type (an info that became an error)
  const node=document.createElement('div'); node.className=`toast ${type}`; node.dataset.identity=identity;
  if(key)node.dataset.key=key;
  if(error)node.setAttribute('role','alert'); else announce(message);
  node.innerHTML=html`<span class="toast-text">${message}</span><span class="toast-count" aria-hidden="true" hidden></span><button type="button" class="toast-close" aria-label="${t('Close')}">×</button>`.text;
  $('.toast-close',node).onclick=()=>removeToast(node);
  stack.append(node);
  if(error){
    const errors=[...stack.querySelectorAll('.toast.error')];
    errors.slice(0,Math.max(0,errors.length-TOAST_ERRORS)).forEach(removeToast);
    return node;
  }
  let timer=null, held=0;
  const start=()=>{clearTimeout(timer);if(!held)timer=setTimeout(()=>removeToast(node),6000);};
  const hold=()=>{held++;clearTimeout(timer);}, release=()=>{held=Math.max(0,held-1);start();};
  node.addEventListener('mouseenter',hold); node.addEventListener('mouseleave',release);
  node.addEventListener('focusin',hold); node.addEventListener('focusout',release);
  start();
  return node;
}
// role=progressbar markup: share of done/total, or indeterminate without a total; label names it, text says it
function progressBar(done, total, label, text='', cls='progress') {
  const share=total?Math.max(0,Math.min(100,done/total*100)):null;
  const value=share==null?'':` aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(share)}"`;
  return html`<div class="${cls}" role="progressbar" aria-label="${label}"${raw(value)} aria-valuetext="${text||(share==null?t('in progress'):`${Math.round(share)}%`)}"><span style="width:${share??0}%"></span></div>`.text;
}
// role=tablist: roving tabindex; arrows, Home and End move the focus, Enter or Space (a click) selects the tab
function tablist(list) {
  const tabs=()=>[...list.querySelectorAll('[role=tab]')];
  const sync=()=>{const all=tabs(),selected=all.find(x=>x.getAttribute('aria-selected')==='true')||all[0];all.forEach(x=>{x.tabIndex=x===selected?0:-1;});};
  list.addEventListener('keydown',e=>{
    const all=tabs(), i=all.indexOf(e.target.closest('[role=tab]'));
    const next={ArrowRight:i+1,ArrowLeft:i-1,Home:0,End:all.length-1}[e.key];
    if(i<0||next===undefined)return;
    e.preventDefault(); const tab=all[(next+all.length)%all.length];
    all.forEach(x=>{x.tabIndex=x===tab?0:-1;}); tab.focus();
  });
  list.addEventListener('focusout',e=>{if(!list.contains(e.relatedTarget))sync();});
  new MutationObserver(sync).observe(list,{subtree:true,attributes:true,attributeFilter:['aria-selected']});
  sync();
}
function confirmDialog({title,description,confirmLabel=t('Confirm'),destructive=false}) {
  return new Promise(resolve=>{
    const dialog=$('#confirmDialog'), accept=$('#confirmAccept'), cancel=$('#confirmCancel');
    $('#confirmTitle').textContent=title; $('#confirmDescription').textContent=description;
    accept.textContent=confirmLabel; accept.classList.toggle('destructive',destructive);
    dialog.setAttribute('role',destructive?'alertdialog':'dialog');
    dialog.showModal();
    const finish=value=>{dialog.close();accept.removeEventListener('click',yes);cancel.removeEventListener('click',no);dialog.removeEventListener('close',closed);resolve(value);};
    const yes=()=>finish(true),no=()=>finish(false),closed=()=>finish(false);
    accept.addEventListener('click',yes);cancel.addEventListener('click',no);dialog.addEventListener('close',closed);
  });
}
function applyTheme(theme) {
  const value=['light','dark','system'].includes(theme)?theme:'system';
  document.documentElement.dataset.theme=value==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):value;
  document.documentElement.classList.toggle('dark',document.documentElement.dataset.theme==='dark');
  store('basal-ui.theme',value);
  document.querySelectorAll('[data-theme-choice]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.themeChoice===value)));
  const toggle=$('#themeToggle'), label=document.documentElement.dataset.theme==='dark'?t('Switch to light theme'):t('Switch to dark theme');
  if(toggle){toggle.setAttribute('aria-label',label);toggle.title=label;}
}
// the sidebar's theme button flips between light and dark; Settings also offers System
const toggleTheme=()=>applyTheme(document.documentElement.dataset.theme==='dark'?'light':'dark');
document.querySelectorAll('[data-theme-choice]').forEach(b=>b.onclick=()=>applyTheme(b.dataset.themeChoice));
$('#themeToggle').onclick=toggleTheme;
applyTheme(stored('basal-ui.theme')||'system');
matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>{if((stored('basal-ui.theme')||'system')==='system')applyTheme('system');});
document.addEventListener('click',e=>{const b=e.target.closest('[data-close]');if(b)$('#'+b.dataset.close).close();});
// "?" is a single-character shortcut (WCAG 2.1.4): Settings turns it off (remembered in this browser)
const singleKeyShortcuts=()=>stored('basal-ui.single-keys')!=='off';
const openShortcuts=()=>{const d=$('#shortcutsDialog');if(!d.open)d.showModal();};
// a modal <dialog> (the palette, a confirm, an options dialog) owns the keyboard: no page shortcut fires under it, so
// Enter in the palette does not also send the Playground request and Alt+digit does not switch the view behind a confirm
document.addEventListener('keydown',e=>{
  if(document.querySelector('dialog[open]'))return;
  const typing=e.target instanceof Element&&e.target.matches('input,textarea,select,[contenteditable],[contenteditable] *');
  if((e.ctrlKey||e.metaKey)&&e.key==='Enter'&&!$('#view-playground').hidden){e.preventDefault();$('#sendButton').click();}
  else if(!typing&&e.key==='?'&&!e.ctrlKey&&!e.metaKey&&!e.altKey&&singleKeyShortcuts()){e.preventDefault();openShortcuts();}
  else if(!typing&&e.altKey&&!e.ctrlKey&&!e.metaKey&&/^Digit[0-9]$/.test(e.code)){const target=document.querySelector(`.side-nav [data-shortcut="${e.code.slice(5)}"]`);if(target){e.preventDefault();showView(target.dataset.view,true);}}
});
document.addEventListener('change',event=>{
  if(event.target.matches('input[type=checkbox]:not(.check)'))event.target.setAttribute('aria-checked',String(event.target.checked));
});
document.addEventListener('keydown',event=>{
  if(event.key==='Enter'&&event.target.matches('input[type=checkbox][role=switch]')) {
    event.preventDefault(); setChecked(event.target,!event.target.checked);
    event.target.dispatchEvent(new Event('change',{bubbles:true}));
  }
});
// Alt+1–9 and Alt+0: the first ten views in menu order; Settings is in the command palette (Ctrl/⌘+K)
document.querySelectorAll('.side-nav [data-view]:not([data-view=settings])').forEach((b,i)=>{b.dataset.shortcut=String((i+1)%10);b.setAttribute('aria-keyshortcuts',`Alt+${(i+1)%10}`);});
// a focused button disabled while its work runs drops keyboard focus to <body>; the returned function gives it back
function holdFocus(button) {
  const had=document.activeElement===button;
  return ()=>{if(had&&button.isConnected&&(!document.activeElement||document.activeElement===document.body))button.focus();};
}
// a list re-rendered under keyboard focus: keepFocus(container) before, the returned function after the new markup gives
// the focus to the same control (tag, class, data-*, label), else the row's Details / information button, else another
// control of the same model (data-id), else the first control of the row (the container's child) at the same place, or
// that row itself (a step whose button became a chip); a re-render that moved the focus elsewhere is left alone
const FOCUSABLE='button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),[tabindex="0"]';
function keepFocus(container) {
  const active=document.activeElement;
  if(!container||!active||!container.contains(active))return ()=>{};
  const key=el=>[el.tagName,el.className,JSON.stringify(el.dataset),el.getAttribute('aria-label')||el.textContent.trim()].join('|');
  const was=key(active), id=active.dataset.id||active.dataset.restart||active.dataset.unload||active.closest('[data-id]')?.dataset.id;
  const place=[...container.children].findIndex(child=>child.contains(active));
  return ()=>{
    if(document.activeElement&&document.activeElement!==document.body)return;
    const all=[...container.querySelectorAll(FOCUSABLE)], byId=id?all.filter(el=>[el.dataset.id,el.dataset.restart,el.dataset.unload].includes(id)):[];
    const same=all.find(el=>key(el)===was)||byId.find(el=>el.classList.contains('model-card-open'))||byId[0];
    if(same)return same.focus();
    const row=container.children[place];
    if(!row)return;
    const control=row.matches(FOCUSABLE)?row:row.querySelector(FOCUSABLE);
    if(control)return control.focus();
    row.tabIndex=-1; row.focus();
  };
}
// container.innerHTML=markup unless it is unchanged (a poll that changed nothing keeps every node and the focus)
function renderKeepingFocus(container, markup) {
  if(container._markup===markup)return false;
  const restore=keepFocus(container); container._markup=markup;
  container.innerHTML=markup; restore(); return true;
}
function setChecked(input, value) {
  input.checked=Boolean(value);
  input.setAttribute('aria-checked',String(input.checked));
}
document.querySelectorAll('input[type=checkbox]').forEach(input=>{input.setAttribute('role','switch');setChecked(input,input.checked);});
document.querySelectorAll('.icon-button').forEach(button=>{
  const icon=button.id==='logsToggle'
    ?'<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 4h12v12H4zM7 8h6M7 11h6M7 14h3"/></svg>'
    :'<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="7" y="7" width="10" height="11" rx="1"/><path d="M4 13V3h10"/></svg>';
  button.className='outline-action button-icon icon-button';
  button.innerHTML=icon+`<span>${esc(button.textContent.trim())}</span>`;
});
async function copyText(text, success=t('Copied to clipboard')) {
  try { await navigator.clipboard.writeText(text); }
  catch {
    const area=document.createElement('textarea'); area.value=text; area.style.position='fixed'; area.style.opacity='0';
    document.body.append(area);
    let copied=false;
    try { area.select(); copied=document.execCommand('copy'); }
    catch {}
    finally { area.remove(); }
    if(!copied) { toast(t('Copy failed — select the text and copy manually'),'error'); return; }
  }
  toast(success);
}
