import { fileURLToPath, URL } from 'node:url'
import { readFileSync, readdirSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { join } from 'node:path'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

const sourceFiles = (directory: string): string[] => readdirSync(directory, { withFileTypes: true })
  .flatMap(entry => entry.isDirectory() ? sourceFiles(join(directory, entry.name)) : [join(directory, entry.name)])
  .filter(path => !path.includes('.test.') && /\.(ts|vue|css)$/.test(path)).sort()
const sourceRoot = fileURLToPath(new URL('./src', import.meta.url))
const hash = createHash('sha256')
for (const path of sourceFiles(sourceRoot)) hash.update(path.slice(sourceRoot.length).replaceAll('\\', '/')).update(readFileSync(path))
for (const input of ['./index.html', './package-lock.json', './legacy/js/capture-v2-offline-worker.js']) hash.update(readFileSync(new URL(input, import.meta.url)))
const assessmentBuildVersion = hash.digest('hex').slice(0, 20)

export default defineConfig(({ command }) => ({
  define: { __KT_ASSESSMENT_BUILD__: JSON.stringify(assessmentBuildVersion) },
  base: command === 'serve' ? '/' : '/static/spa-assets/',
  plugins: [vue(), {
    name: 'capture-offline-worker',
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: 'capture-v2-offline-worker.js',
        source: readFileSync(new URL('./legacy/js/capture-v2-offline-worker.js', import.meta.url), 'utf8') })
    },
  }, {
    name: 'versioned-assessment-shell',
    generateBundle: {
      order: 'post',
      handler(_options, bundle) {
        const html = bundle['index.html']
        if (!html || html.type !== 'asset') throw new Error('Versioned assessment shell requires the built standalone HTML.')
        const source = typeof html.source === 'string' ? html.source : new TextDecoder().decode(html.source)
        this.emitFile({ type: 'asset', fileName: `capture-shell-${assessmentBuildVersion}.html`,
          source: source.replace('</head>', `<meta name="kt-shell-build" content="${assessmentBuildVersion}"></head>`) })
      },
    },
  }],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    watch: {
      ignored: ['**/.e2e-results/**'],
    },
    fs: {
      allow: [
        fileURLToPath(new URL('.', import.meta.url)),
      ],
    },
  },
  build: {
    manifest: true,
    outDir: 'dist/spa-assets',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        standalone: fileURLToPath(new URL('./index.html', import.meta.url)),
        shell: fileURLToPath(new URL('./src/main.ts', import.meta.url)),
      },
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts', 'src/**/*.test.mjs'],
  },
}))
