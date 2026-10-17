import assert from 'node:assert/strict'
import test from 'node:test'
import { performance } from 'node:perf_hooks'

const subject = () => import('./student-capacity-retest-browser.mjs')
test('submission clock starts at confirmation after the dialog is opened', async () => {
  const { submit } = await subject()
  assert.equal(typeof submit, 'function')
  const { chromium } = await import('@playwright/test')
  const browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage()
    await page.route('**/*', async route => {
      if (route.request().method() === 'POST') {
        await route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ data: {
          submission_id: 'submission', status_url: '/api/v1/student/sessions/100/submission/',
        } }) })
      } else {
        await route.fulfill({ contentType: 'text/html', body: `<button data-finish-request
          onclick="setTimeout(()=>document.querySelector('[data-paper-submit-confirm]').hidden=false,1200)">提交</button>
          <button data-paper-submit-confirm hidden onclick="fetch('/api/v1/student/sessions/100/finish/',
          {method:'POST'}).then(()=>setTimeout(()=>location.href='/student/sessions/100/complete/',300))">确认</button>` })
      }
    })
    await page.goto('http://127.0.0.1:18085/clock-test/')
    const record = {}
    const context = { request: { get: async url => ({ ok: () => true, json: async () => ({ data:
      String(url).includes('/submission/') ? { status: 'completed', submission_id: 'submission' }
        : { session_id: 100, status: 'completed' },
    }) }) } }
    await submit({ page, account: { session_id: 100 }, record, context, clock: () => performance.now(), settle: async () => {} })
    assert(record.accepted_ms < 1000, `Dialog opening was included: ${record.accepted_ms}`)
    assert.equal(record.submission_ms, record.accepted_ms)
    assert.equal(record.completion_ms, record.terminal_ms)
    assert.equal(record.processing_ms, record.completion_ms-record.submission_ms)
  } finally {
    await browser.close()
  }
})
test('mobile actor enables touch, mobile viewport semantics and a mobile user agent', async () => {
  const { mobileContextOptions } = await subject()
  assert.equal(mobileContextOptions.isMobile, true)
  assert.equal(mobileContextOptions.hasTouch, true)
  assert.match(mobileContextOptions.userAgent, /iPhone/)
  assert.deepEqual(mobileContextOptions.viewport, { width: 390, height: 844 })
})
test('evidence listener records auth status without racing navigation to read its body', async () => {
  const { installEvidence, makeBrowserRecord } = await subject()
  const listeners = new Map(), p = plan(), f = fixture()
  const record = makeBrowserRecord(f.accounts[0], p.roster[0], p, 0)
  const settle = installEvidence({ on: (event, listener) => listeners.set(event, listener) }, record, () => 100)
  let bodyReads = 0
  for (const path of ['/api/v1/auth/login/', '/api/v1/student/courses/', '/api/v1/student/attempts/100/draft/']) {
    const request = { url: () => 'http://127.0.0.1:18085' + path, method: () => 'POST', postDataJSON: () => ({}) }
    listeners.get('request')(request)
    listeners.get('response')({ request: () => request, status: () => 200,
      headers: () => ({ 'content-type': 'application/json' }), json: async () => { bodyReads++; return { data: {} } } })
  }
  for (const path of ['/api/v1/student/sessions/100/submission/', '/api/v1/student/sessions/100/summary/']) {
    const request = { url: () => 'http://127.0.0.1:18085' + path, method: () => 'GET' }
    listeners.get('request')(request)
    listeners.get('response')({ request: () => request, status: () => 200,
      headers: () => ({ 'content-type': 'application/json' }), json: async () => { bodyReads++; throw new Error('navigated away') } })
  }
  await settle()
  assert.equal(bodyReads, 1)
  assert.deepEqual(record.requests.map(row => row.status), [200, 200, 200, 200, 200])
  assert.equal(record.evidence_errors?.length || 0, 0)
})
test('capture parent rejection is classified as recovered only after an unchanged payload gets a later acknowledgement', async () => {
  const { classifyRetestConsoleErrors } = await subject()
  assert.equal(typeof classifyRetestConsoleErrors, 'function')
  const path = '/api/v1/assessment/process/capture-v2/batch/'
  const event = { client_sequence_no: 2, event_type: 'response_transition', payload: { value: 'B' } }
  const body = { capture_bundle: { client_instance_id: 'client', events: [event], checkpoints: [] } }
  const record = { console: [{ type: 'error', text: 'Failed to load resource: the server responded with a status of 409 (Conflict)',
    location: { url: 'http://127.0.0.1:18085' + path }, at: 20 }],
    capture_events: [{ ...event, event_id: 'event', client_instance_id: 'client' }],
    event_acknowledgements: [{ event_id: 'event' }], checkpoint_acknowledgements: [],
    requests: [{ path, body, client_instance_id: 'client', status: 409, ack_monotonic_ms: 20,
      response: { error: { code: 'parent_not_received' } } },
    { path, body: globalThis.structuredClone(body), client_instance_id: 'client', status: 200, ack_monotonic_ms: 30,
      response: { acknowledgements: [{ event_id: 'event' }], checkpoint_ids: [] } }] }
  const result = classifyRetestConsoleErrors(record, 'http://127.0.0.1:18085')
  assert.equal(result.unexpected_console_errors.length, 0)
  assert.equal(result.expected_capture_recovery_console_errors.length, 1)
  assert.equal(result.capture_recovery_chains.length, 1)
  for (const alter of [r => {r.requests[1].body.capture_bundle.events[0].payload.value='C'},
    r => {r.requests[1].ack_monotonic_ms=10}, r => {r.requests[1].response.acknowledgements=[]}]) {
    const changed=globalThis.structuredClone(record);alter(changed)
    assert.equal(classifyRetestConsoleErrors(changed,'http://127.0.0.1:18085').unexpected_console_errors.length,1)
  }
})
test('server assigned event ids bind raw browser events by client instance and sequence', async () => {
  const { makeBrowserRecord, captureOutbound, captureInbound } = await subject()
  const p = plan(), f = fixture(), record = makeBrowserRecord(f.accounts[0], p.roster[0], p, 0)
  const event = { client_sequence_no: 1, event_type: 'item_rendered', attempt_id: f.accounts[0].questions[0].attempt_id }
  const request = captureOutbound(record, '/api/v1/assessment/process/capture-v2/batch/',
    { capture_bundle: { client_instance_id: 'client', events: [event], checkpoints: [] } }, 100)
  captureOutbound(record, '/api/v1/assessment/process/capture-v2/batch/',
    { capture_bundle: { client_instance_id: 'client', events: [event], checkpoints: [] } }, 110)
  assert.equal(record.capture_events.length, 1)
  captureInbound(record, request, { data: { acknowledgements: [{ event_id: 'assigned', client_sequence_no: 1 }] } }, 200, 120)
  assert.equal(record.capture_events[0].event_id, 'assigned')
  assert.equal(record.capture_events[0].client_instance_id, 'client')
  assert.deepEqual(request.event_ids, ['assigned'])
})
const questions = account => Array.from({ length: 20 }, (_, index) => ({ assignment_id: account * 100 + index,
  attempt_id: account * 100 + index + 10000, question_type: 'short_answer', confidence_condition: index % 2 ? 'not_assigned' : 'prompt', required: true }))
