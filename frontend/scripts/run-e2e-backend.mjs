import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendDir = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repositoryDir = resolve(frontendDir, '..')
const localPython = process.platform === 'win32'
  ? join(repositoryDir, '.venv', 'Scripts', 'python.exe')
  : join(repositoryDir, '.venv', 'bin', 'python')
const python = process.env.PYTHON || (existsSync(localPython) ? localPython : 'python')
const children = []
let shuttingDown = false

function start(args) {
  const child = spawn(python, args, {
    cwd: repositoryDir,
    env: process.env,
    stdio: 'inherit',
  })
  children.push(child)
  return child
}

function shutdown(exitCode) {
  if (shuttingDown) return
  shuttingDown = true
  for (const child of children) {
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM')
  }
  globalThis.setTimeout(() => process.exit(exitCode), 500).unref()
}

const publicationWorker = start([
  'manage.py',
  'run_publication_jobs',
  '--poll-seconds',
  '0.1',
])
const djangoServer = start([
  'manage.py',
  'runserver',
  '127.0.0.1:8000',
  '--noreload',
])

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => shutdown(0))
}

djangoServer.on('exit', (code) => shutdown(code ?? 1))
publicationWorker.on('exit', (code) => shutdown(code ?? 1))

await new Promise(() => {})
