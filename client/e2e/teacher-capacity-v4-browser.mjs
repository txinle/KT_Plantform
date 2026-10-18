/** Actual Chromium/WebKit students and six teacher pages inside the v4 denominator. */
import assert from 'node:assert/strict'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { parseArgs } from 'node:util'
import process from 'node:process'
import { chromium, webkit } from '@playwright/test'
import { prepareLogin } from './student-concurrency-witness.mjs'
import { makeBrowserRecord, installEvidence, login, startup, driveSchedule, submit,
  cleanupRuntime, reconcileBrowserActions, classifyRetestConsoleErrors } from './student-capacity-retest-browser.mjs'

const pause = ms => new Promise(done => globalThis.setTimeout(done, Math.max(0, ms)))
const load = path => JSON.parse(readFileSync(path, 'utf8').replace(/^\uFEFF/, ''))
const privateWrite = (path, value) => writeFileSync(path, JSON.stringify(value), { mode:0o600 })

export function validateV4BrowserInputs(manifest, plan) {
  assert(manifest.version==='teacher-capacity-v4' && manifest.project==='kt-school-teacher-capacity-20261018'
    && manifest.root==='/home/ktdata/student-capacity-tests/20261018-teacher-capacity'
    && manifest.port===18086 && manifest.database==='kt_e2e'
    && JSON.stringify(manifest.browsers)===JSON.stringify(['chromium','webkit']), 'Owned v4 browser manifest required')
  assert(plan.capacity_version==='teacher-capacity-v4' && plan.target_origin==='http://127.0.0.1:18086'
    && [50,180].includes(plan.count) && plan.roster.length===plan.count
    && new Set(plan.roster.map(r=>r.account_id)).size===plan.count, 'Frozen full v4 roster required')
  const browsers=plan.roster.filter(r=>r.driver==='browser')
  assert(browsers.length===2 && plan.roster.every(r=>['browser','protocol'].includes(r.driver)), 'Two included browser actors required')
  return browsers
}

export function teacherCycleDelayMs(plan, cycle, releaseMs, nowMs) {
  if(plan.cell.mode!=='full-flow' || cycle!==17) return 0
  return Math.max(0,releaseMs+plan.workload_duration_ms+plan.login_window_ms/2-nowMs)
}

async function teacherPages(browser, teachers, plan, output, release, clock, records) {
  assert(teachers.project==='kt-school-teacher-capacity-20261018' && teachers.accounts.length===3)
  const contexts=[]
  const origin=plan.target_origin
  const scope=teachers.scope
  const query=new globalThis.URLSearchParams({experiment_id:String(scope.experiment_id),class_id:String(scope.class_id),view:'overview'})
  try {
    for(const account of teachers.accounts) {
      const context=await browser.newContext({baseURL:origin,viewport:{width:1440,height:900}})
      contexts.push(context)
      const page=await context.newPage()
      await prepareLogin(page,account)
      const authenticated=page.waitForResponse(r=>r.request().method()==='POST' && new globalThis.URL(r.url()).pathname==='/api/v1/auth/login/')
      await page.getByRole('button',{name:'登录',exact:true}).click()
      assert((await authenticated).ok(),'Teacher authentication failed')
      await page.close()
    }
    privateWrite(resolve(output,'teacher-ready.json'),{ready:true,plan_id:plan.plan_id})
    while(!existsSync(release)) await pause(50)
    const start=load(release)
    await pause(start.release_wall_time*1000-Date.now())
    for(let cycle=0;cycle<18;cycle++) {
      if(cycle>0 && plan.cell.mode==='entry' && existsSync(resolve(output,'student-pressure-finished.json'))) break
      await pause(teacherCycleDelayMs(plan,cycle,start.release_wall_time*1000,Date.now()))
      await Promise.all(contexts.flatMap((context,teacher)=>[0,1].map(async index=>{
        const page=await context.newPage()
        let stage='base'
        const base=[]
        const pending=new Set()
        page.on('response',response=>{
          const path=new globalThis.URL(response.url()).pathname
          if(response.request().method()!=='GET' || !path.startsWith(`/api/v1/teacher/courses/${scope.course_id}/data/`)) return
          const capturedStage=stage
          const task=(async()=>{
            const request=response.request()
            await response.finished()
            const sample={teacher, page:index, cycle, stage:capturedStage, category:path.endsWith('/status/')?'status':path.endsWith('/students/')?'search':path.endsWith('/advanced/')?'advanced':'base',
              path:path.replace(/\/\d+(?=\/|$)/g,'/:id'),status:response.status(),success:response.ok(),
              duration_ms:request.timing().responseEnd>=0?request.timing().responseEnd:null,
              completed_monotonic_ms:clock(),during_student_pressure:!existsSync(resolve(output,'student-pressure-finished.json'))}
            if(sample.success) {const body=await response.json().catch(()=>null);sample.success=body?.data!==undefined}
            records.push(sample);if(capturedStage==='base' && sample.success) base.push(sample)
            privateWrite(resolve(output,'teacher-records.json'),records)
          })().catch(error=>records.push({teacher,page:index,cycle,stage,success:false,category:'capture-error',error_type:error.name}))
          pending.add(task);task.finally(()=>pending.delete(task))
        })
        page.on('requestfailed',request=>{
          const path=new globalThis.URL(request.url()).pathname
          if(request.method()==='GET' && path.startsWith(`/api/v1/teacher/courses/${scope.course_id}/data/`)) records.push({
            teacher,page:index,cycle,stage,category:path.endsWith('/status/')?'status':path.endsWith('/students/')?'search':'advanced',
            success:false,duration_ms:null,error_type:'request_failed',during_student_pressure:!existsSync(resolve(output,'student-pressure-finished.json'))})
        })
        try {
          await page.goto(`/teacher/courses/${scope.course_id}/data/advanced/?${query}&table=${index?'responses':'sessions'}`,{waitUntil:'domcontentloaded'})
          await pause(30500)
          await Promise.all(pending)
          assert(base.length<=7,'Teacher base lifecycle exceeded seven successful GETs per page')
          stage='search'
          const search=page.getByLabel('按学生姓名或学号搜索')
          if(await search.count()) { await search.fill('隔离');await pause(750) }
          stage='manual'
          const refresh=page.getByTestId('refresh-research-data')
          if(await refresh.count()) { await refresh.click();await pause(750) }
          stage='page'
          const next=page.getByRole('button',{name:'下一页',exact:true})
          if(await next.count() && await next.isEnabled()) {await next.click();await pause(750)}
          await Promise.all(pending)
        } finally { await page.close() }
      })))
    }
  } finally {await Promise.all(contexts.map(c=>c.close()))}
}

