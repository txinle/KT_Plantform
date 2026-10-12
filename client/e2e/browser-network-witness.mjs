import { digest } from './capture-protocol-witness.mjs'

const uuid = value => typeof value === 'string' && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(value) ? value : null
const correlation = value => uuid(value) || (typeof value === 'string' && /^[a-f0-9]{32}$/i.test(value) ? value : null)
const integer = value => Number.isSafeInteger(value) && value >= 0 ? value : null
const timestamp = value => typeof value === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : null
const fields = value => Object.keys(value || {}).filter(key => /^[a-z][a-z0-9_]{0,63}$/.test(key)).sort()
const knownErrors = new Set(['ENTRY_MATERIAL_CONFLICT', 'RESPONSE_VERSION_CONFLICT', 'response_version_conflict',
  'navigation_response_version_conflict', 'navigation_response_conflict', 'writer_lease_conflict',
  'navigation_material_conflict', 'submission_material_conflict', 'task_terminated', 'TASK_TERMINATED',
  'VALIDATION_ERROR', 'AUTHENTICATION_REQUIRED', 'PERMISSION_DENIED', 'NOT_FOUND'])
const knownTriggers = new Set(['resume', 'heartbeat', 'ack_progress', 'navigation', 'session_end', 'pagehide', 'commit'])
const knownEventTypes = new Set(['activity_heartbeat', 'confidence_skipped', 'confidence_committed', 'confidence_observed',
  'confidence_prompt_shown', 'attempt_closed', 'attempt_reopened', 'window_focused', 'window_blurred', 'pageshow', 'pagehide',
  'document_visible', 'document_hidden', 'network_online', 'network_offline', 'research_tool_observed', 'research_tool_requested',
  'research_tool', 'response_transition', 'response_state_changed', 'response_input_started', 'response_pointer_conflict_observed',
  'response_branch_resolved', 'response_commit_requested', 'response_committed', 'response_commit_rejected', 'input_operation',
  'text_edit_observed', 'item_rendered', 'item_left', 'item_entered', 'feedback_exposure', 'feedback_shown', 'explanation_shown',
  'hint_opened', 'hint_closed', 'resource_opened', 'resource_closed', 'simulation_run_started', 'simulation_run_completed',
  'simulation_parameter_changed', 'simulation_error_returned', 'session_started', 'session_completed', 'session_reopened',
  'session_expired', 'session_invalidated', 'legacy_session_start_observed', 'legacy_session_end_observed', 'legacy_visit_observed',
  'legacy_submission_observed', 'legacy_draft_observed'])
const navigationReceiptFields = ['success', 'committed', 'navigation_token', 'acknowledgements', 'checkpoint_ids',
  'response_version', 'active_response_event_id', 'saved_at', 'status', 'writer_lease_released', 'to_assignment_id', 'next_url']
const submissionReceiptFields = ['committed', 'acknowledgements', 'checkpoint_ids', 'final_response_event_id', 'response_version']
const protocolPath = path => /^\/api\/v1\/(?:assessment\/process\/(?:capture-v2\/(?:batch|attempts\/\d+\/(?:tools|resolve))|events\/batch|capture-checkpoints|time-sync\/(?:create|finalize)|attempts\/\d+\/(?:writer-lease\/(?:acquire|renew|release)|confidence\/(?:prompt-shown|commit|skip)))\/|student\/attempts\/\d+\/(?:draft|submit|skip|recover)\/|student\/sessions\/\d+\/questions\/\d+\/)$/.test(path)

export const browserStorageContract = () => ({ eventTypes: [...knownEventTypes], checkpointTriggers: [...knownTriggers] })

