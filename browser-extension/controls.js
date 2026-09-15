const $=selector=>document.querySelector(selector);
async function command(action,extra={}){
  const result=await chrome.runtime.sendMessage({action,...extra});
  if(result.endpoint)$('#endpoint').value=result.endpoint;
  $('#status').textContent=result.error||result.message||'等待 CareerRadar 创建任务';
  $('#status').classList.toggle('error',!result.connected&&Boolean(result.error));
  const metrics=$('#metrics');
  if(result.metrics){
    const seconds=Math.floor(result.metrics.elapsed_ms/1000);
    const duration=seconds<60?`${seconds} 秒`:`${Math.floor(seconds/60)} 分 ${seconds%60} 秒`;
    metrics.textContent=`已采集 ${result.metrics.count} 条 · 已运行 ${duration} · 最近 1 分钟 ${result.metrics.per_minute} 条`;
    metrics.hidden=false;
  }else metrics.hidden=true;
}
for(const action of ['enable','resume','cancel']){
  $(`#${action}`).addEventListener('click',()=>command(action));
}
$('#connect').addEventListener('click',()=>command('configure',{endpoint:$('#endpoint').value}));
$('#use-current').addEventListener('click',async()=>{
  const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
  try{
    const url=new URL(tab?.url||'');
    if(url.protocol!=='http:'||!['127.0.0.1','localhost'].includes(url.hostname))throw new Error();
    $('#endpoint').value=url.origin;await command('configure',{endpoint:url.origin});
  }catch{$('#status').textContent='请先打开 CareerRadar 主程序页面，再点击此按钮';$('#status').classList.add('error');}
});
// ---- 投递辅助 -------------------------------------------------------------
// Built with DOM nodes and textContent rather than innerHTML: the company and
// title come from a scraped page, and must never be interpreted as markup.
async function loadApplications(){
  const endpoint=$('#endpoint').value.trim().replace(/\/+$/,'');
  const list=$('#apply-list');
  try{
    const response=await fetch(`${endpoint}/api/applications`);
    if(!response.ok)throw new Error();
    const items=(await response.json()).filter(item=>['READY','OPENED'].includes(item.status));
    if(!items.length){
      list.innerHTML='<p class="muted">还没有准备好的投递。在 CareerRadar 里对某个岗位点「帮我投递」。</p>';
      return;
    }
    list.innerHTML='';
    for(const item of items){
      const card=document.createElement('div');card.className='apply-card';
      const title=document.createElement('b');title.textContent=`${item.company} · ${item.title}`;
      const meta=document.createElement('small');
      meta.textContent=`打招呼语 ${item.greeting.length} 字${item.resume_export_id?' · 已关联定向简历':''}`;
      const open=document.createElement('button');
      open.textContent='打开岗位页并填入打招呼语';
      open.addEventListener('click',()=>command('apply',{application_id:item.application_id}));
      const done=document.createElement('button');done.className='secondary';
      done.textContent='我已完成投递';
      done.addEventListener('click',()=>command('applied',{application_id:item.application_id}));
      const skip=document.createElement('button');skip.className='secondary';
      skip.textContent='跳过';
      skip.addEventListener('click',()=>command('skip-application',{application_id:item.application_id}));
      card.append(title,meta,open,done,skip);
      list.append(card);
    }
  }catch{
    list.innerHTML='<p class="muted">无法读取投递列表，请先连接主程序。</p>';
  }
}
$('#connect').addEventListener('click',()=>{command('configure',{endpoint:$('#endpoint').value}).then(loadApplications);});
loadApplications();
command('status');
