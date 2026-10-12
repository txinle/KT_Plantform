import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const repositoryDir = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
const yaml = createRequire(import.meta.url)('js-yaml')

test('CI separates backend and client checks and uses the isolated server E2E environment', () => {
  const workflow = yaml.load(readFileSync(join(repositoryDir, '.github', 'workflows', 'ci.yml'), 'utf8'))
  const { backend, client, e2e } = workflow.jobs
  assert.equal(backend.defaults.run['working-directory'], 'server')
  assert.ok(!backend.steps.some(step => step.uses?.startsWith('actions/setup-node') || step['working-directory'] === 'client'))
  assert.ok(backend.steps.some(step => step.run === 'python manage.py export_api_schema --check'))
  assert.ok(!client.steps.some(step => step.uses?.startsWith('actions/setup-python')))
  assert.ok(client.steps.some(step => step.run === 'npm run test:scripts' && step['working-directory'] === 'client'))
  assert.equal(e2e.env.POSTGRES_DB, 'kt_e2e')
  assert.ok(e2e.steps.some(step => step.run?.includes('python -m venv server/.venv') && step.run.includes('server/.venv/bin/python -m pip')))
  assert.ok(e2e.steps.some(step => step.run?.includes('npm run build:check')))
})

test('portable package preserves server/client/contracts and launchers while excluding local state', { skip: process.platform !== 'win32' }, () => {
  const temporary = mkdtempSync(join(tmpdir(), 'kt-portable-layout-'))
  try {
    const source = join(temporary, 'source')
    const put = (relative, contents = 'fixture') => {
      const target = join(source, relative)
      mkdirSync(dirname(target), { recursive: true })
      writeFileSync(target, contents)
    }
    for (const folder of ['apps', 'config', 'fixtures', 'static', 'templates']) put(`server/${folder}/runtime.txt`)
    for (const file of ['manage.py', 'requirements.txt']) put(`server/${file}`)
    for (const file of ['run_local.bat', 'run_local.sh', 'START-HERE.txt', 'README.md', '.env.example']) put(file)
    for (const file of ['package.json', 'package-lock.json', 'src/main.ts', 'src/assessment-runtime/process-sender.ts']) put(`client/${file}`)
    put('contracts/openapi.yaml')
    for (const file of ['.env', 'school-test.env', 'client/.env', 'client/private/secret.txt', 'client/node_modules/package/index.js', 'client/dist/output.js', 'client/.e2e-results/run.json', 'server/apps/__pycache__/app.pyc', 'server/config/private/key.pem', 'server/.venv/secret.txt', 'server/media/upload.txt']) put(file, 'PRIVATE-SENTINEL')
    const scripts = join(source, 'server', 'scripts')
    mkdirSync(scripts, { recursive: true })
    for (const file of ['create_portable_package.ps1', 'create_portable_archive.py', 'run_dev.py']) {
      copyFileSync(join(repositoryDir, 'server', 'scripts', file), join(scripts, file))
    }
    const archivePython = join(repositoryDir, 'server', '.venv', 'Scripts', 'python.exe')
    put('server/scripts/find_python.ps1', `[Console]::Out.WriteLine('${archivePython.replaceAll("'", "''")}'); exit 0\n`)
    const result = spawnSync('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', join(scripts, 'create_portable_package.ps1'), '-OutputRoot', join(temporary, 'output')], { encoding: 'utf8', timeout: 60000 })
    assert.equal(result.status, 0, result.stderr || result.stdout)
    const packageDir = result.stdout.match(/PACKAGE_DIR=(.+)/)?.[1].trim()
    assert.ok(packageDir)
    const paths = readdirSync(packageDir, { recursive: true }).map(path => path.replaceAll('\\', '/'))
    for (const file of ['server/manage.py', 'server/scripts/run_dev.py', 'client/src/main.ts', 'contracts/openapi.yaml', 'run_local.bat', 'run_local.sh', '.env.example']) assert.ok(paths.includes(file), `Missing ${file}`)
    assert.equal(readFileSync(join(packageDir, 'server', 'scripts', 'run_dev.py'), 'utf8'), readFileSync(join(repositoryDir, 'server', 'scripts', 'run_dev.py'), 'utf8'))
    assert.ok(!paths.some(path => /(^|\/)(node_modules|\.venv|private|__pycache__|dist|\.e2e-results)(\/|$)|(^|\/)\.env$|\.pem$|school-test\.env$/.test(path)), paths.join('\n'))
    assert.match(result.stdout, /PACKAGE_ZIP=.+\.zip/)
    assert.match(result.stdout, /PACKAGE_TAR_GZ=.+\.tar\.gz/)
  } finally {
    rmSync(temporary, { recursive: true, force: true })
  }
})
