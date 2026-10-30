// API server view: exposure, keys and the connect snippets.
'use strict';
let serveData=null, serveHost='127.0.0.1', statusCache=null, connectTab='curl';
function connectionEndpoint() {
  const settings=serveData.settings, serving=statusCache?.serving;
  if (serving?.url) return serving.url;
  const host=settings.host==='0.0.0.0'?(serveData.lan_ip||location.hostname):'127.0.0.1';
  return `http://${host}:${settings.port}`;
}
function renderConnect() {
  const settings=serveData.settings, serving=statusCache?.serving;
  const endpoint=connectionEndpoint(), name=selectedModel||readyModels()[0]?.id||'model-id';
  const requireKey=serving?serving.require_key:settings.require_key, mcp=serving?serving.mcp:settings.mcp;
  const key=requireKey?'<your key>':null, auth=key?` -H 'Authorization: Bearer ${key}'`:'';
  const keyArg=key?`, api_key="${key}"`:'';
  const mcpHttp=mcp
    ?JSON.stringify({mcpServers:{basal:{url:`${endpoint}/mcp`,...(key?{headers:{Authorization:`Bearer ${key}`}}:{})}}},null,2)+'\n\n'
    :'';
  const snippets={
    curl:`curl -X POST ${endpoint}/v1/systemone -H 'Content-Type: application/json'${auth} -d '{"model":"${name}","state":"...","questions":{"route":{"type":"choice","instructions":"Choose a route","criteria":{"sales":"sales","support":"support"}}}}'

curl -X POST ${endpoint}/v1/decisions -H 'Content-Type: application/json'${auth} -d '{"model":"${name}","input":"...","questions":[{"type":"choice","name":"route","instructions":"Choose a route","choices":[{"value":"sales"},{"value":"support"}]}]}'`,
    python:`import httpx

response = httpx.post("${endpoint}/v1/systemone", json={
    "model": "${name}",
    "state": "...",
    "questions": {"route": {"type": "choice", "instructions": "Choose a route",
                            "criteria": {"sales": "sales", "support": "support"}}},
}${key?`, headers={"Authorization": "Bearer ${key}"}`:''})
print(response.json())`,
    typesafe:`export TYPESAFE_BASE_URL=${endpoint}
export TYPESAFE_API_KEY=${key||'local'}
export TYPESAFE_DEFAULT_MODEL=${name}
export NO_PROXY=localhost,127.0.0.1`,
    openai:`from openai import OpenAI

client = OpenAI(base_url="${endpoint}/v1", api_key="${key||'local'}")
decision = client.decisions.create(
    model="${name}",
    input="...",
    questions=[{"type": "choice", "instructions": "Choose a route",
                "choices": [{"value": "sales"}, {"value": "support"}]}],
)`,
    litellm:`model_list:
  - model_name: ${name}
    litellm_params:
      model: hosted_vllm/${name}
      api_base: ${endpoint}
      api_key: ${key||'local'}`,
    mcp:mcpHttp+`# stdio bridge (the first loaded model answers)
basal-mcp --url ${endpoint}${key?`\n# ${t('basal-mcp cannot send an API key: use the HTTP transport above, or turn off Require an API key.')}`:''}`,
  };
  $('#connectCurl').textContent=snippets.curl;
  $('#connectPython').textContent=snippets.python;
  $('#connectTypesafe').textContent=snippets.typesafe;
  $('#connectOpenai').textContent=snippets.openai;
  $('#connectLitellm').textContent=snippets.litellm;
  $('#connectMcp').textContent=snippets.mcp;
  renderMcpClient(endpoint,key);
  $('#mcpConnectHint').hidden=Boolean(mcp);
  $('#runningServerNote').hidden=!statusCache?.serving?.restart_required;
  $('#connectTabs').querySelectorAll('[data-connect]').forEach(tab=>{
    const active=tab.dataset.connect===connectTab;
    tab.classList.toggle('active',active); tab.setAttribute('aria-selected',String(active));
  });
  document.querySelectorAll('[data-connect-panel]').forEach(panel=>{panel.hidden=panel.dataset.connectPanel!==connectTab;});
}

