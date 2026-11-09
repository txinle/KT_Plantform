import { spawnSync } from 'node:child_process'
import { expect, type Page, type TestInfo } from '@playwright/test'
import { requireServerPython } from '../scripts/e2e-runtime.mjs'

export async function downloadTemplateImages(page: Page, info: TestInfo) {
  const response = await page.request.get('/api/v1/teacher/import-templates/question-images.zip')
  expect(response.ok()).toBe(true)
  const paths = ['image1.png', 'image2.png', 'image3.png', 'image4.png'].map(name => info.outputPath(name))
  const unpack = spawnSync(requireServerPython(), ['-c', [
    'from io import BytesIO',
    'from pathlib import Path',
    'import sys, zipfile',
    'with zipfile.ZipFile(BytesIO(sys.stdin.buffer.read())) as archive:',
    '    for path in map(Path, sys.argv[1:]):',
    '        path.write_bytes(archive.read(path.name))',
  ].join('\n'), ...paths], { input: await response.body() })
  expect(unpack.status, unpack.stderr.toString()).toBe(0)
  return paths
}