export async function runV4Browser(values) {
  const manifest=load(values.manifest),plan=load(values.plan),fixture=load(values.fixture)
  const selected=validateV4BrowserInputs(manifest,plan)
  const output=resolve(values.output);mkdirSync(output,{recursive:true})
  const engines=[await chromium.launch({headless:true}),await webkit.launch({headless:true})]
  const records=[],currents=[],teacherRecords=[]
  let release
  const clock=()=>release?release.release_monotonic*1000+Date.now()-release.release_wall_time*1000:Date.now()
  const save=()=>{
    writeFileSync(resolve(output,'browser-records.jsonl'),records.map(r=>JSON.stringify(r)).join('\n')+'\n',{mode:0o600})
  }
  let teacherTask
  try {
    for(let index=0;index<selected.length;index++) {
      const row=selected[index],account=fixture.accounts.find(a=>a.account_id===row.account_id)
      assert(account && account.questions.length===20,'Frozen browser account missing')
      const record=makeBrowserRecord(account,row,plan,index)
      record.engine=index?'webkit':'chromium';record.same_DUT=true
      records.push(record)
      const context=await engines[index].newContext({baseURL:plan.target_origin,viewport:record.viewport})
      const page=await context.newPage();page.setDefaultTimeout(60000)
      const current={account,row,record,index,context,page,clock,publish:save}
      current.settle=installEvidence(page,record,clock);currents.push(current)
      if(plan.bootstrap==='warm') await prepareLogin(page,account)
    }
    if(plan.cell.teacher) teacherTask=teacherPages(engines[0],load(values.teachers),plan,output,values['release-file'],clock,teacherRecords)
    // Teacher login is setup; the six pages and student clicks share the release.
    if(teacherTask) {
      const until=Date.now()+300000
      while(!existsSync(resolve(output,'teacher-ready.json'))) {assert(Date.now()<until,'Teacher preparation timeout');await pause(50)}
    }
    privateWrite(values['ready-file'],{ready:true,plan_id:plan.plan_id,run_id:plan.run_id})
    const until=Date.now()+300000
    while(!existsSync(values['release-file'])) {assert(Date.now()<until,'Shared release timeout');await pause(50)}
    release=load(values['release-file'])
    await Promise.all(currents.map(async current=>{
      const {record,row}=current
      current.release=release
      try {
        await pause(release.release_wall_time*1000+row.login_offset_ms-Date.now())
        record.login_planned_monotonic_ms=release.release_monotonic*1000+row.login_offset_ms
        record.cold_navigation_started_monotonic_ms=clock()
        const dispatchSkew=Math.max(0,clock()-record.login_planned_monotonic_ms)
        if(plan.bootstrap==='cold') await prepareLogin(current.page,current.account)
        record.status='running';await login(current)
        if(plan.bootstrap==='cold') {
          record.cold_first_post_delay_ms=Math.max(0,record.login_actual_monotonic_ms-record.cold_navigation_started_monotonic_ms)
          record.release_skew_ms=dispatchSkew
        }
        record.cold_navigation_to_ready_ms=clock()-record.cold_navigation_started_monotonic_ms
        if(row.role==='full-flow') {await startup(current);await driveSchedule(current,plan);await submit(current)}
        await current.settle();reconcileBrowserActions(record,row)
        Object.assign(record,classifyRetestConsoleErrors(record,plan.target_origin))
        assert(!record.page_errors.length && !record.evidence_errors?.length && !record.unexpected_console_errors.length)
        record.status='completed'
      } catch(error) {record.status='failed';record.error={name:error.name,message:error.message}}
      finally {await current.settle();reconcileBrowserActions(record,row);save()}
    }))
    privateWrite(resolve(output,'browser-students-finished.json'),{at:clock(),plan_id:plan.plan_id})
    if(teacherTask) await teacherTask
    for(const current of currents) await cleanupRuntime(current)
  } finally {
    save();privateWrite(resolve(output,'teacher-records.json'),teacherRecords)
    await Promise.all(engines.map(engine=>engine.close()))
  }
  if(records.some(r=>r.status!=='completed')) process.exitCode=1
  return records
}

if(process.argv[1] && import.meta.url===pathToFileURL(resolve(process.argv[1])).href) {
  const {values}=parseArgs({options:Object.fromEntries(['manifest','plan','fixture','teachers','output','ready-file','release-file'].map(k=>[k,{type:'string'}]))})
  await runV4Browser(values)
}
