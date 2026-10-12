import { ApiError, createApiClient } from '../api/client'
import type { CaptureStore, CaptureEvent, CaptureCheckpoint, CaptureResponse, RecordOptions, FlushOptions, ProcessSnapshot, ProcessConfirmation, DurableIntent, SenderOptions, SyncUrls, OnlineOptions, Timer } from './capture-contract'
import { MAX_BATCH_SIZE, DEFAULT_SYNC_INTERVAL_MS, PERMANENT_CAPTURE_V2_REJECTIONS, V2_EVENT_TYPES, clone, sanitizePayload, backoffDelay, normalizeMonotonic } from './process-types'
interface CaptureTransport { (url: string, init: { method: string; headers: Record<string, string>; credentials: string; body: string; keepalive?: boolean }): Promise<{ ok: boolean; status: number; json(): Promise<CaptureResponse> }> }
interface BoundedFlush { recordQueue: Promise<unknown>; timeoutMs: number | null; ignoreVisibility: boolean; keepalive: boolean; promise: Promise<boolean> | null }
type CheckpointQueue = { flights: Map<string, Promise<CaptureResponse | null>>; confirmed: Map<string, CaptureResponse>; queue: Promise<void> }
const checkpointQueues = new WeakMap<CaptureStore, CheckpointQueue>()
export class ProcessEventCollector {
  sessionId: number; attemptId: number; endpoint: string; checkpointEndpoint: string; csrfToken: string; appVersion: string; correlationId: string | null
  outbox: CaptureStore; fetch: CaptureTransport
  wallNow: () => Date; monotonicNow: () => number; timeOrigin: string; isOnline: () => boolean; isVisible: () => boolean
  setTimeout: typeof globalThis.setTimeout; clearTimeout: typeof globalThis.clearTimeout; setInterval: typeof globalThis.setInterval; clearInterval: typeof globalThis.clearInterval
  autoSchedule: boolean; uploadDelayMs: number; clientInstanceId: string; timeSyncId: string | null; failureCount: number; lastCheckpointedContiguousAck: number
  retryTimer: Timer | null; syncTimer: Timer | null; flushPromise: Promise<boolean> | null; boundedFlush: BoundedFlush | null; recordQueue: Promise<unknown>; operationId: string | undefined
  checkpointUploads: CheckpointQueue

  constructor(options: SenderOptions) {
    if (!options || !options.outbox) throw new Error('An outbox is required.');
    this.sessionId = options.sessionId;
    this.attemptId = options.attemptId;
    this.endpoint = options.endpoint;
    this.checkpointEndpoint = options.checkpointEndpoint || '';
    this.csrfToken = options.csrfToken || '';
    this.appVersion = options.appVersion || '';
    this.correlationId = options.correlationId || null;
    this.outbox = options.outbox;
    this.checkpointUploads = checkpointQueues.get(options.outbox) || { flights: new Map(), confirmed: new Map(), queue: Promise.resolve() }
    checkpointQueues.set(options.outbox, this.checkpointUploads)
    const client = createApiClient({ fetchImpl: options.fetch, getCsrfToken: () => this.csrfToken, getOperationId: () => this.operationId })
    this.fetch = async (url, init) => {
      try {
        const data = await client.mutate<CaptureResponse>(url, { method: 'POST', body: init.body ? JSON.parse(init.body) : undefined, keepalive: init.keepalive })
        return { ok: true, status: 200, json: async () => data }
      } catch (error) {
        if (!(error instanceof ApiError)) throw error
        return { ok: false, status: error.status, json: async () => ({ error: { code: error.code } }) }
      }
    }
    this.wallNow = options.wallNow || (() => new Date());
    this.monotonicNow = options.monotonicNow || (() => globalThis.performance.now());
    this.timeOrigin = options.timeOrigin || new Date(
      Date.now() - (globalThis.performance?.now?.() || 0),
    ).toISOString();
    this.isOnline = options.isOnline || (() => globalThis.navigator?.onLine !== false);
    this.isVisible = options.isVisible || (() => globalThis.document?.visibilityState !== 'hidden');
    this.setTimeout = options.setTimeout || globalThis.setTimeout?.bind(globalThis);
    this.clearTimeout = options.clearTimeout || globalThis.clearTimeout?.bind(globalThis);
    this.setInterval = options.setInterval || globalThis.setInterval?.bind(globalThis);
    this.clearInterval = options.clearInterval || globalThis.clearInterval?.bind(globalThis);
    this.autoSchedule = options.autoSchedule !== false;
    this.uploadDelayMs = options.uploadDelayMs ?? 500;
    this.clientInstanceId = (options.uuid || (() => globalThis.crypto.randomUUID()))();
    this.timeSyncId = null;
    this.failureCount = 0;
    this.lastCheckpointedContiguousAck = 0;
    this.retryTimer = null;
    this.syncTimer = null;
    this.flushPromise = null;
    this.boundedFlush = null;
    this.recordQueue = Promise.resolve();
    this.operationId = undefined;
  }

