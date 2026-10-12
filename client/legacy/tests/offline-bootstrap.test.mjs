import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const source = await readFile(new URL('../js/capture-v2-offline-worker.js', import.meta.url), 'utf8');
const page = 'https://kt.test/student/sessions/7/questions/13/';
const authorizedBootstrap = { version: 1, page_path: '/student/sessions/7/questions/13/', assessment_session_id: 7, assignment_id: 13, user: { id: 2 } };
async function prepare(bootstrap = authorizedBootstrap, {
  headers = { 'X-Capture-Contract': 'capture-v2' },
  status = 200,
  failCacheWrite = false,
  sourcePage = page,
  api = '/api/v1/student/sessions/7/questions/13/',
  messageState = { session_id: 7, assignment: { assignment_id: 13, attempt_id: 21 }, process: { visit_id: 41, entry_event_id: 'entry-event' }, offline_authorization: { user_id: 2, session_id: 7, assignment_id: 13 }, entry_receipt: { session_id: 7, assignment_id: 13, attempt_id: 21, visit_id: 41, entry_event_id: 'entry-event' } },
} = {}) {
  const listeners = {}, cache = new Map();
  let offline = false;
  const workerScope = { location: new URL(page), addEventListener: (name, handler) => { listeners[name] = handler; } };
  const messageSource = { url: sourcePage };
  vm.runInNewContext(source, {
    URL, Response, Headers,
    self: workerScope,
    caches: { open: async () => ({
      match: async url => cache.get(url)?.clone(),
      put: async (url, response) => { if (failCacheWrite) throw new Error('cache_write_failed'); cache.set(url, response); },
    }) },
    fetch: async url => {
      if (offline) throw new TypeError('offline');
      return String(url).endsWith('/api/v1/auth/session/')
        ? new Response(JSON.stringify({ data: { user: { id: 2 } }, meta: { request_id: 'auth-test' } }))
        : new Response('<html><head><meta name="kt-shell-build" content="development"></head><body><div id="kt-app"></div></body></html>', { headers, status });
    },
  });
  let pending, result;
  listeners.message({
    source: messageSource,
    data: { type: 'prepare-capture-v2', buildVersion: 'development', shell: 'https://kt.test/static/spa-assets/capture-shell-development.html', page, api, state: messageState, resources: [], bootstrap },
    waitUntil: promise => { pending = promise; }, ports: [{ postMessage: value => { result = value; } }],
  });
  await pending;
  let html = '';
  if (result.ok) {
    offline = true;
    let response;
    listeners.fetch({ request: { url: page, method: 'GET' }, respondWith: value => { response = value; } });
    html = await (await response).text();
  }
  return { result, html };
}

test('a versioned shell carries escaped, page-bound bootstrap for offline refresh', async () => {
  const bootstrap = { version: 1, page_path: '/student/sessions/7/questions/13/', assessment_session_id: 7, assignment_id: 13, csrf_token: 'token', user: { id: 2, display_name: '</script><script>bad()</script>' } };
  const { result, html } = await prepare(bootstrap);
  assert.equal(result.ok, true);
  const match = /<script id="kt-offline-bootstrap" type="application\/json">([\s\S]*?)<\/script>/.exec(html);
  assert.ok(match, 'offline bootstrap must be part of this cached page');
  assert.deepEqual(JSON.parse(match[1]), bootstrap);
  assert.ok(!html.includes('<script>bad()'));
});

test('bootstrap for another question is rejected', async () => {
  const { result } = await prepare({ version: 1, page_path: '/student/sessions/99/questions/13/', assessment_session_id: 99, assignment_id: 13 });
  assert.equal(result.ok, false);
});

test('a readonly DTO without formal entry authorization is never cached', async () => {
  const { result, html } = await prepare(undefined, { messageState: { session_id: 7, assignment: { assignment_id: 13 } } });
  assert.equal(result.ok, false);
  assert.match(result.error, /invalid_authorized_dto/);
  assert.equal(html, '');
});

test('failed shell responses cannot become offline question pages even with the marker', async () => {
  const { result, html } = await prepare(undefined, { status: 500 });
  assert.equal(result.ok, false);
  assert.match(result.error, /capture_shell_unavailable/);
  assert.equal(html, '');
});

test('cache write failure is reported without falsely declaring preparation successful', async () => {
  const { result } = await prepare(undefined, { failCacheWrite: true });
  assert.equal(result.ok, false);
  assert.match(result.error, /cache_write_failed/);
});

test('a question client may prepare another authorized question in the same session', async () => {
  const bootstrap = authorizedBootstrap;
  const { result } = await prepare(bootstrap, {
    sourcePage: 'https://kt.test/student/sessions/7/questions/12/',
  });
  assert.equal(result.ok, true);
});

test('a question client from another session cannot authorize the target page', async () => {
  const bootstrap = { version: 1, page_path: '/student/sessions/7/questions/13/', assessment_session_id: 7, assignment_id: 13 };
  const { result } = await prepare(bootstrap, {
    sourcePage: 'https://kt.test/student/sessions/99/questions/12/',
  });
  assert.equal(result.ok, false);
  assert.match(result.error, /invalid_scope/);
});

test('a cross-origin question client cannot authorize the target page', async () => {
  const bootstrap = { version: 1, page_path: '/student/sessions/7/questions/13/', assessment_session_id: 7, assignment_id: 13 };
  const { result } = await prepare(bootstrap, {
    sourcePage: 'https://other.test/student/sessions/7/questions/12/',
  });
  assert.equal(result.ok, false);
  assert.match(result.error, /invalid_scope/);
});

test('a non-question client cannot authorize the target page', async () => {
  const bootstrap = { version: 1, page_path: '/student/sessions/7/questions/13/', assessment_session_id: 7, assignment_id: 13 };
  const { result } = await prepare(bootstrap, { sourcePage: 'https://kt.test/student/' });
  assert.equal(result.ok, false);
  assert.match(result.error, /invalid_scope/);
});

test('state and bootstrap IDs that disagree with the target question URL are rejected', async () => {
  const bootstrap = { version: 1, page_path: '/student/sessions/7/questions/13/', assessment_session_id: 7, assignment_id: 12 };
  const { result } = await prepare(bootstrap, {
    messageState: { session_id: 7, assignment: { assignment_id: 12 } },
  });
  assert.equal(result.ok, false);
  assert.match(result.error, /invalid_question_scope/);
});

test('online navigation does not overwrite the prepared offline bootstrap with plain HTML', async () => {
  const listeners = {};
  let writes = 0, pending;
  vm.runInNewContext(source, {
    URL, Response, Headers,
    self: { location: new URL(page), addEventListener: (name, handler) => { listeners[name] = handler; } },
    caches: { open: async () => ({
      match: async () => new Response(JSON.stringify({ userId: 2, sessionId: 7, buildVersion: 'development' })),
      put: async () => { writes += 1; },
    }) },
    fetch: async () => new Response('<html><head></head></html>', { headers: { 'X-Capture-Contract': 'capture-v2' } }),
  });
  listeners.fetch({ request: { url: page, method: 'GET' }, respondWith: value => { pending = value; } });
  assert.equal((await pending).status, 200);
  assert.equal(writes, 0);
});
