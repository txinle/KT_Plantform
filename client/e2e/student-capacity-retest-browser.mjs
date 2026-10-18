/** Two actual UI actors in the frozen 50/180 v3 roster. Never a browser percentile estimate. */
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { parseArgs, isDeepStrictEqual } from 'node:util'
import { performance } from 'node:perf_hooks'
import process from 'node:process'
import { devices, expect } from '@playwright/test'
import { prepareLogin, releaseLogin } from './student-concurrency-witness.mjs'
import { operable } from './prepared-startup-witness.mjs'
import { classifyCapacityConsoleErrors } from './student-concurrency-two-page.mjs'
import { createVisualProbeContext, startFixtureTask, questionNavigationSteps, captureVisualLayouts, captureFirstReadyLayout } from './school-navigation-probe.mjs'

const domain = value => value?.data || value
export const mobileContextOptions = {...devices['iPhone 13'],viewport:{width:390,height:844}}
const pause = ms => new Promise(done => globalThis.setTimeout(done, Math.max(0, ms)))
const load = path => JSON.parse(readFileSync(resolve(path), 'utf8').replace(/^\uFEFF/, ''))
const hash = bytes => createHash('sha256').update(bytes).digest('hex')
const modes = ['login', 'startup', 'login-mixed', 'startup-mixed', 'entry-mixed', 'save-only', 'full-flow', 'submission', 'submission-mixed']
const roles = ['login', 'startup', 'save', 'full-flow', 'submission']
const workloadProfile = 'full-flow-20q-gradual-login-v1'
const identityKeys = ['account_id', 'user_id', 'task_id', 'session_id', 'course_id', 'role']
const privateWrite = (path, value) => writeFileSync(path, value, { mode: 0o600 })
const clean = value => Array.isArray(value) ? value.map(clean) : value && typeof value === 'object'
  ? Object.fromEntries(Object.entries(value).filter(([key]) => !/password|token|csrf|cookie|authorization|secret/i.test(key)).map(([key,item]) => [key,clean(item)])) : value

export function validateRetestInputs(manifest, fixture, plan, baseURL, ids) {
  assert(manifest.version === 'student-capacity-retest-v1' && manifest.project === 'kt-school-e2e-capacity-retest-20261016'
    && manifest.root === '/home/ktdata/student-capacity-tests/20261016-retest' && manifest.port === 18085
    && manifest.database === 'kt_e2e' && isDeepStrictEqual(manifest.counts, [50,180])
    && manifest.pressure_driver === 'external_windows', 'Only the authorized isolated retest manifest is permitted')
  const url = new globalThis.URL(baseURL)
  assert(url.protocol === 'http:' && ['localhost','127.0.0.1'].includes(url.hostname) && url.port === '18085'
    && url.pathname === '/' && !url.search && !url.hash && !url.username && !url.password, 'Use the local18085 retest tunnel')
  assert(fixture.isolated_demo === true && fixture.database === 'kt_e2e'
    && fixture.source_ownership?.command === 'prepare_student_capacity_baseline'
    && fixture.baseline_snapshot_id && fixture.source_fixture_id, 'Owned immutable baseline required')
  assert(plan.version === 'student-capacity-protocol-v3' && [50,180].includes(plan.count)
    && plan.roster?.length === plan.count && modes.includes(plan.mode) && ['comparison-2q','paper-20q'].includes(plan.workload)
    && plan.plan_id && plan.run_id && plan.baseline_snapshot_id === fixture.baseline_snapshot_id
    && plan.source_fixture_id === fixture.source_fixture_id, 'Frozen v3 full denominator and baseline identities required')
  assert([workloadProfile,'historical-v3'].includes(plan.workload_profile),'Explicit workload profile required')
  if(manifest.workload_profile) assert.equal(plan.workload_profile,manifest.workload_profile,'Manifest workload profile differs')
  if(plan.workload_profile===workloadProfile) {
    assert(plan.mode==='full-flow' && plan.workload==='paper-20q' && plan.workload_duration_ms===600000
      && plan.login_window_ms===10000,'Current pressure requires one complete ten-minute flow')
    for(const [i,row] of plan.roster.entries()) assert(row.role==='full-flow'
      && row.login_offset_ms===10000*i/(plan.count-1),'Frozen full roster login offset differs')
  }
  if (plan.target_origin) {
    const target = new globalThis.URL(plan.target_origin)
    assert(target.protocol === 'http:' && ['localhost','127.0.0.1'].includes(target.hostname)
      && target.port === '18085', 'Frozen plan must target the authorized tunnel')
  }
  assert(Array.isArray(ids) && ids.length === 2 && new Set(ids).size === 2, 'Two explicit browser accounts required')
  assert(new Set(plan.roster.map(row => row.account_id)).size === plan.count, 'Duplicate planned account')
  const browserRows = plan.roster.filter(row => row.driver === 'browser')
  assert(browserRows.length === 2 && browserRows.every(row => ids.includes(row.account_id))
    && plan.roster.every(row => ['browser','protocol'].includes(row.driver)), 'Browser actors belong inside the total denominator')
  const selected = ids.map(id => {
    const matches = fixture.accounts.filter(a => a.account_id === id)
    assert(matches.length === 1, 'Missing or duplicated fixture actor')
    const account = matches[0], row = browserRows.find(r => r.account_id === id)
    assert(account.username && account.password && account.initial_state === 'prepared' && roles.includes(row.role), 'Fresh prepared UI actor required')
    for (const [source,target] of [['user_id','user_id'],['session_id','session_id'],['taskId','task_id'],['courseId','course_id']]) {
      assert(Number.isSafeInteger(account[source]) && account[source] > 0 && account[source] === row[target], `Frozen identity differs: ${target}`)
    }
    assert(account.questions?.length === 20 && new Set(account.questions.map(q => q.assignment_id)).size === 20, 'Twenty frozen assignments required')
    for (const q of account.questions) assert(Number.isSafeInteger(q.assignment_id) && q.assignment_id > 0
      && Number.isSafeInteger(q.attempt_id) && q.attempt_id > 0 && typeof (q.confidence_required ?? q.required) === 'boolean', 'Invalid assignment contract')
    assert(row.answers?.length === (plan.workload === 'paper-20q' ? 20 : 2) && Array.isArray(row.save_schedule), 'Frozen question workload missing')
    for (const q of row.answers) assert(account.questions.some(a => a.assignment_id === q.assignment_id && a.attempt_id === q.attempt_id), 'Answer binding differs')
    let previous=-1
    for (const action of row.save_schedule) {
      assert(Number.isFinite(action.offset_ms) && action.offset_ms >= previous && action.offset_ms >= 0
        && row.answers.some(q => q.assignment_id === action.assignment_id), 'Invalid frozen action or schedule')
      previous=action.offset_ms
    }
    return {account,row}
  })
  for (const key of ['user_id','session_id','username']) assert(new Set(selected.map(x => x.account[key])).size === 2, 'Independent contexts require independent accounts')
  return selected
}

