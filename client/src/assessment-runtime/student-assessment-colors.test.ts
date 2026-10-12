import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const stylesheet = readFileSync(resolve('src/assessment-runtime/student-assessment.css'), 'utf8')
const rules = [...stylesheet.matchAll(/([^{}]+)\{([^{}]*)\}/g)]

function declarationsFor(selector: string) {
  const rule = rules.find(([, selectors]) => (
    selectors!.split(',').map(candidate => candidate.trim()).includes(selector)
  ))
  return rule?.[2]?.replace(/\s+/g, ' ').trim() ?? ''
}

describe('student assessment navigation colors', () => {
  it('maps every answer state to the approved theme colors', () => {
    expect(declarationsFor(".kt-assessment-nav__item[data-answer-status='unvisited']")).toContain(
      'background: var(--kt-surface); color: var(--kt-muted); border-color: var(--kt-border-strong);',
    )
    expect(declarationsFor(".kt-assessment-nav__item[data-answer-status='visited'][data-has-response='false']")).toContain(
      'background: var(--kt-action-050); color: var(--kt-action-700); border-color: var(--kt-action-600);',
    )
    for (const selector of [
      ".kt-assessment-nav__item[data-answer-status='draft']",
      ".kt-assessment-nav__item[data-answer-status='visited'][data-has-response='true']",
    ]) {
      expect(declarationsFor(selector)).toContain(
        'background: var(--kt-warning-bg); color: var(--kt-warning); border-color: var(--kt-warning);',
      )
    }
    expect(declarationsFor(".kt-assessment-nav__item[data-answer-status='submitted']")).toContain(
      'background: var(--kt-action-600); color: var(--kt-surface); border-color: var(--kt-action-600);',
    )
    expect(declarationsFor(".kt-assessment-nav__item[data-answer-status='submitted'] small")).toContain(
      'color: var(--kt-surface);',
    )
  })
})
