import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { router } from './router'

const catalog = JSON.parse(readFileSync('../contracts/frontend-routes.json', 'utf8')) as { routes: Array<{ name: string; path: string }> }

describe('frontend page catalog', () => {
  for (const route of catalog.routes) {
    it(`owns ${route.name} at its existing business URL`, () => {
      const path = '/' + route.path.replace(/<int:[^>]+>/g, '7')
      expect(router.resolve(path).name, path).not.toBe('not-found')
    })
  }
})
