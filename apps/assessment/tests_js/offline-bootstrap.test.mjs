import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const source = await readFile(new URL('../../../static/js/capture-v2-offline-worker.js', import.meta.url), 'utf8');
const page = 'https://kt.test/student/sessions/7/questions/13/';
async function prepare(bootstrap, {
  headers = { 'X-Capture-Contract': 'capture-v2' },
  status = 200,
  failCacheWrite = false,
  sourcePage = page,
  api = '/api/v1/student/sessions/7/questions/13/',
  messageState = { session_id: 7, assignment: { assignment_id: 13 } },
} = {}) {
  const listeners = {}, cache = new Map();
  const workerScope = { location: new URL(page), addEventListener: (name, handler) => { listeners[name] = handler; } };
  const messageSource = { url: sourcePage };
  vm.runInNewContext(source, {
    URL, Response, Headers,
    self: workerScope,
    caches: { open: async () => ({ put: async (url, response) => { if (failCacheWrite) throw new Error('cache_write_failed'); cache.set(url, response); } }) },
    fetch: async () => new Response('<html><head></head><body><div id="kt-app"></div></body></html>', { headers, status }),
  });
  let pending, result;
  listeners.message({
    source: messageSource,
    data: { type: 'prepare-capture-v2', page, api, state: messageState, resources: [], bootstrap },
    waitUntil: promise => { pending = promise; }, ports: [{ postMessage: value => { result = value; } }],
  });
  await pending;
  return { result, html: cache.has(page) ? await cache.get(page).text() : '' };
}

test('authorized question HTML carries escaped, page-bound bootstrap for offline refresh', async () => {
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

test('a successful HTML response without the authorized contract is never cached', async () => {
  const { result, html } = await prepare(undefined, { headers: {} });
  assert.equal(result.ok, false);
  assert.match(result.error, /capture_contract_unavailable/);
  assert.equal(html, '');
});

test('failed HTML responses cannot become offline question pages even with the marker', async () => {
  const { result, html } = await prepare(undefined, { status: 500 });
  assert.equal(result.ok, false);
  assert.match(result.error, /capture_contract_unavailable/);
  assert.equal(html, '');
});

test('cache write failure is reported without falsely declaring preparation successful', async () => {
  const { result } = await prepare(undefined, { failCacheWrite: true });
  assert.equal(result.ok, false);
  assert.match(result.error, /cache_write_failed/);
});

test('a question client may prepare another authorized question in the same session', async () => {
  const bootstrap = { version: 1, page_path: '/student/sessions/7/questions/13/', assessment_session_id: 7, assignment_id: 13 };
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
    caches: { open: async () => ({ put: async () => { writes += 1; } }) },
    fetch: async () => new Response('<html><head></head></html>', { headers: { 'X-Capture-Contract': 'capture-v2' } }),
  });
  listeners.fetch({ request: { url: page, method: 'GET' }, respondWith: value => { pending = value; } });
  assert.equal((await pending).status, 200);
  assert.equal(writes, 0);
});
