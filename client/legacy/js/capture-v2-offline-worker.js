/* Only previously authorized capture-v2 question pages are available offline. */
const PREFIX = 'kt-capture-v2-runtime-3-';
const META_CACHE = `${PREFIX}identity`;
const META_URL = `${self.location.origin}/assessment/process/capture-v2/offline-identity`;
const questionPage = /^\/student\/sessions\/(\d+)\/questions\/(\d+)\/$/;
const questionApi = /^\/api\/v1\/student\/sessions\/(\d+)\/questions\/(\d+)\/$/;
const asset = (url) => url.origin === self.location.origin && url.pathname.startsWith('/static/');
// These URLs carry content hashes emitted by Vite or Django ManifestStaticFilesStorage.
const immutableAsset = (url) => (url.pathname.startsWith('/static/spa-assets/assets/')
  && /-[A-Za-z0-9_-]{8,}\.[a-z0-9]+$/.test(url.pathname)) || /\.[a-f0-9]{12}\.[a-z0-9]+$/.test(url.pathname);
let identityEpoch = 0;
async function identity() {
  const cache = await caches.open(META_CACHE);
  const response = await cache.match(META_URL);
  return response ? response.json() : null;
}
const pageCacheName = (scope) => `${PREFIX}question-${scope.userId}-${scope.sessionId}-${encodeURIComponent(scope.buildVersion)}`;
const assetCacheName = (scope) => `${PREFIX}assets-${encodeURIComponent(scope.buildVersion)}`;
const envelopeUrl = (page) => `${self.location.origin}/assessment/process/capture-v2/offline-envelope${page.pathname}`;
function authorizedState(state, bootstrap) {
  const authorization = state?.offline_authorization;
  const receipt = state?.entry_receipt;
  return authorization?.user_id === bootstrap?.user?.id
    && authorization?.session_id === state?.session_id && authorization?.assignment_id === state?.assignment?.assignment_id
    && receipt?.session_id === state?.session_id && receipt?.assignment_id === state?.assignment?.assignment_id
    && receipt?.attempt_id === state?.assignment?.attempt_id && receipt?.visit_id === state?.process?.visit_id
    && Number.isInteger(receipt?.visit_id) && receipt?.visit_id > 0
    && receipt?.entry_event_id === state?.process?.entry_event_id;
}
function escapedBootstrap(bootstrap) {
  return JSON.stringify(bootstrap).replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/&/g, '\\u0026');
}
async function clearRuntimeCaches() {
  identityEpoch++;
  const names = await caches.keys();
  await Promise.all(names.filter(name => name.startsWith('kt-capture-v2-runtime-')).map(name => caches.delete(name)));
}
async function prepareResources(cache, resources) {
  const urls = [...new Set(resources.map(resource => new URL(resource, self.location.origin).href))]
    .map(resource => new URL(resource)).filter(asset);
  let next = 0;
  let failure;
  await Promise.all(Array.from({ length: Math.min(4, urls.length) }, async () => {
    while (next < urls.length && !failure) {
      const url = urls[next++];
      try {
        if (immutableAsset(url) && await cache.match(url.href)) continue;
        const response = await fetch(url.href, { credentials: 'same-origin', cache: 'no-cache' });
        if (!response.ok) throw new Error(`mandatory_resource_unavailable:${response.status}:${url.pathname}`);
        await cache.put(url.href, response);
      } catch (error) { failure = error; }
    }
  }));
  if (failure) throw failure;
}
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil((async () => {
  const names = await caches.keys();
  await Promise.all(names.filter(name => name.startsWith('kt-capture-v2-runtime-') && !name.startsWith(PREFIX))
    .map(name => caches.delete(name)));
  await self.clients.claim();
})()));
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  const requestScope = questionPage.exec(url.pathname) || questionApi.exec(url.pathname);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin || !(requestScope || asset(url))) return;
  event.respondWith((async () => {
    const epoch = identityEpoch;
    const scope = await identity();
    const cache = scope && (asset(url) || Number(requestScope[1]) === scope.sessionId)
      ? await caches.open(asset(url) ? assetCacheName(scope) : pageCacheName(scope)) : null;
    try {
      const response = await fetch(event.request);
      if (response.ok && cache && asset(url)) {
        await cache.put(event.request, response.clone());
      }
      return response;
    } catch (error) {
      // Logout/switch invalidation may have occurred while a network request was pending.
      const current = await identity();
      if (!scope || current?.userId !== scope.userId || current?.sessionId !== scope.sessionId
          || current?.buildVersion !== scope.buildVersion) throw error;
      const response = cache && await cache.match(event.request, { ignoreSearch: !asset(url) });
      if (response && questionApi.test(url.pathname)) {
        const envelope = await response.json();
        if (epoch !== identityEpoch) throw error;
        return new Response(JSON.stringify({ ...envelope, data: { ...envelope.data, offline_recovery: true } }), {
          headers: { 'Content-Type': 'application/json', 'X-Capture-Offline': 'true' },
        });
      }
      if (questionPage.test(url.pathname) && cache) {
        const authorized = await cache.match(envelopeUrl(url));
        const envelope = authorized && await authorized.json();
        if (!envelope || !authorizedState(envelope.state, envelope.bootstrap)) throw error;
        const resources = await caches.open(assetCacheName(scope));
        const shell = await resources.match(envelope.shell);
        if (!shell) throw error;
        const html = (await shell.text()).replace('</head>', `<script id="kt-offline-bootstrap" type="application/json">${escapedBootstrap(envelope.bootstrap)}</script></head>`);
        if (epoch !== identityEpoch) throw error;
        return new Response(html, { headers: { 'Content-Type': 'text/html', 'X-Capture-Offline': 'true', 'X-Capture-Contract': 'capture-v2' } });
      }
      if (response && epoch === identityEpoch) return response;
      throw error;
    }
  })());
});
self.addEventListener('message', (event) => {
  if (event.data?.type === 'clear-capture-v2') {
    event.waitUntil(clearRuntimeCaches().then(() => event.ports[0]?.postMessage({ ok: true }))
      .catch(error => event.ports[0]?.postMessage({ ok: false, error: String(error) })));
    return;
  }
  if (event.data?.type !== 'prepare-capture-v2') return;
  event.waitUntil((async () => {
    try {
      let epoch = identityEpoch;
      const page = new URL(event.data.page);
      if (!event.source?.url) throw new Error('invalid_scope');
      const source = new URL(event.source.url);
      const pageScope = questionPage.exec(page.pathname);
      const sourceScope = questionPage.exec(source.pathname);
      if (page.origin !== self.location.origin || source.origin !== self.location.origin
          || !pageScope || !sourceScope || pageScope[1] !== sourceScope[1]) throw new Error('invalid_scope');
      if (Number(pageScope[1]) !== Number(event.data.state?.session_id)
          || Number(pageScope[2]) !== Number(event.data.state?.assignment?.assignment_id)) throw new Error('invalid_question_scope');
      const api = new URL(event.data.api, self.location.origin);
      if (api.origin !== self.location.origin || !questionApi.test(api.pathname)
          || api.pathname.replace('/api/v1', '') !== page.pathname) throw new Error('invalid_question_scope');
      const bootstrap = event.data.bootstrap;
      if (!bootstrap || bootstrap.version !== 1 || bootstrap.page_path !== page.pathname
          || bootstrap.assessment_session_id !== event.data.state.session_id
          || bootstrap.assignment_id !== event.data.state.assignment.assignment_id
          || !Number.isInteger(bootstrap.user?.id)) throw new Error('invalid_bootstrap_scope');
      const authResponse = await fetch(`${self.location.origin}/api/v1/auth/session/`, { credentials: 'same-origin', cache: 'no-store' });
      const auth = authResponse.ok ? (await authResponse.json()).data : null;
      if (epoch !== identityEpoch) throw new Error('offline_identity_changed');
      if (auth?.user?.id !== bootstrap.user.id) {
        await clearRuntimeCaches();
        throw new Error('invalid_account_scope');
      }
      const scope = { userId: auth.user.id, sessionId: event.data.state.session_id,
        buildVersion: String(event.data.buildVersion || 'development') };
      const prior = await identity();
      if (epoch !== identityEpoch) throw new Error('offline_identity_changed');
      if (prior && (prior.userId !== scope.userId || prior.buildVersion !== scope.buildVersion)) {
        epoch++;
        await clearRuntimeCaches();
        if (epoch !== identityEpoch) throw new Error('offline_identity_changed');
      }
      if (!authorizedState(event.data.state, bootstrap)) throw new Error('invalid_authorized_dto');
      const shell = new URL(event.data.shell, self.location.origin);
      if (shell.origin !== self.location.origin || shell.pathname !== `/static/spa-assets/capture-shell-${scope.buildVersion}.html`) throw new Error('invalid_shell_scope');
      const resourceCache = await caches.open(assetCacheName(scope));
      let shellResponse = await resourceCache.match(shell.href);
      if (!shellResponse) {
        const response = await fetch(shell.href, { credentials: 'same-origin', cache: 'no-cache' });
        if (!response.ok) throw new Error('capture_shell_unavailable');
        const html = await response.text();
        if (!html.includes(`name="kt-shell-build" content="${scope.buildVersion}"`) || !html.includes('id="kt-app"')) throw new Error('capture_shell_version_mismatch');
        shellResponse = new Response(html, { headers: { 'Content-Type': 'text/html' } });
        await resourceCache.put(shell.href, shellResponse.clone());
      }
      const html = await shellResponse.text();
      const shellResources = [...html.matchAll(/(?:src|href)=["']([^"']+)["']/g)].map(match => match[1]);
      await prepareResources(resourceCache, [...event.data.resources, ...shellResources]);
      if (epoch !== identityEpoch) throw new Error('offline_identity_changed');
      const cache = await caches.open(pageCacheName(scope));
      await cache.put(envelopeUrl(page), new Response(JSON.stringify({ bootstrap, state: event.data.state, shell: shell.href }), {
        headers: { 'Content-Type': 'application/json' },
      }));
      await cache.put(api.href, new Response(JSON.stringify({ data: event.data.state, meta: { request_id: 'offline-authorized-recovery' } }), {
        headers: { 'Content-Type': 'application/json', 'X-Capture-Contract': 'capture-v2' },
      }));
      if (epoch !== identityEpoch) throw new Error('offline_identity_changed');
      const metadata = await caches.open(META_CACHE);
      if (epoch !== identityEpoch) throw new Error('offline_identity_changed');
      await metadata.put(META_URL, new Response(JSON.stringify(scope), { headers: { 'Content-Type': 'application/json' } }));
      if (epoch !== identityEpoch) throw new Error('offline_identity_changed');
      event.ports[0]?.postMessage({ ok: true });
    } catch (error) { event.ports[0]?.postMessage({ ok: false, error: String(error) }); }
  })());
});
