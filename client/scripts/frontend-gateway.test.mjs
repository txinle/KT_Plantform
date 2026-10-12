import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { URL } from 'node:url'

const { fetch } = globalThis

test('independent gateway serves only registered GET/HEAD pages and preserves API transport', async (t) => {
  const { createFrontendGateway, loadFrontendRoutes } = await import('./frontend-gateway.mjs')
  const directory = await mkdtemp(join(tmpdir(), 'kt-gateway-'))
  t.after(() => rm(directory, { recursive: true, force: true }))
  await mkdir(join(directory, 'assets'))
  await writeFile(join(directory, 'index.html'), '<html>client-owned-shell</html>')
  await writeFile(join(directory, 'assets/app.js'), 'client-owned-script')
  await writeFile(join(directory, 'capture-v2-offline-worker.js'), 'client-owned-worker')
  const requests = []
  const upstream = createServer(async (req, res) => {
    let body = ''
    for await (const chunk of req) body += chunk
    requests.push({ url: req.url, method: req.method, cookie: req.headers.cookie, host: req.headers.host, body })
    res.writeHead(201, { 'Content-Type': 'application/json', 'X-Request-ID': 'server-request', 'Set-Cookie': 'sessionid=next; HttpOnly' })
    res.end(JSON.stringify({ data: { confirmed: true }, meta: { request_id: 'server-request' } }))
  })
  await new Promise((resolve) => upstream.listen(0, '127.0.0.1', resolve))
  t.after(() => new Promise((resolve) => upstream.close(resolve)))
  const gateway = createFrontendGateway({ frontendDir: directory, backendOrigin: `http://127.0.0.1:${upstream.address().port}`, routes: loadFrontendRoutes() })
  await new Promise((resolve) => gateway.listen(0, '127.0.0.1', resolve))
  t.after(() => new Promise((resolve) => gateway.close(resolve)))
  const origin = `http://127.0.0.1:${gateway.address().port}`
  for (const path of ['/', '/teacher/', '/login/', '/teacher/courses/7/', '/student/sessions/2/questions/3/?reason=resume',
    '/teacher/courses/7/experiments/8/class-tasks/9/grading/']) {
    const response = await fetch(origin + path)
    assert.equal(response.status, 200, path)
    assert.equal(await response.text(), '<html>client-owned-shell</html>')
  }
  const head = await fetch(origin + '/login/', { method: 'HEAD' })
  assert.equal(head.status, 200)
  assert.equal(await head.text(), '')
  for (const path of ['/unknown/', '/teacher/courses/not-an-int/', '/teacher/courses/7/unknown/', '/api/old/', '/logout/']) {
    const missing = await fetch(origin + path)
    assert.equal(missing.status, 404, path)
    assert.equal(missing.headers.get('content-type'), 'text/plain; charset=utf-8')
    assert.equal(await missing.text(), 'Not found')
  }
  assert.equal((await fetch(origin + '/login/', { method: 'POST', body: 'username=x' })).status, 405)
  assert.equal((await fetch(origin + '/static/spa-assets/assets/app.js')).status, 200)
  assert.equal((await fetch(origin + '/static/spa-assets/missing.js')).status, 404)
  assert.equal((await fetch(origin + '/static/js/capture-v2-offline-worker.js')).status, 404)
  const worker = await fetch(origin + '/assessment/process/capture-v2/offline-worker.js')
  assert.equal(worker.status, 200)
  assert.equal(worker.headers.get('service-worker-allowed'), '/student/sessions/')
  assert.equal(worker.headers.get('content-type'), 'application/javascript')
  assert.equal(await worker.text(), 'client-owned-worker')
  assert.equal(requests.length, 0, 'business HTML and client assets must never reach Django')
  const response = await fetch(origin + '/api/v1/student/attempts/4/draft/?x=1', { method: 'POST', headers: { Cookie: 'sessionid=original', 'Content-Type': 'application/json' }, body: '{"answer":"甲"}' })
  assert.equal(response.status, 201)
  assert.equal(response.headers.get('x-request-id'), 'server-request')
  assert.equal(response.headers.get('set-cookie'), 'sessionid=next; HttpOnly')
  assert.deepEqual(requests[0], { url: '/api/v1/student/attempts/4/draft/?x=1', method: 'POST', cookie: 'sessionid=original', host: new URL(origin).host, body: '{"answer":"甲"}' })
  for (const path of ['/admin/login/', '/health/', '/media/authorized.png', '/static/admin/css/base.css']) {
    assert.equal((await fetch(origin + path)).status, 201, path)
  }
})
