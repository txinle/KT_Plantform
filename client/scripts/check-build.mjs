import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

export function checkBuild(directory = resolve(import.meta.dirname, '../dist/spa-assets')) {
  const requireFile = (path) => {
    if (!existsSync(resolve(directory, path))) throw new Error(`Missing build resource: ${path}`)
  }
  requireFile('index.html')
  requireFile('capture-v2-offline-worker.js')
  requireFile('.vite/manifest.json')
  const manifest = JSON.parse(readFileSync(resolve(directory, '.vite/manifest.json'), 'utf8'))
  if (!manifest['index.html']?.isEntry) throw new Error('Missing standalone manifest entry')
  for (const entry of Object.values(manifest)) {
    for (const file of [entry.file, ...(entry.css || []), ...(entry.assets || [])]) requireFile(file)
    for (const dependency of [...(entry.imports || []), ...(entry.dynamicImports || [])]) {
      if (!manifest[dependency]) throw new Error(`Missing manifest dependency: ${dependency}`)
    }
  }
  const shells = readdirSync(directory).filter((name) => /^capture-shell-[a-f0-9]{20}\.html$/.test(name))
  if (shells.length !== 1) throw new Error('Expected exactly one versioned capture shell')
  const version = shells[0].slice('capture-shell-'.length, -'.html'.length)
  const shell = readFileSync(resolve(directory, shells[0]), 'utf8')
  if (!shell.includes(`<meta name="kt-shell-build" content="${version}">`)) throw new Error('Capture shell version marker mismatch')
  for (const html of [shell, readFileSync(resolve(directory, 'index.html'), 'utf8')]) {
    const resources = [...html.matchAll(/(?:src|href)="\/static\/spa-assets\/([^"?#]+)/g)]
    if (!resources.length) throw new Error('Standalone HTML has no built assets')
    for (const resource of resources) requireFile(resource[1])
  }
  return { version, shell: shells[0] }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  process.stdout.write(JSON.stringify(checkBuild()) + '\n')
}