  setOperationId(operationId?: string) {
    this.operationId = operationId;
  }

  _requestHeaders() {
    const headers: Record<string, string> = { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken };
    if (this.operationId) headers['X-KT-Operation-ID'] = this.operationId;
    if (globalThis.crypto?.randomUUID) headers['X-Request-ID'] = globalThis.crypto.randomUUID();
    return headers;
  }

  _qualityFlags() {
    const flags = [];
    if (!this.timeSyncId) flags.push('clock_unsynchronized');
    if (!this.isOnline()) flags.push('offline_buffered');
    if (this.outbox.isFallback) flags.push('persistent_outbox_unavailable');
    return flags;
  }

  async record(eventType: string, payload: Record<string, unknown>, options: RecordOptions = {}) {
    const attemptId = this.attemptId;
    const correlation = this.correlationId;
    const syncId = this.timeSyncId;
    const wallTime = options.wallTime || this.wallNow().toISOString();
    const monotonicMs = normalizeMonotonic(options.monotonicMs ?? this.monotonicNow());
    const qualityFlags = [...new Set([
      ...this._qualityFlags(),
      ...(options.qualityFlags || []),
    ])];
    const materialized = options.materialize ? options.materialize() : Promise.resolve(payload);
    void materialized.catch(() => {});
    const operation = this.recordQueue.then(async () => {
    const sanitized = sanitizePayload(eventType, await materialized);
    const event = await this.outbox.enqueue(
      this.clientInstanceId,
      (sequence) => {
        const document: CaptureEvent = {
          event_type: eventType,
          event_version: options.eventVersion || (V2_EVENT_TYPES.has(eventType) ? 2 : 1),
          session_id: this.sessionId,
          attempt_id: attemptId,
          client_sequence_no: sequence,
          client_wall_time_utc: wallTime,
          client_monotonic_ms: monotonicMs,
          client_time_origin: this.timeOrigin,
          payload: sanitized,
          quality_flags: qualityFlags,
          app_version: this.appVersion,
        };
        const correlationId = options.correlationId || correlation;
        if (correlationId) document.correlation_id = correlationId;
        if (options.eventId) document.event_id = options.eventId;
        if (syncId) document.time_sync_id = syncId;
        return document;
      },
    );
    if (this.autoSchedule) this.scheduleUpload(this.uploadDelayMs);
    return event;
    });
    this.recordQueue = operation;
    void operation.catch(() => {});
    return operation;
  }

  scheduleUpload(delay = 0) {
    if (!this.setTimeout || this.retryTimer !== null) return;
    this.retryTimer = this.setTimeout(() => {
      this.retryTimer = null;
      this.flush();
    }, delay);
  }