const fixture = () => ({ isolated_demo: true, database: 'kt_e2e', source_fixture_id: 'source', baseline_snapshot_id: 'snapshot',
  source_ownership: { command: 'prepare_student_capacity_baseline' }, accounts: Array.from({ length: 50 }, (_, i) => ({
    account_id: `a${i}`, user_id: i + 1, session_id: i + 100, courseId: 9, taskId: 10,
    username: `student-${i}`, password: 'do-not-record', initial_state: 'prepared', questions: questions(i + 1) })) })
const manifest = () => ({ version: 'student-capacity-retest-v1', project: 'kt-school-e2e-capacity-retest-20261016',
  root: '/home/ktdata/student-capacity-tests/20261016-retest', port: 18085, database: 'kt_e2e', counts: [50, 180], pressure_driver: 'external_windows' })
const plan = () => ({ version: 'student-capacity-protocol-v3', workload_profile: 'historical-v3', plan_id: 'frozen', run_id: 'cell', count: 50, mode: 'save-only',
  workload: 'paper-20q', workload_duration_ms: 600000, source_fixture_id: 'source', baseline_snapshot_id: 'snapshot',
  roster: fixture().accounts.map((a, i) => ({ account_id: a.account_id, user_id: a.user_id, session_id: a.session_id, course_id: a.courseId,
    task_id: a.taskId, driver: i < 2 ? 'browser' : 'protocol', role: 'save', answers: a.questions.map(q => ({...q, value: 'A', confidence_probability: null})),
    save_schedule: [{ assignment_id: a.questions[0].assignment_id, value: 'A', offset_ms: 1000 }] })) })

