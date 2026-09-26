const $ = selector => document.querySelector(selector);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const formatDate = value => value ? new Date(value).toLocaleDateString('en-NZ',{day:'numeric',month:'short',year:'numeric'}) : 'Not supplied';
const categories={litter:'Litter & rubbish',suspected_industrial:'Suspected industrial',oil_or_fuel:'Possible oil or fuel',suspected_wastewater:'Suspected wastewater',unusual_water:'Unusual water appearance',other:'Other concern',unclassified:'Unclassified concern'};
let adminUser=null, csrfToken=null, dataset=null, page=0, recordId=null, busy=false, requestVersion=0, setupMode=false;
const PAGE_SIZE=20;
function forgetData(){
  requestVersion++;dataset=null;recordId=null;page=0;
  for(const id of ['admin-metrics','admin-records','admin-category-chart','admin-trend-chart','admin-alerts','admin-record-body','admin-vouchers','admin-feedback'])$('#'+id).replaceChildren();
  $('#admin-password').hidden=true;$('#admin-password-form').reset();
  $('#admin-record-dialog').close();$('#admin-workspace').hidden=true;$('#admin-auth').hidden=false;$('#admin-who').textContent='';$('#admin-status').textContent='';
}
function signedOut(message=''){adminUser=null;csrfToken=null;forgetData();$('#admin-sign-out').hidden=true;$('#admin-access-note').textContent=message;}
async function api(path,options={}){
  let response;
  try{response=await fetch(path,{...options,credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json',...(csrfToken&&options.method&&options.method!=='GET'?{'X-CSRF-Token':csrfToken}:{}),...options.headers},signal:AbortSignal.timeout(30000)});}catch{throw new Error('Could not reach the server. Please try again.');}
  const data=await response.json().catch(()=>({}));
  if(!response.ok){
    if(path.startsWith('/api/admin/')&&(response.status===401||response.status===403)){forgetData();if(response.status===401){adminUser=null;csrfToken=null;$('#admin-sign-out').hidden=true;}}
    const error=new Error(data.error||'The request could not be completed.');error.status=response.status;throw error;
  }
  return data;
}
function query(){const params=new URLSearchParams();for(const name of ['community','review','status']){const value=$('#filter-'+name).value;if(value)params.set(name,value);}return params;}
function setSetup(value){setupMode=value;$('#admin-setup-form').hidden=!value;$('#admin-login-form').hidden=value;$('#admin-toggle-setup').textContent=value?'Back to administrator sign in':'I have an administrator invitation';$('#admin-auth-error').textContent='';}
$('#admin-toggle-setup').addEventListener('click',()=>{if(!busy)setSetup(!setupMode);});
async function showSession(session){
  adminUser=session.user;csrfToken=session.csrfToken;$('#admin-sign-out').hidden=!adminUser;
  if(!adminUser){signedOut();return;}
  if(adminUser.role!=='admin'){forgetData();$('#admin-auth-error').textContent='This account does not have administrator access. Sign out to use another account.';return;}
  if(adminUser.password_change_required){forgetData();$('#admin-auth').hidden=true;$('#admin-password').hidden=false;return;}
  $('#admin-password').hidden=true;
  $('#admin-auth').hidden=true;$('#admin-workspace').hidden=false;$('#admin-who').textContent=`Signed in as ${adminUser.display_name}`;$('#admin-auth-error').textContent='';
  await Promise.all([loadData(),loadOperations()]);
}
$('#admin-password-form').addEventListener('submit',async event=>{
  event.preventDefault();if(busy)return;const form=event.target,payload=Object.fromEntries(new FormData(form));$('#admin-password-error').textContent='';
  if(payload.new_password!==payload.confirmation){$('#admin-password-error').textContent='The new passwords do not match.';return;}
  delete payload.confirmation;busy=true;const button=form.querySelector('button');button.disabled=true;
  try{const session=await api('/api/auth/password',{method:'POST',body:JSON.stringify(payload)});form.reset();await showSession(session);}catch(error){$('#admin-password-error').textContent=error.message;}finally{busy=false;button.disabled=false;}
});
for(const selector of ['#admin-login-form','#admin-setup-form'])$(selector).addEventListener('submit',async event=>{
  event.preventDefault();if(busy)return;busy=true;$('#admin-auth-error').textContent='';
  const form=event.target, data=Object.fromEntries(new FormData(form)),controls=[...form.elements];controls.forEach(control=>control.disabled=true);
  try{const session=await api(form.id==='admin-setup-form'?'/api/admin/setup':'/api/auth/login',{method:'POST',body:JSON.stringify(data)});form.reset();await showSession(session);}
  catch(error){$('#admin-auth-error').textContent=error.message;}
  finally{busy=false;controls.forEach(control=>control.disabled=false);}
});
$('#admin-sign-out').addEventListener('click',async()=>{try{await api('/api/auth/logout',{method:'POST',body:'{}'});signedOut('You have signed out.');}catch(error){$('#admin-error').textContent=error.message;}});
function bars(selector,totals){
  const entries=Object.entries(totals).sort(selector==='#admin-trend-chart'?(a,b)=>a[0].localeCompare(b[0]):(a,b)=>b[1]-a[1]),max=Math.max(1,...entries.map(x=>x[1]));
  $(selector).innerHTML=entries.length?entries.map(([name,count])=>`<div class="admin-bar"><span>${escapeHTML(categories[name]||name)}</span><div class="admin-bar-track"><div class="admin-bar-fill" style="width:${count/max*100}%"></div></div><b>${count}</b></div>`).join(''):'<p class="admin-note">No observations match this selection.</p>';
}
function render(){
  if(!dataset)return;const records=dataset.observations,summary=dataset.summary;
  const approved=records.filter(r=>r.export_eligible).length,pending=records.filter(r=>r.review_status==='pending').length,guests=records.filter(r=>r.rewards_waived).length;
  const metrics=[['Selected observations',records.length,'Original photos and words preserved'],['Awaiting review',pending,'AI output is a suggestion'],['Eligible reviewed evidence',approved,'Approved and passing quality checks'],['Guest contributions',guests,'Rewards permanently waived']];
  $('#admin-metrics').innerHTML=metrics.map(([title,count,note])=>`<article class="admin-metric"><span>${title}</span><strong>${count}</strong><small>${note}</small></article>`).join('');
  const pollution={},months={};for(const record of records){for(const category of record.analysis?.pollution_types||[])pollution[category]=(pollution[category]||0)+1;const month=record.created.slice(0,7);months[month]=(months[month]||0)+1;}
  bars('#admin-category-chart',pollution);bars('#admin-trend-chart',months);
  $('#admin-record-count').textContent=`${records.length} records · ${approved} eligible for a reviewed dataset`;
  $('#admin-alerts').innerHTML=(dataset.alerts||[]).length?dataset.alerts.map(alert=>`<article class="admin-alert"><div><strong>${escapeHTML(alert.community)} · ${escapeHTML(categories[alert.category]||alert.category)}</strong><p>${alert.evidence?.distinct_photos||0} different photos · ${alert.evidence?.contributing_accounts||0} contributing accounts · Reports span ${alert.evidence?.reporting_span_days||0} days</p></div><span>${escapeHTML(alert.status)}</span></article>`).join(''):'<p class="admin-note">No attention signals match this selection.</p>';
  renderTable();
}
function renderTable(){
  if(!dataset)return;const records=dataset.observations;page=Math.min(page,Math.max(0,Math.ceil(records.length/PAGE_SIZE)-1));
  $('#admin-records').innerHTML=records.length?records.slice(page*PAGE_SIZE,(page+1)*PAGE_SIZE).map(record=>`<tr><td><strong>${escapeHTML(record.community)}</strong><small>${formatDate(record.observed_at)}</small></td><td><strong>${escapeHTML(record.analysis?.summary||record.feelings||'Photo observation')}</strong><small>${escapeHTML(record.contributor_type==='guest'?'Guest · rewards waived':record.author_name||'Community member')}</small></td><td><span class="admin-badge ${escapeHTML(record.status)}">${escapeHTML(record.status)}</span></td><td><span class="admin-badge ${escapeHTML(record.review_status)}">${escapeHTML(record.review_status)}</span></td><td>${record.quality_flags.length?`${record.quality_flags.length} notes`:'No flags'}<small>${record.export_eligible?'Reviewed export eligible':'Not curated'}</small></td><td><button class="admin-inspect" type="button" data-record="${escapeHTML(record.id)}">Inspect ↗</button></td></tr>`).join(''):'<tr><td colspan="6" class="admin-empty">No observations yet. Community uploads will appear here.</td></tr>';
  const pages=Math.max(1,Math.ceil(records.length/PAGE_SIZE));$('#admin-page-number').textContent=`Page ${page+1} of ${pages}`;$('#admin-previous').disabled=page===0;$('#admin-next').disabled=page+1>=pages;
}
async function loadData(){
  if(adminUser?.role!=='admin')return;const version=++requestVersion;$('#admin-error').textContent='';$('#admin-status').textContent='Loading community data…';
  try{const result=await api('/api/admin/data?'+query());if(version!==requestVersion||adminUser?.role!=='admin')return;dataset=result;render();$('#admin-status').textContent=`Updated ${new Date().toLocaleTimeString('en-NZ')} · ${result.notice||'Community observations, not laboratory findings.'}`;
    for(const record of result.observations)if(![...$('#filter-community').options].some(option=>option.value===record.community))$('#filter-community').add(new Option(record.community,record.community));
  }catch(error){$('#admin-error').textContent=error.message;$('#admin-auth-error').textContent=error.status===401||error.status===403?error.message:'';$('#admin-status').textContent='';}
}
function inspect(id){
  const record=dataset?.observations.find(r=>r.id===id);if(!record)return;recordId=id;$('#admin-record-title').textContent=record.community;
  const meta=[['Observed',formatDate(record.observed_at)],['Submitted',formatDate(record.created)],['Analysis status',record.status],['Review status',record.review_status],['Contribution',record.rewards_waived?'Guest · rewards waived':'Account contribution'],['Points awarded',record.points_awarded||0]];
  $('#admin-record-body').innerHTML=`<img class="admin-record-photo" src="${escapeHTML(record.photo)}" alt="Original community observation"><div class="admin-record-meta">${meta.map(([title,value])=>`<div><span>${title}</span>${escapeHTML(value)}</div>`).join('')}</div><h3>Original words</h3><p>${escapeHTML(record.feelings||'No words supplied.')}</p><h3>Data quality</h3><p>${escapeHTML(record.quality_flags.join(' · ')||'No quality flags recorded.')}</p>${record.duplicate_of?`<p>Repeated image linked to ${escapeHTML(record.duplicate_of)}</p>`:''}<h3>Model analysis</h3><p class="admin-note">Model: ${escapeHTML(record.model||'Not available')}. Model output requires review and cannot establish water safety.</p><pre>${escapeHTML(record.analysis?JSON.stringify(record.analysis,null,2):'Analysis is not available. The original evidence remains saved.')}</pre><h3>Review history</h3>${(record.review_history||[]).map(review=>`<div class="admin-review-history"><strong>${escapeHTML(review.decision)} · ${escapeHTML(review.reviewer)}</strong><p>${escapeHTML(review.note)}</p><span>${formatDate(review.created)}</span></div>`).join('')||'<p>No review has been recorded.</p>'}`;
  $('#admin-review-form').reset();$('#review-error').textContent='';$('#review-eligibility').textContent=record.review_blocks?.length?'Approval currently blocked: '+record.review_blocks.join('; '):'Check the photo, stated date, location, model classification and uncertainty before approving.';
  $('#admin-review-form [value="approved"]').disabled=!!record.review_blocks?.length;if(!$('#admin-record-dialog').open)$('#admin-record-dialog').showModal();
}
$('#admin-records').addEventListener('click',event=>{const button=event.target.closest('[data-record]');if(button)inspect(button.dataset.record);});
$('#close-admin-record').addEventListener('click',()=>{if(!busy)$('#admin-record-dialog').close();});
$('#admin-record-dialog').addEventListener('cancel',event=>{if(busy)event.preventDefault();});
$('#admin-review-form').addEventListener('submit',async event=>{event.preventDefault();if(busy||!recordId)return;busy=true;const id=recordId,payload=Object.fromEntries(new FormData(event.target)),button=event.target.querySelector('button[type="submit"]');button.disabled=true;$('#review-error').textContent='';try{await api(`/api/admin/reviews/${id}`,{method:'POST',body:JSON.stringify(payload)});await loadData();if(dataset?.observations.some(r=>r.id===id))inspect(id);else $('#admin-record-dialog').close();}catch(error){$('#review-error').textContent=error.message;}finally{busy=false;button.disabled=false;}});
for(const name of ['community','review','status'])$('#filter-'+name).addEventListener('change',()=>{page=0;loadData();});
$('#reset-admin-filters').addEventListener('click',()=>{for(const name of ['community','review','status'])$('#filter-'+name).value='';page=0;loadData();});
$('#refresh-admin').addEventListener('click',loadData);$('#admin-previous').addEventListener('click',()=>{page--;renderTable();});$('#admin-next').addEventListener('click',()=>{page++;renderTable();});
async function download(format){
  if(adminUser?.role!=='admin')return;const userId=adminUser.id,params=query();params.set('format',format);$('#admin-error').textContent='';
  try{const response=await fetch('/api/admin/export?'+params,{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(30000)});if(!response.ok){const error=await response.json();if(response.status===401||response.status===403){forgetData();$('#admin-auth-error').textContent=error.error;}throw new Error(error.error||'Could not download data.');}const blob=await response.blob();if(adminUser?.id!==userId||adminUser?.role!=='admin')return;const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=`coastkind-research-${new Date().toISOString().slice(0,10)}.${format}`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);$('#admin-status').textContent='Research download ready. Review and quality fields distinguish eligible evidence from unverified records.';}catch(error){$('#admin-error').textContent=error.message;}
}
$('#export-csv').addEventListener('click',()=>download('csv'));$('#export-json').addEventListener('click',()=>download('json'));
let operationsRevision=0;
async function loadOperations(){
  if(adminUser?.role!=='admin'||adminUser.password_change_required)return;const userId=adminUser.id,revision=++operationsRevision;$('#operations-error').textContent='';
  try{const [vouchers,feedback]=await Promise.all([api('/api/admin/vouchers'),api('/api/admin/feedback')]);
    if(adminUser?.id!==userId||$('#admin-workspace').hidden||revision!==operationsRevision)return;
    $('#admin-vouchers').innerHTML=vouchers.requests.length?vouchers.requests.map(item=>`<article class="admin-operation"><div class="admin-panel-heading"><div><strong>${escapeHTML(item.title||item.brand)}</strong><p>${escapeHTML(item.display_name||item.user_name||'Member')} · ${item.points_cost} points · ${escapeHTML(item.value_label)}</p></div><span class="admin-badge">${escapeHTML(item.status)}</span></div><p class="admin-note">Requested ${formatDate(item.created_at||item.requested_at)}${item.review_note?' · '+escapeHTML(item.review_note):''}</p>${item.status==='pending'?`<form data-voucher-review="${escapeHTML(item.id)}"><label>Decision<select name="decision" required><option value="">Choose a decision</option><option value="approved">Approve and issue voucher</option><option value="rejected">Reject request</option></select></label><label>Review note<textarea name="note" required maxlength="2000" rows="2" placeholder="Record the reason for this decision."></textarea></label><button class="button secondary" type="submit">Save voucher decision</button></form>`:''}</article>`).join(''):'<p class="admin-note">No voucher requests yet.</p>';
    $('#admin-feedback').innerHTML=feedback.feedback.length?feedback.feedback.map(item=>`<article class="admin-operation"><div class="admin-panel-heading"><strong>${escapeHTML(item.category)} · ${escapeHTML(item.display_name||item.user_name||'Member')}</strong><span class="admin-badge">${escapeHTML(item.status.replaceAll('_',' '))}</span></div><p class="feedback-body">${escapeHTML(item.body)}</p><p class="admin-note">Received ${formatDate(item.created_at)}</p><form data-feedback-review="${escapeHTML(item.id)}"><label>Status<select name="status">${['pending','in_progress','resolved','dismissed'].map(status=>`<option value="${status}" ${item.status===status?'selected':''}>${status.replaceAll('_',' ')}</option>`).join('')}</select></label><label>Response to member<textarea name="note" required maxlength="2000" rows="2">${escapeHTML(item.admin_note||'')}</textarea></label><button class="button secondary" type="submit">Save feedback response</button></form></article>`).join(''):'<p class="admin-note">No user feedback yet.</p>';
  }catch(error){$('#operations-error').textContent=error.message;}
}
$('#refresh-operations').addEventListener('click',loadOperations);
document.addEventListener('submit',async event=>{
  const form=event.target;if(!form.matches('[data-voucher-review],[data-feedback-review]'))return;event.preventDefault();if(busy)return;
  const voucherId=form.dataset.voucherReview,feedbackId=form.dataset.feedbackReview,payload=Object.fromEntries(new FormData(form)),controls=[...form.elements];busy=true;controls.forEach(control=>control.disabled=true);$('#operations-error').textContent='';
  try{await api(voucherId?`/api/admin/vouchers/${voucherId}/review`:`/api/admin/feedback/${feedbackId}`,{method:'POST',body:JSON.stringify(payload)});await loadOperations();}
  catch(error){$('#operations-error').textContent=error.message;$('#operations-error').scrollIntoView({block:'nearest'});}finally{busy=false;controls.forEach(control=>control.disabled=false);}
});
const setupToken=new URLSearchParams(location.hash.slice(1)).get('setup');
if(setupToken){history.replaceState(null,'',location.pathname+location.search);setSetup(true);$('#admin-setup-form [name="token"]').value=setupToken;}
api('/api/auth/me').then(showSession).catch(error=>{$('#admin-auth-error').textContent=error.message;});
addEventListener('pageshow',event=>{if(event.persisted)api('/api/auth/me').then(showSession).catch(()=>signedOut('Sign in again to continue.'));});
