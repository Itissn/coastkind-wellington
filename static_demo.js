/* Static, fictional dataset adapter for GitHub Pages. No backend writes. */
(() => {
  'use strict';
  const source = new URL('data/preview.json', document.baseURI);
  let loading;
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
  window.coastkindDemoApi = async (path, options = {}) => {
    if ((options.method || 'GET').toUpperCase() !== 'GET') {
      throw error('This fictional presentation is read-only. Uploads, accounts and rewards are unavailable.', 405);
    }
    if (path === '/api/health') return {storage: 'static synthetic dataset', demoMode: true, synthetic: true, aiConfigured: false};
    if (path === '/api/auth/me') return {user: null, csrfToken: null};
    if (path === '/api/wallet' || path === '/api/rewards/mine') throw error('Accounts and rewards are unavailable in this presentation.', 401);
    if (path === '/api/rewards/catalog') return {rewards: []};
    if (path === '/api/observations') return {observations: (await dataset()).observations};
    if (path === '/api/concerns') { const data = await dataset(); return {concerns: data.concerns || [], alerts: data.alerts || []}; }
    if (path === '/api/demo-data') return dataset();
    throw error('This route is not available in the presentation.', 404);
  };
})();
