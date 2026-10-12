import { spawnSync } from 'node:child_process'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const clientDir = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const contract = resolve(clientDir, '..', 'contracts', 'openapi.yaml')
const openApiCli = join(clientDir, 'node_modules', 'openapi-typescript', 'bin', 'cli.js')

const result = spawnSync(
  process.execPath,
  [openApiCli, contract, '-o', join(clientDir, 'src', 'api', 'generated', 'schema.d.ts')],
  { cwd: clientDir, encoding: 'utf8', shell: false },
)
if (result.stdout) process.stdout.write(result.stdout)
if (result.stderr) process.stderr.write(result.stderr)
if (result.error) throw result.error
if (result.status !== 0) process.exit(result.status ?? 1)