  async _flushLoop(options: FlushOptions = {}) {
    if (!this.fetch || !this.isOnline()) return false;
    if (!options.ignoreVisibility && !this.isVisible()) return false;
    let completed = true;
    while (this.isOnline() && (options.ignoreVisibility || this.isVisible())) {
      const batch = await this.outbox.peekBatch(MAX_BATCH_SIZE, options.boundary);
      if (!batch) break;
      while (batch.events.length > 1 && new TextEncoder().encode(JSON.stringify(batch.events)).byteLength > 48 * 1024) batch.events.pop();
      const body = JSON.stringify({
        client_instance_id: batch.clientInstanceId,
        events: batch.events,
      });
      let response;
      let responseDocument;
      try {
        response = await this.fetch(this.endpoint, {
          method: 'POST',
          headers: this._requestHeaders(),
          body,
          credentials: 'same-origin',
          keepalive: Boolean(options.keepalive) && new TextEncoder().encode(body).byteLength < 60 * 1024,
        });
        responseDocument = await response.json();
      } catch {
        completed = false;
        break;
      }
      if (!response.ok) {
        const rejectionCode = responseDocument?.error?.code;
        const permanentResponse = response.status >= 400
          && response.status < 500
          && response.status !== 408
          && response.status !== 429
          && (
            !this.endpoint.includes('/capture-v2/')
            || PERMANENT_CAPTURE_V2_REJECTIONS.has(rejectionCode || '')
          );
        if (permanentResponse) {
          for (const event of batch.events) {
            await this.outbox.moveToDeadLetter?.(
              batch.clientInstanceId,
              event.client_sequence_no,
              {
                client_sequence_no: event.client_sequence_no,
                code: rejectionCode || 'permanent_rejection',
                permanent: true,
                http_status: response.status,
              },
            );
          }
          continue;
        }
        completed = false;
        break;
      }
      if (!responseDocument?.success) {
        completed = false;
        break;
      }
      const available = new Set(batch.events.map((event) => event.client_sequence_no));
      const acknowledged = (responseDocument.acknowledgements || [])
        .map((item) => item.client_sequence_no)
        .filter((sequence) => available.has(sequence));
      const permanent = (responseDocument.rejections || []).filter((item) => item.permanent);
      if (!acknowledged.length && !permanent.length) {
        completed = false;
        break;
      }
      for (const rejection of permanent) {
        await this.outbox.moveToDeadLetter?.(
          batch.clientInstanceId,
          rejection.client_sequence_no,
          rejection,
        );
      }
      if (acknowledged.length) await this.outbox.deleteAcknowledged(batch.clientInstanceId, acknowledged);
    }
    if (completed) {
      this.failureCount = 0;
      const state = await this.outbox.captureState?.(this.clientInstanceId);
      if (
        state
        && state.highestContiguousAcked > this.lastCheckpointedContiguousAck
      ) {
        if (this.checkpointEndpoint) await this._createCheckpoint('ack_progress');
      }
      if (this.checkpointEndpoint) {
        const checkpoints = await this.outbox.pendingCheckpoints(false);
        const confirmations = await Promise.all(checkpoints.map((checkpoint) => this._uploadCheckpoint(checkpoint, options)));
        if (confirmations.some((confirmation) => !confirmation)) completed = false;
      }
    }
    if (!completed) {
      const delay = backoffDelay(this.failureCount);
      this.failureCount += 1;
      if (this.autoSchedule) this.scheduleUpload(delay);
    }
    return completed;
  }

  _freezeBoundary() {
    const boundary = this.recordQueue.then(() => this.outbox.freezeBoundary(this.clientInstanceId));
    this.recordQueue = boundary.then(() => undefined);
    void this.recordQueue.catch(() => {});
    return boundary;
  }

  flush(options: FlushOptions = {}) {
    if (this.flushPromise) return this.flushPromise;
    const boundary = options.boundary ? Promise.resolve(options.boundary) : this._freezeBoundary();
    this.flushPromise = boundary.then((frozen) => this._flushLoop({ ...options, boundary: frozen })).finally(() => {
      this.flushPromise = null;
    });
    return this.flushPromise;
  }

  flushConfirmed(options: FlushOptions = {}) {
    return this.flushBounded(null, options);
  }