export function makeBrowserRecord(account, row, plan, index) {
  return {...Object.fromEntries(identityKeys.map(key=>[key,row[key]])), plan_id:plan.plan_id, run_id:plan.run_id,
    workload_profile:plan.workload_profile,login_offset_ms:row.login_offset_ms??0,
    driver:'browser', transport:'browser', same_DUT:false, status:'not_executed', fresh_context:true,
    viewport:index ? {width:390,height:844} : {width:1440,height:900}, capture_events:[], event_acknowledgements:[],
    capture_checkpoints:[],checkpoint_acknowledgements:[],visits:[],lease_acquisitions:[],lease_releases:[],time_syncs:[],
    requests:[],pointer_saves:[],console:[],page_errors:[],screenshots:[],navigation:[],unexpected_console_errors:[],
    expected_attempts:account.questions.map(q=>({attempt_id:q.attempt_id,assignment_id:q.assignment_id,answer:q.question_type==='multiple_choice'?[]:['short_answer','fill_blank','numeric'].includes(q.question_type)?'':null,
      response_version:0,target_event_id:null,confidence_probability:null,transition_event_ids:[]})),
    save_samples:row.save_schedule.map((a,index)=>({schedule_index:index,assignment_id:a.assignment_id,value:a.value,
      offset_ms:a.offset_ms,status:'not_executed',planned_monotonic_ms:null,actual_event_monotonic_ms:null,
      http_send_monotonic_ms:null,event_ack_monotonic_ms:null,scheduled_ms:null}))}
}

export async function runFrozenSchedule(actions, start, apply, {now=()=>Date.now(),pause:wait=pause,samples=null,paced=true}={}) {
  const rows=samples || actions.map((a,index)=>({schedule_index:index,assignment_id:a.assignment_id,status:'not_executed'}))
  for (let i=0;i<actions.length;i++) {
    const planned=start+actions[i].offset_ms
    if(paced) await wait(Math.max(0,planned-now()))
    const actual=now()
    Object.assign(rows[i],{planned_monotonic_ms:planned,action_start_monotonic_ms:actual,actual_event_monotonic_ms:actual,
      client_backlog_ms:Math.max(0,actual-planned),status:'emitted'})
    try { await apply(actions[i],rows[i],i) }
    catch(error) { rows[i].status='failed';rows[i].error={name:error.name,message:error.message};throw error }
  }
  return rows
}

function appendUnique(target, rows, key) {
  const known=new Set(target.map(row=>row[key]))
  for(const row of rows || []) if(row?.[key] && !known.has(row[key])) {target.push(clean(row));known.add(row[key])}
}