test('v3 binds exactly two browser accounts inside total50/180 and the authorized isolated target', async () => {
  const { validateRetestInputs } = await subject()
  assert.equal(validateRetestInputs(manifest(), fixture(), plan(), 'http://127.0.0.1:18085', ['a0', 'a1']).length, 2)
  for (const change of [v => {v.port=18082}, v => {v.root+='/other'}, v => {v.project='production'}]) {
    const m=manifest(); change(m); assert.throws(() => validateRetestInputs(m, fixture(), plan(), 'http://localhost:18085', ['a0','a1']))
  }
  for (const target of ['http://example.org:18085', 'http://localhost:18082', 'https://localhost:18085', 'http://localhost:18085/other']) {
    assert.throws(() => validateRetestInputs(manifest(), fixture(), plan(), target, ['a0', 'a1']))
  }
  const p=plan();p.roster[2].driver='browser'
  assert.throws(() => validateRetestInputs(manifest(), fixture(), p, 'http://localhost:18085', ['a0','a1']))
  const f=fixture();f.baseline_snapshot_id='other'
  assert.throws(() => validateRetestInputs(manifest(), f, plan(), 'http://localhost:18085', ['a0','a1']))
})

const gradualPlan = () => {
  const p=plan()
  Object.assign(p,{workload_profile:'full-flow-20q-gradual-login-v1',mode:'full-flow',login_window_ms:10000})
  for(const [i,row] of p.roster.entries()) Object.assign(row,{role:'full-flow',login_offset_ms:10000*i/(p.count-1)})
  return p
}

test('gradual arrival validates the full roster offsets before selecting either browser', async () => {
  const {validateRetestInputs}=await subject()
  const p=gradualPlan(),m={...manifest(),workload_profile:p.workload_profile}
  const selected=validateRetestInputs(m,fixture(),p,'http://127.0.0.1:18085',['a0','a1'])
  assert.equal(selected[1].row.login_offset_ms,10000/49)
  for(const alter of [v=>{v.roster[1].login_offset_ms=10000},v=>{v.roster[49].login_offset_ms=0},
    v=>{v.mode='save-only'},v=>{v.workload_profile='historical-v3'}]) {
    const changed=globalThis.structuredClone(p);alter(changed)
    assert.throws(()=>validateRetestInputs(m,fixture(),changed,'http://127.0.0.1:18085',['a0','a1']))
  }
})

test('personal arrival waits for its frozen offset without counting nominal delay as generator skew', async () => {
  const {waitPersonalLogin}=await subject()
  assert.equal(typeof waitPersonalLogin,'function')
  let now=100000
  const row={login_offset_ms:10000},record={}
  await waitPersonalLogin(row,{release_monotonic:100},record,{now:()=>now,pause:async delay=>{now+=delay}})
  assert.equal(now,110000)
  assert.equal(record.login_planned_monotonic_ms,110000)
  assert.equal(record.login_actual_monotonic_ms,110000)
  assert.equal(record.release_skew_ms,0)
  now=111100
  await waitPersonalLogin(row,{release_monotonic:100},record,{now:()=>now,pause:async delay=>{now+=delay}})
  assert.equal(record.release_skew_ms,1100)
})

test('absolute scheduling retains late actions and keeps future timestamps fixed', async () => {
  const { runFrozenSchedule } = await subject()
  let now=1000
  const seen=[]
  const samples=await runFrozenSchedule([{offset_ms:0,assignment_id:1,value:'A'}, {offset_ms:30,assignment_id:1,value:'B'},
    {offset_ms:200,assignment_id:2,value:'C'}], 1000, async (action, sample) => {
    seen.push([action.value,sample.planned_monotonic_ms,sample.client_backlog_ms]);now+=80
  }, {now:()=>now, pause:async ms=>{now+=ms}})
  assert.deepEqual(seen,[['A',1000,0],['B',1030,50],['C',1200,0]])
  assert.equal(samples.length,3)
})

