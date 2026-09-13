import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { requireIsolatedPostgres } from './e2e-database.mjs'

const frontendDir = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repositoryDir = resolve(frontendDir, '..')
const localPython = process.platform === 'win32'
  ? join(repositoryDir, '.venv', 'Scripts', 'python.exe')
  : join(repositoryDir, '.venv', 'bin', 'python')
const python = process.env.PYTHON || (existsSync(localPython) ? localPython : 'python')
const environment = {
  ...process.env,
  ...requireIsolatedPostgres(process.env),
  PYTHONUTF8: '1',
}

const commands = [['manage.py', 'migrate', '--noinput']]
commands.push(['manage.py', 'flush', '--noinput'])
commands.push(['manage.py', 'seed_demo'])
commands.push(['manage.py', 'seed_e2e_student_feedback'])
commands.push(['manage.py', 'seed_e2e_manual_short_answer'])
if (process.argv.includes('--capture-v2')) commands.push(['manage.py', 'seed_e2e_capture_v2'])

for (const args of commands) {
  const result = spawnSync(python, args, {
    cwd: repositoryDir,
    env: environment,
    encoding: 'utf8',
  })
  if (result.stdout) process.stdout.write(result.stdout)
  if (result.stderr) process.stderr.write(result.stderr)
  if (result.error) throw result.error
  if (result.status !== 0) process.exit(result.status ?? 1)
}
