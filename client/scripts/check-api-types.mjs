import { spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const clientDir = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const contract = resolve(clientDir, '..', 'contracts', 'openapi.yaml')
const openApiCli = join(clientDir, 'node_modules', 'openapi-typescript', 'bin', 'cli.js')
const temporaryDir = mkdtempSync(join(tmpdir(), 'kt-api-check-'))
const temporaryTypes = join(temporaryDir, 'schema.d.ts')

function run(command, args, cwd) {
  const result = spawnSync(command, args, { cwd, encoding: 'utf8', shell: false })
  if (result.error) throw result.error
  if (result.stdout) process.stdout.write(result.stdout)
  if (result.stderr) process.stderr.write(result.stderr)
  if (result.status !== 0) process.exit(result.status ?? 1)
}

try {
  run(process.execPath, [openApiCli, contract, '-o', temporaryTypes], clientDir)
  const tracked = join(clientDir, 'src', 'api', 'generated', 'schema.d.ts')
  const stale = !existsSync(tracked) || readFileSync(temporaryTypes, 'utf8') !== readFileSync(tracked, 'utf8')
  if (stale) {
    process.stderr.write('Generated TypeScript types are stale. Run npm run api:generate.\n')
    process.exitCode = 1
  }
} finally {
  rmSync(temporaryDir, { recursive: true, force: true })
}