  async flushBounded(timeoutMs: number | null = 1500, options: FlushOptions = {}) {
    const existing = this.boundedFlush;
    if (existing
      && existing.recordQueue === this.recordQueue
      && existing.timeoutMs === timeoutMs
      && existing.ignoreVisibility === Boolean(options.ignoreVisibility)
      && existing.keepalive === Boolean(options.keepalive)) return existing.promise;
    const flight: BoundedFlush = {
      recordQueue: this.recordQueue,
      timeoutMs,
      ignoreVisibility: Boolean(options.ignoreVisibility),
      keepalive: Boolean(options.keepalive),
      promise: null,
    };
    const boundaryReady = this._freezeBoundary();
    // Calls for the same record fence share the same bounded wait.
    flight.recordQueue = this.recordQueue;
    const flushLatest = async () => {
      const boundary = await boundaryReady;
      while (true) {
        if (this.flushPromise) await this.flushPromise;
        let confirmed = true;
        for (const [clientInstanceId, sequence] of Object.entries(boundary)) {
          const state = await this.outbox.captureState(clientInstanceId);
          if (state.highestContiguousAcked < sequence) confirmed = false;
        }
        if (confirmed && !this.checkpointEndpoint) return true;
        // Another caller may start an older boundary while captureState awaits.
        const joinedEarlierFlush = Boolean(this.flushPromise);
        const uploaded = await this.flush({ ...options, boundary });
        if (!uploaded) return false;
        confirmed = true;
        for (const [clientInstanceId, sequence] of Object.entries(boundary)) {
          const state = await this.outbox.captureState(clientInstanceId);
          if (state.highestContiguousAcked < sequence) confirmed = false;
        }
        if (confirmed) return true;
        if (!joinedEarlierFlush) return false;
      }
    };
    const drain = flushLatest().finally(() => {
      if (this.boundedFlush === flight) this.boundedFlush = null;
    });
    let timeoutId: Timer | undefined;
    flight.promise = !this.setTimeout || timeoutMs === null ? drain : Promise.race([
      drain,
      new Promise<boolean>((resolve) => {
        timeoutId = this.setTimeout(() => resolve(false), timeoutMs);
      }),
    ]).finally(() => {
      if (timeoutId && this.clearTimeout) this.clearTimeout(timeoutId);
    });
    this.boundedFlush = flight;
    return flight.promise;
  }

  async _createCheckpoint(trigger: string) {
    const state = await this.outbox.captureState(this.clientInstanceId);
    const document: CaptureCheckpoint = {
      checkpoint_id: (globalThis.crypto?.randomUUID?.() || `00000000-0000-4000-8000-${String(Date.now()).slice(-12).padStart(12, '0')}`),
      session_id: this.sessionId,
      client_instance_id: this.clientInstanceId,
      highest_emitted_sequence_no: state.highestEmitted,
      highest_acked_sequence_no: state.highestAcked,
      highest_contiguous_acked_sequence_no: state.highestContiguousAcked,
      pending_event_count: state.pending,
      failed_event_count: state.failed,
      missing_local_sequence_ranges: state.missingLocalRanges,
      client_monotonic_ms: normalizeMonotonic(this.monotonicNow()),
      client_wall_time_utc: this.wallNow().toISOString(),
      client_time_origin: this.timeOrigin,
      time_sync_id: this.timeSyncId,
      trigger,
      quality_flags: this._qualityFlags(),
      app_version: this.appVersion,
    };
    await this.outbox.enqueueCheckpoint(document);
    return document;
  }

  async checkpoint(trigger: string, options: FlushOptions = {}) {
    if (!this.checkpointEndpoint || !this.fetch || !this.outbox.captureState) return null;
    const document = await this._createCheckpoint(trigger);
    const pending = await this.outbox.pendingCheckpoints(false);
    const uploads = pending.map((checkpoint) => this._uploadCheckpoint(checkpoint, options));
    const ownIndex = pending.findIndex((checkpoint) => checkpoint.checkpoint_id === document.checkpoint_id);
    return ownIndex < 0 ? null : uploads[ownIndex];
  }

