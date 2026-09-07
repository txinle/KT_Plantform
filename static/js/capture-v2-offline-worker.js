/* Only previously authorized capture-v2 question pages are available offline. */
const CACHE = 'kt-capture-v2-runtime-1';
const questionPage = /^\/student\/sessions\/\d+\/questions\/\d+\/$/;
const questionApi = /^\/api\/v1\/student\/sessions\/\d+\/questions\/\d+\/$/;
const asset = (url) => url.origin === self.location.origin && url.pathname.startsWith('/static/');
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin
      || !(questionPage.test(url.pathname) || questionApi.test(url.pathname) || asset(url))) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    try {
      const response = await fetch(event.request);
      if (response.ok && (asset(url) || (questionApi.test(url.pathname) && response.headers.get('X-Capture-Contract') === 'capture-v2'))) {
        await cache.put(event.request, response.clone());
      }
      return response;
    } catch (error) {
      const response = await cache.match(event.request, { ignoreSearch: !asset(url) });
      if (response && questionApi.test(url.pathname)) {
        const state = await response.json();
        return new Response(JSON.stringify({ ...state, offline_recovery: true }), {
          headers: { 'Content-Type': 'application/json', 'X-Capture-Offline': 'true' },
        });
      }
      if (response) return response;
      throw error;
    }
  })());
});
self.addEventListener('message', (event) => {
  if (event.data?.type !== 'prepare-capture-v2') return;
  event.waitUntil((async () => {
    try {
      const page = new URL(event.data.page);
      const source = new URL(event.source.url);
      if (page.origin !== self.location.origin || page.href !== source.href || !questionPage.test(page.pathname)) throw new Error('invalid_scope');
      const cache = await caches.open(CACHE);
      const response = await fetch(page.href, { credentials: 'same-origin' });
      if (!response.ok || response.headers.get('X-Capture-Contract') !== 'capture-v2') throw new Error('capture_contract_unavailable');
      const bootstrap = event.data.bootstrap;
      if (bootstrap) {
        if (bootstrap.version !== 1 || bootstrap.page_path !== page.pathname
            || bootstrap.assessment_session_id !== event.data.state.session_id
            || bootstrap.assignment_id !== event.data.state.assignment.assignment_id) throw new Error('invalid_bootstrap_scope');
        const json = JSON.stringify(bootstrap).replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/&/g, '\\u0026');
        const html = (await response.text()).replace('</head>', `<script id="kt-offline-bootstrap" type="application/json">${json}</script></head>`);
        const headers = new Headers(response.headers);
        headers.delete('Content-Length');
        headers.delete('Content-Encoding');
        await cache.put(page.href, new Response(html, { status: response.status, headers }));
      } else {
        await cache.put(page.href, response);
      }
      const api = new URL(event.data.api, self.location.origin);
      if (api.origin !== self.location.origin || !questionApi.test(api.pathname)
          || api.pathname.replace('/api/v1', '') !== page.pathname) throw new Error('invalid_question_scope');
      await cache.put(api.href, new Response(JSON.stringify(event.data.state), {
        headers: { 'Content-Type': 'application/json', 'X-Capture-Contract': 'capture-v2' },
      }));
      for (const resource of event.data.resources) {
        const url = new URL(resource);
        if (!asset(url)) continue;
        const file = await fetch(url.href, { credentials: 'same-origin' });
        if (file.ok) await cache.put(url.href, file);
      }
      event.ports[0]?.postMessage({ ok: true });
    } catch (error) { event.ports[0]?.postMessage({ ok: false, error: String(error) }); }
  })());
});
