import { spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendDir = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repositoryDir = resolve(frontendDir, '..')
const localPython = process.platform === 'win32'
  ? join(repositoryDir, '.venv', 'Scripts', 'python.exe')
  : join(repositoryDir, '.venv', 'bin', 'python')
const python = process.env.PYTHON || (existsSync(localPython) ? localPython : 'python')
const openApiCli = join(frontendDir, 'node_modules', 'openapi-typescript', 'bin', 'cli.js')
const temporaryDir = mkdtempSync(join(tmpdir(), 'kt-api-check-'))
const temporarySchema = join(temporaryDir, 'openapi-schema.yml')
const temporaryTypes = join(temporaryDir, 'schema.d.ts')

function run(command, args, cwd) {
  const result = spawnSync(command, args, { cwd, encoding: 'utf8', shell: false })
  if (result.error) throw result.error
  if (result.stdout) process.stdout.write(result.stdout)
  if (result.stderr) process.stderr.write(result.stderr)
  if (result.status !== 0) process.exit(result.status ?? 1)
}

try {
  run(
    python,
    ['manage.py', 'spectacular', '--file', temporarySchema, '--validate'],
    repositoryDir,
  )
  run(process.execPath, [openApiCli, temporarySchema, '-o', temporaryTypes], frontendDir)

  const comparisons = [
    [temporarySchema, join(repositoryDir, 'openapi-schema.yml')],
    [temporaryTypes, join(frontendDir, 'src', 'api', 'generated', 'schema.d.ts')],
  ]
  const stale = comparisons.some(
    ([generated, tracked]) =>
      !existsSync(tracked) || readFileSync(generated, 'utf8') !== readFileSync(tracked, 'utf8'),
  )
  if (stale) {
    process.stderr.write('OpenAPI schema or generated TypeScript types are stale. Run npm run api:generate.\n')
    process.exitCode = 1
  }
} finally {
  rmSync(temporaryDir, { recursive: true, force: true })
}
