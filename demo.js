const q = selector => document.querySelector(selector);
const safe = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const names = {litter:'Litter & rubbish',suspected_industrial:'Suspected industrial',oil_or_fuel:'Possible oil or fuel',suspected_wastewater:'Suspected wastewater',unusual_water:'Unusual water',other:'Other concern',swimming:'Swimming',recreational_fishing:'Recreational fishing',paddling:'Paddling',boating:'Boating',commercial_fishing:'Commercial fishing',conservation:'Conservation',unknown:'Not analysed'};
const label = value => names[value] || String(value || 'Unknown').replaceAll('_',' ');
const shortDate = value => value ? new Date(value).toLocaleDateString('en-NZ',{day:'numeric',month:'short',year:'numeric'}) : 'Not supplied';
const controls=['community','activity','category','review','contributor','quality','search'];
let allRecords=[],selection=[],page=0;
const PAGE_SIZE=20;
const qualityNotes=record=>record.quality_flags.filter(flag=>flag!=='synthetic_demo');
function options(id,values){const select=q(id);for(const value of [...new Set(values)].sort()){const option=document.createElement('option');option.value=value;option.textContent=label(value);select.append(option);}}
function counts(values){const totals={};for(const value of values)totals[value]=(totals[value]||0)+1;return totals;}
function bars(selector,values){const totals=Object.entries(counts(values)).sort((a,b)=>b[1]-a[1]);const maximum=Math.max(1,...totals.map(x=>x[1]));q(selector).innerHTML=totals.length?totals.map(([key,count])=>`<div class="bar-row"><span>${safe(label(key))}</span><div class="bar-track"><div class="bar-fill" style="width:${count/maximum*100}%"></div></div><b>${count}</b></div>`).join(''):'<p class="dataset-note">No matching observations.</p>';}
function applyFilters(){
  const values=Object.fromEntries(controls.map(name=>[name,q('#data-'+name).value]));
  selection=allRecords.filter(record=>{
    const categories=record.analysis?.pollution_types||[];
    return (!values.community||record.community===values.community)&&(!values.activity||record.activity===values.activity)&&(!values.category||(values.category==='none'?record.analysis&&categories.length===0:categories.includes(values.category)))&&(!values.review||record.review_status===values.review)&&(!values.contributor||record.contributor_type===values.contributor)&&(!values.quality||(values.quality==='flagged'?qualityNotes(record).length>0:values.quality==='duplicate'?!!record.duplicate_of:qualityNotes(record).length===0))&&(!values.search||[record.id,record.feelings,record.analysis?.summary].join(' ').toLowerCase().includes(values.search.toLowerCase()));
  });
  page=0;renderSelection();
}
function timeline(){
  if(!allRecords.length)return;
  const first=new Date(allRecords.reduce((earliest,r)=>r.created<earliest?r.created:earliest,allRecords[0].created));
  first.setUTCHours(0,0,0,0);const week=7*24*60*60*1000;
  const last=Math.max(...allRecords.map(r=>new Date(r.created).getTime()));const bins=Array(Math.floor((last-first)/week)+1).fill(0);
  for(const record of selection)bins[Math.floor((new Date(record.created)-first)/week)]++;
  const maximum=Math.max(1,...bins);
  q('#timeline-chart').innerHTML=bins.map((count,i)=>{const date=new Date(first.getTime()+i*week).toLocaleDateString('en-NZ',{day:'numeric',month:'short',timeZone:'UTC'});return `<div class="timeline-bin" title="Week of ${safe(date)}: ${count} simulated observations"><b>${count}</b><div class="timeline-bar" style="height:${Math.max(2,count/maximum*100)}px"></div><span>${safe(date)}</span></div>`;}).join('');
}
function renderSelection(){
  q('#matching-count').textContent=`${selection.length} of ${allRecords.length} fictional observations match these filters.`;
  bars('#activity-chart',selection.map(r=>r.activity));bars('#category-chart',selection.flatMap(r=>r.analysis?.pollution_types||[]));timeline();renderTable();
}
function renderTable(){
  const rows=selection.slice(page*PAGE_SIZE,(page+1)*PAGE_SIZE);
  q('#dataset-rows').innerHTML=rows.length?rows.map(record=>`<tr><td><strong>${safe(record.community)}</strong><small>${shortDate(record.observed_at)}</small></td><td><strong>${safe((record.analysis?.summary||record.feelings).replace(/^\[DEMO[^\]]*\]\s*/i,''))}</strong><small>${safe((record.analysis?.pollution_types||[]).map(label).join(' · ')||'No classified concern')} · ${safe(label(record.status))}</small></td><td>${safe(label(record.activity))}</td><td>${record.contributor_type==='guest'?'Guest<small>Rewards waived</small>':`${safe(record.author_name)}<small>Fictional account</small>`}</td><td><span class="dataset-pill ${safe(record.review_status)}">${safe(label(record.review_status))}</span>${qualityNotes(record).length?`<small>${qualityNotes(record).length} quality notes</small>`:''}</td><td>${record.points_awarded}</td><td><button class="dataset-inspect" data-inspect="${safe(record.id)}" type="button">Inspect ↗</button></td></tr>`).join(''):'<tr><td colspan="7">No observations match. Try removing a filter.</td></tr>';
  const pages=Math.max(1,Math.ceil(selection.length/PAGE_SIZE));q('#page-number').textContent=`Page ${page+1} of ${pages}`;q('#previous-page').disabled=page===0;q('#next-page').disabled=page+1>=pages;
}
function inspect(id){
  const record=allRecords.find(r=>r.id===id);if(!record)return;
  q('#record-title').textContent=record.community;
  const facts=[['Observed',shortDate(record.observed_at)],['Submitted',shortDate(record.created)],['Contributor',record.contributor_type==='guest'?'Guest · permanently waived rewards':record.author_name],['Activity',label(record.activity)],['Analysis status',label(record.status)],['Simulated review',label(record.review_status)],['Demo points',record.points_awarded],['Dataset consent (simulated)',record.dataset_consent?'Opted in':'Not opted in']];
  q('#record-body').innerHTML=`<img class="record-image" src="${safe(record.photo)}" alt="Clearly labelled synthetic test illustration, not a real coastal photograph"><div class="record-facts">${facts.map(([key,value])=>`<div><span>${safe(key)}</span>${safe(value)}</div>`).join('')}</div><h3>Scripted observation</h3><p>${safe(record.feelings)}</p><h3>Quality notes</h3><p>${safe(record.quality_flags.join(' · ')||'No quality flags')}</p>${record.duplicate_of?`<p>Repeated image linked to ${safe(record.duplicate_of)}</p>`:''}<h3>Simulated analysis</h3><p class="dataset-note">This result was scripted for the demonstration. No AI model analysed this image.</p><pre>${safe(record.analysis?JSON.stringify(record.analysis,null,2):'No analysis available for this scenario.')}</pre><h3>Community follow-up</h3>${record.comments.map(c=>`<p><strong>${safe(c.author)}</strong><br>${safe(c.body)}</p>`).join('')||'<p>No follow-up comments.</p>'}<p class="dataset-note">Record ${safe(record.id)} · ${safe(record.schema_version)} · Synthetic data only.</p>`;
  q('#dataset-detail').showModal();
}
for(const name of controls)q('#data-'+name).addEventListener(name==='search'?'input':'change',applyFilters);
q('#reset-filters').addEventListener('click',()=>{for(const name of controls)q('#data-'+name).value='';applyFilters();});
q('#previous-page').addEventListener('click',()=>{page--;renderTable();});q('#next-page').addEventListener('click',()=>{page++;renderTable();});
q('#dataset-rows').addEventListener('click',event=>{const button=event.target.closest('[data-inspect]');if(button)inspect(button.dataset.inspect);});
q('#close-record').addEventListener('click',()=>q('#dataset-detail').close());
function showInput(){
  const record=allRecords.find(r=>r.id===q('#ai-example').value);if(!record)return;
  q('#ai-photo').src=record.photo;q('#ai-words').textContent=record.feelings;
  q('#ai-output').innerHTML='<p class="dataset-note">Select “Show simulated analysis” to inspect this example.</p>';
  q('#ai-stored').innerHTML='<p class="dataset-note">Original input is preserved separately from analysis and review.</p>';
  q('#ai-stage').textContent='Fictional input selected. This demonstration does not call an AI model.';
}
q('#ai-example').addEventListener('change',showInput);
q('#ai-inspect').addEventListener('click',()=>inspect(q('#ai-example').value));
q('#replay-ai').addEventListener('click',()=>{
  const record=allRecords.find(r=>r.id===q('#ai-example').value);if(!record)return;
  const a=record.analysis;
  q('#ai-output').innerHTML=a?`<p><strong>Visible evidence (scripted)</strong><br>${safe(a.visible_evidence.join(' '))}</p><p><strong>Reported experience (scripted)</strong><br>${safe(a.reported_experience)}</p><p><strong>Uncertainty</strong><br>${safe(a.uncertainties.join(' '))}</p><details><summary>View structured JSON</summary><pre>${safe(JSON.stringify(a,null,2))}</pre></details>`:`<p>No output in this scenario.</p><p class="dataset-note">Status: ${safe(record.status)}. Original evidence remains stored when analysis is pending, unavailable or skipped for a repeated image.</p>`;
  q('#ai-stored').innerHTML=`<dl><dt>Observation ID</dt><dd>${safe(record.id)}</dd><dt>Model identifier</dt><dd>${safe(record.model||'No model result')}</dd><dt>Quality notes</dt><dd>${safe(qualityNotes(record).join(', ')||'No quality flags')}</dd><dt>Simulated review</dt><dd>${safe(record.review_status)}</dd><dt>Demo points</dt><dd>${record.points_awarded}${record.rewards_waived?' · Guest rewards waived':''}</dd></dl>`;
  q('#ai-stage').textContent='Showing a stored, scripted example. No AI request, database write or real reward was created.';
});
q('#download-filtered').addEventListener('click',()=>{
  const fields=['id','synthetic','community','observed_at','created','activity','contributor_type','rewards_waived','status','review_status','points_awarded','dataset_consent','duplicate_of','feelings','pollution_types','quality_flags'];
  const cell=value=>'"'+String(value??'').replaceAll('"','""')+'"';
  const csv='\uFEFF'+[fields.join(','),...selection.map(record=>fields.map(field=>cell(field==='pollution_types'?(record.analysis?.pollution_types||[]).join(';'):field==='quality_flags'?record.quality_flags.join(';'):record[field])).join(','))].join('\r\n');
  const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download='wainet-synthetic-selection.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
async function start(){
  try{const response=await fetch(document.body.dataset.demoSource||'/api/demo-data');if(!response.ok)throw new Error('Could not load the demonstration dataset.');const data=await response.json();if(!data.synthetic||data.observations.some(r=>!r.synthetic))throw new Error('This page only accepts explicitly synthetic data.');
    allRecords=data.observations;const s=data.summary;
    const stats=[['Fictional observations',s.observations,`${Object.keys(s.communities).length} Wellington coastal communities`],['Fictional contributors',s.users,`${s.guest_observations} guest uploads · rewards waived`],['Simulated attention alerts',s.alerts,'Demonstration rules, not real pollution'],['Simulated review approvals',s.review_status.approved||0,`${s.duplicates} repeated images linked separately`]];
    q('#dataset-stats').innerHTML=stats.map(([title,total,note])=>`<article class="dataset-stat"><span>${safe(title)}</span><strong>${total}</strong><small>${safe(note)}</small></article>`).join('');
    q('#dataset-period').textContent=`Fictional reporting period: ${shortDate(s.submitted_from)} – ${shortDate(s.submitted_to)}`;
    const purposes={observations:'Images, original words, model output and quality flags',users:'Fictional account identities',comments:'Community follow-up observations',support:'Account support, separate from photographic evidence',reviews:'Simulated review decisions and audit history',points_ledger:'Account point adjustments; guests earn zero',alerts:'Simulated signals from repeated concerns'};
    q('#database-tables').innerHTML=Object.entries(s.table_counts||{}).map(([table,total])=>`<article><code>${safe(table)}</code><strong>${total}</strong><p>${safe(purposes[table]||'Demonstration records')}</p></article>`).join('');
    q('#ai-example').innerHTML=allRecords.map(record=>`<option value="${safe(record.id)}">${safe(record.scenario_id)} · ${safe(record.community)} · ${safe(label(record.activity))} · ${safe(record.status)}</option>`).join('');
    const example=allRecords.find(r=>r.analysis&&r.analysis.observation_type==='concern');if(example)q('#ai-example').value=example.id;showInput();
    options('#data-community',allRecords.map(r=>r.community));options('#data-activity',allRecords.map(r=>r.activity));options('#data-category',Object.keys(names).filter(k=>['litter','suspected_industrial','oil_or_fuel','suspected_wastewater','unusual_water','other'].includes(k)));const empty=document.createElement('option');empty.value='none';empty.textContent='Analysed · no pollution category';q('#data-category').append(empty);applyFilters();
  }catch(error){q('#dataset-error').textContent=error.message;q('#dataset-stats').innerHTML='';}
}
start();