test('raw capture normalization reconciles superseded actions without losing transitions or exposing credentials', async () => {
  const { makeBrowserRecord, captureOutbound, captureInbound, reconcileBrowserActions } = await subject()
  const a=fixture().accounts[0],row=plan().roster[0]
  row.save_schedule=[{assignment_id:a.questions[0].assignment_id,value:'A',offset_ms:0},{assignment_id:a.questions[0].assignment_id,value:'B',offset_ms:30}]
  const record=makeBrowserRecord(a,row,plan(),0)
  record.save_samples.forEach((s,i)=>Object.assign(s,{status:'emitted',actual_event_monotonic_ms:1000+i*30,planned_monotonic_ms:1000+i*30}))
  const events=['A','B'].map((value,i)=>({event_id:`e${i}`,client_sequence_no:i+1,event_type:'response_transition',attempt_id:a.questions[0].attempt_id,
    client_instance_id:'client',payload:{after_state:{value}}}))
  const sent=captureOutbound(record,'/api/v1/student/attempts/1/draft/',{password:'private',writer_lease_token:'private',target_event_id:'e1',
    capture_bundle:{client_instance_id:'client',events,checkpoints:[{checkpoint_id:'c1'}]}},1100)
  captureInbound(record,sent,{success:true,committed:true,active_response_event_id:'e1',response_version:1,
    acknowledgements:events.map(e=>({event_id:e.event_id,client_sequence_no:e.client_sequence_no,server_ingest_sequence_no:e.client_sequence_no})),checkpoint_ids:['c1']},200,1200)
  reconcileBrowserActions(record,row)
  assert.deepEqual(record.save_samples.map(s=>s.status),['superseded','acknowledged'])
  assert.equal(record.capture_events.length,2)
  assert.equal(record.expected_attempts.length,20)
  assert.equal(record.expected_attempts[0].target_event_id,'e1')
  assert.equal(record.save_samples[0].scheduled_ms,200)
  assert(!JSON.stringify(record).includes('private'))
  assert(!JSON.stringify(record).includes('do-not-record'))
})

test('failed and unperformed actions keep the complete browser denominator without browser quantiles', async () => {
  const { makeBrowserRecord, browserSummary } = await subject()
  const rows=plan().roster.slice(0,2).map((r,i)=>makeBrowserRecord(fixture().accounts[i],r,plan(),i))
  rows[0].status='completed';rows[1].status='failed'
  const result=browserSummary(rows)
  assert.equal(result.planned_pages,2);assert.equal(result.failed_pages,1);assert.equal(result.passed,false)
  assert.equal(rows[1].save_samples[0].status,'not_executed')
  assert(!JSON.stringify(result).includes('p95'))
})

test('navigation snapshots preserve raw events and explicit server lease release plus next-entry receipt', async () => {
  const {makeBrowserRecord,captureOutbound,captureInbound}=await subject()
  const account=fixture().accounts[0],row=plan().roster[0],record=makeBrowserRecord(account,row,plan(),0)
  const first=account.questions[0],next=account.questions[1]
  record.lease_acquisitions.push({attempt_id:first.attempt_id,client_instance_id:'client',confirmed:true,expires_at:'2099-01-01T00:00:00Z',operation_index:1})
  const event={event_id:'leave',event_type:'item_left',client_sequence_no:1,attempt_id:first.attempt_id,client_instance_id:'client',payload:{}}
  const request=captureOutbound(record,`/api/v1/student/attempts/${first.attempt_id}/draft/`,{writer_client_instance_id:'client',
    save_and_leave:{client_instance_id:'client',events:[event],checkpoints:[{checkpoint_id:'nav-check'}]}},10)
  captureInbound(record,request,{success:true,writer_lease_released:true,acknowledgements:[{event_id:'leave',client_sequence_no:1}],checkpoint_ids:['nav-check'],
    next_entry:{session_id:account.session_id,assignment:{assignment_id:next.assignment_id,attempt_id:next.attempt_id},draft_answer:'',process:{response_version:0,active_response_event_id:null},
      entry_receipt:{navigation_id:'nav2',visit_id:2,session_id:account.session_id,attempt_id:next.attempt_id},
      initial_writer_lease:{writer_client_instance_id:'client',expires_at:'2099-01-01T00:00:00Z',writer_lease_token:'secret-omit'}}},200,20)
  assert.equal(record.capture_events[0].event_id,'leave')
  assert.equal(record.lease_releases[0].attempt_id,first.attempt_id)
  assert.equal(record.lease_acquisitions[1].attempt_id,next.attempt_id)
  assert.equal(record.visits[0].visit_id,2)
  assert(!JSON.stringify(record).includes('secret-omit'))
})