export function captureOutbound(record,path,body,at) {
  const material=body?.capture_bundle || body?.save_and_leave || body || {}
  if(material.client_instance_id && (material.events || material.checkpoints)) record.client_instance_id=material.client_instance_id
  for(const event of material.events || []) {
    const client=event.client_instance_id || material.client_instance_id
    const known=record.capture_events.some(row=>event.event_id && row.event_id===event.event_id
      || client && row.client_instance_id===client && row.client_sequence_no===event.client_sequence_no)
    if(!known) record.capture_events.push(clean({...event,client_instance_id:client}))
  }
  appendUnique(record.capture_checkpoints,material.checkpoints,'checkpoint_id')
  if(path.endsWith('/capture-checkpoints/') && body?.checkpoint_id) appendUnique(record.capture_checkpoints,[body],'checkpoint_id')
  const relevant=/^\/api\/v1\/(student|assessment)\//.test(path)
  const request={path,method:'POST',sent_monotonic_ms:at,
    ...(relevant?{body:clean(body)}:{}),client_instance_id:material.client_instance_id,
    event_sequences:(material.events||[]).map(e=>e.client_sequence_no),
    event_ids:(material.events||[]).map(e=>e.event_id).filter(Boolean),checkpoint_ids:(material.checkpoints||[]).map(c=>c.checkpoint_id)}
  record.requests.push(request)
  return request
}

function runtimeProjection(record,dto) {
  if(!dto?.assignment || dto.session_id!==record.session_id) return
  const expected=record.expected_attempts.find(a=>a.attempt_id===dto.assignment.attempt_id && a.assignment_id===dto.assignment.assignment_id)
  assert(expected,'Observed runtime attempt differs from frozen assignment')
  if(dto.entry_receipt) appendUnique(record.visits,[dto.entry_receipt],'navigation_id')
  if(dto.process) Object.assign(expected,{answer:dto.draft_answer,response_version:dto.process.response_version,
    target_event_id:dto.process.active_response_event_id,confidence_probability:dto.inline_confidence?.probability??null})
  const lease=dto.initial_writer_lease
  if(lease) {
    record.client_instance_id=lease.writer_client_instance_id
    record.lease_acquisitions.push({attempt_id:expected.attempt_id,client_instance_id:lease.writer_client_instance_id,
      confirmed:true,expires_at:lease.expires_at,operation_index:record.lease_acquisitions.length+record.lease_releases.length+1})
  }
}

