const state={profile:null,runId:null,comparisonTask:null,comparison:null,jobs:[],conversationId:null,chatBusy:false,targetJob:null,tailoring:null,draftBundle:null,draft:null,resumeTemplate:'technical',exportIds:[]};
const $=selector=>document.querySelector(selector);
const $$=selector=>[...document.querySelectorAll(selector)];

function escapeHtml(value=''){return String(value).replace(/[&<>'"]/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]))}
function escapeAttr(value=''){return escapeHtml(value).replace(/`/g,'&#96;')}
function toast(message){const element=$('#toast');element.textContent=message;element.classList.add('show');setTimeout(()=>element.classList.remove('show'),3200)}
function step(number){$$('.panel').forEach(panel=>panel.classList.toggle('active',panel.id===`panel-${number}`));$$('.steps li').forEach((item,index)=>{item.classList.toggle('active',index+1===number);item.classList.toggle('done',index+1<number)});window.scrollTo({top:0,behavior:'smooth'})}
async function api(url,options={}){const response=await fetch(url,options);const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.detail||'请求失败');return data}

function setChatOpen(open){document.body.classList.toggle('chat-open',open);$('#chat-toggle').setAttribute('aria-expanded',String(open));localStorage.setItem('careerRadarChatOpen',open?'1':'0')}
$('#chat-toggle').addEventListener('click',()=>setChatOpen(!document.body.classList.contains('chat-open')));
$('#chat-close').addEventListener('click',()=>setChatOpen(false));
const savedChatOpen=localStorage.getItem('careerRadarChatOpen');
setChatOpen(savedChatOpen===null?window.innerWidth>900:savedChatOpen==='1');

async function createConversation(context={}){
  const conversation=await api('/api/conversations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(context)});
  state.conversationId=conversation.conversation_id;
  localStorage.setItem('careerRadarConversation',state.conversationId);
  renderConversation(conversation);
  return conversation;
}
async function ensureConversation(){
  if(state.conversationId)return refreshConversation();
  const saved=localStorage.getItem('careerRadarConversation');
  if(saved){try{const conversation=await api(`/api/conversations/${saved}`);state.conversationId=saved;renderConversation(conversation);return conversation}catch{}}
  return createConversation();
}
async function conversationForProfile(profileId,runId=null){
  let current=null;if(state.conversationId){try{current=await api(`/api/conversations/${state.conversationId}`)}catch{}}
  if(current&&current.profile_id===profileId){const update={profile_id:profileId};if(runId)update.run_id=runId;return bindConversationContext(update)}
  return createConversation({profile_id:profileId,run_id:runId});
}
async function bindConversationContext(update){
  if(!state.conversationId)await ensureConversation();
  const conversation=await api(`/api/conversations/${state.conversationId}/context`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(update)});
  renderConversation(conversation);return conversation;
}
async function refreshConversation(){if(!state.conversationId)return null;const conversation=await api(`/api/conversations/${state.conversationId}`);renderConversation(conversation);return conversation}

function chatQuickPrompts(conversation){
  if(!conversation.profile_id)return ['我适合什么岗位？','如何准备项目经历？'];
  if(!conversation.run_id)return ['我的优势和缺口是什么？','把目标城市改到杭州'];
  if(!conversation.comparison_id)return ['当前抓到了多少岗位？','开始分析已有岗位'];
  return ['为什么第一名得分最高？','我需要补哪些能力？','为第一名岗位修改简历','改到杭州并重新搜索'];
}
function renderCitation(citation,index){const source=citation.source_type==='resume'?'简历证据':'岗位证据';return `<details><summary>${source} ${index+1} · ${escapeHtml(citation.block_id)}</summary><blockquote>${escapeHtml(citation.quote)}</blockquote></details>`}
function actionValue(value){if(value===null||value===undefined||value==='')return '未设置';if(Array.isArray(value))return value.map(item=>typeof item==='object'?(item.role||JSON.stringify(item)):item).join('、')||'未设置';if(typeof value==='object')return JSON.stringify(value);return String(value)}
function actionLabel(kind){return {UPDATE_PROFILE:'修改候选人画像',RESTART_DISCOVERY:'调整条件并重新搜索',START_COMPARISON:'分析当前岗位',START_RESUME_TAILORING:'为目标岗位修改简历'}[kind]||kind}
function actionFieldLabel(field){return {selected_roles:'岗位方向',cities:'目标城市',salary_preference:'期望薪资',experience_years:'工作年限',work_type_preference:'工作类型'}[field]||field}
function actionStatus(status){return {PENDING:'等待确认',EXECUTING:'正在执行',EXECUTED:'已执行',REJECTED:'已取消',FAILED:'执行失败'}[status]||status}
function renderAction(action){
  const preview=action.preview||{},before=preview.before||{},after=preview.after||{};
  const keys=[...new Set([...Object.keys(before),...Object.keys(after)])];
  const changes=action.kind==='START_RESUME_TAILORING'?`<div class="action-change"><div><small>目标公司</small><b>${escapeHtml(preview.company||'')}</b></div><span>→</span><div><small>目标岗位</small><b>${escapeHtml(preview.title||'')}</b></div></div>`:keys.length?keys.map(key=>`<div class="action-change"><div><small>当前${escapeHtml(actionFieldLabel(key))}</small><b>${escapeHtml(actionValue(before[key]))}</b></div><span>→</span><div><small>修改后</small><b>${escapeHtml(actionValue(after[key]))}</b></div></div>`).join(''):`<div class="action-change"><div><small>当前任务</small><b>${escapeHtml(preview.current_run_id||'无')}</b></div><span>→</span><div><small>确认后</small><b>${escapeHtml(action.kind==='START_COMPARISON'?'生成岗位排名':'创建新任务')}</b></div></div>`;
  const taskInfo=preview.current_run_id?`当前任务 ${escapeHtml(preview.current_run_id)} · ${escapeHtml(preview.current_run_status||'未知')} · ${Number(preview.current_job_count||0)} 个岗位`:'';
  const active=['QUEUED','RUNNING','NEEDS_MANUAL_INPUT'].includes(preview.current_run_status);
  const impact=action.kind==='RESTART_DISCOVERY'?(active?'确认后将停止当前任务并创建新的浏览器采集任务；旧岗位和报告继续保留。':'确认后将创建新的浏览器采集任务；旧岗位和报告继续保留。'):action.kind==='START_COMPARISON'?(active?`确认后将停止当前采集，并使用已有 ${Number(preview.current_job_count||0)} 个岗位生成排名。`:`确认后将使用已有 ${Number(preview.current_job_count||0)} 个岗位生成排名。`):action.kind==='START_RESUME_TAILORING'?'确认后会对照岗位要求提出最多 5 个事实问题，不会立即改写原简历。':'';
  const buttons=action.status==='PENDING'?`<div class="action-buttons"><button class="action-confirm" data-confirm-action="${escapeAttr(action.action_id)}">确认执行</button><button class="action-reject" data-reject-action="${escapeAttr(action.action_id)}">取消</button></div>`:'';
  const result=action.status==='FAILED'?`<p>${escapeHtml(action.result?.error||'操作未执行')}</p>`:action.status==='EXECUTED'?`<p>${escapeHtml(action.result?.message||'操作已执行')}</p>`:'';
  return `<article class="chat-action ${action.status.toLowerCase()}"><h4>${escapeHtml(actionLabel(action.kind))} · ${escapeHtml(actionStatus(action.status))}</h4><p>${escapeHtml(preview.user_intent||'')}</p>${changes}${taskInfo?`<p>${taskInfo}</p>`:''}${impact?`<p>${impact}</p>`:''}${result}${buttons}</article>`;
}
function renderConversation(conversation){
  const count=state.jobs.length;
  $('#chat-context-label').textContent=conversation.profile_id?(conversation.run_id?`已关联简历 · ${count} 个岗位`:'已关联简历 · 尚未搜索'):'通用咨询 · 未使用个人简历';
  $('#chat-quick').innerHTML=chatQuickPrompts(conversation).map(text=>`<button type="button">${escapeHtml(text)}</button>`).join('');
  $$('#chat-quick button').forEach(button=>button.addEventListener('click',()=>sendChat(button.textContent)));
  const content=[];
  for(const message of conversation.messages||[]){const failed=!['SUCCEEDED','PENDING'].includes(message.status);const agentLabel=message.citations?.length?'PydanticAI · LangGraph · 已校验证据':'PydanticAI · LangGraph';content.push(`<article class="chat-message ${message.role} ${failed?'failed':''}"><div class="message-content">${escapeHtml(message.content)}</div>${message.citations?.length?`<div class="chat-citations">${message.citations.map(renderCitation).join('')}</div>`:''}<small class="message-meta">${message.role==='assistant'?(message.source==='langgraph'?agentLabel:message.source==='fallback'?'本地备用分析':'系统消息'):'你'}</small></article>`)}
  for(const action of conversation.actions||[])content.push(renderAction(action));
  $('#chat-messages').innerHTML=content.join('')||'<div class="chat-empty"><span>✦</span><b>可以直接和我讨论求职问题</b><p>提交简历后，我会结合简历证据、真实岗位和固定评分回答。</p></div>';
  $$('[data-confirm-action]').forEach(button=>button.addEventListener('click',()=>confirmAction(button.dataset.confirmAction)));
  $$('[data-reject-action]').forEach(button=>button.addEventListener('click',()=>rejectAction(button.dataset.rejectAction)));
  const box=$('#chat-messages');box.scrollTop=box.scrollHeight;
}
async function sendChat(text){
  const message=String(text||'').trim();if(!message||state.chatBusy)return;
  try{await ensureConversation();state.chatBusy=true;setChatOpen(true);$('#chat-input').value='';$('#chat-form button').disabled=true;$('#chat-typing').classList.remove('hidden');const result=await api(`/api/conversations/${state.conversationId}/messages`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message})});await refreshConversation();pollChatTurn(result.task_id)}catch(error){state.chatBusy=false;$('#chat-form button').disabled=false;$('#chat-typing').classList.add('hidden');toast(error.message)}
}
async function pollChatTurn(taskId){
  try{const result=await api(`/api/chat-turns/${taskId}`);if(['QUEUED','RUNNING'].includes(result.task.status))return setTimeout(()=>pollChatTurn(taskId),1000);state.chatBusy=false;$('#chat-form button').disabled=false;$('#chat-typing').classList.add('hidden');await refreshConversation();if(!['SUCCEEDED','FAILED_VALIDATION'].includes(result.task.status))toast(result.task.error||result.task.message)}catch(error){state.chatBusy=false;$('#chat-form button').disabled=false;$('#chat-typing').classList.add('hidden');toast(error.message)}
}
async function confirmAction(actionId){
  try{const action=await api(`/api/chat-actions/${actionId}/confirm`,{method:'POST'}),result=action.result||{};if(result.profile_id){state.profile=await api(`/api/resumes/${result.profile_id}/profile`);renderProfile()}if(result.run_id){state.runId=result.run_id;state.comparisonTask=null;state.comparison=null;state.jobs=[];history.replaceState(null,'',`?task=${state.runId}`);step(3);pollDiscovery()}if(action.kind==='UPDATE_PROFILE')step(2);if(action.kind==='START_COMPARISON'&&result.task_id){state.comparisonTask=result.task_id;pollComparison()}if(action.kind==='START_RESUME_TAILORING'&&result.tailoring_id){await loadTailoring(result.tailoring_id);step(6)}await refreshConversation();toast('操作已确认并执行')}catch(error){await refreshConversation().catch(()=>{});toast(error.message)}
}
async function rejectAction(actionId){try{await api(`/api/chat-actions/${actionId}/reject`,{method:'POST'});await refreshConversation();toast('已取消这项操作')}catch(error){toast(error.message)}}
$('#chat-form').addEventListener('submit',event=>{event.preventDefault();sendChat($('#chat-input').value)});
$('#chat-input').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendChat(event.currentTarget.value)}});

function targetSummary(target){return `<b>${escapeHtml(target.company)} · ${escapeHtml(target.title)}</b><p>${escapeHtml(target.source_type==='snapshot'?'来自当前岗位快照':target.source_type==='boss_url'?'来自 BOSS 公开职位链接':'来自粘贴的岗位描述')} · ${target.required_skills?.length?`要求 ${escapeHtml(target.required_skills.join('、'))}`:'技能要求待从原文判断'}</p>`}
async function createTailoringForTarget(targetJobId){
  const tailoring=await api('/api/resume-tailorings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({profile_id:state.profile.profile_id,target_job_id:targetJobId,conversation_id:state.conversationId})});
  await loadTailoring(tailoring.tailoring_id);step(6);
}
async function beginTailoring(snapshotId){
  try{const result=await api('/api/target-jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({profile_id:state.profile.profile_id,source_type:'snapshot',snapshot_id:snapshotId})});await createTailoringForTarget(result.target_job_id)}catch(error){toast(error.message)}
}
async function loadTailoring(tailoringId,updateHistory=true){
  const result=await api(`/api/resume-tailorings/${tailoringId}`);state.tailoring=result.tailoring;state.targetJob=result.target_job;$('#tailor-target').innerHTML=targetSummary(state.targetJob);renderTailoringQuestions();if(state.tailoring.draft_id)await loadDraft(state.tailoring.draft_id);
  if(updateHistory)history.replaceState(null,'',`?tailoring=${encodeURIComponent(tailoringId)}`);
}
function renderTailoringQuestions(){
  const form=$('#tailoring-questions'),list=$('#question-list');if(!state.tailoring)return;
  if(state.tailoring.status==='SUCCEEDED'){form.classList.add('hidden');return}
  form.classList.remove('hidden');
  if(!state.tailoring.questions.length){list.innerHTML='<p>现有简历证据已经覆盖主要岗位要求，可以直接生成。</p>';return}
  list.innerHTML=state.tailoring.questions.map(item=>`<div class="tailor-question" data-question-id="${escapeAttr(item.question_id)}"><b>${escapeHtml(item.question)}</b><textarea maxlength="1000" placeholder="只填写真实经历；建议包含场景、职责和结果">${escapeHtml(item.answer||'')}</textarea><label class="skip-line"><input type="checkbox" ${item.status==='SKIPPED'?'checked':''}>没有相关经历，跳过</label></div>`).join('');
  $$('.tailor-question input').forEach(box=>box.addEventListener('change',()=>{const textarea=box.closest('.tailor-question').querySelector('textarea');textarea.disabled=box.checked;if(box.checked)textarea.value=''}));
}
$('#target-job-form').addEventListener('submit',async event=>{
  event.preventDefault();if(!state.profile)return toast('请先提交简历');
  try{const result=await api('/api/target-jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({profile_id:state.profile.profile_id,source_type:'pasted',company:$('#target-company').value,title:$('#target-title').value,content:$('#target-content').value,url:$('#target-url').value})});await createTailoringForTarget(result.target_job_id)}catch(error){toast(error.message)}
});
$('#capture-target').addEventListener('click',async()=>{
  if(!state.profile)return toast('请先提交简历');const url=$('#target-url').value.trim();if(!url)return toast('请填写 BOSS 公开职位链接');
  try{const result=await api('/api/target-jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({profile_id:state.profile.profile_id,source_type:'boss_url',url})});$('#tailor-target').innerHTML='<b>正在读取目标岗位</b><p>如果页面要求登录或验证，可以改为粘贴岗位描述。</p>';step(6);pollTargetJob(result.task_id,result.target_job_id)}catch(error){toast(error.message)}
});
async function pollTargetJob(taskId,targetJobId){
  try{const task=await api(`/api/tasks/${taskId}`);if(['QUEUED','RUNNING'].includes(task.status))return setTimeout(()=>pollTargetJob(taskId,targetJobId),1000);if(task.status!=='SUCCEEDED')throw new Error(task.error||task.message);await createTailoringForTarget(targetJobId)}catch(error){toast(error.message);$('#tailor-target').innerHTML='<b>职位链接暂时无法读取</b><p>请在下方粘贴这份岗位描述继续。</p>'}
}
$('#tailoring-questions').addEventListener('submit',async event=>{
  event.preventDefault();const answers={};
  $$('.tailor-question').forEach(item=>{const skipped=item.querySelector('input').checked;answers[item.dataset.questionId]=skipped?null:item.querySelector('textarea').value.trim()||null});
  try{const tailoring=await api(`/api/resume-tailorings/${state.tailoring.tailoring_id}/answers`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({answers})});state.tailoring=tailoring;if(tailoring.status!=='READY')return toast('请回答或跳过全部问题');const queued=await api(`/api/resume-tailorings/${tailoring.tailoring_id}/confirm`,{method:'POST'});$('#tailoring-questions').classList.add('hidden');$('#tailoring-progress').classList.remove('hidden');pollTailoring(queued.task_id,tailoring.tailoring_id)}catch(error){toast(error.message)}
});
async function pollTailoring(taskId,tailoringId){
  try{const task=await api(`/api/tasks/${taskId}`);if(['QUEUED','RUNNING'].includes(task.status))return setTimeout(()=>pollTailoring(taskId,tailoringId),1000);$('#tailoring-progress').classList.add('hidden');if(task.status!=='SUCCEEDED')throw new Error(task.error||task.message);await loadTailoring(tailoringId);toast('定向简历已生成')}catch(error){$('#tailoring-progress').classList.add('hidden');toast(error.message)}
}
function draftBullets(draft){const values=[...(draft.summary||[]),...(draft.skills||[])];for(const section of draft.sections||[]){values.push(...(section.bullets||[]));for(const entry of section.entries||[])values.push(...(entry.bullets||[]))}return values}
function bulletEditor(item){const label=item.provenance==='uploaded_resume'?'原简历证据':item.provenance==='user_confirmed'?'已确认补充':'用户编辑';return `<div class="resume-bullet-editor"><textarea maxlength="500" data-bullet-id="${escapeAttr(item.bullet_id)}">${escapeHtml(item.text)}</textarea><small>${label}</small></div>`}
function renderDraft(){
  const draft=state.draft;if(!draft)return;const technical=state.resumeTemplate==='technical';
  const summary=`<h2>个人概述</h2>${draft.summary.map(bulletEditor).join('')}`,skills=`<h2>专业技能</h2>${draft.skills.map(bulletEditor).join('')}`;
  const sections=draft.sections.map(section=>`<h2>${escapeHtml(section.title)}</h2>${section.bullets.map(bulletEditor).join('')}${section.entries.map(entry=>`<h3>${escapeHtml(entry.heading)} <small>${escapeHtml(entry.subheading)} ${escapeHtml(entry.date_range)}</small></h3>${entry.bullets.map(bulletEditor).join('')}`).join('')}`).join('');
  $('#resume-preview').className=`resume-preview ${state.resumeTemplate}`;$('#resume-preview').innerHTML=`<div class="resume-contact"><input id="resume-name" class="resume-name" value="${escapeAttr(draft.contact.name)}" placeholder="姓名"><input id="resume-phone" value="${escapeAttr(draft.contact.phone)}" placeholder="电话"><input id="resume-email" value="${escapeAttr(draft.contact.email)}" placeholder="邮箱"><input id="resume-location" value="${escapeAttr(draft.contact.location)}" placeholder="所在城市"></div><input id="resume-headline" class="resume-headline" value="${escapeAttr(draft.headline)}">${technical?skills+summary+sections:summary+sections+skills}`;
  $('#resume-diff').innerHTML=draft.change_log?.length?`<details><summary>查看本版本修改记录（${draft.change_log.length}）</summary>${draft.change_log.map(item=>item.kind==='generated'?`<p>根据 ${escapeHtml(item.target||'目标岗位')} 生成</p>`:`<p><del>${escapeHtml(item.before||'新增内容')}</del><br><ins>${escapeHtml(item.after||'')}</ins></p>`).join('')}</details>`:'';
}
async function loadDraft(draftId){state.draftBundle=await api(`/api/resume-drafts/${draftId}`);state.draft=state.draftBundle.current;$('#resume-workbench').classList.remove('hidden');$('#resume-version').innerHTML=state.draftBundle.versions.map(item=>`<option value="${escapeAttr(item.version_id)}" ${item.version_id===state.draft.version_id?'selected':''}>版本 ${item.version} · ${escapeHtml(item.source)}</option>`).join('');renderDraft()}
$('#resume-version').addEventListener('change',event=>{state.draft=state.draftBundle.versions.find(item=>item.version_id===event.target.value);const latest=state.draftBundle.current.version_id===state.draft.version_id;$('#save-resume-version').textContent=latest?'保存为新版本':'将此版本恢复为新版本';renderDraft()});
$$('[data-template]').forEach(button=>button.addEventListener('click',()=>{state.resumeTemplate=button.dataset.template;$$('[data-template]').forEach(item=>item.classList.toggle('active',item===button));renderDraft()}));
$('#save-resume-version').addEventListener('click',async()=>{
  if(!state.draft)return;const value=JSON.parse(JSON.stringify(state.draft));value.contact={...value.contact,name:$('#resume-name').value.trim(),phone:$('#resume-phone').value.trim(),email:$('#resume-email').value.trim(),location:$('#resume-location').value.trim()};value.headline=$('#resume-headline').value.trim();const index=Object.fromEntries(draftBullets(value).map(item=>[item.bullet_id,item]));$$('[data-bullet-id]').forEach(item=>{if(index[item.dataset.bulletId])index[item.dataset.bulletId].text=item.value.trim()});
  try{const saved=await api(`/api/resume-drafts/${value.draft_id}/versions`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});await loadDraft(saved.draft_id);toast(`已保存版本 ${saved.version}`)}catch(error){toast(error.message)}
});
$('#export-resume').addEventListener('click',async()=>{if(!state.draft)return;try{const result=await api(`/api/resume-drafts/${state.draft.draft_id}/exports`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({templates:['technical','business']})});state.exportIds=result.exports.map(item=>item.export_id);$('#export-links').innerHTML='<div class="export-card">正在生成两套 DOCX 和 PDF…</div>';pollExports(result.task_id)}catch(error){toast(error.message)}});
async function pollExports(taskId){try{const task=await api(`/api/tasks/${taskId}`);if(['QUEUED','RUNNING'].includes(task.status))return setTimeout(()=>pollExports(taskId),1000);if(task.status!=='SUCCEEDED')throw new Error(task.error||task.message);const exports=await Promise.all(state.exportIds.map(id=>api(`/api/resume-exports/${id}`)));$('#export-links').innerHTML=exports.map(item=>`<div class="export-card"><b>${item.template==='technical'?'简洁技术版':'紧凑商务版'}</b><br><a href="/api/resume-exports/${item.export_id}/download?format=docx">下载 DOCX</a><a href="/api/resume-exports/${item.export_id}/download?format=pdf">下载 PDF</a></div>`).join('');toast('两套简历文件已生成')}catch(error){toast(error.message);$('#export-links').innerHTML=`<div class="export-card">${escapeHtml(error.message)}</div>`}}

$('#resume-file').addEventListener('change',event=>{const file=event.target.files[0];if(file)event.target.closest('label').querySelector('b').textContent=file.name});
$('#resume-form').addEventListener('submit',async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;button.firstChild.textContent='Agent 正在分析… ';
  try{state.profile=await api('/api/resumes',{method:'POST',body:new FormData(event.target)});state.runId=null;state.comparisonTask=null;state.comparison=null;state.jobs=[];state.targetJob=null;state.tailoring=null;state.draftBundle=null;state.draft=null;state.exportIds=[];history.replaceState(null,'',location.pathname);await createConversation({profile_id:state.profile.profile_id});renderProfile();step(2)}catch(error){toast(error.message)}finally{button.disabled=false;button.firstChild.textContent='开始分析 '}
});
function renderProfile(){
  const profile=state.profile;if(!profile)return;
  $('#profile-strip').innerHTML=[...(profile.skills||[]),profile.education,profile.experience_years!=null?`${profile.experience_years} 年经验`:null].filter(Boolean).map(value=>`<span>${escapeHtml(value)}</span>`).join('');$('#years').value=profile.experience_years??'';$('#salary').value=profile.salary_preference??'';$$('input[name=cities]').forEach(input=>{input.checked=(profile.cities||[]).includes(input.value)});
  const chosen=new Set((profile.selected_roles||[]).map(item=>item.role));
  $('#role-cards').innerHTML=profile.recommendations.map((role,index)=>{const selected=chosen.size?chosen.has(role.role):index<2;return `<article class="role-card ${selected?'selected':''}" data-index="${index}"><input class="choose" type="checkbox" ${selected?'checked':''} aria-label="选择岗位"><span class="confidence">${escapeHtml(role.confidence)}置信度</span><input class="role-name" type="text" value="${escapeAttr(role.role)}" aria-label="岗位名称"><input class="keywords" type="text" value="${escapeAttr(role.keywords.join('，'))}" aria-label="搜索关键词"><textarea class="rationale" readonly>${escapeHtml(role.rationale)}</textarea><button type="button" class="evidence-link" data-citations='${escapeAttr(JSON.stringify(role.citations))}'>查看 ${role.citations.length} 条简历证据</button></article>`}).join('');
  $$('.choose').forEach(box=>box.addEventListener('change',()=>{if($$('.choose:checked').length>2){box.checked=false;toast('最多选择两个岗位方向')}box.closest('.role-card').classList.toggle('selected',box.checked)}));$$('.evidence-link').forEach(button=>button.addEventListener('click',()=>toast(JSON.parse(button.dataset.citations).map(item=>`“${item.quote}”`).join(' · '))));
}
$('#confirm-form').addEventListener('submit',async event=>{
  event.preventDefault();const roleCards=$$('.role-card').filter(card=>card.querySelector('.choose').checked),cities=$$('input[name=cities]:checked').map(input=>input.value);if(!roleCards.length||!cities.length)return toast('请至少选择一个岗位方向和一个城市');if(cities.length>2)return toast('最多选择两个城市');
  const selected_roles=roleCards.map(card=>{const base=state.profile.recommendations[Number(card.dataset.index)];return {...base,role:card.querySelector('.role-name').value.trim(),keywords:card.querySelector('.keywords').value.split(/[，,]/).map(value=>value.trim()).filter(Boolean).slice(0,4)}});
  try{state.profile=await api(`/api/profiles/${state.profile.profile_id}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({selected_roles,cities,salary_preference:$('#salary').value||null,experience_years:$('#years').value?Number($('#years').value):null})});const run=await api('/api/discovery-runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({profile_id:state.profile.profile_id})});state.runId=run.task_id;history.replaceState(null,'',`?task=${state.runId}`);await bindConversationContext({profile_id:state.profile.profile_id,run_id:state.runId,comparison_id:null});step(3);pollDiscovery()}catch(error){toast(error.message)}
});
async function pollDiscovery(){
  try{const task=await api(`/api/tasks/${state.runId}`);$('#progress-value').textContent=`${task.progress}%`;$('#progress-bar').style.width=`${task.progress}%`;$('#progress-message').textContent=task.message;$('#progress-meta').textContent=`任务 ${state.runId} · ${task.status}`;$('#capture-task-id').textContent=state.runId;if(task.payload.mode==='browser'||task.status==='NEEDS_MANUAL_INPUT'){$('#manual-form').classList.remove('hidden');$('#manual-reason').textContent=task.message}state.jobs=await api(`/api/jobs?run_id=${state.runId}`);$('#login-status').textContent=`已采集 ${state.jobs.length} 个岗位`;$('#login-status').classList.toggle('connected',state.jobs.length>0);if(['QUEUED','RUNNING','NEEDS_MANUAL_INPUT'].includes(task.status))return setTimeout(pollDiscovery,2000);if(task.status!=='SUCCEEDED'){$('#retry-task').classList.remove('hidden');throw new Error(task.error||task.message)}await startComparison()}catch(error){toast(error.message)}
}
$('#retry-task').addEventListener('click',async()=>{try{const retry=await api(`/api/tasks/${state.runId}/retry`,{method:'POST'});state.runId=retry.task_id;history.replaceState(null,'',`?task=${state.runId}`);await bindConversationContext({run_id:state.runId,comparison_id:null});$('#retry-task').classList.add('hidden');$('#progress-value').textContent='0%';$('#progress-bar').style.width='0';pollDiscovery()}catch(error){toast(error.message)}});
$('#copy-task-id').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(state.runId);toast('任务编号已复制')}catch{toast(`任务编号：${state.runId}`)}});
$('#refresh-capture-count').addEventListener('click',async()=>{try{state.jobs=await api(`/api/jobs?run_id=${state.runId}`);$('#login-status').textContent=`已采集 ${state.jobs.length} 个岗位`;$('#login-status').classList.toggle('connected',state.jobs.length>0);await refreshConversation()}catch(error){toast(error.message)}});
$('#finish-browser-capture').addEventListener('click',async()=>{const button=$('#finish-browser-capture');button.disabled=true;try{const result=await api(`/api/browser-captures/${state.runId}/finish`,{method:'POST'});$('#login-status').textContent=`已提交 ${result.count} 个岗位，正在生成排名`;$('#login-status').classList.add('connected')}catch(error){toast(error.message);button.disabled=false}});
$('#manual-form').addEventListener('submit',async event=>{event.preventDefault();try{await api(`/api/jobs/${state.runId}/manual-content`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:$('#manual-url').value,content:$('#manual-content').value,city:state.profile.cities[0]})});state.jobs=await api(`/api/jobs?run_id=${state.runId}`);await startComparison()}catch(error){toast(error.message)}});
async function startComparison(){if(state.comparisonTask)return;state.comparisonTask='pending';try{const result=await api('/api/comparisons',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({profile_id:state.profile.profile_id,run_id:state.runId})});state.comparisonTask=result.task_id;if(result.comparison_id)state.comparisonId=result.comparison_id;pollComparison()}catch(error){state.comparisonTask=null;throw error}}
$('#cancel-browser-task').addEventListener('click',async()=>{try{await api(`/api/tasks/${state.runId}`,{method:'DELETE'});toast('已请求取消任务')}catch(error){toast(error.message)}});
async function pollComparison(){try{const task=await api(`/api/tasks/${state.comparisonTask}`);if(['QUEUED','RUNNING'].includes(task.status))return setTimeout(pollComparison,2000);if(task.status!=='SUCCEEDED')throw new Error(task.error||task.message);const comparison=await api(`/api/tasks/${state.comparisonTask}/comparison`);state.comparison=comparison;await bindConversationContext({comparison_id:comparison.comparison_id});renderResults(comparison);step(4)}catch(error){toast(error.message)}}
function renderResults(comparison){
  const jobs=Object.fromEntries(state.jobs.map(job=>[job.snapshot_id,job]));$('#ranking-list').innerHTML=comparison.rankings.map((ranking,index)=>{const job=jobs[ranking.job_id]||{};return `<article class="rank-card"><div class="rank-num">${String(index+1).padStart(2,'0')}</div><div><h3>${escapeHtml(job.title||'岗位')}</h3><p>${escapeHtml(job.company||'')} · ${escapeHtml(job.city||'')} · ${escapeHtml(job.salary||'薪资未识别')}</p><p>${escapeHtml(ranking.explanation)}</p><div class="skill-row"><b>已匹配 ${escapeHtml(ranking.matched_skills.join('、')||'无法判断')}</b> · <em>待补 ${escapeHtml(ranking.missing_skills.join('、')||'暂无明确缺口')}</em></div>${ranking.risks.map(item=>`<p class="risk">⚠ ${escapeHtml(item)}</p>`).join('')}<button type="button" class="evidence-link" data-quotes='${escapeAttr(JSON.stringify(ranking.citations.map(item=>item.quote)))}'>查看证据引用</button><button type="button" class="tailor-button" data-tailor-job="${escapeAttr(ranking.job_id)}">为这个岗位修改简历</button></div><div class="score">${ranking.total}<small>适配分</small></div></article>`}).join('');$$('[data-quotes]').forEach(button=>button.addEventListener('click',()=>toast(JSON.parse(button.dataset.quotes).map(item=>`“${item}”`).join(' · '))));$$('[data-tailor-job]').forEach(button=>button.addEventListener('click',()=>beginTailoring(button.dataset.tailorJob)));$('#plan-list').innerHTML=comparison.action_plan.map(item=>`<li>${escapeHtml(item.replace(/^第\s*\d+\s*天[：:]?/,'').trim())}</li>`).join('');
}
$('#view-plan').addEventListener('click',()=>step(5));
$('#restart').addEventListener('click',()=>{state.profile=null;state.runId=null;state.comparison=null;state.jobs=[];history.replaceState(null,'','/');step(1)});
async function restoreRun(taskId,conversation=null){
  const task=await api(`/api/tasks/${taskId}`);
  state.profile=await api(`/api/resumes/${task.payload.profile_id}/profile`);state.runId=taskId;state.jobs=await api(`/api/jobs?run_id=${taskId}`);
  if(!conversation||conversation.profile_id!==state.profile.profile_id||conversation.run_id!==taskId)conversation=await conversationForProfile(state.profile.profile_id,taskId);
  renderProfile();history.replaceState(null,'',`?task=${taskId}`);
  if(conversation?.comparison_id){state.comparison=await api(`/api/comparisons/${conversation.comparison_id}`);renderResults(state.comparison);step(4);return}
  step(3);pollDiscovery();
}
async function restoreTask(){const params=new URLSearchParams(location.search),tailoringId=params.get('tailoring'),taskId=params.get('task');if(tailoringId){try{const result=await api(`/api/resume-tailorings/${tailoringId}`);state.profile=await api(`/api/resumes/${result.tailoring.profile_id}/profile`);await conversationForProfile(state.profile.profile_id);renderProfile();await loadTailoring(tailoringId,false);step(6);return}catch(error){toast(`无法恢复定向简历：${error.message}`)}}try{const conversation=await ensureConversation();if(!taskId&&conversation.run_id)taskId=conversation.run_id;if(taskId){await restoreRun(taskId,conversation);return}if(conversation.profile_id){state.profile=await api(`/api/resumes/${conversation.profile_id}/profile`);renderProfile();step(2)}}catch(error){toast(`无法恢复任务：${error.message}`);if(taskId)history.replaceState(null,'',location.pathname)}}
restoreTask();
