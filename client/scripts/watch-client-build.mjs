import { spawn } from 'node:child_process'
import { existsSync, watch } from 'node:fs'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const clientDir = resolve(import.meta.dirname, '..')
let activeBuild

function buildClient() {
  if (!process.env.npm_execpath) throw new Error('Start the development watcher with npm run dev:watch.')
  return new Promise((resolveBuild, reject) => {
    activeBuild = spawn(process.execPath, [process.env.npm_execpath, 'run', 'build', '--', '--emptyOutDir=false'], {
      cwd: clientDir, stdio: 'inherit', windowsHide: true,
    })
    activeBuild.once('error', reject)
    activeBuild.once('exit', (code) => { activeBuild = undefined; if (code === 0) resolveBuild(); else reject(new Error(`Client build exited ${code}`)) })
  })
}

export function watchClientBuild({ directory = clientDir, build = buildClient, debounceMs = 200 } = {}) {
  let dirty = false
  let running = false
  let closed = false
  let timer
  const rebuild = async () => {
    if (running || closed) return
    running = true
    while (dirty && !closed) {
      dirty = false
      try { await build() } catch (error) { process.stderr.write(String(error) + '\n') }
    }
    running = false
  }
  const schedule = () => {
    dirty = true
    globalThis.clearTimeout(timer)
    timer = globalThis.setTimeout(rebuild, debounceMs)
  }
  const watchers = ['src', 'public'].filter((name) => existsSync(resolve(directory, name)))
    .map((name) => watch(resolve(directory, name), { recursive: true }, schedule))
  watchers.push(watch(directory, (_event, filename) => {
    if (['index.html', 'vite.config.ts', 'package-lock.json'].includes(String(filename))) schedule()
  }))
  return { close() { closed = true; globalThis.clearTimeout(timer); for (const watcher of watchers) watcher.close() } }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const watcher = watchClientBuild()
  process.stdout.write('Watching client sources; each build refreshes its version and preserves earlier assets.\n')
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => { watcher.close(); activeBuild?.kill('SIGTERM') })
}
