import { spawn } from 'node:child_process'
import { createFrontendGateway } from './frontend-gateway.mjs'
import { requireIsolatedPostgres } from './e2e-database.mjs'
import { requireServerPython, serverDir } from './e2e-runtime.mjs'

const python = requireServerPython()
const environment = { ...process.env, ...requireIsolatedPostgres(process.env), PYTHONUTF8: '1' }
const gatewayPort = Number(process.env.E2E_GATEWAY_PORT || '8000')
const backendPort = Number(process.env.E2E_BACKEND_PORT || String(gatewayPort + 1))
const gateway = createFrontendGateway({ backendOrigin: `http://127.0.0.1:${backendPort}` })
const children = []
let shuttingDown = false

function start(args) {
  const child = spawn(python, args, {
    cwd: serverDir,
    env: environment,
    stdio: 'inherit',
  })
  children.push(child)
  return child
}

function shutdown(exitCode) {
  if (shuttingDown) return
  shuttingDown = true
  gateway.close()
  for (const child of children) {
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM')
  }
  globalThis.setTimeout(() => process.exit(exitCode), 500).unref()
}

const publicationWorker = process.env.E2E_PUBLICATION_WORKER === '1' ? start([
  'manage.py',
  'run_publication_jobs',
  '--poll-seconds',
  '0.1',
]) : undefined
const djangoServer = start([
  'manage.py',
  'runserver',
  `127.0.0.1:${backendPort}`,
  '--noreload',
])

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => shutdown(0))
}

djangoServer.on('exit', (code) => shutdown(code ?? 1))
publicationWorker?.on('exit', (code) => shutdown(code ?? 1))

gateway.listen(gatewayPort, '127.0.0.1')
gateway.on('error', (error) => { process.stderr.write(String(error) + '\n'); shutdown(1) })
await new Promise(() => {})
