import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const source = await readFile(new URL('../../../static/js/capture-v2-offline-worker.js', import.meta.url), 'utf8');
const page = 'https://kt.test/student/sessions/7/questions/13/';
async function prepare(bootstrap) {
  const listeners = {}, cache = new Map();
  vm.runInNewContext(source, {
    URL, Response, Headers,
    self: { location: new URL(page), addEventListener: (name, handler) => { listeners[name] = handler; } },
    caches: { open: async () => ({ put: async (url, response) => { cache.set(url, response); } }) },
    fetch: async () => new Response('<html><head></head><body><div id="kt-app"></div></body></html>', { headers: { 'X-Capture-Contract': 'capture-v2' } }),
  });
  let pending, result;
  listeners.message({
    source: { url: page },
    data: { type: 'prepare-capture-v2', page, api: '/api/v1/student/sessions/7/questions/13/', state: { session_id: 7, assignment: { assignment_id: 13 } }, resources: [], bootstrap },
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