function clock(value) {
  return { client_wall_time_utc: timestamp(value.client_wall_time_utc), client_occurred_at: timestamp(value.client_occurred_at),
    client_monotonic_ms: ((typeof value.client_monotonic_ms === 'number' && Number.isFinite(value.client_monotonic_ms) && value.client_monotonic_ms >= 0)
      || (typeof value.client_monotonic_ms === 'string' && /^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(value.client_monotonic_ms)
        && Number.isFinite(Number(value.client_monotonic_ms)))) ? value.client_monotonic_ms : null,
    client_time_origin: timestamp(value.client_time_origin), time_sync_id: uuid(value.time_sync_id) }
}
function event(value) {
  return { event_id: uuid(value.event_id), client_sequence_no: integer(value.client_sequence_no), session_id: integer(value.session_id),
    attempt_id: integer(value.attempt_id), event_type: knownEventTypes.has(value.event_type) ? value.event_type : null,
    event_version: integer(value.event_version), correlation_id: uuid(value.correlation_id), ...clock(value),
    app_version_sha256: digest(value.app_version ?? ''), quality_flags_sha256: digest(value.quality_flags ?? []),
    payload_fields: fields(value.payload), payload_sha256: digest(value.payload ?? {}), material_sha256: digest(value) }
}
function checkpoint(value) {
  return { checkpoint_id: uuid(value.checkpoint_id), client_instance_id: uuid(value.client_instance_id), session_id: integer(value.session_id),
    trigger: knownTriggers.has(value.trigger) ? value.trigger : null, ...clock(value),
    ...Object.fromEntries(['highest_emitted_sequence_no', 'highest_acked_sequence_no', 'highest_contiguous_acked_sequence_no',
      'pending_event_count', 'failed_event_count'].map(key => [key, integer(value[key])])),
    app_version_sha256: digest(value.app_version ?? ''), quality_flags_sha256: digest(value.quality_flags ?? []),
    missing_local_sequence_ranges_sha256: digest(value.missing_local_sequence_ranges ?? []),
    material_sha256: digest(value), material_fields: fields(value) }
}
function entryReceipt(value) {
  if (!value || typeof value !== 'object') return null
  return { navigation_id: uuid(value.navigation_id), entry_event_id: uuid(value.entry_event_id), entered_at: timestamp(value.entered_at),
    ...Object.fromEntries(['session_id', 'assignment_id', 'attempt_id', 'visit_id', 'visit_index'].map(key => [key, integer(value[key])])) }
}
function acknowledgement(value) {
  return { event_id: uuid(value.event_id), client_sequence_no: integer(value.client_sequence_no), server_ingest_sequence_no: integer(value.server_ingest_sequence_no) }
}
export function browserRequestMaterial(path, body) {
  if (!protocolPath(path) || !body || typeof body !== 'object') return null
  const bundle = body.atomic_submission ?? body.save_and_leave ?? body
  const row = { path, client_instance_id: uuid(bundle.client_instance_id ?? body.writer_client_instance_id),
    material_sha256: digest(body), material_fields: fields(body),
    events: Array.isArray(bundle.events) ? bundle.events.map(event) : [],
    checkpoints: (Array.isArray(bundle.checkpoints) ? bundle.checkpoints : body.checkpoint_id ? [body] : []).map(checkpoint) }
  if (/\/questions\/\d+\/$/.test(path)) row.entry = { navigation_id: uuid(body.navigation_id),
    reason: ['navigation_click', 'resume'].includes(body.reason) ? body.reason : null, ...clock(body), material_sha256: digest(body) }
  if (body.save_and_leave) row.navigation = { token: uuid(body.navigation_token), leave_event_id: uuid(body.leave_event_id),
    target_event_id: uuid(body.target_event_id), to_assignment_id: integer(body.to_assignment_id), expected_version: integer(bundle.expected_version),
    client_clicked_at: timestamp(body.client_clicked_at), material_sha256: digest(Object.fromEntries([
      'save_and_leave', 'leave_event_id', 'target_event_id', 'to_assignment_id', 'client_clicked_at',
    ].map(key => [key, body[key]]))) }
  if (body.submission_token) row.submission = { token: uuid(body.submission_token), target_event_id: uuid(body.target_event_id),
    expected_version: integer(bundle.expected_version), bundle_sha256: digest(body.atomic_submission ?? null),
    material_sha256: digest({ atomic_submission: body.atomic_submission ?? null, target_event_id: body.target_event_id ?? null, confidence: body.confidence ?? null }) }
  if (body.tool) row.tool = { operation_id: uuid(body.operation_id),
    tool: ['check', 'confidence', 'hint', 'doubt'].includes(body.tool) ? body.tool : null,
    stage: ['requested', 'presented', 'started', 'finished', 'responded', 'closed', 'mark_added', 'mark_changed', 'mark_removed'].includes(body.stage) ? body.stage : null }
  return row
}

