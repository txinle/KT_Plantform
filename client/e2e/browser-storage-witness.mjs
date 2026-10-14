import { browserStorageContract } from './browser-network-witness.mjs'

// This function runs in the page. Only digests and the explicit projections leave it.
export async function readOnlyBrowserStorage({ sessionIds, clientInstanceIds = [], timeoutMs = 2000, eventTypes = [], checkpointTriggers = [] }) {
  const unknown = reason => ({ schema_version: 1, read_complete: false, scope_complete: false, status: 'unknown', reason,
    database: { name: 'kt-process-events-v1', version: 2 } })
  const uuid = value => typeof value === 'string' && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(value) ? value : null
  const integer = value => Number.isSafeInteger(value) && value >= 0 ? value : null
  const timestamp = value => typeof value === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : null
  const fields = value => Object.keys(value || {}).filter(key => /^[a-z][a-z0-9_]{0,63}$/.test(key)).sort()
  const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object'
    ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value
  const digest = async value => Array.from(new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256',
    new globalThis.TextEncoder().encode(JSON.stringify(canonical(value))))), byte => byte.toString(16).padStart(2, '0')).join('')
  const clock = value => ({ client_wall_time_utc: timestamp(value.client_wall_time_utc), client_occurred_at: timestamp(value.client_occurred_at),
    client_monotonic_ms: ((typeof value.client_monotonic_ms === 'number' && Number.isFinite(value.client_monotonic_ms) && value.client_monotonic_ms >= 0)
      || (typeof value.client_monotonic_ms === 'string' && /^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(value.client_monotonic_ms)
        && Number.isFinite(Number(value.client_monotonic_ms)))) ? value.client_monotonic_ms : null,
    client_time_origin: timestamp(value.client_time_origin), time_sync_id: uuid(value.time_sync_id) })
  const event = async value => ({ event_id: uuid(value.event_id), client_sequence_no: integer(value.client_sequence_no), session_id: integer(value.session_id),
    attempt_id: integer(value.attempt_id), event_type: eventTypes.includes(value.event_type) ? value.event_type : null,
    event_version: integer(value.event_version), correlation_id: uuid(value.correlation_id), ...clock(value),
    app_version_sha256: await digest(value.app_version ?? ''), quality_flags_sha256: await digest(value.quality_flags ?? []),
    payload_fields: fields(value.payload), payload_sha256: await digest(value.payload ?? {}), material_sha256: await digest(value) })
  const checkpoint = async value => ({ checkpoint_id: uuid(value.checkpoint_id), client_instance_id: uuid(value.client_instance_id), session_id: integer(value.session_id),
    trigger: checkpointTriggers.includes(value.trigger) ? value.trigger : null, ...clock(value),
    ...Object.fromEntries(['highest_emitted_sequence_no', 'highest_acked_sequence_no', 'highest_contiguous_acked_sequence_no',
      'pending_event_count', 'failed_event_count'].map(key => [key, integer(value[key])])),
    app_version_sha256: await digest(value.app_version ?? ''), quality_flags_sha256: await digest(value.quality_flags ?? []),
    missing_local_sequence_ranges_sha256: await digest(value.missing_local_sequence_ranges ?? []),
    material_sha256: await digest(value), material_fields: fields(value) })
  let database
  let transaction
  let expired = false
  let timer
  const sessions = new Set(sessionIds?.filter(value => integer(value) !== null && value > 0))
  if (!sessions.size) return unknown('session_unavailable')
  if (!globalThis.indexedDB?.databases || !globalThis.crypto?.subtle) return unknown('storage_unavailable')
  const operation = async () => {
    const available = await globalThis.indexedDB.databases()
    if (expired) return unknown('read_timeout')
    if (!available.some(row => row.name === 'kt-process-events-v1')) return unknown('database_absent')
    database = await new Promise((resolve, reject) => {
      const request = globalThis.indexedDB.open('kt-process-events-v1')
      request.onupgradeneeded = () => { request.transaction.abort(); reject(new Error('schema_changed')) }
      request.onerror = request.onblocked = () => reject(new Error('open_failed'))
      request.onsuccess = () => { if (expired) { request.result.close(); reject(new Error('expired')) } else resolve(request.result) }
    })
    const stores = ['events', 'meta', 'checkpoints', 'dead_letters']
    if (database.version !== 2 || stores.some(name => !database.objectStoreNames.contains(name))) return unknown('schema_unavailable')
    const cutoff = new Date().toISOString()
    transaction = database.transaction(stores, 'readonly')
    const completed = new Promise((resolve, reject) => {
      transaction.oncomplete = resolve
      transaction.onabort = transaction.onerror = () => reject(new Error('read_failed'))
    })
    const [rows] = await Promise.all([Promise.all(stores.map(name => new Promise((resolve, reject) => {
      const request = transaction.objectStore(name).getAll()
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(new Error('read_failed'))
    }))), completed])
    transaction = null
    if (expired) return unknown('read_timeout')
    const [records, metadata, checkpoints, failed] = rows
    const selected = row => sessions.has(row.event?.session_id)
    const selectedRecords = records.filter(selected)
    const selectedFailed = failed.filter(selected)
    const selectedCheckpoints = checkpoints.filter(row => sessions.has(row.session_id))
    if ([...selectedRecords, ...selectedFailed].some(row => (row.event?.event_id != null && !uuid(row.event.event_id))
      || integer(row.client_sequence_no) === null || row.client_sequence_no < 1 || row.client_sequence_no !== row.event.client_sequence_no)) return unknown('invalid_record')
    const intents = metadata.filter(row => (/^(entry|navigation|submission)-intent:(\d+):/.test(row.key)
      || /^capture-mutation:(\d+):/.test(row.key) && row.value?.body?.leave_event_id)
      && sessions.has(Number(row.key.split(':')[1])) && row.value)
    const clients = new Set(clientInstanceIds.map(uuid).filter(Boolean))
    for (const row of [...selectedRecords, ...selectedFailed, ...selectedCheckpoints]) {
      if (!uuid(row.client_instance_id)) return unknown('invalid_record')
      clients.add(row.client_instance_id)
    }
    for (const row of intents) if (uuid(row.value.processSnapshot?.client_instance_id)) clients.add(row.value.processSnapshot.client_instance_id)
    const output = { schema_version: 1, read_complete: true, scope_complete: true, status: 'observed', reason: null,
      database: { name: 'kt-process-events-v1', version: 2 }, snapshot_cutoff_utc: cutoff,
      session_ids: [...sessions], client_instance_ids: [...clients], events: [], checkpoints: [], intents: [], streams: [] }
    for (const [source, delivery] of [[selectedRecords, 'pending'], [selectedFailed, 'failed']]) {
      for (const row of source) output.events.push({ ...await event(row.event), client_instance_id: row.client_instance_id, delivery_status: delivery })
    }
    for (const row of selectedCheckpoints) output.checkpoints.push({ ...await checkpoint(row), delivery_status: 'pending' })
    for (const row of intents) {
      const [prefix, session, attempt] = row.key.split(':')
      const kind = prefix === 'capture-mutation' ? 'close' : prefix.replace('-intent', '')
      const value = row.value
      const snapshot = value.processSnapshot
      let material = null
      const bundle = snapshot && integer(value.expectedResponseVersion) !== null
        ? { version: 1, ...snapshot, expected_version: value.expectedResponseVersion } : null
      if (kind === 'entry') {
        const original = { navigation_id: value.navigationId, reason: value.reason, client_occurred_at: value.clientOccurredAt,
          client_monotonic_ms: typeof value.clientMonotonicMs === 'number' && Number.isFinite(value.clientMonotonicMs)
            ? value.clientMonotonicMs.toFixed(3) : null, client_time_origin: value.clientTimeOrigin }
        material = { navigation_id: uuid(original.navigation_id), reason: ['navigation_click', 'resume'].includes(original.reason) ? original.reason : null,
          ...clock(original), material_sha256: await digest(original) }
        if (!material.navigation_id || !material.reason || !material.client_occurred_at || !material.client_time_origin
          || material.client_monotonic_ms === null || material.client_monotonic_ms < 0 || integer(value.assignmentId) === null) material = null
      } else if (bundle && kind === 'navigation') material = { token: uuid(value.token), leave_event_id: uuid(value.leaveEventId),
        target_event_id: uuid(value.head), to_assignment_id: integer(value.toAssignmentId), expected_version: integer(value.expectedResponseVersion),
        client_clicked_at: timestamp(value.clickedAt), material_sha256: await digest({ save_and_leave: bundle, leave_event_id: value.leaveEventId,
          target_event_id: value.head, to_assignment_id: value.toAssignmentId, client_clicked_at: value.clickedAt,
          ...(value.nextEntry ? { next_entry: value.nextEntry } : {}) }) }
      else if (bundle && kind === 'submission') material = { token: uuid(value.token), target_event_id: uuid(value.head),
        expected_version: integer(value.expectedResponseVersion), bundle_sha256: await digest(bundle),
        material_sha256: await digest({ atomic_submission: bundle, target_event_id: value.head ?? null, confidence: value.confidence ?? null }) }
      else if (kind === 'close' && snapshot && value.body) material = { token: uuid(value.body.capture_operation_id),
        leave_event_id: uuid(value.body.leave_event_id), material_sha256: await digest(Object.fromEntries(Object.entries({
          ...value.body, capture_bundle: snapshot }).filter(([key]) => !['lease_token','client_instance_id'].includes(key)))) }
      const next = value.nextEntry ?? value.body?.next_entry
      let nextProjection
      if (next !== undefined) {
        const original = { ...next }; delete original.assignment_id
        nextProjection = { assignment_id: integer(next.assignment_id), navigation_id: uuid(next.navigation_id),
          reason: ['navigation_click','resume'].includes(next.reason) ? next.reason : null, ...clock(next),
          material_sha256: await digest(original) }
        if (!nextProjection.assignment_id || !nextProjection.navigation_id || !nextProjection.reason
          || !nextProjection.client_occurred_at || !nextProjection.client_time_origin || nextProjection.client_monotonic_ms === null) material = null
      }
      output.intents.push({ kind, session_id: Number(session), attempt_id: kind === 'entry' ? null : integer(Number(attempt)),
        token: uuid(kind === 'entry' ? value.navigationId : kind === 'close' ? value.body.capture_operation_id : value.token), assignment_id: integer(value.assignmentId),
        to_assignment_id: integer(value.toAssignmentId), delivery_status: 'unconfirmed', document_sha256: await digest(value),
        material_sha256: material?.material_sha256 ?? null, material,
        ...(next !== undefined ? { next_entry: nextProjection, next_entry_sha256: await digest(next) } : {}),
        snapshot: snapshot ? { client_instance_id: uuid(snapshot.client_instance_id), event_boundary: integer(snapshot.event_boundary),
          events: await Promise.all((snapshot.events || []).map(event)), checkpoints: await Promise.all((snapshot.checkpoints || []).map(checkpoint)) } : null })
    }
    const metadataByKey = new Map(metadata.map(row => [row.key, row.value]))
    for (const client of clients) {
      const next = metadataByKey.get(`next-sequence:${client}`)
      const ack = metadataByKey.get(`acknowledged:${client}`) ?? []
      if (integer(next) === null || next < 1 || !Array.isArray(ack) || ack.some(sequence => integer(sequence) === null || sequence < 1)) return unknown('invalid_boundary')
      const highest = next - 1
      if (ack.some(sequence => sequence > highest)) return unknown('invalid_boundary')
      const acknowledged = [...new Set(ack)].sort((a, b) => a - b)
      let contiguous = 0
      const known = new Set(acknowledged)
      while (known.has(contiguous + 1)) contiguous++
      const pendingRows = output.events.filter(row => row.client_instance_id === client && row.delivery_status === 'pending')
      const failedRows = output.events.filter(row => row.client_instance_id === client && row.delivery_status === 'failed')
      for (const row of [...pendingRows, ...failedRows]) known.add(row.client_sequence_no)
      const missing = []
      let cursor = 1
      for (const sequence of [...known].filter(sequence => integer(sequence) !== null && sequence > 0 && sequence <= highest).sort((a, b) => a - b)) {
        if (sequence > cursor) missing.push([cursor, sequence - 1])
        cursor = sequence + 1
      }
      if (cursor <= highest) missing.push([cursor, highest])
      output.streams.push({ client_instance_id: client, highest_emitted_sequence_no: highest,
        highest_acked_sequence_no: acknowledged.at(-1) || 0, highest_contiguous_acked_sequence_no: contiguous,
        acknowledged_sequence_nos: acknowledged, pending_event_count: pendingRows.length, failed_event_count: failedRows.length,
        missing_local_sequence_ranges: missing })
    }
    const allScoped = new Set([...records, ...failed, ...checkpoints].map(row => row.client_instance_id))
    const unscoped = metadata.filter(row => row.key.startsWith('next-sequence:') && !clients.has(row.key.slice(14)) && !allScoped.has(row.key.slice(14))).length
    const incompleteIntent = output.intents.some(intent => !intent.material)
    output.scope_complete = unscoped === 0 && !incompleteIntent
    output.boundary = { event_count: output.events.length, checkpoint_count: output.checkpoints.length, intent_count: output.intents.length,
      pending_event_count: selectedRecords.length, failed_event_count: selectedFailed.length, unscoped_client_count: unscoped }
    if (!output.scope_complete) { output.status = 'unknown'; output.reason = incompleteIntent ? 'intent_material_unavailable' : 'unscoped_client' }
    return output
  }
  try {
    return await Promise.race([operation().catch(() => unknown('read_failed')), new Promise(resolve => {
      timer = globalThis.setTimeout(() => {
        expired = true
        try { transaction?.abort() } catch { /* A completed readonly transaction cannot be aborted. */ }
        resolve(unknown('read_timeout'))
      }, timeoutMs)
    })])
  } finally { expired = true; globalThis.clearTimeout(timer); database?.close() }
}

export async function captureBrowserStorage(page, evidence, timeoutMs = 2000) {
  let timer
  try {
    return await Promise.race([page.evaluate(readOnlyBrowserStorage, { ...browserStorageContract(), sessionIds: evidence.session_ids,
      clientInstanceIds: [...new Set(evidence.requests.map(row => row.client_instance_id).filter(Boolean))], timeoutMs }),
    new Promise(resolve => { timer = globalThis.setTimeout(() => resolve({ schema_version: 1, read_complete: false, scope_complete: false,
      status: 'unknown', reason: 'page_read_timeout', database: { name: 'kt-process-events-v1', version: 2 } }), timeoutMs + 100) })])
  } catch { return { schema_version: 1, read_complete: false, scope_complete: false, status: 'unknown', reason: 'page_unavailable',
    database: { name: 'kt-process-events-v1', version: 2 } } }
  finally { globalThis.clearTimeout(timer) }
}
