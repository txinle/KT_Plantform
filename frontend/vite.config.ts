import { fileURLToPath, URL } from 'node:url'
import { readFileSync } from 'node:fs'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

export default defineConfig(({ command }) => ({
  base: command === 'serve' ? '/' : '/static/spa-assets/',
  plugins: [vue(), {
    name: 'capture-offline-worker',
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: 'capture-v2-offline-worker.js',
        source: readFileSync(new URL('../static/js/capture-v2-offline-worker.js', import.meta.url), 'utf8') })
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
        fileURLToPath(new URL('../static/js', import.meta.url)),
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
    include: ['src/**/*.test.ts'],
  },
}))
