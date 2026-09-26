const $ = (selector, root = document) => root.querySelector(selector);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const dateLabel = value => new Date(value).toLocaleDateString('en-NZ', {day:'numeric', month:'short', year:'numeric'});
// Approximate community locations, kept in sync with server.COMMUNITY_POINTS.
// GWRC coastal monitoring appendix (2013/14), LINZ Mākara locality and WCC coastal reserves maps.
const coastalCommunities = [
  {name:'Oriental Bay', slug:'oriental-bay', point:[-41.2911,174.7943], side:'right', homeLabel:true},
  {name:'Lyall Bay', slug:'lyall-bay', point:[-41.3294,174.7959], side:'right', homeLabel:true},
  {name:'Island Bay', slug:'island-bay', point:[-41.3435,174.7735], side:'left', homeLabel:true},
  {name:'Porirua Harbour', slug:'porirua-harbour', point:[-41.114,174.8449], side:'left', homeLabel:true},
  {name:'Petone Beach', slug:'petone-beach', point:[-41.2325,174.8892], side:'right', homeLabel:true},
  {name:'Scorching Bay', slug:'scorching-bay', point:[-41.297,174.8336], side:'right'},
  {name:'Worser Bay', slug:'worser-bay', point:[-41.3135,174.8288], side:'left'},
  {name:'Seatoun Beach', slug:'seatoun-beach', point:[-41.3188,174.8296], side:'right'},
  {name:'Days Bay', slug:'days-bay', point:[-41.2808,174.9064], side:'right'},
  {name:'Rona Bay', slug:'rona-bay', point:[-41.2895,174.8956], side:'left'},
  {name:'Mākara Beach', slug:'makara-beach', point:[-41.2202,174.7126], side:'left'},
  {name:'Tītahi Bay', slug:'titahi-bay', point:[-41.106,174.8353], side:'left'},
  {name:'Plimmerton Beach', slug:'plimmerton-beach', point:[-41.0833,174.8656], side:'right'},
  {name:'Paremata', slug:'paremata', point:[-41.1015,174.8714], side:'right'},
  {name:'Pukerua Bay', slug:'pukerua-bay', point:[-41.0292,174.892], side:'right'},
  {name:'Houghton Bay', slug:'houghton-bay', point:[-41.3437,174.7853], side:'left'},
  {name:'Ōwhiro Bay', slug:'owhiro-bay', point:[-41.3449,174.7585], side:'left'},
  {name:'Princess Bay', slug:'princess-bay', point:[-41.3441,174.7879], side:'right'},
  {name:'Hataitai Beach', slug:'hataitai-beach', point:[-41.3058,174.7994], side:'left'},
  {name:'Breaker Bay', slug:'breaker-bay', point:[-41.3302,174.8321], side:'right'}
];
const pollutionLabels = {litter:'Litter & rubbish', suspected_industrial:'Suspected industrial pollution', oil_or_fuel:'Possible oil or fuel', suspected_wastewater:'Suspected wastewater', unusual_water:'Unusual water appearance', other:'Other concern'};
pollutionLabels.unclassified = 'Reported coastal concerns';
let currentUser = null, csrfToken = null, authMode = 'login', authBusy = false, authIntent = null, authRevision = 0;
let rewardCatalog = [], myVouchers = [], redemption = null;
let posts = [], filter = 'all', currentPost = null, coastMap = null, mapLoaded = false;
let photoData = '', photoVersion = 0, photoLoading = false, position = null, locationVersion = 0;
let submitting = false, submissionId = '', submissionHint = 'observation', backendReady = false, aiConfigured = false, demoMode = false;
let refreshing = false, loaded = false;
let concerns = [], alerts = [], focusedConcern = null;
let wallet = {points:0,pending_observations:0,entries:[]};

