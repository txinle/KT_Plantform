import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const clientDir = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repositoryDir = resolve(clientDir, '..')

test('API type generation and drift checks work with only client and committed contracts', () => {
  const fixture = mkdtempSync(join(tmpdir(), 'kt-client-contract-'))
  try {
    const client = join(fixture, 'client')
    mkdirSync(join(client, 'scripts'), { recursive: true })
    mkdirSync(join(client, 'src', 'api', 'generated'), { recursive: true })
    mkdirSync(join(fixture, 'contracts'))
    for (const script of ['generate-api-schema.mjs', 'check-api-types.mjs']) {
      copyFileSync(join(clientDir, 'scripts', script), join(client, 'scripts', script))
    }
    symlinkSync(join(clientDir, 'node_modules'), join(client, 'node_modules'), process.platform === 'win32' ? 'junction' : 'dir')
    copyFileSync(join(repositoryDir, 'contracts', 'openapi.yaml'), join(fixture, 'contracts', 'openapi.yaml'))
    const run = script => spawnSync(process.execPath, [join(client, 'scripts', script)], {
      cwd: client, encoding: 'utf8', env: { ...process.env, PYTHON: 'missing-python' },
    })
    const generated = run('generate-api-schema.mjs')
    assert.equal(generated.status, 0, generated.stderr || generated.stdout)
    const types = join(client, 'src', 'api', 'generated', 'schema.d.ts')
    assert.equal(readFileSync(types, 'utf8'), readFileSync(join(clientDir, 'src', 'api', 'generated', 'schema.d.ts'), 'utf8'))
    const current = run('check-api-types.mjs')
    assert.equal(current.status, 0, current.stderr || current.stdout)
    writeFileSync(types, '// stale types\n')
    const stale = run('check-api-types.mjs')
    assert.equal(stale.status, 1)
    assert.match(stale.stderr, /TypeScript types are stale/)
  } finally {
    rmSync(fixture, { recursive: true, force: true })
  }
})

test('client owns capture sources and preserves the offline-worker delivery contract', () => {
  const vite = readFileSync(join(clientDir, 'vite.config.ts'), 'utf8')
  assert.match(vite, /\.\/legacy\/js\/capture-v2-offline-worker\.js/)
  assert.doesNotMatch(vite, /\.\.\/static|\.\.\/server/)
  assert.match(vite, /fileName: 'capture-v2-offline-worker\.js'/)
  for (const obsolete of ['collector-loader.ts', 'process-bridge.ts']) assert.equal(existsSync(join(clientDir, 'src', 'assessment-runtime', obsolete)), false)
  assert.equal(existsSync(join(clientDir, 'legacy', 'js', 'process-event-collector.js')), false)
  const controller = readFileSync(join(clientDir, 'src', 'assessment-runtime', 'session-controller.ts'), 'utf8')
  assert.doesNotMatch(controller, /KTProcessData|ensureProcessCollector/)
  const cache = readFileSync(join(clientDir, 'src', 'assessment-runtime', 'offline-cache.ts'), 'utf8')
  assert.match(cache, /OFFLINE_WORKER_URL = '\/assessment\/process\/capture-v2\/offline-worker\.js'/)
  assert.match(cache, /OFFLINE_WORKER_SCOPE = '\/student\/sessions\/'/)
})

test('client production image builds without server or a Python stage', () => {
  assert.ok(existsSync(join(clientDir, 'Dockerfile')), 'Client Dockerfile is required')
  const dockerfile = readFileSync(join(clientDir, 'Dockerfile'), 'utf8')
  assert.match(dockerfile, /FROM node:24/)
  assert.match(dockerfile, /COPY contracts\//)
  assert.match(dockerfile, /FROM nginx:/)
  assert.doesNotMatch(dockerfile, /COPY server|--from=python|manage\.py|requirements/)
  assert.match(dockerfile, /\/app\/client\/dist\/spa-assets\/ \/static\/spa-assets\//)
  assert.match(dockerfile, /\/app\/client\/dist\/spa-assets\/assets\/ \/opt\/kt-frontend-assets\//)
  assert.match(dockerfile, /COPY --from=build \/app\/client\/dist\/spa-assets\/capture-shell-\*\.html \/opt\/kt-frontend-shells\//)
  assert.match(dockerfile, /COPY deploy\/docker\/20-frontend-assets\.sh \/docker-entrypoint\.d\//)
  assert.match(dockerfile, /\/app\/client\/legacy\/js\/ \/static\/js\//)
  const nginx = readFileSync(join(clientDir, 'nginx.conf'), 'utf8')
  assert.match(nginx, /location \/static\/ \{\s+alias \/static\//)
  assert.match(nginx, /alias \/static\/spa-assets\/assets\/shells\/\$1;/)
  assert.match(nginx, /Service-Worker-Allowed "\/student\/sessions\/" always/)
})
