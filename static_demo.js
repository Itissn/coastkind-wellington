/* Static, fictional dataset adapter for GitHub Pages. No backend writes. */
(() => {
  'use strict';
  const source = new URL('data/preview.json', document.baseURI);
  let loading;
  let officialLoading;
  window.wainetReloadOfficial = () => { officialLoading=null; };
  async function officialDataset(){
    if(!officialLoading)officialLoading=fetch(new URL('data/official.json',document.baseURI),{credentials:'omit',cache:'no-cache'}).then(async response=>{if(!response.ok)throw error('Official source snapshot unavailable.',503);const data=await response.json();if(data.kind!=='official-data-snapshot-v1'||!Array.isArray(data.communities))throw error('Invalid official source snapshot.',503);return data;}).catch(problem=>{officialLoading=null;throw problem;});
    return officialLoading;
  }
  function currentOfficialRecord(record){
    const copy=structuredClone(record),now=Date.now();
    for(const source of [copy.water,copy.geonet])if(source?.status==='available'&&(!source.retrieved_at||now-Date.parse(source.retrieved_at)>900000))source.status='stale';
    if(copy.geonet)copy.geonet.events=(copy.geonet.events||[]).filter(event=>{const age=now-Date.parse(event.time);return Number.isFinite(age)&&age>=0&&age<=30*86400000;});
    return copy;
  }
  function error(message, status) { const result = new Error(message); result.status = status; return result; }
  async function dataset() {
    if (!loading) loading = fetch(source, {credentials: 'omit'}).then(async response => {
      if (!response.ok) throw error('The demonstration dataset could not load.', response.status);
      const data = await response.json();
      if (data.synthetic !== true || data.dataset_kind !== 'synthetic-demo-v1' ||
          !Array.isArray(data.observations) || data.observations.some(row => row.synthetic !== true)) {
        throw error('This presentation requires an explicitly synthetic dataset.', 503);
      }
      return data;
    });
    return loading;
  }
  window.wainetDemoApi = async (path, options = {}) => {
    if ((options.method || 'GET').toUpperCase() !== 'GET') {
      throw error('This fictional presentation is read-only. Uploads, accounts and rewards are unavailable.', 405);
    }
    if (path === '/api/health') return {storage: 'static synthetic dataset', demoMode: true, synthetic: true, aiConfigured: false};
    if (path === '/api/auth/me') return {user: null, csrfToken: null};
    if (path === '/api/wallet' || path === '/api/rewards/mine') throw error('Accounts and rewards are unavailable in this presentation.', 401);
    if (path === '/api/rewards/catalog') return {rewards: []};
    if (path === '/api/observations') return {observations: (await dataset()).observations};
    if (path === '/api/community-status') {const data=await dataset();if(!data.community_status)throw error('Community signals are not available in this demo snapshot.',503);return data.community_status;}
    if (path === '/api/official-map') return (await officialDataset()).map || {water_stations:[],earthquakes:[]};
    if (path.startsWith('/api/official-data?')) {const community=new URL(path,location.origin).searchParams.get('community'),record=(await officialDataset()).communities.find(item=>item.community===community);if(!record)throw error('Choose a known coastal community.',400);return currentOfficialRecord(record);}
    if (path === '/api/concerns') { const data = await dataset(); return {concerns: data.concerns || [], alerts: data.alerts || []}; }
    if (path === '/api/demo-data') return dataset();
    throw error('This route is not available in the presentation.', 404);
  };
})();