export function captureInbound(record,request,payload,status,at) {
  const dto=domain(payload)
  if(!/^\/api\/v1\/(student|assessment)\//.test(request.path)) {Object.assign(request,{status,ack_monotonic_ms:at});return}
  Object.assign(request,{status,ack_monotonic_ms:at,response:clean(dto)})
  if(status<200 || status>=300) return
  appendUnique(record.event_acknowledgements,dto?.acknowledgements,'event_id')
  for(const ack of dto?.acknowledgements || []) {
    const event=record.capture_events.find(row=>row.client_instance_id===request.client_instance_id
      && row.client_sequence_no===ack.client_sequence_no)
    if(event) {
      assert(!event.event_id || event.event_id===ack.event_id,'Raw browser event acknowledgement identity changed')
      event.event_id=ack.event_id
    }
  }
  request.event_ids=record.capture_events.filter(row=>row.client_instance_id===request.client_instance_id
    && request.event_sequences?.includes(row.client_sequence_no)).map(row=>row.event_id).filter(Boolean)
  for(const id of [...(dto?.checkpoint_ids||[]),...(dto?.checkpoint_id?[dto.checkpoint_id]:[])]) {
    if(!record.checkpoint_acknowledgements.includes(id)) record.checkpoint_acknowledgements.push(id)
  }
  if(dto?.writer_lease_released===true) {
    const attempt=/\/attempts\/(\d+)\//.exec(request.path)
    if(attempt) {
      const prior=record.lease_acquisitions.findLast(row=>row.attempt_id===Number(attempt[1]))
      record.lease_releases.push({attempt_id:Number(attempt[1]),client_instance_id:request.body?.writer_client_instance_id || prior?.client_instance_id,
        confirmed:true,expires_at:prior?.expires_at,operation_index:record.lease_acquisitions.length+record.lease_releases.length+1})
    }
  }
  const runtime=dto?.runtime || dto
  runtimeProjection(record,runtime)
  if(dto?.next_entry) runtimeProjection(record,dto.next_entry)
  if(/\/tasks\/\d+\/(?:start|resume)\/$/.test(request.path) && runtime?.entry_receipt)
    record.activation_receipt={session_id:dto.session_id??runtime.session_id,task_id:record.task_id,entry_receipt:clean(runtime.entry_receipt)}
  const match=/\/attempts\/(\d+)\/writer-lease\/(acquire|renew|release)\/$/.exec(request.path)
  if(match) {
    const kind=match[2]==='release'?'lease_releases':'lease_acquisitions'
    record.client_instance_id=request.body?.client_instance_id || dto.client_instance_id || record.client_instance_id
    const prior=record.lease_acquisitions.findLast(row=>row.attempt_id===Number(match[1]))
    record[kind].push({attempt_id:Number(match[1]),client_instance_id:record.client_instance_id,confirmed:true,
      expires_at:dto.expires_at || prior?.expires_at,operation_index:record.lease_acquisitions.length+record.lease_releases.length+1})
  }
  if(/\/time-sync\/(create|finalize)\/$/.test(request.path)) {
    if(request.path.includes('/create/')) record.time_syncs.push({create:request.body,confirmed:clean(dto)})
    else {
      const sync=record.time_syncs.find(row=>row.confirmed?.time_sync_id===dto.time_sync_id)
      if(sync) Object.assign(sync,{finalize:request.body,confirmed:clean(dto)})
      else record.evidence_errors=[...(record.evidence_errors||[]),{message:'Time sync finalize lacks its creation receipt'}]
    }
  }
  const head=dto?.active_response_event_id || dto?.target_event_id
  if(Number.isSafeInteger(dto.response_version) && ('active_response_event_id' in dto || 'target_event_id' in dto)) {
    const event=record.capture_events.find(e=>e.event_id===head)
    const attempt=/\/attempts\/(\d+)\//.exec(request.path)
    const expected=record.expected_attempts.find(a=>a.attempt_id===(event?.attempt_id??(attempt?Number(attempt[1]):null)))
    if(expected) Object.assign(expected,{answer:event?event.payload?.after_state?.value:request.body?.answer??expected.answer,response_version:dto.response_version,target_event_id:head??null,
      confidence_probability:dto.inline_confidence?.probability??null})
    record.pointer_saves.push({assignment_id:expected?.assignment_id,target_event_id:head,response_version:dto.response_version,
      http_send_monotonic_ms:request.sent_monotonic_ms,ack_monotonic_ms:at,duration_ms:at-request.sent_monotonic_ms,
      capture_event_ids:request.event_ids,ack_event_ids:(dto.acknowledgements||[]).map(e=>e.event_id)})
  }
}

export function reconcileBrowserActions(record,row) {
  const used=new Set()
  const transitions=record.capture_events.filter(e=>e.event_type==='response_transition').sort((a,b)=>a.client_sequence_no-b.client_sequence_no)
  for(const expected of record.expected_attempts) expected.transition_event_ids=transitions.filter(e=>e.attempt_id===expected.attempt_id).map(e=>e.event_id)
  for(const sample of record.save_samples) {
    if(sample.status==='not_executed' || sample.status==='failed') continue
    const attempt=record.expected_attempts.find(e=>e.assignment_id===sample.assignment_id)
    const action=row.save_schedule[sample.schedule_index]
    const event=transitions.find(e=>!used.has(e.event_id) && e.attempt_id===attempt.attempt_id && isDeepStrictEqual(e.payload?.after_state?.value,action.value))
    if(!event) {sample.status='failed';sample.error={message:'No emitted transition matches the frozen UI action'};continue}
    used.add(event.event_id);sample.event_id=event.event_id
    const eventWall=Date.parse(event.client_time_origin)+Number(event.client_monotonic_ms)
    if(Number.isFinite(eventWall) && record.release_clock) {
      sample.actual_event_monotonic_ms=record.release_clock.release_monotonic*1000+eventWall-record.release_clock.release_wall_time*1000
      sample.client_backlog_ms=Math.max(0,sample.actual_event_monotonic_ms-sample.planned_monotonic_ms)
    }
    const delivery=record.requests.find(r=>r.event_ids?.includes(event.event_id) && r.response?.acknowledgements?.some(a=>a.event_id===event.event_id))
    const pointer=record.pointer_saves.find(p=>p.target_event_id===event.event_id) || record.pointer_saves.find(p=>p.assignment_id===attempt.assignment_id && transitions.some(e=>e.event_id===p.target_event_id && e.client_sequence_no>=event.client_sequence_no))
    if(delivery && pointer) Object.assign(sample,{status:pointer.target_event_id===event.event_id?'acknowledged':'superseded',
      target_event_id:event.event_id,pointer_target_event_id:pointer.target_event_id,response_version:pointer.response_version,
      capture_http_send_monotonic_ms:delivery.sent_monotonic_ms,http_send_monotonic_ms:pointer.http_send_monotonic_ms,
      event_ack_monotonic_ms:delivery.ack_monotonic_ms,event_scheduled_ms:delivery.ack_monotonic_ms-sample.planned_monotonic_ms,
      ack_monotonic_ms:pointer.ack_monotonic_ms,
      http_ms:pointer.duration_ms,duration_ms:pointer.duration_ms,scheduled_ms:pointer.ack_monotonic_ms-sample.planned_monotonic_ms})
    else {sample.status='failed';sample.error={message:'Transition or pointer lacks durable acknowledgement'}}
  }
  return record
}

export function browserSummary(records) {
  assert(records.length===2 && new Set(records.map(r=>r.account_id)).size===2,'Keep both browser outcomes')
  return {scope:'two_browser_rows_inside_formal_roster',planned_pages:2,completed_pages:records.filter(r=>r.status==='completed').length,
    failed_pages:records.filter(r=>r.status==='failed').length,not_executed_pages:records.filter(r=>r.status==='not_executed').length,
    browser_capacity_percentiles_estimated:false,pooled_with_protocol_quantiles:false,
    passed:records.every(r=>r.status==='completed' && !r.page_errors.length && !r.unexpected_console_errors?.length
      && (r.workload_profile!==workloadProfile || Number.isFinite(r.release_skew_ms) && r.release_skew_ms<=1000)
      && r.save_samples.every(s=>['acknowledged','superseded'].includes(s.status)))}
}

export function classifyRetestConsoleErrors(record,baseURL) {
  const classified=classifyCapacityConsoleErrors(record.console,baseURL,undefined,record.login,record)
  const acknowledged=new Set(record.event_acknowledgements.map(row=>row.event_id))
  const checkpoints=new Set(record.checkpoint_acknowledgements)
  const chains=[]
  const withoutAssignedId=event=>Object.fromEntries(Object.entries(event).filter(([key])=>key!=='event_id'))
  for(const rejected of record.requests) {
    if(rejected.path!=='/api/v1/assessment/process/capture-v2/batch/' || rejected.status!==409
      || rejected.response?.error?.code!=='parent_not_received') continue
    const bundle=rejected.body?.capture_bundle,events=bundle?.events || [],pending=bundle?.checkpoints || []
    if(!events.length) continue
    const ids=events.map(event=>record.capture_events.find(row=>row.client_instance_id===bundle.client_instance_id
      && row.client_sequence_no===event.client_sequence_no)?.event_id)
    if(ids.some(id=>!id || !acknowledged.has(id)) || pending.some(row=>!checkpoints.has(row.checkpoint_id))) continue
    const confirmed=record.requests.find(request=>request.status>=200 && request.status<300
      && /^\/api\/v1\/(?:student|assessment)\//.test(request.path)
      && request.ack_monotonic_ms>rejected.ack_monotonic_ms
      && request.body?.capture_bundle?.client_instance_id===bundle.client_instance_id
      && ids.every(id=>request.response?.acknowledgements?.some(row=>row.event_id===id))
      && pending.every(row=>request.response?.checkpoint_ids?.includes(row.checkpoint_id))
      && events.every(event=>request.body.capture_bundle.events.some(row=>row.client_sequence_no===event.client_sequence_no
        && isDeepStrictEqual(withoutAssignedId(row),withoutAssignedId(event)))))
    if(confirmed) chains.push({path:rejected.path,client_instance_id:bundle.client_instance_id,event_ids:ids,
      checkpoint_ids:pending.map(row=>row.checkpoint_id),rejected_at:rejected.ack_monotonic_ms,confirmed_at:confirmed.ack_monotonic_ms})
  }
  const origin=new globalThis.URL(baseURL).origin,expected=[]
  const unexpected=classified.unexpected_console_errors.filter(row=>{
    if(expected.length>=chains.length || row.text!=='Failed to load resource: the server responded with a status of 409 (Conflict)') return true
    let url
    try {url=new globalThis.URL(row.location?.url)} catch {return true}
    if(url.origin!==origin || url.pathname!=='/api/v1/assessment/process/capture-v2/batch/') return true
    expected.push(row);return false
  })
  return {...classified,expected_capture_recovery_console_errors:expected,capture_recovery_chains:chains,unexpected_console_errors:unexpected}
}

export function installEvidence(page,record,clock) {
  const requests=new Map(),pending=new Set()
  page.on('console',message=>record.console.push({type:message.type(),text:message.text(),location:message.location(),at:clock()}))
  page.on('pageerror',error=>record.page_errors.push({name:error.name,message:error.message,at:clock()}))
  page.on('request',request=>{
    const path=new globalThis.URL(request.url()).pathname
    let body
    if(request.method()==='POST') {try{body=request.postDataJSON()}catch{body=undefined}}
    const row=captureOutbound(record,path,body,clock());row.method=request.method();requests.set(request,row)
  })
  page.on('requestfailed',request=>{const row=requests.get(request);if(row) row.transport_error=request.failure()?.errorText})
  page.on('response',response=>{
    const request=requests.get(response.request())
    if(!request) return
    request.status=response.status()
    const terminalRead=request.method==='GET' && /^\/api\/v1\/student\/sessions\/\d+\/(?:submission|summary)\/$/.test(request.path)
    if(terminalRead || !/^\/api\/v1\/(?:assessment\/|student\/(?:attempts\/|sessions\/|tasks\/\d+\/(?:start|resume)\/))/.test(request.path)) {
      request.ack_monotonic_ms=clock();return
    }
    if(!response.headers()['content-type']?.includes('application/json')) return
    const read=response.json().then(body=>captureInbound(record,request,body,response.status(),clock()))
      .catch(error=>{record.evidence_errors??=[];record.evidence_errors.push({name:error.name,message:error.message,path:request.path})})
      .finally(()=>pending.delete(read))
    pending.add(read)
  })
  return async()=>{while(pending.size) await Promise.all([...pending])}
}

export function browserEvidenceSettled(record) {
  const events=new Set(record.event_acknowledgements.map(row=>row.event_id))
  const checkpoints=new Set(record.checkpoint_acknowledgements)
  return record.capture_events.length>0 && record.capture_checkpoints.length>0 && record.time_syncs.length>0
    && record.capture_events.every(row=>events.has(row.event_id))
    && record.capture_checkpoints.every(row=>checkpoints.has(row.checkpoint_id))
    && record.time_syncs.every(row=>row.confirmed?.sync_quality==='valid')
}

async function settleDurableEvidence(current) {
  if(current.row.role==='login') return current.settle()
  const deadline=Date.now()+60000
  do {
    await current.settle()
    if(browserEvidenceSettled(current.record)) return
    await pause(100)
  } while(Date.now()<deadline)
  throw new Error('Browser event, checkpoint or clock confirmation remains incomplete')
}

export async function cleanupRuntime(current) {
  if(current.row.role==='login' || current.record.final_status==='completed') return settleDurableEvidence(current)
  // Ordinary UI navigation runs the application close guard and its capture/lease protocol.
  await settleDurableEvidence(current)
  await current.page.locator('a[href="/password/change/"]').click()
  await expect(current.page).toHaveURL(url=>url.pathname==='/password/change/',{timeout:60000})
  current.record.cleanup={ui_navigation:'/password/change/',at:current.clock()}
  await settleDurableEvidence(current)
}

export async function navigate(current,assignment,{phase='workload'}={}) {
  const path=`/student/sessions/${current.account.session_id}/questions/${assignment}/`
  if(new globalThis.URL(current.page.url()).pathname===path) return
  const steps=questionNavigationSteps(current.page,assignment,path,()=>operable(current.page),{mobile:current.index===1})
  const requested=current.clock()
  const sample={assignment_id:assignment,phase,operation_index:current.record.navigation.length,
    requested_monotonic_ms:requested,status:'not_executed'}
  current.record.navigation.push(sample)
  try {
    await steps.enabled(60000)
    sample.clicked_monotonic_ms=current.clock();sample.button_wait_ms=sample.clicked_monotonic_ms-requested
    await steps.click(60000);await steps.target(60000);await steps.ready()
    sample.ready_monotonic_ms=current.clock();sample.duration_ms=sample.ready_monotonic_ms-sample.clicked_monotonic_ms
    sample.status='completed'
  } catch(error) {sample.status='failed';throw error}
}

async function setAnswer(page,value) {
  const input=page.locator('[name="answer"]').first(),type=await input.getAttribute('type')
  if(type==='radio') {
    if(value===null) await page.locator('[data-clear-response]').click()
    else await page.locator(`[name="answer"][value=${JSON.stringify(String(value))}]`).check()
  } else if(type==='checkbox') {
    for(const option of await page.locator('[name="answer"]').all()) await option.setChecked((value||[]).includes(await option.getAttribute('value')))
  } else await input.fill(value??'')
}

export async function login(current) {
  const began=current.clock(),clockDelta=began-performance.now()
  current.record.login_actual_monotonic_ms=began
  try {
    current.record.login=await releaseLogin(current.page,current.account)
    const actual=current.record.login.login_clicked_driver_ms+clockDelta
    current.record.login_actual_monotonic_ms=actual
    if(Number.isFinite(current.record.login_planned_monotonic_ms))
      current.record.release_skew_ms=Math.max(0,actual-current.record.login_planned_monotonic_ms)
    // Task list loading is part of the real login journey. No additional bootstrap request.
    current.start=await startFixtureTask(current.page,current.account,()=>operable(current.page))
    const ready=current.clock()
    current.record.login_ready_monotonic_ms=ready
    current.record.login_planned_to_ready_ms=ready-(current.record.login_planned_monotonic_ms??actual)
  } finally {current.record.login_ms=current.clock()-current.record.login_actual_monotonic_ms}
}

export async function startup(current) {
  const began=current.clock()
  await current.start();current.record.startup_ms=current.clock()-began
  await current.settle()
  const ids=await current.page.locator('[data-nav-assignment]').evaluateAll(nodes=>[...new Set(nodes.map(n=>Number(n.getAttribute('data-nav-assignment'))))])
  assert.deepEqual(ids.sort((a,b)=>a-b),current.account.questions.map(q=>q.assignment_id).sort((a,b)=>a-b),'UI manifest must expose every assigned question')
  current.record.manifest_verified=true
}

export async function driveSchedule(current,plan,{preparation=false}={}) {
  const start=current.clock()
  if(preparation) current.record.preparation_save_samples=true
  const base=preparation || current.row.role==='full-flow'?start:current.release.release_monotonic*1000
  current.record.workload_start_monotonic_ms=base
  await runFrozenSchedule(current.row.save_schedule,base,async(action,sample,index)=>{
    await navigate(current,action.assignment_id,{phase:preparation?'preparation':'workload'})
    sample.actual_event_monotonic_ms=current.clock();sample.client_backlog_ms=Math.max(0,sample.actual_event_monotonic_ms-sample.planned_monotonic_ms)
    await setAnswer(current.page,action.value)
    const next=current.row.save_schedule[index+1]
    if(!next || next.assignment_id!==action.assignment_id) {
      const desired=current.row.answers.find(q=>q.assignment_id===action.assignment_id)
      const control=current.page.locator('[data-inline-confidence]')
      if(desired.confidence_condition==='prompt') {
        await expect(control).toBeVisible();await expect(control).toBeEnabled()
        await control.fill(desired.confidence_probability===null?'':String(desired.confidence_probability))
      } else assert.equal(await control.count(),0,'No confidence prompt is authorized for the frozen control assignment')
    }
    current.publish()
  },{now:current.clock,pause,samples:current.record.save_samples,paced:!preparation})
  if(!preparation) await pause(Math.max(0,base+plan.workload_duration_ms-current.clock()))
  await expect(current.page.locator('[data-save-status]')).toContainText('草稿已保存',{timeout:60000})
  await navigate(current,current.row.answers[0].assignment_id,{phase:preparation?'preparation':'history-review'})
  await expect(current.page.locator('[name="answer"]').first()).toBeEnabled()
  current.record.return_verified=true
  await current.settle()
}

export async function submit(current) {
  const {page,account,record}=current
  const accepted=page.waitForResponse(r=>r.request().method()==='POST' && new globalThis.URL(r.url()).pathname===`/api/v1/student/sessions/${account.session_id}/finish/`,{timeout:60000})
  void accepted.catch(()=>undefined)
  const button=page.locator('[data-finish-request]').first()
  if(await button.isVisible()) await button.click()
  else {await page.locator('[data-mobile-navigation-trigger]').click();await page.locator('#assessment-mobile-navigation').getByRole('button',{name:'提交试卷',exact:true}).click()}
  const confirm=page.locator('[data-paper-submit-confirm]')
  await expect(confirm).toBeVisible({timeout:60000})
  await expect(confirm).toBeEnabled()
  const began=current.clock()
  record.submission_started_monotonic_ms=began
  await confirm.click()
  const response=await accepted,receipt=domain(await response.json())
  assert.equal(response.status(),202,'Whole paper must be durably accepted')
  assert(receipt.submission_id && receipt.status_url,'Missing submission identity')
  const statusURL=new globalThis.URL(receipt.status_url,page.url())
  assert(statusURL.origin===new globalThis.URL(page.url()).origin && statusURL.pathname===`/api/v1/student/sessions/${account.session_id}/submission/`,'Submission receipt scope differs')
  record.submission_id=receipt.submission_id;record.accepted_ms=current.clock()-began
  record.submission_ms=record.accepted_ms
  await page.waitForURL(url=>url.pathname===`/student/sessions/${account.session_id}/complete/`,{timeout:300000})
  const final=await current.context.request.get(statusURL.href),terminal=domain(await final.json())
  assert(final.ok() && terminal.status==='completed' && terminal.submission_id===record.submission_id,'Submission must reach its terminal state')
  record.final_status=terminal.status;record.terminal_ms=current.clock()-began
  record.completion_ms=record.terminal_ms;record.processing_ms=record.completion_ms-record.submission_ms
  const summary=await current.context.request.get(`/api/v1/student/sessions/${account.session_id}/summary/`)
  const result=domain(await summary.json())
  assert(summary.ok() && result.session_id===account.session_id && result.status==='completed','Finished summary differs')
  record.finished_read_verified=true
}

export function rebasePreparationClocks(record,release) {
  const delta=release.release_monotonic*1000-release.release_wall_time*1000
  const adjust=row=>{for(const key of Object.keys(row)) if(key.endsWith('_monotonic_ms') && Number.isFinite(row[key])) row[key]+=delta}
  adjust(record)
  for(const collection of ['save_samples','requests','pointer_saves']) for(const row of record[collection]) adjust(row)
}

async function waitRelease(path,timeout=300000) {
  const deadline=Date.now()+timeout
  while(!existsSync(path)) {assert(Date.now()<deadline,'Coordinator release timed out');await pause(100)}
  const release=load(path)
  assert(Number.isFinite(release.release_monotonic) && release.release_monotonic>0 && Number.isFinite(release.release_wall_time),'Both release clocks required')
  return release
}

export async function waitPersonalLogin(row,release,record,{now=()=>Date.now(),pause:wait=pause}={}) {
  const planned=release.release_monotonic*1000+(row.login_offset_ms??0)
  await wait(Math.max(0,planned-now()))
  const actual=now()
  Object.assign(record,{login_planned_monotonic_ms:planned,login_actual_monotonic_ms:actual,
    release_skew_ms:Math.max(0,actual-planned)})
}

export async function runRetestBrowser(values) {
  for(const key of ['run-manifest','fixture','plan','output','base-url','account-ids','ready-file','release-file']) assert(values[key],`--${key} required`)
  const manifest=load(values['run-manifest']),fixture=load(values.fixture),plan=load(values.plan)
  const selected=validateRetestInputs(manifest,fixture,plan,values['base-url'],values['account-ids'].split(','))
  if(plan.fixture_sha256) assert.equal(hash(readFileSync(resolve(values.fixture))),plan.fixture_sha256,'Fixture bytes differ from frozen plan')
  const output=resolve(values.output);mkdirSync(output,{recursive:true})
  const records=selected.map(({account,row},index)=>makeBrowserRecord(account,row,plan,index))
  let visual,mobile,release=null
  const currents=[]
  let lastPublish=0
  const clock=()=>release?release.release_monotonic*1000+Date.now()-release.release_wall_time*1000:Date.now()
  const serialize=value=>{
    let text=JSON.stringify(clean(value))
    for(const {account} of selected) if(account.password) text=text.split(account.password).join('[credential omitted]')
    return text
  }
  const save=()=>{
    privateWrite(resolve(output,'browser-records.jsonl'),records.map(r=>serialize(r)).join('\n')+'\n')
    privateWrite(resolve(output,'browser-summary.json'),JSON.stringify({run_id:plan.run_id,plan_id:plan.plan_id,...browserSummary(records)},null,2))
  }
  try {
    visual=await createVisualProbeContext(values['base-url'])
    mobile=await visual.context.browser().newContext({...mobileContextOptions,baseURL:values['base-url']})
    for(let index=0;index<2;index++) {
      const context=index?mobile:visual.context,page=context.pages()[0]||await context.newPage()
      await page.setViewportSize(records[index].viewport);page.setDefaultTimeout(60000)
      const current={...selected[index],record:records[index],index,context,page,clock,release:null}
      current.publish=()=>{if(Date.now()-lastPublish>=1000){lastPublish=Date.now();save()}};current.settle=installEvidence(page,current.record,clock);currents.push(current)
    }
    assert(currents[0].context!==currents[1].context,'Independent browser contexts required')
    await Promise.all(currents.map(async current=>{
      try {
        current.record.status='preparing';await prepareLogin(current.page,current.account)
        if(['startup','save','submission'].includes(current.row.role)) await login(current)
        if(['save','submission'].includes(current.row.role)) await startup(current)
        if(current.row.role==='submission') await driveSchedule(current,plan,{preparation:true})
        current.record.status='prepared'
      } catch(error) {current.record.status='failed';current.record.error={name:error.name,message:error.message}}
    }))
    save()
    assert(records.every(r=>r.status==='prepared'),'Both browser roles must be prepared before the ready signal')
    privateWrite(resolve(values['ready-file']),JSON.stringify({ready:true,state:'ready',plan_id:plan.plan_id,run_id:plan.run_id,
      browser_account_ids:records.map(r=>r.account_id),prepared_roles:records.map(r=>r.role)}))
    release=await waitRelease(values['release-file'])
    for(const record of records) {rebasePreparationClocks(record,release);record.release_clock=release}
    for(const current of currents) current.release=release
    await pause(Math.max(0,release.release_wall_time*1000-Date.now()))
    await Promise.all(currents.map(async current=>{
      const record=current.record
      try {
        await waitPersonalLogin(current.row,release,record,{now:current.clock})
        record.status='running'
        if(['login','full-flow'].includes(current.row.role)) await login(current)
        if(['startup','full-flow'].includes(current.row.role)) await startup(current)
        if(['save','full-flow'].includes(current.row.role)) await driveSchedule(current,plan)
        if(current.row.terminal_required || ['full-flow','submission'].includes(current.row.role)
          || current.row.role==='save' && plan.workload==='paper-20q') await submit(current)
        await current.settle();reconcileBrowserActions(record,current.row)
        assert(record.save_samples.every(s=>['acknowledged','superseded'].includes(s.status)),'Frozen actions lack durable acknowledgement')
        Object.assign(record,classifyRetestConsoleErrors(record,values['base-url']))
        assert(!record.page_errors.length && !record.evidence_errors?.length && !record.unexpected_console_errors.length,'Browser application or evidence errors')
        record.status='completed'
      } catch(error) {record.status='failed';record.error={name:error.name,message:error.message}}
      finally {await current.settle();reconcileBrowserActions(record,current.row);save()}
    }))
    if(values['pressure-finished-file']) {
      const deadline=Date.now()+1800000
      while(!existsSync(values['pressure-finished-file'])) {assert(Date.now()<deadline,'Pressure completion signal timed out');await pause(250)}
      for(const current of currents) {
        if(current.index===0) current.record.visual=await captureVisualLayouts(current.page,output,factor=>visual.setZoom(current.page,factor))
        else {
          const layout=await captureFirstReadyLayout(current.page,output,current.index,current.record.viewport)
          current.record.visual={layouts:[layout],passed:layout.passed}
        }
        current.record.visual_phase='after_pressure'
      }
    }
    for(const current of currents) {
      try {await cleanupRuntime(current)}
      catch(error) {current.record.status='failed';current.record.cleanup_error={name:error.name,message:error.message}}
    }
  } catch(error) {
    for(const record of records) if(record.status!=='failed') {record.status='failed';record.error={name:error.name,message:error.message}}
  } finally {
    for(const current of currents) {await current.settle();reconcileBrowserActions(current.record,current.row)}
    await mobile?.close().catch(()=>undefined);await visual?.close().catch(()=>undefined)
    for(const current of currents) await current.settle()
    for(const record of records) Object.assign(record,classifyRetestConsoleErrors(record,values['base-url']))
    save()
  }
  const summary=browserSummary(records)
  if(!summary.passed) process.exitCode=1
  return {records,summary,plan_id:plan.plan_id,run_id:plan.run_id,fixture_sha256:hash(readFileSync(resolve(values.fixture)))}
}

export const retestBrowserOptions = {
  'run-manifest':{type:'string'},fixture:{type:'string'},plan:{type:'string'},output:{type:'string'},
  'base-url':{type:'string',default:'http://127.0.0.1:18085'},'account-ids':{type:'string'},
  'ready-file':{type:'string'},'release-file':{type:'string'},'pressure-finished-file':{type:'string'},
}
if(process.argv[1] && import.meta.url===pathToFileURL(resolve(process.argv[1])).href) {
  const {values}=parseArgs({options:retestBrowserOptions});await runRetestBrowser(values)
}