  _uploadCheckpoint(checkpoint: CaptureCheckpoint, options: FlushOptions = {}) {
    const confirmed = this.checkpointUploads.confirmed.get(checkpoint.checkpoint_id);
    if (confirmed) return Promise.resolve(confirmed);
    const existing = this.checkpointUploads.flights.get(checkpoint.checkpoint_id);
    if (existing) return existing;
    const upload = this.checkpointUploads.queue.then(async () => {
      try {
        const available = await this.outbox.pendingCheckpoints(false);
        if (!available.some((row) => row.checkpoint_id === checkpoint.checkpoint_id)) {
          return this.checkpointUploads.confirmed.get(checkpoint.checkpoint_id) || null;
        }
        const response = await this.fetch(this.checkpointEndpoint, {
          method: 'POST',
          headers: this._requestHeaders(),
          body: JSON.stringify(checkpoint),
          credentials: 'same-origin',
          keepalive: Boolean(options.keepalive) || checkpoint.trigger === 'pagehide' || checkpoint.trigger === 'session_end',
        });
        if (!response.ok) return null;
        const result = await response.json();
        if (!result?.success || (result.checkpoint_id && result.checkpoint_id !== checkpoint.checkpoint_id)) return null;
        await this.outbox.deleteCheckpoint(checkpoint.checkpoint_id);
        this.checkpointUploads.confirmed.set(checkpoint.checkpoint_id, result);
        if (checkpoint.trigger === 'ack_progress' && checkpoint.client_instance_id === this.clientInstanceId) {
          this.lastCheckpointedContiguousAck = Math.max(this.lastCheckpointedContiguousAck, checkpoint.highest_contiguous_acked_sequence_no);
        }
        return result;
      } catch {
        return null;
      }
    }).finally(() => { this.checkpointUploads.flights.delete(checkpoint.checkpoint_id); });
    this.checkpointUploads.flights.set(checkpoint.checkpoint_id, upload);
    this.checkpointUploads.queue = upload.then(() => undefined);
    return upload;
  }

  freezeSubmissionSnapshot<T extends DurableIntent>(intent: T): Promise<T & { processSnapshot: ProcessSnapshot }> {
    const recordFence = this.recordQueue;
    const uploading = this.flushPromise;
    const checkpointFence = this.checkpointUploads.queue;
    const frozen = recordFence.then(async () => {
      const saved = await this.outbox.restoreSubmissionIntent(this.sessionId, this.attemptId);
      if (saved?.processSnapshot) {
        const savedIntent = { ...saved };
        delete savedIntent.processSnapshot;
        const requestedIntent = { ...intent };
        delete requestedIntent.processSnapshot;
        if (JSON.stringify(savedIntent) !== JSON.stringify(requestedIntent)) throw new Error('Submission snapshot is immutable.');
        return clone(saved) as T & { processSnapshot: ProcessSnapshot };
      }
      if (uploading) await uploading;
      await checkpointFence;
      const boundary = await this.outbox.freezeBoundary(this.clientInstanceId);
      if (Object.keys(boundary).some((id) => id !== this.clientInstanceId)) {
        throw Object.assign(new Error('Earlier client evidence must be confirmed before submission.'), { code: 'submission_snapshot_backlog' });
      }
      const sequence = boundary[this.clientInstanceId];
      const state = await this.outbox.captureState(this.clientInstanceId);
      if (state.failed || state.missingLocalRanges.length) {
        throw Object.assign(new Error('Submission evidence has a rejected event or local sequence gap.'), { code: 'submission_snapshot_gap' });
      }
      const events = await this.outbox.pendingEvents(this.clientInstanceId, sequence);
      if (events.length > MAX_BATCH_SIZE) {
        throw Object.assign(new Error('Submission snapshot exceeds the bounded event limit.'), { code: 'submission_snapshot_too_large' });
      }
      await this._createCheckpoint('commit');
      const checkpoints = (await this.outbox.pendingCheckpoints()).filter((checkpoint) => checkpoint.session_id === this.sessionId);
      if (checkpoints.some((checkpoint) => checkpoint.client_instance_id !== this.clientInstanceId)) {
        throw Object.assign(new Error('Earlier client checkpoints must be confirmed before submission.'), { code: 'submission_snapshot_backlog' });
      }
      const processSnapshot = { client_instance_id: this.clientInstanceId, event_boundary: sequence, events, checkpoints };
      if (checkpoints.length > MAX_BATCH_SIZE || new TextEncoder().encode(JSON.stringify(processSnapshot)).byteLength > 8 * 1024 * 1024 - 64 * 1024) {
        throw Object.assign(new Error('Submission snapshot exceeds the bounded envelope limit.'), { code: 'submission_snapshot_too_large' });
      }
      const result = { ...clone(intent), processSnapshot };
      await this.outbox.saveSubmissionIntent(this.sessionId, this.attemptId, result);
      return result;
    });
    // A bounded-envelope rejection must not prevent later recording or a drain/retry.
    this.recordQueue = frozen.then(() => undefined, () => recordFence);
    return frozen;
  }