export function observeBrowserProtocol(page, { candidate = {}, taskId, probe } = {}) {
  const evidence = { schema_version: 1, evidence_kind: 'real_browser_network_witness',
    candidate: Object.fromEntries(['source_sha256', 'frontend_sha256', 'config_sha256'].map(key => [key,
      typeof candidate?.[key] === 'string' && /^[a-f0-9]{64}$/.test(candidate[key]) ? candidate[key] : null])),
    task_id: integer(taskId), probe: integer(probe), session_ids: [], requests: [], settlement: null }
  const byRequest = new Map()
  const pending = new Set()
  const waiters = new Set()
  const handlers = {}
  let closed = false
  const changed = () => { for (const resolve of waiters) resolve(); waiters.clear() }
  const listen = (name, handler) => { handlers[name] = handler; page.on(name, handler) }
  listen('request', request => {
    if (closed || request.method() !== 'POST') return
    let body
    try { body = request.postDataJSON() } catch { return }
    const row = browserRequestMaterial(new globalThis.URL(request.url()).pathname, body)
    if (!row) return
    const headers = request.headers()
    Object.assign(row, { request_index: evidence.requests.length, sent_at: new Date().toISOString(),
      request_id: correlation(headers['x-request-id']), operation_id: correlation(headers['x-kt-operation-id']) })
    evidence.requests.push(row)
    const entrySession = /\/sessions\/(\d+)\/questions\//.exec(row.path)
    for (const session of [...row.events.map(event => event.session_id), ...row.checkpoints.map(checkpoint => checkpoint.session_id),
      ...(entrySession ? [Number(entrySession[1])] : [])]) {
      if (session !== null && !evidence.session_ids.includes(session)) evidence.session_ids.push(session)
    }
    byRequest.set(request, row)
    changed()
  })
  listen('requestfinished', request => {
    if (closed) return
    const row = byRequest.get(request)
    if (row) row.transport_finished = true
    changed()
  })
  listen('requestfailed', request => {
    if (closed) return
    const row = byRequest.get(request)
    if (row) row.transport_failure = true
    changed()
  })
  listen('response', response => {
    if (closed) return
    const row = byRequest.get(response.request())
    if (!row) return
    row.status = response.status()
    row.response_request_id = correlation(response.headers()['x-request-id'])
    const work = Promise.resolve().then(() => response.json()).then(body => {
      if (closed) return
      const data = body?.data
      row.success = typeof data?.success === 'boolean' ? data.success : null
      row.error_code = knownErrors.has(body?.error?.code) ? body.error.code : null
      row.acknowledgements = Array.isArray(data?.acknowledgements) ? data.acknowledgements.map(acknowledgement) : []
      row.checkpoint_ids = Array.isArray(data?.checkpoint_ids) ? data.checkpoint_ids.map(uuid) : data?.checkpoint_id ? [uuid(data.checkpoint_id)] : []
      row.rejection_count = Array.isArray(data?.rejections) ? data.rejections.length : 0
      if (row.entry) row.entry_receipt = entryReceipt(data?.entry_receipt)
      if (row.navigation || row.submission) row.receipt = { navigation_token: uuid(data?.navigation_token), submission_token: uuid(data?.submission_token),
        response_version: integer(data?.response_version), active_response_event_id: uuid(data?.active_response_event_id),
        final_response_event_id: uuid(data?.final_response_event_id), committed: data?.committed === true,
        to_assignment_id: integer(data?.to_assignment_id), saved_at: timestamp(data?.saved_at),
        writer_lease_released: data?.writer_lease_released === true,
        status: ['draft', 'visited', 'submitted', 'timed_out', 'unanswered'].includes(data?.status) ? data.status : null,
        persisted_material_sha256: digest(Object.fromEntries((row.navigation ? navigationReceiptFields : submissionReceiptFields)
          .map(key => [key, data?.[key] ?? null]))),
        material_sha256: digest(data ?? null) }
    }).catch(() => { if (!closed) row.response_json_unavailable = true }).finally(() => { pending.delete(work); changed() })
    pending.add(work)
  })
  return { evidence, async settle(timeoutMs = 2000) {
    if (closed) return evidence.settlement
    const deadline = globalThis.performance.now() + timeoutMs
    const unfinished = () => evidence.requests.filter(row => !row.transport_finished && !row.transport_failure).length
    while ((pending.size || unfinished()) && globalThis.performance.now() < deadline) {
      let timer
      let notify
      await new Promise(resolve => {
        notify = resolve
        waiters.add(resolve)
        timer = globalThis.setTimeout(resolve, Math.max(0, deadline - globalThis.performance.now()))
      })
      globalThis.clearTimeout(timer)
      waiters.delete(notify)
    }
    evidence.settlement = { complete: pending.size === 0 && unfinished() === 0,
      pending_response_count: pending.size, unfinished_request_count: unfinished(), budget_ms: timeoutMs }
    closed = true
    for (const [name, handler] of Object.entries(handlers)) page.off(name, handler)
    return evidence.settlement
  } }
}
