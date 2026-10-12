import { createReadStream, readFileSync } from 'node:fs'
import { stat } from 'node:fs/promises'
import { createServer, request } from 'node:http'
import { extname, resolve, sep } from 'node:path'
import { pathToFileURL, URL } from 'node:url'

const clientDir = resolve(import.meta.dirname, '..')
const contentTypes = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.woff2': 'font/woff2', '.ico': 'image/x-icon' }

export function loadFrontendRoutes() {
  return JSON.parse(readFileSync(resolve(clientDir, '../contracts/frontend-routes.json'), 'utf8')).routes
}

function routePattern(path) {
  const parts = path.split(/(<int:[^>]+>)/)
  return new RegExp(`^/${parts.map((part) => part.startsWith('<int:') ? '[0-9]+' : part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('')}$`)
}

export function createFrontendGateway({ frontendDir = resolve(clientDir, 'dist/spa-assets'), backendOrigin = 'http://127.0.0.1:8001', routes = loadFrontendRoutes() } = {}) {
  const pages = routes.map((route) => routePattern(route.path))
  return createServer(async (incoming, outgoing) => {
    const url = new URL(incoming.url, 'http://gateway.local')
    let pathname
    try { pathname = decodeURIComponent(url.pathname) } catch { outgoing.writeHead(400).end(); return }
    const page = pages.some((pattern) => pattern.test(pathname))
    let file
    const worker = pathname === '/assessment/process/capture-v2/offline-worker.js'
    if (worker) file = resolve(frontendDir, 'capture-v2-offline-worker.js')
    else if (page) file = resolve(frontendDir, 'index.html')
    else if (pathname.startsWith('/static/spa-assets/')) {
      file = resolve(frontendDir, pathname.slice('/static/spa-assets/'.length))
      if (!file.startsWith(resolve(frontendDir) + sep)) { outgoing.writeHead(404).end(); return }
    } else if (pathname.startsWith('/static/js/')) {
      outgoing.writeHead(404).end(); return
    }
    if (file) {
      if (!['GET', 'HEAD'].includes(incoming.method)) { outgoing.writeHead(405, { Allow: 'GET, HEAD' }).end(); return }
      try {
        const info = await stat(file)
        if (!info.isFile()) { outgoing.writeHead(404).end(); return }
        outgoing.writeHead(200, { 'Content-Type': worker ? 'application/javascript' : contentTypes[extname(file)] || 'application/octet-stream', 'Content-Length': info.size, 'Cache-Control': 'no-cache', ...(worker ? { 'Service-Worker-Allowed': '/student/sessions/' } : {}) })
        if (incoming.method === 'HEAD') outgoing.end()
        else createReadStream(file).on('error', () => outgoing.destroy()).pipe(outgoing)
      } catch { outgoing.writeHead(404).end() }
      return
    }
    const proxy = pathname.startsWith('/api/v1/') || pathname.startsWith('/admin/') || pathname === '/health/' || pathname.startsWith('/media/') || pathname.startsWith('/static/')
    if (!proxy) { outgoing.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' }).end('Not found'); return }
    const upstream = request(new URL(url.pathname + url.search, backendOrigin), { method: incoming.method, headers: { ...incoming.headers, 'x-forwarded-proto': 'http' } }, (response) => {
      outgoing.writeHead(response.statusCode, response.headers)
      response.pipe(outgoing)
    })
    upstream.on('error', () => { if (!outgoing.headersSent) outgoing.writeHead(502); outgoing.end() })
    incoming.on('aborted', () => upstream.destroy())
    incoming.pipe(upstream)
  })
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const port = Number(process.env.KT_GATEWAY_PORT || '8000')
  const server = createFrontendGateway({ backendOrigin: process.env.KT_BACKEND_ORIGIN || 'http://127.0.0.1:8001' })
  server.listen(port, process.env.KT_GATEWAY_HOST || '127.0.0.1', () => process.stdout.write(`Frontend gateway: http://127.0.0.1:${port}\n`))
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => server.close(() => process.exit(0)))
}