  freezeNavigationSnapshot<T extends DurableIntent>(intent: T): Promise<T & { processSnapshot: ProcessSnapshot }> {
    const recordFence = this.recordQueue;
    const frozen = recordFence.then(async () => {
      const saved = await this.outbox.restoreNavigationIntent(this.sessionId, this.attemptId);
      if (saved?.processSnapshot) {
        const savedIntent = { ...saved };
        delete savedIntent.processSnapshot;
        const requestedIntent = { ...intent };
        delete requestedIntent.processSnapshot;
        if (JSON.stringify(savedIntent) !== JSON.stringify(requestedIntent)) throw new Error('Navigation snapshot is immutable.');
        return clone(saved) as T & { processSnapshot: ProcessSnapshot };
      }
      const boundary = await this.outbox.freezeBoundary(this.clientInstanceId);
      const sequence = boundary[this.clientInstanceId];
      const state = await this.outbox.captureState(this.clientInstanceId);
      if (state.failed || state.missingLocalRanges.length) {
        throw Object.assign(new Error('Navigation evidence has a rejected event or local sequence gap.'), { code: 'navigation_snapshot_gap' });
      }
      const events = await this.outbox.pendingEvents(this.clientInstanceId, sequence);
      if (events.length > MAX_BATCH_SIZE) {
        throw Object.assign(new Error('Navigation snapshot exceeds the bounded event limit.'), { code: 'navigation_snapshot_too_large' });
      }
      await this._createCheckpoint('navigation');
      const checkpoints = (await this.outbox.pendingCheckpoints()).filter(checkpoint =>
        checkpoint.session_id === this.sessionId && checkpoint.client_instance_id === this.clientInstanceId
        && checkpoint.highest_emitted_sequence_no <= sequence);
      const processSnapshot = { client_instance_id: this.clientInstanceId, event_boundary: sequence, events, checkpoints };
      if (checkpoints.length > MAX_BATCH_SIZE || new TextEncoder().encode(JSON.stringify(processSnapshot)).byteLength > 8 * 1024 * 1024 - 64 * 1024) {
        throw Object.assign(new Error('Navigation snapshot exceeds the bounded envelope limit.'), { code: 'navigation_snapshot_too_large' });
      }
      const result = { ...clone(intent), processSnapshot };
      await this.outbox.saveNavigationIntent(this.sessionId, this.attemptId, result);
      return result;
    });
    this.recordQueue = frozen.then(() => undefined, () => recordFence);
    return frozen;
  }

  async acknowledgeSubmission(snapshot: ProcessSnapshot, response: ProcessConfirmation) {
    const saved = await this.outbox.restoreSubmissionIntent(this.sessionId, this.attemptId);
    if (!saved?.processSnapshot || JSON.stringify(saved.processSnapshot) !== JSON.stringify(snapshot)) {
      throw new Error('Submission confirmation does not match the durable submission snapshot.');
    }
    return this._acknowledgeProcessSnapshot(snapshot, response);
  }

  async acknowledgeNavigation(snapshot: ProcessSnapshot, response: ProcessConfirmation) {
    const saved = await this.outbox.restoreNavigationIntent(this.sessionId, this.attemptId);
    if (!saved?.processSnapshot || JSON.stringify(saved.processSnapshot) !== JSON.stringify(snapshot)
      || response?.navigation_token !== saved.token || response?.to_assignment_id !== saved.toAssignmentId
      || response?.writer_lease_released !== true
      || (saved.head != null && response?.active_response_event_id !== saved.head)) {
      throw new Error('Navigation confirmation does not match the durable navigation snapshot.');
    }
    return this._acknowledgeProcessSnapshot(snapshot, response);
  }

