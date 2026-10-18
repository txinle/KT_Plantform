import assert from 'node:assert/strict'
import test from 'node:test'
import { validateV4BrowserInputs, teacherCycleDelayMs } from './teacher-capacity-v4-browser.mjs'

test('v4 browser identity refuses historical and public origins', () => {
  const manifest = {version:'teacher-capacity-v4',project:'kt-school-teacher-capacity-20261018',
    root:'/home/ktdata/student-capacity-tests/20261018-teacher-capacity',port:18086,
    database:'kt_e2e',browsers:['chromium','webkit']}
  const plan = {capacity_version:'teacher-capacity-v4',target_origin:'http://127.0.0.1:18086',count:50,
    roster:Array.from({length:50},(_,i)=>({account_id:`a${i}`,driver:i===0||i===49?'browser':'protocol'}))}
  assert.equal(validateV4BrowserInputs(manifest,plan).length,2)
  assert.throws(()=>validateV4BrowserInputs({...manifest,port:18085},plan))
  assert.throws(()=>validateV4BrowserInputs(manifest,{...plan,target_origin:'https://public.example'}))
  assert.throws(()=>validateV4BrowserInputs(manifest,{...plan,roster:plan.roster.slice(0,49)}))
})

test('final full-flow teacher cycle overlaps the submission and publication interval', () => {
  const plan={cell:{mode:'full-flow'},workload_duration_ms:600000,login_window_ms:10000}
  assert.equal(teacherCycleDelayMs(plan,16,1000,1000),0)
  assert.equal(teacherCycleDelayMs(plan,17,1000,541000),65000)
  assert.equal(teacherCycleDelayMs(plan,17,1000,610000),0)
  assert.equal(teacherCycleDelayMs({...plan,cell:{mode:'entry'}},17,1000,1000),0)
})
