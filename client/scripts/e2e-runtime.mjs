import { existsSync } from 'node:fs'
import { join, resolve } from 'node:path'

export const serverDir = resolve(import.meta.dirname, '..', '..', 'server')

export function requireServerPython(directory = serverDir) {
  const python = join(directory, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  if (!existsSync(python)) throw new Error(`Server Python environment is missing: ${python}`)
  return python
}