  async _acknowledgeProcessSnapshot(snapshot: ProcessSnapshot, response: ProcessConfirmation) {
    const acknowledgements = response?.acknowledgements;
    const checkpointIds = response?.checkpoint_ids;
    const eventSequences = new Set(snapshot.events.map((event) => event.client_sequence_no));
    const validEvents = Array.isArray(acknowledgements)
      && acknowledgements.length === snapshot.events.length
      && new Set(acknowledgements.map((ack) => ack.client_sequence_no)).size === eventSequences.size
      && snapshot.events.every((event) => acknowledgements.some((ack) => ack.client_sequence_no === event.client_sequence_no
        && (!event.event_id || ack.event_id === event.event_id)));
    const validCheckpoints = Array.isArray(checkpointIds)
      && checkpointIds.length === snapshot.checkpoints.length
      && new Set(checkpointIds).size === snapshot.checkpoints.length
      && snapshot.checkpoints.every((checkpoint) => checkpointIds.includes(checkpoint.checkpoint_id));
    if (response?.committed !== true || !validEvents || !validCheckpoints) {
      throw new Error('Submission confirmation is incomplete or does not match the frozen evidence.');
    }
    await this.outbox.deleteAcknowledged(snapshot.client_instance_id, [...eventSequences]);
    for (const checkpoint of snapshot.checkpoints) {
      await this.outbox.deleteCheckpoint(checkpoint.checkpoint_id);
      const result = { success: true, checkpoint_id: checkpoint.checkpoint_id };
      this.checkpointUploads.confirmed.set(checkpoint.checkpoint_id, result);
      if (checkpoint.client_instance_id === this.clientInstanceId) {
        this.lastCheckpointedContiguousAck = Math.max(this.lastCheckpointedContiguousAck, checkpoint.highest_contiguous_acked_sequence_no);
      }
    }
  }

  async synchronizeClock({ createUrl, finalizeUrl }: SyncUrls = {}) {
    if (!this.fetch || !this.isOnline() || !createUrl || !finalizeUrl) return null;
    const sentWall = this.wallNow().toISOString();
    const sentMonotonic = normalizeMonotonic(this.monotonicNow());
    try {
      const createdResponse = await this.fetch(createUrl, {
        method: 'POST',
        headers: this._requestHeaders(),
        credentials: 'same-origin',
        body: JSON.stringify({
          session_id: this.sessionId,
          client_instance_id: this.clientInstanceId,
          client_wall_time_utc: sentWall,
          client_monotonic_ms: sentMonotonic,
          client_time_origin: this.timeOrigin,
        }),
      });
      const created = await createdResponse.json();
      if (!createdResponse.ok || !created.success) return null;
      const receivedWall = this.wallNow().toISOString();
      const receivedMonotonic = normalizeMonotonic(this.monotonicNow());
      const finalizedResponse = await this.fetch(finalizeUrl, {
        method: 'POST',
        headers: this._requestHeaders(),
        credentials: 'same-origin',
        body: JSON.stringify({
          time_sync_id: created.time_sync_id,
          client_instance_id: this.clientInstanceId,
          client_received_wall_time_utc: receivedWall,
          client_received_monotonic_ms: receivedMonotonic,
        }),
      });
      const finalized = await finalizedResponse.json();
      if (!finalizedResponse.ok || !finalized.success || finalized.sync_quality !== 'valid') {
        this.timeSyncId = null;
        return null;
      }
      this.timeSyncId = finalized.time_sync_id || null;
      return finalized;
    } catch {
      this.timeSyncId = null;
      return null;
    }
  }

  startSynchronization(
    urls: SyncUrls,
    intervalMs = DEFAULT_SYNC_INTERVAL_MS,
    runImmediately = true,
  ) {
    const run = () => this.synchronizeClock(urls);
    if (runImmediately) run();
    if (this.setInterval) this.syncTimer = this.setInterval(run, intervalMs);
  }

  async handleOnline(options: OnlineOptions = {}) {
    if (options.synchronize !== false && options.syncUrls) {
      await this.synchronizeClock(options.syncUrls);
    }
    return this.flush();
  }

  dispose() {
    if (this.retryTimer !== null && this.clearTimeout) this.clearTimeout(this.retryTimer);
    if (this.syncTimer !== null && this.clearInterval) this.clearInterval(this.syncTimer);
    this.retryTimer = null;
    this.syncTimer = null;
  }
}
