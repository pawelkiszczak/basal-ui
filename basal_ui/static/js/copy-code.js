// Playground "Copy as code": the request in the form as cURL / Python for System One and OpenAI Decisions, or an MCP
// tool call, against the API server's endpoint (docs/API.md). The menu is a role=menu with arrow-key navigation.
'use strict';
function playgroundRequest() {
  const request=$('#editJsonToggle').checked?JSON.parse($('#requestJson').value):buildRequest();
  if(!request||typeof request!=='object'||Array.isArray(request))throw new Error(t('The request must be a JSON object.'));
  return {...request,model:selectedModel||request.model};
}
// System One → OpenAI Decisions for the three types it translates (noul → predicate, choice, score); the names of the
// questions it cannot carry come back in `skipped`, media in `media`
function decisionsRequest(request) {
  const entries=c=>Array.isArray(c)?c.map(v=>[String(v),null]):c&&typeof c==='object'?Object.entries(c):[];
  const described=(field,key,d)=>({[field]:key,...(d!=null&&d!==''?{description:typeof d==='string'?d:JSON.stringify(d)}:{})});
  const skipped=[];
  const questions=Object.entries(request.questions||{}).flatMap(([name,q])=>{
    const instructions=q.instructions||q.question||name;
    if(q.type==='noul')return [{type:'predicate',name,instructions}];
    if(q.type==='choice')return [{type:'choice',name,instructions,choices:entries(q.criteria).map(([k,d])=>described('value',k,d))}];
    if(q.type==='score')return [{type:'score',name,instructions,levels:entries(q.levels||q.criteria).map(([k,d])=>described('label',k,d))}];
    skipped.push(name); return [];
  });
  return {body:{model:request.model,input:typeof request.state==='string'?request.state:JSON.stringify(request.state??''),questions},
    skipped,media:Boolean(request.media?.length)};
}
// a JSON value as a Python literal (True/False/None); JSON string escapes are valid Python string escapes
function pyLiteral(v, pad='') {
  if(v==null)return 'None';
  if(typeof v==='boolean')return v?'True':'False';
  if(typeof v==='number')return Number.isFinite(v)?String(v):'None';
  if(typeof v==='string')return JSON.stringify(v);
  const inner=`${pad}    `;
  if(Array.isArray(v))return v.length?`[\n${v.map(x=>inner+pyLiteral(x,inner)).join(',\n')},\n${pad}]`:'[]';
  const e=Object.entries(v).filter(([,x])=>x!==undefined);
  return e.length?`{\n${e.map(([k,x])=>`${inner}${JSON.stringify(k)}: ${pyLiteral(x,inner)}`).join(',\n')},\n${pad}}`:'{}';
}
// media in a snippet: never the base64 itself (megabytes in one shell argument or source line); each item's data is a
// marker that the snippet replaces with a file reference (Python) or a placeholder to fill in (cURL)
const mediaMarker=i=>`__basal_media_${i}__`;
function mediaFiles(request) {
  return (Array.isArray(request.media)?request.media:[]).map((m,i)=>{
    const name=selectedMedia[i]?.file?.name||`media-${i+1}.${m?.type==='audio'?'wav':'png'}`;
    return {name,type:m?.type||'media'};
  });
}
function withMediaMarkers(request) {
  if(!Array.isArray(request.media)||!request.media.length)return request;
  return {...request,media:request.media.map((m,i)=>({...m,data:mediaMarker(i)}))};
}
function codeSnippet(kind) {
  const request=playgroundRequest(), endpoint=serveData?connectionEndpoint():'http://127.0.0.1:8000';
  const key=(statusCache?.serving?.require_key??serveData?.settings?.require_key)?'<your key>':null;
  const shell=text=>`'${text.replaceAll("'","'\\''")}'`, auth=key?` -H 'Authorization: Bearer ${key}'`:'';
  if(kind==='mcp'){  // decide_many takes the request's state, questions, lang and facts; the gateway picks the model
    const args={state:request.state,questions:request.questions};
    for(const k of ['lang','facts'])if(request[k])args[k]=request[k];
    const message={jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'decide_many',arguments:args}};
    return `# ${t('MCP (streamable HTTP, JSON-RPC 2.0) at {url}: any MCP client can send this tools/call; the gateway picks the model and media is not sent.',{url:`${endpoint}/mcp`})}\n`
      +`curl ${endpoint}/mcp -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream'${auth} \\\n  -d ${shell(JSON.stringify(message,null,2))}`;
  }
  const openai=kind.startsWith('openai'), translated=openai?decisionsRequest(request):null;
  const files=openai?[]:mediaFiles(request), body=openai?translated.body:withMediaMarkers(request);
  const notes=(openai?[
    ...(translated.skipped.length?[t('skipped: {names} (types OpenAI Decisions does not carry)',{names:translated.skipped.join(', ')})]:[]),
    ...(translated.media?[t('media is not included; send images as input_image parts')]:[]),
  ]:files.length&&kind==='curl'?[t('media: put the base64 of each file (base64 -i FILE) in place of its BASE64_OF_ placeholder; for large media save the JSON as request.json and send it with -d @request.json')]:[]).map(n=>`# ${n}\n`).join('');
  if(kind==='curl'||kind==='openai-curl'){
    let json=JSON.stringify(body);
    files.forEach((f,i)=>{json=json.replace(JSON.stringify(mediaMarker(i)),JSON.stringify(`BASE64_OF_${f.name}`));});
    return `${notes}curl ${endpoint}${kind==='curl'?'/v1/systemone':'/v1/decisions'} -H 'Content-Type: application/json'${auth} \\\n  -d ${shell(json)}`;
  }
  if(kind==='python'){
    let literal=pyLiteral(body,'    ');
    files.forEach((f,i)=>{literal=literal.replace(JSON.stringify(mediaMarker(i)),`media(${JSON.stringify(f.name)})`);});
    const reader=files.length?`\ndef media(path):  # ${t('the file as base64, as the request carries it')}\n    with open(path, "rb") as f:\n        return base64.b64encode(f.read()).decode()\n\n\n`:'';
    return `${files.length?'import base64\n':''}import httpx\n\n${reader}response = httpx.post(\n    "${endpoint}/v1/systemone",\n    json=${literal},${key?`\n    headers={"Authorization": "Bearer ${key}"},`:''}\n    timeout=60,\n)\nprint(response.json())`;
  }
  return `${notes}from openai import OpenAI\n\nclient = OpenAI(base_url="${endpoint}/v1", api_key="${key||'local'}")\ndecision = client.decisions.create(**${pyLiteral(body)})\nprint(decision)`;
}
(()=>{
  const button=$('#copyCodeButton'), menu=$('#copyCodeMenu'), items=()=>[...menu.querySelectorAll('[role=menuitem]')];
  const close=(focus=true)=>{menu.hidden=true;button.setAttribute('aria-expanded','false');if(focus)button.focus();};
  // the OpenAI items are off when no question of the request translates
  const syncOpenai=()=>{
    let none=false;
    try{none=!decisionsRequest(playgroundRequest()).body.questions.length;}catch{none=false;}
    menu.querySelectorAll('[data-snippet^=openai]').forEach(item=>{
      item.setAttribute('aria-disabled',String(none));
      item.title=none?t('None of these question types can be sent to OpenAI Decisions (predicate, choice and score only).'):'';
    });
  };
  const open=()=>{syncOpenai();menu.hidden=false;button.setAttribute('aria-expanded','true');items()[0]?.focus();};
  button.onclick=()=>menu.hidden?open():close();
  menu.addEventListener('keydown',e=>{
    const all=items(), i=all.indexOf(document.activeElement), next={ArrowDown:i+1,ArrowUp:i-1,Home:0,End:all.length-1}[e.key];
    if(e.key==='Escape'){e.preventDefault();close();}
    else if(next!==undefined){e.preventDefault();all[(next+all.length)%all.length].focus();}
    else if(e.key==='Tab')close(false);
  });
  menu.addEventListener('click',async e=>{
    const item=e.target.closest('[data-snippet]'); if(!item||item.getAttribute('aria-disabled')==='true')return;
    close();
    try{
      if(!serveData)await loadServe();
      const kind=item.dataset.snippet;
      copyText(codeSnippet(kind),t('Code copied'));
    }catch(error){toast(error.message,'error');}
  });
  document.addEventListener('click',e=>{if(!menu.hidden&&!e.target.closest('.copy-menu'))close(false);});
})();
