import { createHash } from 'node:crypto'

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical)
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]))
  return value
}
export function digest(value) {
  return createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex')
}
export function projectPayload(value, fields, eventType) {
  const enrichment = eventType === 'response_transition' ? ['before_snapshot_id', 'after_snapshot_id']
    : ['research_tool_requested', 'research_tool_observed', 'confidence_observed'].includes(eventType) ? ['snapshot_id'] : []
  if (Object.keys(value).some((key) => !fields.includes(key) && !enrichment.includes(key))) throw new Error('Unexpected server payload enrichment')
  return Object.fromEntries(fields.map((key) => [key, value[key]]))
}
export function safeRequest(path, body) {
  if (!/^\/(api\/v1\/assessment\/process\/|api\/v1\/student\/attempts\/)/.test(path) || !body || typeof body !== 'object') return null
  const material = body.atomic_submission ?? body.save_and_leave ?? body
  return { path, field_names: Object.keys(body).sort(), client_instance_id: material.client_instance_id ?? body.writer_client_instance_id ?? null,
    has_writer_lease: Boolean(body.writer_lease_token || body.lease_token),
    events: (material.events || []).map((event) => ({ event_id: event.event_id, client_sequence_no: event.client_sequence_no,
      session_id: event.session_id, attempt_id: event.attempt_id, event_type: event.event_type,
      payload_fields: Object.keys(event.payload || {}).sort(), payload_sha256: digest(event.payload) })) }
}
export async function captureProtocol(page) {
  const requests = []
  const clientAcknowledgements = []
  const byRequest = new Map()
  const pending = new Set()
  await page.exposeBinding('__ktWitnessAcknowledgement', (_, row) => {
    if (!row || typeof row.client_instance_id !== 'string' || !Array.isArray(row.acknowledgements)) throw new Error('Invalid witness acknowledgement')
    clientAcknowledgements.push({ client_instance_id: row.client_instance_id, acknowledgements: row.acknowledgements.map((ack) => ({
      event_id: ack.event_id, client_sequence_no: ack.client_sequence_no, server_ingest_sequence_no: ack.server_ingest_sequence_no })) })
  })
  await page.addInitScript(() => {
    const fetch = globalThis.fetch.bind(globalThis)
    globalThis.fetch = async (input, options) => {
      const path = new globalThis.URL(typeof input === 'string' ? input : input instanceof globalThis.URL ? input.href : input.url, globalThis.location.href).pathname
      const response = await fetch(input, options)
      if (options?.method?.toUpperCase() === 'POST' && /^\/(api\/v1\/assessment\/process\/|api\/v1\/student\/attempts\/)/.test(path)) {
        const request = JSON.parse(String(options.body))
        const material = request.atomic_submission ?? request.save_and_leave ?? request
        if (material.client_instance_id && Array.isArray(material.events)) {
          const body = (await response.clone().json()).data
          await globalThis.__ktWitnessAcknowledgement({ client_instance_id: material.client_instance_id, acknowledgements: body?.acknowledgements || [] })
        }
      }
      return response
    }
  })
  page.on('request', (request) => {
    if (request.method() !== 'POST') return
    let body
    try { body = request.postDataJSON() } catch { return }
    const row = safeRequest(new globalThis.URL(request.url()).pathname, body)
    if (!row) return
    row.sent_at = new Date().toISOString()
    byRequest.set(request, row)
    requests.push(row)
  })
  page.on('requestfailed', (request) => {
    const row = byRequest.get(request)
    if (row) row.transport_failure = true
  })
  page.on('response', (response) => {
    const row = byRequest.get(response.request())
    if (!row) return
    row.status = response.status()
    const work = response.json().then((body) => {
      row.success = body.data?.success ?? null
      row.error_code = body.error?.code ?? null
      row.acknowledgements = (body.data?.acknowledgements || []).map((ack) => ({ event_id: ack.event_id,
        client_sequence_no: ack.client_sequence_no, server_ingest_sequence_no: ack.server_ingest_sequence_no }))
      row.rejection_count = (body.data?.rejections || []).length
    }).catch(() => { row.response_json_unavailable = true }).finally(() => pending.delete(work))
    pending.add(work)
  })
  return { requests, clientAcknowledgements, audit: null, async settle() { await Promise.all([...pending]) } }
}
