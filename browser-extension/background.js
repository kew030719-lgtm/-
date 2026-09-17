// Persist transitions: Chrome can suspend this worker between events.
importScripts('queue.js');
const defaultEndpoint='http://127.0.0.1:8000';
let busy=false;
let wakeTimer=null;
function scheduleTick(when=Date.now()){
  if(wakeTimer!==null)clearTimeout(wakeTimer);
  wakeTimer=setTimeout(()=>{wakeTimer=null;tick();},Math.max(0,when-Date.now()));
}
function normalizeEndpoint(value){
  let url;
  try{url=new URL(String(value||defaultEndpoint).trim());}catch{throw new Error('主程序地址格式无效');}
  if(url.protocol!=='http:'||!['127.0.0.1','localhost'].includes(url.hostname)||url.username||url.password||url.pathname!=='/'||url.search||url.hash){
    throw new Error('主程序地址只能是本机 HTTP 地址，例如 http://127.0.0.1:8000');
  }
  return url.origin;
}
async function getEndpoint(){
  const stored=await chrome.storage.local.get(['endpoint']);
  return normalizeEndpoint(stored.endpoint||defaultEndpoint);
}
async function preferredTaskId(){
  const endpoint=await getEndpoint();
  const tabs=await chrome.tabs.query({});
  const candidates=[];
  for(const tab of tabs){
    try{
      const url=new URL(tab.url||'');
      if(url.origin!==endpoint)continue;
      const taskId=url.searchParams.get('task');
      if(taskId)candidates.push({taskId,active:Boolean(tab.active),lastAccessed:Number(tab.lastAccessed||0)});
    }catch{/* ignore browser-internal and incomplete tab URLs */}
  }
  candidates.sort((a,b)=>Number(b.active)-Number(a.active)||b.lastAccessed-a.lastAccessed);
  return candidates[0]?.taskId||null;
}
async function pendingTask(){
  const preferred=await preferredTaskId();
  // Only the task explicitly selected in the CareerRadar page may be claimed.
  // This prevents a completed run from rolling into an older queued run.
  if(!preferred)return {task_id:null};
  return api(`/api/browser-runs/pending?preferred_run_id=${encodeURIComponent(preferred)}`);
}
async function api(path,body){
  const endpoint=await getEndpoint();
  let response;
  try{
    response=await fetch(endpoint+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(15000)});
  }catch(cause){
    const error=new Error('主程序暂时无法连接，助手会自动重试');
    error.transient=true;error.cause=cause;throw error;
  }
  const data=await response.json().catch(()=>({}));
  if(!response.ok){const error=new Error(data.detail||`本地服务请求失败（${response.status}）`);error.status=response.status;throw error;}
  return data;
}
async function save(job){await chrome.storage.local.set({job});}
async function progress(job,status,message){job.message=message;await save(job);await api(`/api/browser-runs/${job.task_id}/progress`,{status,message});}
async function arm(){await chrome.alarms.create('career-radar',{periodInMinutes:0.5});}
// Runs inside the job page. Writes the greeting into the site's message box and
// dispatches an input event so the page's own framework registers it.
//
// This is the ONLY page mutation the helper ever performs. It never clicks
// submit, never clicks 立即沟通, and never touches a control: sending an
// application stays the user's action, which is what the sites' terms require
// and what keeps the account safe.
function fillGreeting(greeting){
  const visible=el=>el&&(el.offsetParent!==null||el.getClientRects().length>0);
  const boxes=[...document.querySelectorAll('textarea'),...document.querySelectorAll('[contenteditable="true"]')].filter(visible);
  const box=boxes.find(el=>el.tagName==='TEXTAREA')||boxes[0];
  if(!box)return false;
  if(box.tagName==='TEXTAREA'){
    box.value=greeting;
    box.dispatchEvent(new Event('input',{bubbles:true}));
  }else{
    box.textContent=greeting;
    box.dispatchEvent(new InputEvent('input',{bubbles:true}));
  }
  box.focus();
  return true;
}
async function applyOne(applicationId){
  const endpoint=await getEndpoint();
  const application=await api(`/api/applications/${applicationId}`);
  if(!application.greeting)throw new Error('这条投递还没有生成打招呼语');
  const tab=await chrome.tabs.create({url:application.url,active:true});
  await waitForTab(tab.id);
  let filled=false;
  try{
    const [outcome]=await chrome.scripting.executeScript({
      target:{tabId:tab.id},func:fillGreeting,args:[application.greeting],
    });
    filled=Boolean(outcome&&outcome.result);
  }catch{/* the page may block injection; the popup still offers the text */ }
  await api(`/api/applications/${applicationId}/opened`,{});
  return {
    filled,
    greeting:application.greeting,
    endpoint,
    message:filled
      ? '已填入页面输入框，并把内容复制到剪贴板。请核对后自行点击发送。'
      : '未能自动填入（页面结构可能已变化）。内容已复制到剪贴板，请手动粘贴后自行发送。',
  };
}
async function waitForTab(tabId){
  for(let attempt=0;attempt<60;attempt++){
    const tab=await chrome.tabs.get(tabId).catch(()=>null);
    if(tab&&tab.status==='complete')return true;
    await new Promise(resolve=>setTimeout(resolve,500));
  }
  return false;
}
// A host the manifest does not pre-grant can only be requested from a user
// gesture, which a background alarm is not. Returning false makes the caller
// fall back to the manual paste path rather than stalling on an unusable tab.
async function ensureHost(url){
  if(!chrome.permissions||!chrome.permissions.contains)return true;
  let origin;
  try{origin=new URL(url).origin+'/*';}catch{return false;}
  try{
    if(await chrome.permissions.contains({origins:[origin]}))return true;
    return await chrome.permissions.request({origins:[origin]});
  }catch{return false;}
}
chrome.runtime.onInstalled.addListener(()=>arm());
chrome.runtime.onStartup.addListener(()=>arm());
chrome.alarms.onAlarm.addListener(alarm=>{if(alarm.name==='career-radar')tick();});
chrome.tabs.onUpdated.addListener((tabId,changeInfo)=>{
  if(changeInfo.status==='complete')return tick(tabId);
});
function readPage(){
  const root=document.documentElement.cloneNode(true);
  root.querySelectorAll('script:not([type="application/ld+json"]),input,textarea,iframe,noscript').forEach(node=>node.remove());
  return {url:location.href,html:root.outerHTML.slice(0,2000000),text:document.body?.innerText||''};
}
async function tick(completedTabId){
  if(busy)return;
  busy=true;
  let job;
  try{
    const stored=await chrome.storage.local.get(['enabled','job']);
    if(!stored.enabled)return;
    try{await api('/api/browser-helper/heartbeat',{});}catch{/* normal retry handling below covers task calls */}
    job=stored.job;
    if(completedTabId!==undefined&&(!job||job.done||job.phase!=='read'||completedTabId!==job.tabId))return;
    const preferred=await preferredTaskId();
    if(job&&!job.done&&preferred&&preferred!==job.task_id){
      try{
        const target=await api(`/api/tasks/${preferred}`);
        if(['QUEUED','NEEDS_MANUAL_INPUT'].includes(target.status)){
          try{await progress(job,'NEEDS_MANUAL_INPUT','浏览器助手已切换到主程序当前任务；此任务可稍后继续');}catch{/* old service may be unavailable */}
          await chrome.storage.local.remove('job');job=null;
        }
      }catch{/* keep the current valid job when the page points at a missing task */}
    }
    if(!job||job.done){
      const pending=await pendingTask();
      if(!pending.task_id)return;
      job={...await api(`/api/browser-runs/${pending.task_id}/start`,{}),seen:[],count:0,phase:'navigate',message:'正在开始自动搜索',runStartedAt:Date.now(),captureTimes:[]};
      await save(job);
    }
    let task;
    try{task=await api(`/api/tasks/${job.task_id}`);}catch(error){
      if(error.status!==404)throw error;
      await chrome.storage.local.remove('job');job=null;
      const pending=await pendingTask();
      if(!pending.task_id)return;
      job={...await api(`/api/browser-runs/${pending.task_id}/start`,{}),seen:[],count:0,phase:'navigate',message:'旧任务已失效，已连接当前主程序',runStartedAt:Date.now(),captureTimes:[]};
      await save(job);task=await api(`/api/tasks/${job.task_id}`);
    }
    if(['FAILED','FAILED_VALIDATION','SUCCEEDED'].includes(task.status)){
      job.done=true;job.message=task.message;await save(job);return;
    }
    if(job.paused)return;
    job.runStartedAt=job.runStartedAt||job.startedAt||Date.now();
    job.captureTimes=Array.isArray(job.captureTimes)?job.captureTimes:[];
    // A job persisted before the extension learned the per-site allow rules has
    // no `allow`. Re-acquire it whenever the backend will still serve /start, and
    // otherwise say so plainly — every navigation would fail otherwise, one
    // confusing error at a time.
    if(task.status==='QUEUED'){
      const started=await api(`/api/browser-runs/${job.task_id}/start`,{});
      job={...job,...started,allow:started.allow,queue:started.queue};
      await save(job);
    }
    if(!job.allow&&task.payload&&task.payload.allow){
      // The run carries its own rules, so an updated helper can adopt a job that
      // an older version persisted without losing the queue position.
      job.allow=task.payload.allow;await save(job);
    }
    if(!job.allow){
      job.paused=true;
      await progress(job,'NEEDS_MANUAL_INPUT','当前任务开始于扩展更新之前，请先取消这个任务，再重新发起一次搜索');
      return;
    }
    job.count=(await api(`/api/jobs?run_id=${job.task_id}`)).length;
    if(job.count>=job.max_jobs||!job.queue.length){
      if(!job.count){job.paused=true;await progress(job,'NEEDS_MANUAL_INPUT','搜索完成但没有有效岗位，请调整方向或检查页面');return;}
      await api(`/api/browser-captures/${job.task_id}/finish`,{});
      job.done=true;job.message=`自动采集完成：${job.count} 个唯一岗位`;await save(job);return;
    }
    const item=job.queue[0];
    if(!CareerQueue.allowed(job,item.url))throw new Error('该地址不在本次任务的站点允许列表内，已拒绝访问');
    if(job.phase==='navigate'){
      if(Date.now()<(job.nextAt||0))return;
      if(!await ensureHost(item.url)){
        job.paused=true;await progress(job,'NEEDS_MANUAL_INPUT','未获得该站点的访问权限，请在扩展详情页手动授予，或改用粘贴岗位描述');
        return;
      }
      if(job.tabId){try{await chrome.tabs.get(job.tabId);}catch{job.tabId=null;}}
      const tab=job.tabId?await chrome.tabs.update(job.tabId,{url:item.url}):await chrome.tabs.create({url:item.url,active:true});
      job.tabId=tab.id;job.phase='read';job.startedAt=Date.now();job.nextAt=Date.now()+job.interval_ms;
      await progress(job,'RUNNING',`${item.kind==='search'?'搜索':'读取岗位'} · ${item.city} · 已采集 ${job.count} 个`);
      // Usually the tab-complete event wakes us first. This short timer covers
      // pages that completed before onUpdated was delivered.
      scheduleTick(Date.now()+500);
      return;
    }
    const tab=await chrome.tabs.get(job.tabId);
    if(tab.status!=='complete'){
      if(Date.now()-job.startedAt>60000)throw new Error('页面加载超时，请检查专用采集标签页后继续');
      scheduleTick(Date.now()+500);
      return;
    }
    const renderWait=Math.max(0,Number(item.render_wait_ms||0));
    if(Date.now()-job.startedAt<renderWait){
      scheduleTick(job.startedAt+renderWait);
      return;
    }
    if(!CareerQueue.allowed(job,tab.url))throw new Error('采集页跳转到其他域名，已暂停');
    const [{result:page}]=await chrome.scripting.executeScript({target:{tabId:job.tabId},func:readPage});
    if(CareerQueue.isLogin(job,page.url))throw new Error('请在采集标签页完成登录，然后点击扩展中的继续任务');
    if(!page.text.trim()){
      // `complete` only means the document loaded. BOSS renders the useful SPA
      // body shortly afterwards, so an immediate empty read is not a white page.
      if(Date.now()-job.startedAt<=15000){scheduleTick(Date.now()+500);return;}
      throw new Error('页面持续白屏，请在采集标签页检查后继续任务');
    }
    const body={run_id:job.task_id,url:page.url,html:page.html,city:item.city,page_duration_ms:Math.max(0,Date.now()-job.startedAt)};
    if(item.kind==='search'){
      const result=await api('/api/browser-search-pages',body);
      CareerQueue.discovered(job,result.links,item.city);
    }else{
      const result=await api('/api/browser-captures',body);
      job.count=result.count;job.queue.shift();
      job.captureTimes.push(Date.now());
      job.captureTimes=job.captureTimes.filter(value=>value>=Date.now()-60000);
    }
    job.phase='navigate';await save(job);scheduleTick(job.nextAt);
  }catch(error){
    if(job){
      if(error.transient){
        job.message=error.message;await save(job);scheduleTick(Date.now()+30000);return;
      }
      job.paused=true;job.message=`已暂停：${error.message}`;await save(job);
      try{await progress(job,'NEEDS_MANUAL_INPUT',job.message);}catch{/* service may be offline */}
    }
  }finally{busy=false;}
}
chrome.runtime.onMessage.addListener((message,sender,reply)=>{
  (async()=>{
    const stored=await chrome.storage.local.get(['job','enabled','endpoint']);
    let job=stored.job;
    if(message.action==='configure'){
      const previous=normalizeEndpoint(stored.endpoint||defaultEndpoint),endpoint=normalizeEndpoint(message.endpoint);
      if(previous!==endpoint){await chrome.storage.local.set({endpoint,enabled:false});await chrome.storage.local.remove('job');job=null;}
      else await chrome.storage.local.set({endpoint});
      const health=await api('/health');
      reply({endpoint,connected:true,message:`已连接 ${endpoint} · ${health.model||'CareerRadar'}`});return;
    }else if(message.action==='enable'){
      await chrome.storage.local.set({enabled:true});await arm();await tick();
    }else if(message.action==='apply'){
      const result=await applyOne(message.application_id);
      reply({connected:true,...result});return;
    }else if(message.action==='applied'){
      const application=await api(`/api/applications/${message.application_id}/submitted`,{});
      reply({connected:true,message:`已记录：${application.company} · ${application.title}`});return;
    }else if(message.action==='skip-application'){
      await api(`/api/applications/${message.application_id}/skip`,{});
      reply({connected:true,message:'已跳过这条投递'});return;
    }else if(message.action==='resume'){
      if(busy)throw new Error('正在处理页面，请稍后重试');
      if(!job||job.done)throw new Error('没有可恢复的任务');
      await progress(job,'RUNNING','已恢复，继续当前页面');
      job.paused=false;job.phase='navigate';await save(job);
      await chrome.storage.local.set({enabled:true});await tick();
    }else if(message.action==='cancel'){
      await chrome.storage.local.set({enabled:false});
      if(job&&!job.done){
        const endpoint=await getEndpoint();
        await fetch(`${endpoint}/api/tasks/${job.task_id}`,{method:'DELETE'});
        job.done=true;job.message='任务已停止';await save(job);
      }
    }
    const latest=await chrome.storage.local.get(['job','enabled','endpoint']);
    const endpoint=normalizeEndpoint(latest.endpoint||defaultEndpoint);
    try{
      const health=await api('/health');
      const metrics=latest.job?jobMetrics(latest.job):null;
      reply({endpoint,connected:true,metrics,message:latest.job?.message||(latest.enabled?`已连接 ${endpoint}；等待任务（最多 30 秒）`:`已连接 ${endpoint} · ${health.model||'CareerRadar'}；请启用自动接单`)});
    }catch(error){reply({endpoint,connected:false,error:`无法连接 ${endpoint}：${error.message}`});}
  })().catch(error=>reply({error:error.message}));
  return true;
});
function jobMetrics(job){
  const now=Date.now(),started=job.runStartedAt||job.startedAt||now;
  const recent=(Array.isArray(job.captureTimes)?job.captureTimes:[]).filter(value=>value>=now-60000);
  return {count:Number(job.count||0),elapsed_ms:Math.max(0,now-started),per_minute:recent.length};
}
