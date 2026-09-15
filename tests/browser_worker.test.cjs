const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const dir=path.join(__dirname,'../browser-extension');
function harness(){
  const state={enabled:true}, calls=[], posts=[];
  let status='QUEUED', pageKind='search', fail=false, networkDown=false, taskMissing=false, now=0, count=0, taskAllow=true, currentUrl='';
  const listeners={};
  const scheduled=[];
  const chrome={storage:{local:{get:async()=>structuredClone(state),set:async data=>Object.assign(state,structuredClone(data)),remove:async key=>delete state[key]}},
    alarms:{create:async()=>{},onAlarm:{addListener:fn=>listeners.alarm=fn}},
    runtime:{onInstalled:{addListener(){}},onStartup:{addListener(){}},onMessage:{addListener:fn=>listeners.message=fn}},
    tabs:{query:async()=>[],onUpdated:{addListener:fn=>listeners.updated=fn},create:async({url})=>{calls.push(url);currentUrl=url;pageKind=url.includes('job_detail')?'detail':'search';return{id:1};},update:async(id,{url})=>{calls.push(url);currentUrl=url;pageKind=url.includes('job_detail')?'detail':'search';return{id};},get:async()=>({status:'complete',url:currentUrl})},
    scripting:{executeScript:async()=>[{result:{url:currentUrl,html:'fixture',text:fail?'':'职位详情'}}]}};
  const fetch=async(url,options)=>{
    if(networkDown)throw new TypeError('fetch failed');
    let data={};
    if(options&&options.method==='POST')posts.push(url);
    if(taskMissing&&url.includes('/api/tasks/'))return{ok:false,status:404,json:async()=>({detail:'任务不存在'})};
    if(url.endsWith('/pending'))data={task_id:!taskMissing&&status==='QUEUED'?'task_test':null};
    else if(url.endsWith('/start')){status='RUNNING';data={task_id:'task_test',queue:[{url:'https://www.zhipin.com/web/geek/job?page=1',city:'北京',kind:'search'},{url:'https://www.zhipin.com/web/geek/job?page=2',city:'北京',kind:'search'}],interval_ms:10000,max_jobs:2,allow:{boss:{hosts:['www.zhipin.com','m.zhipin.com'],path_pattern:'/job_detail/[A-Za-z0-9_-]+\\.html',login_pattern:'/web/user|/login'}}};}
    else if(url.includes('/api/tasks/'))data={status,payload:{sites:['boss'],...(taskAllow?{allow:{boss:{hosts:['www.zhipin.com'],path_pattern:'/job_detail/[A-Za-z0-9_-]+\\.html',login_pattern:'/web/user'}}}:{})}};
    else if(url.endsWith('/progress')){status=JSON.parse(options.body).status;}
    else if(url.endsWith('/api/browser-search-pages'))data={links:['https://www.zhipin.com/job_detail/a.html?ka=1','https://www.zhipin.com/job_detail/a.html?ka=2','https://www.zhipin.com/job_detail/b.html','https://evil.test/job_detail/c.html']};
    else if(url.includes('/api/jobs?'))data=Array(count).fill({});
    else if(url.endsWith('/api/browser-captures'))data={count:++count};
    else if(url.endsWith('/finish'))status='SUCCEEDED';
    else if(url.includes('/api/applications/'))data={application_id:'app_test',company:'星图科技',title:'Python 后端工程师',url:'https://www.zhipin.com/job_detail/ap.html',status:'READY',greeting:'您好，希望应聘贵司岗位。',greeting_citations:[],resume_export_id:null};
    return{ok:true,status:200,json:async()=>data};
  };
  let context;
  let timerId=0;
  function reload(){context=vm.createContext({chrome,fetch,URL,AbortSignal,Date:{now:()=>now},console,setTimeout:(fn,delay)=>{scheduled.push(delay);return++timerId;},clearTimeout:()=>{},importScripts:file=>vm.runInContext(fs.readFileSync(path.join(dir,file),'utf8'),context)});vm.runInContext(fs.readFileSync(path.join(dir,'background.js'),'utf8'),context);}
  reload();
  return{state,calls,posts,scheduled,reload,setFail:v=>fail=v,setNetworkDown:v=>networkDown=v,setStatus:v=>status=v,setTaskMissing:v=>taskMissing=v,setTaskAllow:v=>taskAllow=v,tick:async(delta=30000)=>{now+=delta;await vm.runInContext('tick()',context);},complete:()=>listeners.updated(1,{status:'complete'}),command:(action,extra={})=>new Promise(resolve=>listeners.message({action,...extra},{},resolve))};
}
test('a completed page is read immediately while the next navigation keeps the site interval',async()=>{
  const h=harness();
  await h.tick();
  assert.equal(h.calls.length,1);
  await h.complete();
  assert.equal(h.state.job.queue.length,3,'search results should replace the search page immediately');
  await h.tick(9999);assert.equal(h.calls.length,1,'must not navigate before the ten-second floor');
  await h.tick(1);assert.equal(h.calls.length,2);
  assert.ok(h.scheduled.includes(500),'page-load polling should not wait for the 30-second fallback alarm');
});
test('a temporary local-service outage retries without pausing or losing the cursor',async()=>{
  const h=harness();await h.tick();
  h.setNetworkDown(true);await h.complete();
  assert.notEqual(h.state.job.paused,true);
  assert.match(h.state.job.message,/自动重试/);
  h.setNetworkDown(false);await h.tick();
  assert.equal(h.state.job.queue[0].kind,'detail');
});
test('status reports persisted count, elapsed time and one-minute throughput',async()=>{
  const h=harness();await h.tick();await h.complete();
  await h.tick(10000);await h.complete();
  const result=await h.command('status');
  assert.equal(result.metrics.count,1);
  assert.equal(result.metrics.elapsed_ms,10000);
  assert.equal(result.metrics.per_minute,1);
});
test('auto search, canonical link dedup, persistent queue and stop at cap without popup',async()=>{
  const h=harness();
  await h.tick();await h.tick();
  assert.equal(h.state.job.queue.length,3);
  h.reload(); // MV3 suspension, same persisted cursor
  for(let i=0;i<5;i++)await h.tick();
  assert.equal(h.state.job.count,2);assert.equal(h.state.job.done,true);
  assert.equal(h.calls.length,3); // page two never requested after cap
  assert.equal(h.calls.filter(x=>x.includes('/a.html')).length,1);
});
test('blank page pauses same cursor and resumes after user action',async()=>{
  const h=harness();await h.tick();h.setFail(true);await h.tick();
  assert.equal(h.state.job.paused,true);
  const before=h.calls.length;await h.tick();assert.equal(h.calls.length,before);
  h.setFail(false);await h.command('resume');await h.tick();
  assert.equal(h.state.job.paused,false);assert.equal(h.state.job.queue[0].kind,'detail');
});
test('backend cancellation stops before next navigation',async()=>{
  const h=harness();await h.tick();h.setStatus('FAILED');await h.tick();
  assert.equal(h.state.job.done,true);assert.equal(h.calls.length,1);
});
test('cancelled paused task releases queue for the next task',async()=>{
  const h=harness();await h.tick();h.setFail(true);await h.tick();
  assert.equal(h.state.job.paused,true);
  h.setStatus('FAILED');await h.tick();assert.equal(h.state.job.done,true);
  h.setStatus('QUEUED');h.setFail(false);await h.tick();
  assert.equal(h.state.job.phase,'read');assert.equal(h.state.job.paused,undefined);
});
test('configuring the current local service clears an old server task',async()=>{
  const h=harness();await h.tick();assert.ok(h.state.job);
  const result=await h.command('configure',{endpoint:'http://127.0.0.1:8012'});
  assert.equal(result.connected,true);assert.equal(h.state.endpoint,'http://127.0.0.1:8012');
  assert.equal(h.state.enabled,false);assert.equal(h.state.job,undefined);
  const invalid=await h.command('configure',{endpoint:'https://example.com'});
  assert.match(invalid.error,/本机 HTTP 地址/);
});
test('a task missing from the current server is discarded instead of staying paused',async()=>{
  const h=harness();await h.tick();h.setStatus('QUEUED');h.setTaskMissing(true);await h.tick();
  assert.equal(h.state.job,undefined);
});
test('a job persisted before the allow rules existed re-acquires them while queued',async()=>{
  const h=harness();
  await h.tick();
  assert.ok(h.state.job.allow);
  delete h.state.job.allow;          // as an older extension version persisted it
  h.setStatus('QUEUED');
  h.reload();
  await h.tick();
  assert.ok(h.state.job.allow,'should have refreshed the allow map from /start');
});
test('a running job adopts the allow rules from the task payload, keeping its queue',async()=>{
  const h=harness();
  await h.tick();await h.tick();
  const queued=h.state.job.queue.length;
  delete h.state.job.allow;
  h.setStatus('NEEDS_MANUAL_INPUT');   // paused mid-run: /start would reset the queue
  h.reload();
  await h.tick();
  assert.ok(h.state.job.allow,'should adopt the rules carried by the run');
  // Re-calling /start would have replaced the queue with a fresh one.
  assert.equal(h.state.job.queue.length,queued);
  assert.notEqual(h.state.job.paused,true);
});
test('a job with no allow rules anywhere pauses with an actionable message',async()=>{
  const h=harness();
  await h.tick();
  delete h.state.job.allow;
  h.setStatus('RUNNING');              // backend refuses /start once running
  h.setTaskAllow(false);               // and the run carries no rules either
  h.reload();
  await h.tick();
  assert.equal(h.state.job.paused,true);
  assert.match(h.state.job.message,/取消这个任务/);
  const before=h.calls.length;
  await h.tick();
  assert.equal(h.calls.length,before,'must not keep trying to navigate');
});
test('filling a greeting writes the box and never clicks anything',()=>{
  // The whole safety claim of assisted apply rests on this: write the message
  // box, touch no control. The fake element records any click it receives.
  const events=[], clicks=[];
  const box={tagName:'TEXTAREA',value:'',offsetParent:{},getClientRects:()=>[{}],
    dispatchEvent:e=>events.push(e.type),focus:()=>{},click:()=>clicks.push('box')};
  const document={querySelectorAll:sel=>sel==='textarea'?[box]:[]};
  const chromeStub={
    storage:{local:{get:async()=>({}),set:async()=>{},remove:async()=>{}}},
    alarms:{create:async()=>{},onAlarm:{addListener(){}}},
    runtime:{onInstalled:{addListener(){}},onStartup:{addListener(){}},onMessage:{addListener(){}}},
    tabs:{onUpdated:{addListener(){}}},
  };
  const sandbox={chrome:chromeStub,document,Event:class{constructor(t){this.type=t}},
    InputEvent:class{constructor(t){this.type=t}},URL,AbortSignal,console,setTimeout:()=>1,clearTimeout:()=>{}};
  let ctx;
  sandbox.importScripts=file=>vm.runInContext(fs.readFileSync(path.join(dir,file),'utf8'),ctx);
  ctx=vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(dir,'background.js'),'utf8'),ctx);

  assert.equal(sandbox.fillGreeting('您好，希望应聘贵司岗位。'),true);
  assert.equal(box.value,'您好，希望应聘贵司岗位。');
  assert.ok(events.includes('input'),'must dispatch input so the page framework registers it');
  assert.deepEqual(clicks,[],'must never click anything, least of all submit');
});
test('the apply command opens the posting, fills it and records "opened"',async()=>{
  const h=harness();
  const result=await h.command('apply',{application_id:'app_test'});
  assert.ok(h.calls.some(u=>u.includes('zhipin')),'should open the posting in a tab');
  assert.ok(h.posts.some(u=>u.endsWith('/api/applications/app_test/opened')),
    'should record that the page was opened');
  assert.equal(result.filled,true);
  assert.match(result.message,/自行点击发送/);
});
test('recording a submission is an explicit user action',async()=>{
  const h=harness();
  const result=await h.command('applied',{application_id:'app_test'});
  assert.ok(h.posts.some(u=>u.endsWith('/api/applications/app_test/submitted')));
  assert.match(result.message,/已记录/);
});
test('allow rules travel with the run, so a second site needs no extension change',()=>{
  const sandbox={URL,job:null};
  const ctx=vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(dir,'queue.js'),'utf8'),ctx);
  sandbox.job={seen:[],queue:[{url:'https://www.zhipin.com/web/geek/job',city:'北京',kind:'search'}],
    allow:{
      boss:{hosts:['www.zhipin.com'],path_pattern:'/job_detail/[A-Za-z0-9_-]+\\.html',login_pattern:'/web/user|/login'},
      zhaopin:{hosts:['www.zhaopin.com'],path_pattern:'/jobdetail/[A-Za-z0-9]+\\.htm',login_pattern:'passport\\.zhaopin\\.com'},
      job51:{hosts:['jobs.51job.com'],path_pattern:'/[a-z-]+/\\d+\\.html',login_pattern:'/pc/login'}}};
  const q=sandbox.CareerQueue,job=sandbox.job;
  // Both sites' hosts are acceptable because the backend listed them for this run.
  assert.equal(q.allowed(job,'https://www.zhipin.com/job_detail/a.html'),true);
  assert.equal(q.allowed(job,'https://www.zhaopin.com/jobdetail/Z1.htm'),true);
  // Anything not listed, or not https, is refused.
  assert.equal(q.allowed(job,'https://evil.test/job_detail/c.html'),false);
  assert.equal(q.allowed(job,'http://www.zhipin.com/job_detail/a.html'),false);
  assert.equal(q.allowed(job,'https://www.zhipin.com:444/job_detail/a.html'),false);
  // Detail-URL shape and login route are per site, not hardcoded.
  assert.equal(q.isDetail(job,'https://www.zhaopin.com/jobdetail/Z1.htm'),true);
  assert.equal(q.isDetail(job,'https://www.zhaopin.com/jobdetail/Z1.htm?x=1'),true);
  assert.equal(q.isDetail(job,'https://www.zhipin.com/job_detail/a.html'),true);
  assert.equal(q.isDetail(job,'https://jobs.51job.com/guangzhou/173657951.html'),true);
  assert.equal(q.isDetail(job,'https://jobs.51job.com/guangzhou-thq/173657296.html?from=search'),true);
  assert.equal(q.isDetail(job,'https://www.zhipin.com/web/geek/job'),false);
  assert.equal(q.isLogin(job,'https://passport.zhaopin.com/login'),true);
  assert.equal(q.isLogin(job,'https://www.zhipin.com/job_detail/a.html'),false);
  q.discovered(job,['https://www.zhipin.com/job_detail/n.html','https://www.zhaopin.com/jobdetail/Z2.htm','https://evil.test/job_detail/x.html'],'北京');
  assert.equal(job.queue.length,2); // two details queued, the foreign host dropped
});