// MCP client configs for the streamable-HTTP endpoint at /mcp; Claude Desktop's config file starts local (stdio)
// servers only, so it runs the engine's basal-mcp bridge to the API server
const MCP_CLIENTS={
  'claude-desktop':(url,key,endpoint)=>[JSON.stringify({mcpServers:{basal:{command:'basal-mcp',args:['--url',endpoint]}}},null,2),
    `${t('Add to claude_desktop_config.json (Settings → Developer → Edit Config) and restart Claude Desktop; basal-mcp comes with the engine.')}${key?` ${t('The basal-mcp bridge cannot send an API key: with Require an API key on, add the server in Claude Desktop as a remote (HTTP) connector with the /mcp URL instead, or turn the key off for loopback use.')}`:''}`],
  'claude-code':(url,key)=>[`claude mcp add --transport http basal ${url}${key?` --header "Authorization: Bearer ${key}"`:''}`,
    t('Run in a terminal; /mcp in Claude Code lists the server and its decide and decide_many tools.')],
  cursor:(url,key)=>[JSON.stringify({mcpServers:{basal:{url,...(key?{headers:{Authorization:`Bearer ${key}`}}:{})}}},null,2),
    t('Add to ~/.cursor/mcp.json (or .cursor/mcp.json in a project).')],
  vscode:(url,key)=>[JSON.stringify({servers:{basal:{type:'http',url,...(key?{headers:{Authorization:`Bearer ${key}`}}:{})}}},null,2),
    t('Add to .vscode/mcp.json in the workspace (or MCP: Add Server… in the command palette).')],
  'open-webui':(url,key)=>[`Type: MCP (Streamable HTTP)\nURL:  ${url}\nAuth: ${key?`Bearer ${key}`:'None'}`,
    t('In Open WebUI: Admin Settings → External Tools → add a server with these values.')],
};
function renderMcpClient(endpoint, key) {
  const [code,note]=MCP_CLIENTS[$('#mcpClient').value||'claude-desktop'](`${endpoint}/mcp`,key,endpoint);
  $('#connectClients').textContent=code;
  const mcp=statusCache?.serving?statusCache.serving.mcp:serveData?.settings?.mcp;
  $('#mcpClientNote').textContent=mcp?note:`${note} ${t('Turn on MCP at /mcp and save settings to enable HTTP transport.')}`;
}
$('#mcpClient').addEventListener('change',()=>{if(serveData)renderConnect();});
// the endpoints side by side: the wire shape each one speaks and what basal adds over the plain shape (docs/API.md)
function renderCompatMatrix() {
  const yes=`<span class="yes">✓</span>`, no=`<span class="no">—</span>`;
  const rows=[[t('Question types'),t('all basal types: choice, noul, score, multi, act, tool, classify, verify, compress, extract'),t('predicate, choice and score'),t('all basal types')],
    [t('Calibration metadata'),yes+' '+t('confidence, calibration status, deferral at max_error'),t('confidence only'),yes],
    [t('Evidence spans'),yes,no,yes],
    [t('Request ids'),yes+' x-request-id',yes+' x-request-id',t('in the HTTP response headers')],
    [t('Typed errors'),yes+' '+t('error and code'),yes+' '+t('OpenAI error envelope'),t('tool errors')],
    [t('Auth'),'Authorization: Bearer / X-API-Key','Authorization: Bearer','Authorization: Bearer'],
    [t('Verified with'),'TypeSafe SDK 0.7.5 · LiteLLM 1.106.1','OpenAI Python SDK 3.35.1',no],
    [t('Beyond plain chat servers'),t('calibrated probabilities per option, certified error thresholds, deferral'),t('per-choice probabilities'),t('typed decisions as agent tools')]];
  $('#compatMatrix').innerHTML=`<table class="compat-table"><caption class="sr-only">${t('What each endpoint offers')}</caption><thead><tr><th scope="col"><span class="sr-only">${t('Feature')}</span></th><th scope="col">System One <code>/v1/systemone</code></th><th scope="col">OpenAI Decisions <code>/v1/decisions</code></th><th scope="col">MCP <code>/mcp</code></th></tr></thead><tbody>${rows.map(([name,...cells])=>`<tr><th scope="row">${name}</th>${cells.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}
renderCompatMatrix();
// the API server banner: Running / Stopped / Restart needed / Error, where the API answers now and what it serves
function renderServeStatus(status) {
  const serving=status?.serving, restart=Boolean(serving?.restart_required);
  const [state,tone]=serving?.error?[t('Error'),'danger']:restart?[t('Restart needed'),'warning']:serving?.running?[t('Running'),'success']:[t('Stopped'),''];
  $('#serveStatus').className=`status-banner serve-status${tone?` ${tone}`:''}`;
  $('#serveDot').className=`status-dot${serving?.running?' ready':serving?.error?' error':''}`;
  $('#serveState').textContent=state;
  const text=$('#serveStatus .serve-status-text');
  text.innerHTML=serving?.running||serving?.error
    ?`${serving.url?`<span class="mono">${esc(serving.url)}</span>`:''} <span class="chip">${tp(serving.models?.length??0,'{n} model','{n} models')}</span>${serving.require_key?` <span class="chip">${t('key required')}</span>`:''}${serving.mcp?` <span class="chip">${t('mcp')}</span>`:''}${serving.error?` <span class="reason">${esc(serving.error)}</span>`:''}${restart?` <span>${t('Saved settings differ from the running server.')}</span>`:''}`
    :`<span>${restart?t('Saved settings start the server after Restart.'):t('Only this app can reach the models. Turn on Serve the API and save to let other apps connect.')}</span>`;
  $('#bannerRestart').hidden=!restart;
  $('#restartServe').disabled=!(serveData?.settings?.enabled||serving?.running);
  $('#apiSummary').textContent=serving?.running?t('Serving'):serving?.error?t('Error'):t('Off');
  $('#apiSummaryMeta').textContent=`${serving?.url||serving?.error||t('API server is not serving')} · ${tp(serveData?.keys?.length ?? 0,'{n} API keys','{n} API keys')}`;
  renderConnect();
}
async function loadServe(){
  try{
    serveData=await api('/api/serve');
    const s=serveData.settings;
    setChecked($('#serveEnabled'),s.enabled); $('#servePort').value=s.port; setChecked($('#serveRequireKey'),s.require_key);
    $('#serveMaxLen').value=s.max_len; $('#serveCors').value=s.cors_origins.join('\n'); $('#serveLog').value=s.log_decisions; $('#serveLog').dispatchEvent(new Event('pathcheck'));
    setChecked($('#serveMcp'),s.mcp); $('#serveMcp').disabled=!serveData.mcp_available; $('#mcpHint').textContent=serveData.mcp_hint||'';
    setChecked($('#autoLoad'),serveData.auto_load); serveHost=s.host;
    setExposure(); serveSaved=serveFormState(); markServeDirty();
    $('#serveWarning').hidden=s.host!=='0.0.0.0'||s.require_key;
    renderServeStatus(statusCache);
    $('#serveKeys').innerHTML=serveData.keys.map(k=>`<div class="disk-row"><span class="disk-info">${esc(k.name)} · ${esc(k.prefix)} · ${t('created')} ${esc(new Date(k.created*1000).toLocaleString())} · ${t('last used')} ${k.last_used?esc(new Date(k.last_used*1000).toLocaleString()):t('never')}</span><button type="button" data-revoke="${esc(k.name)}">${t('Revoke')}</button></div>`).join('')||`<p class="muted">${t('No API keys created.')}</p>`;
    $('#serveKeys').querySelectorAll('[data-revoke]').forEach(button=>button.addEventListener('click',async()=>{
      if(await confirmDialog({title:t('Revoke API key?'),description:t('{key} will no longer be able to access this server.',{key:button.dataset.revoke}),confirmLabel:t('Revoke'),destructive:true})){
        try{await api(`/api/serve/keys/${encodeURIComponent(button.dataset.revoke)}`,{method:'DELETE'});toast(t('API key revoked'));await loadServe();}
        catch(error){showServeError(error.message);toast(error.message,'error');}
      }
    }));
    $('#serveError').hidden=true;
    renderConnect();
  }catch(error){showServeError(error.message);}
}
// unsaved changes on the API server form: Save writes them, Restart applies saved ones to a running server
let serveSaved='';
const serveFormState=()=>JSON.stringify([$('#serveEnabled').checked,serveHost,$('#servePort').value,$('#serveMaxLen').value,$('#serveLog').value,$('#serveCors').value,$('#serveRequireKey').checked,$('#serveMcp').checked]);
function markServeDirty() {
  const dirty=Boolean(serveSaved)&&serveFormState()!==serveSaved;
  $('#serveDirty').hidden=!dirty; $('#serveSaveHint').hidden=!dirty; $('#saveServe').classList.toggle('dirty',dirty);
}
['#serveEnabled','#servePort','#serveMaxLen','#serveLog','#serveCors','#serveRequireKey','#serveMcp'].forEach(sel=>{$(sel).addEventListener('input',markServeDirty);$(sel).addEventListener('change',markServeDirty);});
function showServeError(message) { $('#serveError').textContent=message; $('#serveError').hidden=!message; }
function setExposure(){for(const [b,host] of [[$('#serveLoopback'),'127.0.0.1'],[$('#serveLan'),'0.0.0.0']]){b.classList.toggle('active',serveHost===host);b.setAttribute('aria-pressed',String(serveHost===host));}}
$('#serveLoopback').onclick=()=>{serveHost='127.0.0.1';$('#serveWarning').hidden=true;setExposure();markServeDirty();};
$('#serveLan').onclick=()=>{serveHost='0.0.0.0';$('#serveWarning').hidden=$('#serveRequireKey').checked;setExposure();markServeDirty();};
$('#serveRequireKey').addEventListener('change',()=>{$('#serveWarning').hidden=serveHost!=='0.0.0.0'||$('#serveRequireKey').checked;if(serveData)renderConnect();});
$('#connectTabs').querySelectorAll('[data-connect]').forEach(button=>button.addEventListener('click',()=>{connectTab=button.dataset.connect;renderConnect();}));
$('#saveServe').onclick=async()=>{
  try{
    await api('/api/serve',{method:'PUT',body:JSON.stringify({
      enabled:$('#serveEnabled').checked,host:serveHost,port:Number($('#servePort').value),
      require_key:$('#serveRequireKey').checked,max_len:Number($('#serveMaxLen').value),
      cors_origins:$('#serveCors').value.split('\n').map(value=>value.trim()).filter(Boolean),
      log_decisions:$('#serveLog').value,mcp:$('#serveMcp').checked
    })});
    showServeError(''); await loadServe(); await refreshStatus();
    toast(statusCache?.serving?.restart_required?t('Settings saved — restart the server to apply'):t('Settings saved'));
  }catch(error){showServeError(error.message);toast(error.message,'error');}
};
$('#createKey').onclick=async()=>{
  try{
    const key=await api('/api/serve/keys',{method:'POST',body:JSON.stringify({name:$('#newKeyName').value})});
    $('#newKeySecret').innerHTML=`<code>${esc(key.key)}</code><p>${t('Copy it now: it is not shown again.')}</p><button type="button" id="copyNewKey">${t('Copy')}</button>`;
    $('#newKeySecret').hidden=false; $('#copyNewKey').onclick=()=>copyText(key.key,t('API key copied'));
    $('#newKeyName').value=''; await loadServe(); toast(t('API key created'));
  }catch(error){showServeError(error.message);toast(error.message,'error');}
};
$('#restartServe').onclick=async()=>{try{await api('/api/serve/restart',{method:'POST'});toast(t('API server restarted'));await refreshStatus();}catch(e){showServeError(e.message);toast(e.message,'error');}};
$('#bannerRestart').onclick=()=>$('#restartServe').click();
// the API endpoint, from any view (Library rows, the command palette); a copy while the server runs completes "Connect
// your app" (an endpoint copied while it is off connects nothing)
const markConnected=()=>{if(statusCache?.serving?.running)store('basal-ui.connected','1')};
async function copyEndpoint() {
  if(!serveData)await loadServe();
  if(!serveData)return;
  markConnected();
  await copyText(connectionEndpoint(),t('Endpoint copied'));
  if(!statusCache?.serving?.running)toast(t('The API server is off: turn on Serve the API in API server so other apps can connect.'),'info');
  renderFirstRun();
}
$('#copyServeUrl').onclick=()=>copyEndpoint();
$('#copyServeConnect').onclick=()=>{markConnected();copyText($(`[data-connect-panel="${connectTab}"] code`).textContent,t('Snippet copied'));};