function toast(message) {
  $('#toast').textContent = message; $('#toast').classList.add('visible');
  clearTimeout(toast.timer); toast.timer = setTimeout(() => $('#toast').classList.remove('visible'), 5000);
}
async function api(path, options = {}) {
  if (window.coastkindDemoApi) return window.coastkindDemoApi(path, options);
  if (location.protocol === 'file:') throw new Error('Open http://localhost:8000 to save observations.');
  let response;
  try { response = await fetch(path, {...options, credentials:'same-origin', headers:{'Content-Type':'application/json', ...(csrfToken && options.method && options.method!=='GET'?{'X-CSRF-Token':csrfToken}:{}), ...options.headers}, signal:AbortSignal.timeout(25000)}); }
  catch { throw new Error('Could not reach the server. Your form is still here; please try again.'); }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    if(response.status===401 && !path.startsWith('/api/auth/')){currentUser=null;csrfToken=null;clearAccountData();renderAccount();}
    const error=new Error(body.error || 'The request could not be completed. Please try again.');error.status=response.status;throw error;
  }
  return body;
}
function postType(p) {
  if (p.hint === 'concern' || p.analysis?.observation_type === 'concern') return 'concern';
  if (p.analysis?.observation_type === 'moment' || p.hint === 'moment') return 'moment';
  return 'observation';
}
function titleFor(p) { return p.analysis?.summary || (postType(p) === 'concern' ? 'A coastal concern' : `An observation from ${p.community}`); }
function statusText(p) {
  if(demoMode){if(p.review_status==='approved')return 'Simulated review · Approved example';if(p.review_status==='rejected')return 'Simulated review · Rejected example';return p.status==='analyzed'?'Scripted analysis · Demo only':`Demo workflow · ${p.status}`;}
  if(p.review_status==='approved')return 'Human reviewed · Community evidence';
  if(p.review_status==='rejected')return 'Reviewed · Not eligible for data export';
  return {pending:'Saved · Awaiting AI analysis', processing:'Saved · AI is analysing', analyzed:'AI suggestion · Needs review', failed:'Saved · AI analysis unavailable', duplicate:'Saved · Repeated photo linked'}[p.status] || 'Saved observation';
}
function tagsFor(p) {
  const tags = (p.analysis?.pollution_types || []).map(t => `<span class="pollution-tag">${escapeHTML(pollutionLabels[t] || t)}</span>`);
  if (p.analysis?.activity) tags.push(`<span>${escapeHTML(p.analysis.activity)}</span>`);
  if (p.duplicate_of) tags.push('<span>Repeated photo · Linked record</span>');
  return `<div class="story-tags">${tags.join('')}</div>`;
}
function render() {
  const community = $('#location-filter').value;
  const local = posts.filter(p => community === 'all' || p.community === community);
  $('.community .section-heading h2').textContent = community === 'all' ? 'Around the coast' : `${community} community`;
  $('.community .section-heading p').textContent = 'For swimmers, anglers, boaters, fishing crews and everyone who cares for our waters.';
  $('#community-summary').textContent = `${local.length} observations · ${local.filter(p => postType(p) === 'concern').length} concerns`;
  renderConcerns(community);
  const focused = concerns.find(g=>g.id===focusedConcern);
  $('#focused-concern').hidden = !focused;
  if(focused)$('#focused-concern span').textContent = `Showing: ${pollutionLabels[focused.category]}`;
  const list = local.filter(p => (filter === 'all' || postType(p) === filter) && (!focused || focused.report_ids.includes(p.id))).sort((a,b) => $('#sort').value === 'supported' ? b.likes-a.likes : new Date(b.created)-new Date(a.created));
  $('#posts').innerHTML = list.length ? list.map(p => `<article class="post-card"><div class="post-photo"><img src="${escapeHTML(p.photo)}" alt="Community coastal observation" loading="lazy"><span class="tag ${postType(p)==='concern'?'concern':''}">${postType(p)==='concern'?'Coastal concern':postType(p)==='moment'?'Community update':'Coastal observation'}</span></div><div class="post-content"><div class="author-row"><span class="avatar">CK</span><div><span class="author-name">${escapeHTML(p.author_name || 'Guest contributor')}</span><time class="post-date">${dateLabel(p.created)}</time></div></div><div class="post-place">⌖ ${escapeHTML(p.community)}</div><h3 class="post-title">${escapeHTML(titleFor(p))}</h3>${p.feelings?`<p class="post-body">${escapeHTML(p.feelings)}</p>`:''}${tagsFor(p)}<span class="post-status">${statusText(p)}</span></div><div class="post-footer"><button data-like="${p.id}" aria-label="Support this observation" aria-pressed="${p.liked}">${p.liked?'♥':'♡'} ${p.likes}</button><button data-detail="${p.id}" aria-label="Read comments">↳ ${p.comments.length}</button><button class="read-story" data-detail="${p.id}">View observation ↗</button></div></article>`).join('') : `<div class="empty-state"><span class="empty-wave" aria-hidden="true">≋</span><h3>${!loaded?'Your coastal community':'Be the first pair of eyes.'}</h3><p>${!backendReady?'Connect to the local server to load and save community observations.':filter==='all'?'A quiet swim. Something out of place. Share what you see.':'No observations match this filter yet.'}</p><button class="button primary" data-compose="observation">＋ Share a photo</button></div>`;
  $('.feed-end').hidden = list.length === 0;
}
async function refreshPosts() {
  if (refreshing || authBusy) return;
  refreshing = true;
  const revision=authRevision;
  try {
    const session=await api('/api/auth/me');
    if(revision!==authRevision)return;
    currentUser=session.user;csrfToken=session.csrfToken;
    if(!currentUser)clearAccountData();
    renderAccount();
    const [health, result, trends, balance] = await Promise.all([api('/api/health'), api('/api/observations'), api('/api/concerns'), currentUser?api('/api/wallet'):Promise.resolve({points:0,pending_observations:0,entries:[]})]);
    if(revision!==authRevision)return;
    const first = !loaded;
    backendReady = true; loaded = true; aiConfigured = health.aiConfigured; demoMode = !!health.demoMode;
    const changed = JSON.stringify(posts) !== JSON.stringify(result.observations) || JSON.stringify(concerns)!==JSON.stringify(trends.concerns) || JSON.stringify(alerts)!==JSON.stringify(trends.alerts);
    posts = result.observations; concerns = trends.concerns; alerts = trends.alerts;
    wallet=balance;renderWallet();
    $('#connection-status').textContent = demoMode ? 'Synthetic demonstration · Scripted analysis and reviews · No real pollution findings or rewards' : aiConfigured ? 'Saved to the community database · AI suggestions await human review' : 'Your observations are saved to the local database. AI analysis is awaiting setup.';
    if (changed || first) render();
  } catch (error) { if(revision===authRevision){backendReady = false; $('#connection-status').textContent = error.message; render();} }
  finally { refreshing = false; }
}
function openComposer(hint = 'observation') {
  if(demoMode){toast('This fictional presentation is read-only.');return;}
  if (submitting) return;
  $('#compose-form').reset(); photoVersion++; locationVersion++; photoData = ''; photoLoading = false; position = null;
  submissionId = crypto.randomUUID(); submissionHint = hint;
  $('#upload-as-guest').checked=!currentUser;renderUploadIdentity();
  $('#concern-hint').checked = hint === 'concern';
  $('#photo-preview').hidden = true; $('#form-error').textContent = ''; $('#photo-quality').textContent = '';
  $('#location-status').textContent = 'Optional. Only add your current location if you are at the photo location.';
  $('#clear-location').hidden = true; $('#location-confirm-label').hidden = true; $('#get-location').disabled = false;
  $('#photo-details').open = false;$('.dataset-preference').open=false;
  const community = $('#location-filter').value;
  if (community !== 'all') $('#compose-form [name="location"]').value = community;
  const localNow = new Date(Date.now() - new Date().getTimezoneOffset()*60000).toISOString().slice(0,16);
  $('#observed-at').max = localNow;
  $('#compose-dialog').showModal();
}
async function loadPhoto(event) {
  const version = ++photoVersion;
  const file = event.target.files[0];
  photoData = ''; photoLoading = false; $('#photo-preview').hidden = true; $('#form-error').textContent = ''; $('#photo-quality').textContent = '';
  if (!file) return;
  if (!['image/jpeg','image/png','image/webp'].includes(file.type) || file.size > 5*1024*1024) {
    $('#form-error').textContent = 'Choose a JPG, PNG, or WebP photo smaller than 5 MB.'; event.target.value = ''; return;
  }
  photoLoading = true;
  try {
    const bitmap = await createImageBitmap(file);
    const small = Math.min(bitmap.width,bitmap.height)<400;
    const scale = Math.min(1,1800/Math.max(bitmap.width,bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bitmap.width*scale); canvas.height = Math.round(bitmap.height*scale);
    canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height); bitmap.close();
    if (version !== photoVersion) return;
    photoData = canvas.toDataURL('image/jpeg',.9);
    $('#photo-preview').src = photoData; $('#photo-preview').hidden = false;
    $('#photo-quality').textContent = small ? 'This photo is quite small. A larger, clear photo will give more useful evidence.' : 'Photo ready. Make sure the detail you want to share is clearly visible.';
  } catch { if (version === photoVersion) $('#form-error').textContent = 'This photo could not be read. Please choose another one.'; }
  finally { if (version === photoVersion) photoLoading = false; }
}
$('#photo').addEventListener('change', loadPhoto);
$('#camera').addEventListener('change', loadPhoto);
$('#get-location').addEventListener('click', () => {
  if (!navigator.geolocation) { $('#location-status').textContent = 'Location is not available. You can still share a photo.'; return; }
  const version = ++locationVersion; position = null;
  $('#get-location').disabled = true; $('#location-confirm-label').hidden = true; $('#location-confirm').checked = false;
  $('#location-status').textContent = 'Requesting your device location…';
  navigator.geolocation.getCurrentPosition(result => {
    if (version !== locationVersion) return;
    position = {latitude:result.coords.latitude, longitude:result.coords.longitude, accuracy:result.coords.accuracy};
    $('#location-status').textContent = `Location found (${position.latitude.toFixed(4)}, ${position.longitude.toFixed(4)}; accuracy about ${Math.round(position.accuracy)} m).`;
    $('#clear-location').hidden = false; $('#location-confirm-label').hidden = false; $('#get-location').disabled = false;
  }, error => {
    if (version !== locationVersion) return;
    position = null;
    $('#location-status').textContent = error.code === 1 ? 'Location permission was declined. You can still submit without it.' : 'Could not get your location. Try again or submit without it.';
    $('#get-location').disabled = false;
  }, {enableHighAccuracy:true, timeout:12000, maximumAge:0});
});
$('#clear-location').addEventListener('click', () => {
  locationVersion++; position = null; $('#clear-location').hidden = true; $('#location-confirm-label').hidden = true; $('#location-confirm').checked = false; $('#get-location').disabled = false;
  $('#location-status').textContent = 'Location removed. Only your selected community will be saved.';
});
$('#compose-dialog').addEventListener('cancel', event => { if (submitting) event.preventDefault(); });
$('#compose-form').addEventListener('submit', async event => {
  event.preventDefault(); if (submitting) return;
  $('#form-error').textContent = '';
  if (photoLoading || !photoData) { $('#form-error').textContent = photoLoading ? 'Your photo is still loading. Please wait a moment.' : 'Add a photo to share your observation.'; return; }
  if (position && !$('#location-confirm').checked) { $('#form-error').textContent = 'Confirm this is where you took the photo, or remove the location.'; return; }
  if(!$('#upload-as-guest').checked && !currentUser){$('#form-error').textContent='Sign in to upload with your account, or select guest upload to waive rewards.';return;}
  const community = $('#compose-form [name="location"]').value;
  const observed = $('#observed-at').value;
  const payload = {id:submissionId, community, feelings:$('#compose-form [name="feelings"]').value.trim(), photo:photoData, position, location_confirmed:!!position, hint:$('#concern-hint').checked?'concern':'observation', observed_at:observed?new Date(observed).toISOString():null, dataset_consent:$('#dataset-consent').checked,as_guest:$('#upload-as-guest').checked};
  submitting = true; locationVersion++;
  const controls = [...$('#compose-form').elements]; controls.forEach(control => control.disabled = true);
  $('#submit-observation').textContent = 'Saving your observation…';
  try {
    await api('/api/observations', {method:'POST', body:JSON.stringify(payload)});
    $('#compose-dialog').close(); await refreshPosts(); enterCommunity(community);
    toast(payload.as_guest?'Guest observation saved. Rewards are waived for this upload.':aiConfigured ? 'Saved to your account. AI analysis will appear shortly.' : 'Saved to your account. Awaiting AI analysis setup.');
  } catch (error) { $('#form-error').textContent = error.message; }
  finally { submitting = false; controls.forEach(control => control.disabled = false); $('#submit-observation').textContent = 'Share observation ↗'; }
});
function analysisDetail(p) {
  if (!p.analysis) return `<section class="analysis-panel"><strong>${statusText(p)}</strong><p>${p.status==='duplicate'?'This photo is linked to an earlier record and will not be counted as independent evidence.':p.status==='failed'?'Your photo and words are saved in the database. Analysis can be retried by the site operator.':'Your original observation is saved. Analysis will be added when available.'}</p></section>`;
  const a = p.analysis;
  return `<section class="analysis-panel"><div class="eyebrow">AI SUGGESTION · HUMAN REVIEW REQUIRED</div><p>${escapeHTML(a.summary)}</p>${tagsFor(p)}<strong>Visible evidence</strong><ul>${a.visible_evidence.map(x=>`<li>${escapeHTML(x)}</li>`).join('') || '<li>No specific evidence identified.</li>'}</ul>${a.reported_experience?`<strong>Reported experience</strong><p>${escapeHTML(a.reported_experience)}</p>`:''}<strong>Uncertainty</strong><ul>${a.uncertainties.map(x=>`<li>${escapeHTML(x)}</li>`).join('') || '<li>This observation has not been independently verified.</li>'}</ul><small>Model: ${escapeHTML(p.model)} · This is not a water-quality test or a finding of responsibility.</small></section>`;
}
function openDetail(id) {
  const p = posts.find(p=>p.id===id); if (!p) return; currentPost = id;
  $('#detail-content').innerHTML = `<img class="detail-image" src="${escapeHTML(p.photo)}" alt="Community coastal observation"><h2 class="detail-heading">${escapeHTML(titleFor(p))}</h2><p class="detail-meta">${escapeHTML(p.community)} · Submitted ${dateLabel(p.created)}${p.observed_at?` · Observed ${dateLabel(p.observed_at)}`:' · Photo date not supplied'}${p.position?' · Approximate location recorded':''}</p>${p.feelings?`<p class="detail-body">${escapeHTML(p.feelings)}</p>`:''}${p.quality_flags?.length?`<p class="report-note">Data notes: ${p.quality_flags.map(escapeHTML).join(' · ')}</p>`:''}${analysisDetail(p)}<button class="button primary" id="download-report">Download observation draft ↓</button><p class="report-note">Not submitted to government. Attach your original photo before sending.</p><section class="comments"><h3>Community follow-up</h3>${p.comments.map(c=>`<div class="comment"><strong>${escapeHTML(c.author)}</strong><p>${escapeHTML(c.body)}</p><small>${dateLabel(c.created)}</small></div>`).join('') || '<p class="detail-meta">Add another observation or share an update.</p>'}<form id="comment-form" class="comment-form"><p class="detail-meta">${currentUser ? `Posting as ${escapeHTML(currentUser.display_name)}` : 'Sign in to add a follow-up.'}</p><label>Your update<textarea name="body" required maxlength="1500" rows="3"></textarea></label><p class="form-error" id="comment-error" role="alert"></p><button class="button primary" type="submit">Save update ↗</button></form></section>`;
  if (!$('#detail-dialog').open) $('#detail-dialog').showModal();
}
$('#detail-content').addEventListener('submit', async event => {
  if (event.target.id !== 'comment-form') return; event.preventDefault();
  if(!currentUser){openAuth('login','comment');return;}
  const form = event.target, data = new FormData(form), id = currentPost;
  const button = $('button[type="submit"]',form); button.disabled = true;
  try { await api(`/api/observations/${id}/comments`, {method:'POST', body:JSON.stringify({body:data.get('body').trim()})}); await refreshPosts(); if ($('#detail-dialog').open && currentPost === id) openDetail(id); toast('Your follow-up is saved.'); }
  catch (error) { $('#comment-error').textContent = error.message; }
  finally { button.disabled = false; }
});
function downloadReport() {
  const p = posts.find(p=>p.id===currentPost);
  const report = ['COASTKIND — OBSERVATION DRAFT','NOT SUBMITTED TO GOVERNMENT','Unverified community evidence',`Record: ${p.id}`,`Community: ${p.community}`,`Submitted: ${p.created}`,`Observed: ${p.observed_at || 'Not supplied'}`,p.position?`Approximate device coordinates: ${p.position.latitude}, ${p.position.longitude}`:'No device location shared.',`Data quality notes: ${(p.quality_flags||[]).join('; ')||'None recorded'}`,p.duplicate_of?`Repeated image linked to record: ${p.duplicate_of}`:'','', 'ORIGINAL FEELINGS',p.feelings || 'No text supplied.','', 'AI ANALYSIS — REQUIRES HUMAN REVIEW',p.analysis?JSON.stringify(p.analysis,null,2):statusText(p),'','FOLLOW-UP',...p.comments.map(c=>`${c.created} — ${c.author}: ${c.body}`),'','Attach the original photo. Review all observations before submission. This draft has not been sent to a council.'].join('\n');
  const url = URL.createObjectURL(new Blob([report],{type:'text/plain;charset=utf-8'}));
  const a = document.createElement('a'); a.href=url; a.download=`coastkind-${p.id}.txt`; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
}
document.addEventListener('click', async event => {
  if(event.target.closest('[data-resources]')) $('#resources-dialog').showModal();
  const auth=event.target.closest('[data-auth]');if(auth)openAuth(auth.dataset.auth);
  const authTab=event.target.closest('[data-auth-mode]');if(authTab&&!authBusy)setAuthMode(authTab.dataset.authMode);
  const group = event.target.closest('[data-concern-group]');
  if(group){focusedConcern=group.dataset.concernGroup;filter='all';document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('selected',b.dataset.filter==='all'));render();$('#focused-concern').scrollIntoView({behavior:'smooth',block:'start'});}
  const download = event.target.closest('[data-concern-download]');if(download)downloadConcern(download.dataset.concernDownload);
  if(event.target.closest('#clear-concern')){focusedConcern=null;render();}
  const compose = event.target.closest('[data-compose]'); if (compose) openComposer(compose.dataset.compose);
  const close = event.target.closest('.close,.close-info'); if (close && !(close.closest('#compose-dialog') && submitting)) close.closest('dialog').close();
  const tab = event.target.closest('[data-filter]'); if (tab) {filter=tab.dataset.filter; document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('selected',b===tab)); render();}
  const detail = event.target.closest('[data-detail]'); if (detail) openDetail(detail.dataset.detail);
  const like = event.target.closest('[data-like]'); if (like) {
    if(!currentUser){openAuth('login','support');return;}
    const p=posts.find(p=>p.id===like.dataset.like); like.disabled=true;
    try {await api(`/api/observations/${p.id}/support`, {method:'POST',body:JSON.stringify({supported:!p.liked})});await refreshPosts();}
    catch(error){toast(error.message);like.disabled=false;}
  }
  if(event.target.closest('#report-info')) $('#info-dialog').showModal();
  if(event.target.closest('#download-report')) downloadReport();
});
function enterCommunity(name) {
  const c=coastalCommunities.find(c=>c.name===name); if(!c)return;
  const hash=`#coast/${c.slug}`; if(location.hash===hash)applyCommunityRoute();else location.hash=hash;
}
function applyCommunityRoute() {
  const c=coastalCommunities.find(c=>location.hash===`#coast/${c.slug}`);
  document.body.classList.toggle('in-community',!!c); $('.community-return').hidden=!c;
  $('#location-filter').value=c?c.name:'all'; $('#community-picker').value=c?c.name:'';
  filter='all'; document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('selected',b.dataset.filter==='all'));
  focusedConcern=null;
  document.title=c?`${c.name} community — Coastkind`:'Coastkind — Wellington coast';
  render(); window.scrollTo({top:0,behavior:'instant'});
  if(!c && coastMap)requestAnimationFrame(fitCoastalMap);
  if(c)refreshPosts();
}
// The same list drives navigation, filtering and uploads so new map points are usable everywhere.
for (const selector of ['#community-picker', '#location-filter', '#compose-form [name="location"]']) {
  const select = $(selector);
  while (select.options.length > 1) select.remove(1);
  for (const community of [...coastalCommunities].sort((a,b)=>a.name.localeCompare(b.name,'en-NZ'))) {
    select.add(new Option(community.name, community.name));
  }
}
$('#community-links').innerHTML=coastalCommunities.filter(c=>c.homeLabel).map(c=>`<a href="#coast/${c.slug}">${escapeHTML(c.name)} ↗</a>`).join('');
$('#community-picker').addEventListener('change',event=>enterCommunity(event.target.value));
$('#location-filter').addEventListener('change',()=>{if($('#location-filter').value==='all')location.hash='explore';else enterCommunity($('#location-filter').value);});
$('#sort').addEventListener('change',render);
function renderConcerns(community){
  const groups=concerns.filter(g=>g.community===community);
  $('#shared-concerns').hidden=!groups.length;
  const attention=alerts.filter(a=>a.community===community && a.status!=='inactive');
  $('#concerns-title').textContent=attention.length?'Repeated concerns need a closer look':'What our community is noticing';
  $('#concern-groups').innerHTML=groups.map(g=>`<article class="concern-group"><div class="concern-title"><h4>${escapeHTML(pollutionLabels[g.category]||g.category)}</h4><span>${g.reporting_span_days?`Reports span ${g.reporting_span_days} days`:'Reports received on one day'}</span></div><div class="concern-metrics"><div><strong>${g.report_count}</strong><span>reports received</span></div><div><strong>${g.distinct_photos}</strong><span>different photos</span></div><div><strong>${g.supporting_accounts}</strong><span>supporting accounts</span></div><div><strong>${g.followups}</strong><span>follow-up updates</span></div></div><p>First reported ${dateLabel(g.first_report)} · Latest ${dateLabel(g.last_report)}<br>${g.contributing_accounts} contributing accounts · ${g.duplicates} repeated photos · ${g.reviewed_reports} human-reviewed records</p><div class="concern-group-actions"><button class="text-button" data-concern-group="${g.id}">View related observations ↗</button><button class="text-button" data-concern-download="${g.id}">Download community summary ↓</button></div></article>`).join('');
}
function downloadConcern(id){
  const group=concerns.find(g=>g.id===id);if(!group)return;
  const related=posts.filter(p=>group.report_ids.includes(p.id));
  const text=['COASTKIND — COMMUNITY CONCERN SUMMARY','UNVERIFIED COMMUNITY SIGNALS — NOT SUBMITTED TO GOVERNMENT',`Community: ${group.community}`,`Topic: ${pollutionLabels[group.category]}`,`Reports received: ${group.report_count}`,`Different photos: ${group.distinct_photos}`,`Repeated photos: ${group.duplicates}`,`Contributing accounts (not verified people): ${group.contributing_accounts}`,`Supporting accounts (not evidence): ${group.supporting_accounts}`,`Follow-ups: ${group.followups}`,`Human-reviewed records: ${group.reviewed_reports}`,`First report received: ${group.first_report}`,`Latest report received: ${group.last_report}`,'','This is an area/topic summary, not a finding that all reports refer to one incident. Repeated photographs are not independent evidence. Report frequency does not establish pollution severity or responsibility. Submission dates do not prove continuous pollution.','',...related.map(p=>[`Record: ${p.id}`,`Submitted: ${p.created}`,`Observed: ${p.observed_at||'Unknown'}`,`Review: ${p.review_status}`,`Original words: ${p.feelings||'None'}`,`Quality notes: ${(p.quality_flags||[]).join(', ')}`,`AI suggestion: ${p.analysis?JSON.stringify(p.analysis):'Not available'}`,...p.comments.map(c=>`Follow-up ${c.created}: ${c.body}`),''].join('\n'))].join('\n');
  const url=URL.createObjectURL(new Blob([text],{type:'text/plain;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=`coastkind-community-${id}.txt`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function renderWallet(){
  $('#points-count').textContent=wallet.points;
  $('#wallet-balance').textContent=wallet.points;
  $('#wallet-pending').textContent=wallet.pending_observations?`${wallet.pending_observations} observations awaiting review`:'No observations awaiting review.';
  $('#points-history').innerHTML=wallet.entries.length?wallet.entries.map(e=>`<div class="points-entry"><div><strong>${escapeHTML(e.reason)}</strong><span>${dateLabel(e.created)}</span></div><b>${e.delta>0?'+':''}${e.delta}</b></div>`).join(''):'<p class="form-note">Your points will appear here after a contribution is approved.</p>';
}
function clearAccountData(){
  wallet={points:0,pending_observations:0,entries:[]};myVouchers=[];rewardCatalog=[];redemption=null;
  $('#my-vouchers').replaceChildren();$('#points-history').replaceChildren();$('#reward-catalog').replaceChildren();
  $('#redemption-confirm').hidden=true;$('#reward-error').textContent='';
  $('#account-email').textContent='';$('#account-name').textContent='Your account';
  $('#rewards-dialog').close();renderWallet();
}
function renderAccount(){
  $('#header-sign-in').hidden=!!currentUser;$('#header-register').hidden=!!currentUser;
  $('#header-account').hidden=!currentUser;$('#open-rewards').hidden=!currentUser;
  if(currentUser){$('#account-name').textContent=currentUser.display_name;$('#account-email').textContent=currentUser.email;}
  renderUploadIdentity();
}
function renderUploadIdentity(){
  const guest=$('#upload-as-guest').checked;
  $('#guest-choice-label').hidden=!currentUser && guest;
  $('#sign-in-for-points').hidden=!!currentUser && !guest;
  $('#sign-in-for-points').textContent=currentUser?'Use my account and earn points ↗':'Sign in to earn contribution points ↗';
  $('#upload-identity-title').textContent=guest?'Sharing as a guest':currentUser?`Sharing as ${currentUser.display_name}`:'Sign in to keep your reward eligibility';
  $('#upload-identity-note').textContent=guest?'Guest uploads automatically waive rewards, even if you create an account later.':currentUser?'Eligible observations earn points after human review. Your points belong to your account.':'Your photo is still here. Sign in again, or choose guest upload to waive rewards.';
}
function setAuthMode(mode){
  authMode=mode==='register'?'register':'login';const registering=authMode==='register';
  $('#auth-title').textContent=registering?'Join your coastal community.':'Welcome back.';
  $('#auth-submit').textContent=registering?'Create account':'Sign in';
  $('#display-name-label').hidden=!registering;$('#auth-form [name="display_name"]').required=registering;
  $('#password-guidance').hidden=!registering;
  $('#auth-form [name="password"]').minLength=registering?12:1;
  $('#auth-form [name="password"]').autocomplete=registering?'new-password':'current-password';
  document.querySelectorAll('[data-auth-mode]').forEach(b=>b.classList.toggle('selected',b.dataset.authMode===authMode));
  $('#auth-error').textContent='';
}
function openAuth(mode='login',intent=null){
  if(currentUser){if(intent==='earn'){$('#upload-as-guest').checked=false;renderUploadIdentity();}else openAccount();return;}
  authIntent=intent;$('#auth-form').reset();setAuthMode(mode);
  if(!$('#auth-dialog').open)$('#auth-dialog').showModal();
}
$('#auth-form').addEventListener('submit',async event=>{
  event.preventDefault();if(authBusy)return;
  authBusy=true;authRevision++;
  const data=new FormData(event.target),controls=[...event.target.elements];controls.forEach(c=>c.disabled=true);
  $('#auth-error').textContent='';$('#auth-submit').textContent=authMode==='register'?'Creating your account…':'Signing in…';
  try{
    const result=await api(`/api/auth/${authMode}`,{method:'POST',body:JSON.stringify({email:data.get('email').trim(),password:data.get('password'),display_name:data.get('display_name').trim()})});
    currentUser=result.user;csrfToken=result.csrfToken;authRevision++;
    $('#auth-form [name="password"]').value='';$('#auth-dialog').close();
    if(authIntent==='earn')$('#upload-as-guest').checked=false;
    renderAccount();toast(authMode==='register'?'Your account is ready. Guest uploads from before registration remain reward-free.':'You are signed in.');
    if(authIntent==='rewards')await openAccount();
  }catch(error){$('#auth-error').textContent=error.message;}
  finally{authBusy=false;controls.forEach(c=>c.disabled=false);$('#auth-submit').textContent=authMode==='register'?'Create account':'Sign in';refreshPosts();}
});
$('#auth-dialog').addEventListener('cancel',event=>{if(authBusy)event.preventDefault();});
$('#auth-continue-guest').addEventListener('click',()=>{
  $('#auth-dialog').close();
  if($('#compose-dialog').open){$('#upload-as-guest').checked=true;renderUploadIdentity();}
});
$('#sign-in-for-points').addEventListener('click',()=>openAuth('login','earn'));
$('#upload-as-guest').addEventListener('change',renderUploadIdentity);
$('#sign-out').addEventListener('click',async()=>{
  const button=$('#sign-out');button.disabled=true;authRevision++;
  try{await api('/api/auth/logout',{method:'POST',body:'{}'});currentUser=null;csrfToken=null;authRevision++;clearAccountData();renderAccount();render();toast('You are signed out. Your account points are saved.');}
  catch(error){$('#reward-error').textContent=error.message;}
  finally{button.disabled=false;refreshPosts();}
});
async function loadRewards(){
  if(!currentUser)return;
  const userId=currentUser.id,revision=authRevision;
  try{
    const [catalog,issued,balance]=await Promise.all([api('/api/rewards/catalog'),api('/api/rewards/mine'),api('/api/wallet')]);
    if(currentUser?.id!==userId||revision!==authRevision)return;
    rewardCatalog=catalog.rewards;myVouchers=issued.redemptions;wallet=balance;renderWallet();renderRewards();
  }catch(error){if(currentUser?.id===userId)$('#reward-error').textContent=error.message;}
}
async function openAccount(){
  if(!currentUser){openAuth('login','rewards');return;}
  renderAccount();renderWallet();$('#reward-error').textContent='';
  if(!$('#rewards-dialog').open)$('#rewards-dialog').showModal();
  await loadRewards();
}
function renderRewards(){
  $('#reward-catalog').innerHTML=rewardCatalog.map(r=>`<div><strong>${escapeHTML(r.brand)}</strong><span>${escapeHTML(r.title)}</span><span>${r.points_cost?`${r.points_cost} points${r.value_label?' · '+escapeHTML(r.value_label):''}`:'Reward details to be confirmed'}</span><button class="reward-redeem" data-redeem="${escapeHTML(r.id)}" ${!r.available || wallet.points<r.points_cost?'disabled':''}>${r.status==='unavailable'?'Not available yet':r.status==='out_of_stock'?'Out of stock':wallet.points<r.points_cost?'Not enough points':'Redeem'}</button></div>`).join('');
  $('#my-vouchers').innerHTML=myVouchers.length?myVouchers.map(v=>`<article class="issued-voucher"><div><strong>${escapeHTML(v.brand)}</strong><span>${escapeHTML(v.value_label)} · ${v.points_cost} points</span></div><p>${escapeHTML(v.title)}</p><code>${escapeHTML(v.voucher_code)}</code><small>Issued ${dateLabel(v.redeemed_at)}${v.expires_at?' · Expires '+dateLabel(v.expires_at):''}</small><button class="text-button" data-copy-voucher="${escapeHTML(v.id)}">Copy voucher code</button></article>`).join(''):'<p class="form-note">No vouchers yet. When you redeem an available reward, your voucher will appear here.</p>';
}
document.addEventListener('click',async event=>{
  const redeem=event.target.closest('[data-redeem]');
  if(redeem){const reward=rewardCatalog.find(r=>r.id===redeem.dataset.redeem);if(!reward||!reward.available)return;redemption={reward_id:reward.id,request_id:crypto.randomUUID()};$('#redemption-description').textContent=`Redeem ${reward.brand} · ${reward.value_label} for ${reward.points_cost} points?`;$('#redemption-confirm').hidden=false;$('#redemption-confirm').scrollIntoView({block:'nearest',behavior:'smooth'});}
  const copy=event.target.closest('[data-copy-voucher]');
  if(copy){const voucher=myVouchers.find(v=>v.id===copy.dataset.copyVoucher);if(voucher){try{await navigator.clipboard.writeText(voucher.voucher_code);toast('Voucher code copied.');}catch{toast('Select and copy the voucher code shown in your account.');}}}
});
$('#cancel-redemption').addEventListener('click',()=>{redemption=null;$('#redemption-confirm').hidden=true;});
$('#confirm-redemption').addEventListener('click',async()=>{
  if(!redemption||!currentUser)return;
  const button=$('#confirm-redemption'),userId=currentUser.id;button.disabled=true;$('#cancel-redemption').disabled=true;$('#reward-error').textContent='';
  try{await api('/api/rewards/redeem',{method:'POST',body:JSON.stringify(redemption)});if(currentUser?.id!==userId)return;redemption=null;$('#redemption-confirm').hidden=true;await loadRewards();toast('Your voucher has been delivered to My vouchers.');}
  catch(error){$('#reward-error').textContent=error.message;}
  finally{button.disabled=false;$('#cancel-redemption').disabled=false;}
});
$('#open-rewards').addEventListener('click',openAccount);
$('#header-account').addEventListener('click',openAccount);
function fitCoastalMap() {
  if(!coastMap || document.body.classList.contains('in-community'))return;
  coastMap.invalidateSize(); const mobile=innerWidth<=700, panel=$('.explorer-copy');
  coastMap.fitBounds(coastalCommunities.map(c=>c.point),{paddingTopLeft:mobile?[75,40]:[panel.offsetWidth+55,65],paddingBottomRight:mobile?[100,panel.offsetHeight+65]:[145,55],maxZoom:12,animate:false});
}
const leaflet=document.createElement('script');
leaflet.src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';leaflet.integrity='sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=';leaflet.crossOrigin='';
function mapUnavailable(){if(!mapLoaded)$('#coast-map').innerHTML='<p class="map-loading">The map is unavailable.<br>Choose a community by name to explore.</p>';}
leaflet.onerror=mapUnavailable;
leaflet.onload=()=>{
  mapLoaded=true; $('#coast-map').replaceChildren();
  coastMap=L.map('coast-map',{scrollWheelZoom:false}).setView([-41.23,174.82],10);coastMap.zoomControl.setPosition('topright');fitCoastalMap();
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:18,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(coastMap).on('tileerror',()=>{$('#map-note').textContent='Some map tiles could not load. You can still choose a community by name.';});
  const communityMarkers=coastalCommunities.map(c=>{
    const marker=L.marker(c.point,{title:`Enter ${c.name} community`,alt:`Enter ${c.name} community`,keyboard:true,
      icon:L.divIcon({className:`coast-marker${c.homeLabel?'':' coast-marker-secondary'}`,html:'<span></span>',iconSize:[28,28],iconAnchor:[14,14]})})
      .addTo(coastMap).on('click',()=>enterCommunity(c.name));
    marker.getElement()?.addEventListener('focus',()=>marker.openTooltip());
    marker.getElement()?.addEventListener('blur',()=>{if(!marker.getTooltip()?.options.permanent)marker.closeTooltip();});
    return {community:c,marker};
  });
  function updateCommunityLabels(){
    for(const {community:c,marker} of communityMarkers){
      const permanent=!!c.homeLabel || coastMap.getZoom()>=12.5;
      if(marker.getTooltip()?.options.permanent===permanent)continue;
      marker.unbindTooltip().bindTooltip(escapeHTML(c.name),{permanent,direction:c.side,className:'coast-label',offset:c.side==='left'?[-13,0]:[13,0]});
    }
  }
  coastMap.on('zoomend',updateCommunityLabels);updateCommunityLabels();
};
document.head.append(leaflet);setTimeout(mapUnavailable,12000);
addEventListener('hashchange',applyCommunityRoute);addEventListener('resize',fitCoastalMap);
setInterval(()=>{if(!document.hidden && document.body.classList.contains('in-community'))refreshPosts();},5000);
applyCommunityRoute();refreshPosts();