test('release rebasing leaves raw browser clocks intact and preserves preparation-only samples', async () => {
  const {makeBrowserRecord,rebasePreparationClocks}=await subject()
  const record=makeBrowserRecord(fixture().accounts[0],plan().roster[0],plan(),0)
  record.capture_events.push({event_id:'e',client_monotonic_ms:30,client_time_origin:'2026-01-01T00:00:00Z'})
  record.requests.push({sent_monotonic_ms:100000,body:{client_monotonic_ms:30}})
  rebasePreparationClocks(record,{release_monotonic:1,release_wall_time:100})
  assert.equal(record.requests[0].sent_monotonic_ms,1000)
  assert.equal(record.requests[0].body.client_monotonic_ms,30)
  assert.equal(record.capture_events[0].client_monotonic_ms,30)
})

test('event capture acknowledgement cannot impersonate a later durable pointer save', async () => {
  const {makeBrowserRecord,captureOutbound,captureInbound,reconcileBrowserActions}=await subject()
  const account=fixture().accounts[0],row=plan().roster[0],record=makeBrowserRecord(account,row,plan(),0)
  Object.assign(record.save_samples[0],{status:'emitted',planned_monotonic_ms:1000,actual_event_monotonic_ms:1000})
  const event={event_id:'event',event_type:'response_transition',client_sequence_no:1,attempt_id:account.questions[0].attempt_id,
    client_instance_id:'client',payload:{after_state:{value:'A'}}}
  const batch=captureOutbound(record,'/api/v1/assessment/process/capture-v2/batch/',{client_instance_id:'client',events:[event]},1100)
  captureInbound(record,batch,{acknowledgements:[{event_id:'event',client_sequence_no:1}]},200,1200)
  const pointer=captureOutbound(record,'/api/v1/student/attempts/1/draft/',{answer:'A',target_event_id:'event'},1300)
  captureInbound(record,pointer,{success:true,active_response_event_id:'event',response_version:1},200,1600)
  reconcileBrowserActions(record,row)
  assert.equal(record.save_samples[0].event_ack_monotonic_ms,1200)
  assert.equal(record.save_samples[0].ack_monotonic_ms,1600)
  assert.equal(record.save_samples[0].scheduled_ms,600)
  assert.equal(record.save_samples[0].event_scheduled_ms,200)
  assert.equal(record.save_samples[0].http_send_monotonic_ms,1300)
})

test('startup evidence cannot settle with a missing checkpoint, unacked event, or unfinished clock sync', async () => {
  const {browserEvidenceSettled}=await subject()
  const record={capture_events:[{event_id:'e'}],event_acknowledgements:[{event_id:'e'}],capture_checkpoints:[{checkpoint_id:'c'}],checkpoint_acknowledgements:['c'],time_syncs:[{confirmed:{sync_quality:'valid'}}]}
  assert.equal(browserEvidenceSettled(record),true)
  for(const key of ['capture_checkpoints','checkpoint_acknowledgements','event_acknowledgements','time_syncs']) {
    assert.equal(browserEvidenceSettled({...record,[key]:[]}),false)
  }
  assert.equal(browserEvidenceSettled({...record,time_syncs:[{confirmed:{sync_quality:'pending'}}]}),false)
})

test('blank cleanup pointer receipts still preserve the server response version', async () => {
  const {makeBrowserRecord,captureOutbound,captureInbound}=await subject()
  const account=fixture().accounts[0],record=makeBrowserRecord(account,plan().roster[0],plan(),0)
  const attempt=account.questions[0].attempt_id
  const request=captureOutbound(record,`/api/v1/student/attempts/${attempt}/draft/`,{answer:'',target_event_id:null},10)
  captureInbound(record,request,{success:true,active_response_event_id:null,response_version:1},200,20)
  assert.equal(record.expected_attempts[0].response_version,1)
  assert.equal(record.expected_attempts[0].target_event_id,null)
  assert.equal(record.expected_attempts[0].answer,'')
})
