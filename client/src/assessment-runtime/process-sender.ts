import { ApiError, createApiClient } from '../api/client'
import type { CaptureStore, CaptureEvent, CaptureCheckpoint, CaptureResponse, CaptureBundle, CaptureBundleCapability, CaptureMutation, RecordOptions, FlushOptions, ProcessSnapshot, ProcessConfirmation, DurableIntent, SenderOptions, SyncUrls, OnlineOptions, Timer } from './capture-contract'
import { measureConfirmedMaterial, timedAssessmentStage } from './operation-timing'
import { withTransportTimeout } from './api'
import { MAX_BATCH_SIZE, DEFAULT_SYNC_INTERVAL_MS, PERMANENT_CAPTURE_V2_REJECTIONS, V2_EVENT_TYPES, clone, sanitizePayload, backoffDelay, normalizeMonotonic } from './process-types'
interface CaptureTransport { (url: string, init: { method: string; headers: Record<string, string>; credentials: string; body: string; keepalive?: boolean; timeoutMs?: number }): Promise<{ ok: boolean; status: number; json(): Promise<CaptureResponse> }> }
interface BoundedFlush { recordQueue: Promise<unknown>; timeoutMs: number | null; ignoreVisibility: boolean; keepalive: boolean; boundary?: Record<string, number>; promise: Promise<boolean> | null }
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
  captureBundle?: CaptureBundleCapability
  private progressWaiters = new Set<() => void>()

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
    this.captureBundle = options.captureBundle;
    this.checkpointUploads = checkpointQueues.get(options.outbox) || { flights: new Map(), confirmed: new Map(), queue: Promise.resolve() }
    checkpointQueues.set(options.outbox, this.checkpointUploads)
    this.fetch = async (url, init) => {
      try {
        const client = createApiClient({ fetchImpl: options.fetch, getCsrfToken: () => this.csrfToken, getOperationId: () => init.headers['X-KT-Operation-ID'] })
        const data = await withTransportTimeout(signal => client.mutate<CaptureResponse>(url, {
          method: 'POST', body: init.body ? JSON.parse(init.body) : undefined, keepalive: init.keepalive, signal,
        }), init.timeoutMs ?? 30000)
        return { ok: true, status: 200, json: async () => data }
      } catch (error) {
        if (!(error instanceof ApiError)) throw error
        return { ok: false, status: error.status, json: async () => ({ error: { code: error.code } }) }
      }
    }
    this.wallNow = options.wallNow || (() => new Date());
    this.monotonicNow = options.monotonicNow || (() => globalThis.performance.now());
    const browserTimeOrigin = globalThis.performance?.timeOrigin;
    this.timeOrigin = options.timeOrigin || new Date(Number.isFinite(browserTimeOrigin)
      ? browserTimeOrigin : Date.now() - (globalThis.performance?.now?.() || 0)).toISOString();
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

  closeMetadataKey() {
    return `close-local:${this.sessionId}:${this.attemptId}:${this.correlationId || 'unscoped'}`;
  }

  _requestHeaders(operationId: string | null = this.operationId ?? null) {
    const headers: Record<string, string> = { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken };
    if (operationId) headers['X-KT-Operation-ID'] = operationId;
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
    const operationId = this.operationId;
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
    const operation = timedAssessmentStage('record_queue', () => this.recordQueue.then(async () => {
    const sanitized = sanitizePayload(eventType, await materialized);
    const event = await timedAssessmentStage('local_store', () => this.outbox.enqueue(
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
    ), operationId ?? null);
    if (this.autoSchedule) this.scheduleUpload(this.uploadDelayMs);
    return event;
    }), operationId ?? null);
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

  async _flushBundleLoop(options: FlushOptions, resumeOnly = false) {
    if (!this.fetch || !this.isOnline() || (!options.ignoreVisibility && !this.isVisible())) return false;
    const capability = this.captureBundle!;
    const key = `capture-flush:${this.sessionId}`;
    const roundCheckpoints = new Set((await this.outbox.pendingCheckpoints(false)).map(row => row.checkpoint_id));
    let completed = true;
    while (this.isOnline() && (options.ignoreVisibility || this.isVisible())) {
      let intent = await this.outbox.readMetadata<DurableIntent & { processSnapshot: CaptureBundle }>(key);
      if (!intent) {
        if (resumeOnly) break;
        const batch = await this.outbox.peekBatch(Math.min(MAX_BATCH_SIZE, capability.max_events), options.boundary);
        const pending = (await this.outbox.pendingCheckpoints(false)).filter(row => roundCheckpoints.has(row.checkpoint_id) && row.session_id === this.sessionId
          && row.highest_emitted_sequence_no <= (options.boundary?.[row.client_instance_id] ?? Number.MAX_SAFE_INTEGER));
        const clientId = batch?.clientInstanceId || pending[0]?.client_instance_id;
        if (!clientId) break;
        const events = batch?.events || [];
        const checkpoints = pending.filter(row => row.client_instance_id === clientId).slice(0, capability.max_checkpoints);
        const snapshot: CaptureBundle = { version: 1, client_instance_id: clientId,
          event_boundary: options.boundary?.[clientId] ?? Math.max(0, ...events.map(event => event.client_sequence_no), ...checkpoints.map(row => row.highest_emitted_sequence_no)),
          events, checkpoints };
        while (new TextEncoder().encode(JSON.stringify(snapshot)).byteLength > capability.max_bytes - 64 * 1024) {
          if (snapshot.checkpoints.length) snapshot.checkpoints.pop();
          else if (snapshot.events.length > 1) snapshot.events.pop();
          else return false;
        }
        intent = { processSnapshot: snapshot, operationId: options.operationId };
        await timedAssessmentStage('local_store', () => this.outbox.writeMetadata(key, intent), options.operationId ?? null);
      }
      const snapshot = intent.processSnapshot;
      try {
        const body = JSON.stringify({ capture_bundle: snapshot });
        const response = await this.fetch(this.endpoint, { method: 'POST', headers: this._requestHeaders(intent.operationId as string || null),
          body, credentials: 'same-origin', keepalive: Boolean(options.keepalive) && new TextEncoder().encode(body).byteLength < 60 * 1024,
          timeoutMs: options.timeoutMs });
        const result = await response.json();
        if (!response.ok || !result.success) { completed = false; break; }
        await timedAssessmentStage('ack', () => this._acknowledgeProcessSnapshot(snapshot, result, true, intent.operationId as string || null), intent.operationId as string || null);
        await this.outbox.writeMetadata(key, null);
        for (const notify of this.progressWaiters) notify();
        this.progressWaiters.clear();
        const state = await this.outbox.captureState(this.clientInstanceId);
        if (!resumeOnly && state.highestContiguousAcked > this.lastCheckpointedContiguousAck) {
          const pendingProgress = (await this.outbox.pendingCheckpoints()).some(row => row.trigger === 'ack_progress'
            && row.client_instance_id === this.clientInstanceId && row.highest_contiguous_acked_sequence_no >= state.highestContiguousAcked);
          if (!pendingProgress) {
            await this._createCheckpoint('ack_progress', options.operationId ?? null);
            if (this.autoSchedule) this.scheduleUpload(Math.min(1000, this.uploadDelayMs));
          }
        }
      } catch { completed = false; break; }
    }
    if (completed) this.failureCount = 0;
    else if (this.autoSchedule) this.scheduleUpload(backoffDelay(this.failureCount++));
    return completed;
  }

  async _flushLoop(options: FlushOptions = {}) {
    if (this.captureBundle?.enabled && this.captureBundle.version === 1) return this._flushBundleLoop(options);
    const ownedPacket = await this.outbox.readMetadata(`capture-flush:${this.sessionId}`);
    if (ownedPacket) {
      if (this.captureBundle?.version !== 1 || this.captureBundle.recovery_supported !== true) return false;
      if (!await this._flushBundleLoop(options, true)) return false;
    }
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
          headers: this._requestHeaders(options.operationId ?? null),
          body,
          credentials: 'same-origin',
          keepalive: Boolean(options.keepalive) && new TextEncoder().encode(body).byteLength < 60 * 1024,
          timeoutMs: options.timeoutMs,
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
        if (this.checkpointEndpoint) await this._createCheckpoint('ack_progress', options.operationId ?? null);
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
    const boundary = options.boundary ? this.recordQueue.then(() => options.boundary!) : this._freezeBoundary();
    const operationId = options.operationId ?? this.operationId;
    this.flushPromise = boundary.then((frozen) => this._flushLoop({ ...options, boundary: frozen, operationId })).finally(() => {
      this.flushPromise = null;
      for (const notify of this.progressWaiters) notify();
      this.progressWaiters.clear();
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
      && JSON.stringify(existing.boundary) === JSON.stringify(options.boundary)
      && existing.ignoreVisibility === Boolean(options.ignoreVisibility)
      && existing.keepalive === Boolean(options.keepalive)) return existing.promise;
    const flight: BoundedFlush = {
      recordQueue: this.recordQueue,
      timeoutMs,
      ignoreVisibility: Boolean(options.ignoreVisibility),
      keepalive: Boolean(options.keepalive),
      promise: null,
      boundary: options.boundary,
    };
    const boundaryReady = options.boundary ? Promise.resolve(options.boundary) : this._freezeBoundary();
    // Calls for the same record fence share the same bounded wait.
    flight.recordQueue = this.recordQueue;
    const flushLatest = async () => {
      const boundary = await boundaryReady;
      if (this.captureBundle?.enabled && this.captureBundle.version === 1) {
        const requiredCheckpoints = (await this.outbox.pendingCheckpoints()).filter(row => row.session_id === this.sessionId
          && row.highest_emitted_sequence_no <= (boundary[row.client_instance_id] ?? -1)).map(row => row.checkpoint_id);
        while (true) {
          let notify!: () => void;
          const changed = new Promise<void>(resolve => { notify = resolve; this.progressWaiters.add(notify); });
          try {
            const states = await Promise.all(Object.entries(boundary).map(async ([id, sequence]) => (await this.outbox.captureState(id)).highestContiguousAcked >= sequence));
            const pendingIds = new Set((await this.outbox.pendingCheckpoints()).map(row => row.checkpoint_id));
            if (states.every(Boolean) && requiredCheckpoints.every(id => !pendingIds.has(id))) return true;
            const earlier = this.flushPromise;
            const upload = earlier || this.flush({ ...options, boundary });
            const result = await Promise.race([upload, changed]);
            // flush() notifies waiters in finally before its result settles.
            if (!earlier && (result === false || (!this.flushPromise && await upload === false))) return false;
          } finally { this.progressWaiters.delete(notify); }
        }
      }
      while (true) {
        if (this.flushPromise) await this.flushPromise;
        let confirmed = true;
        for (const [clientInstanceId, sequence] of Object.entries(boundary)) {
          const state = await this.outbox.captureState(clientInstanceId);
          if (state.highestContiguousAcked < sequence) confirmed = false;
        }
        if (confirmed && !this.checkpointEndpoint && !await this.outbox.readMetadata(`capture-flush:${this.sessionId}`)) return true;
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

  async _createCheckpoint(trigger: string, operationId: string | null = this.operationId ?? null) {
    return timedAssessmentStage('checkpoint', async () => {
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
    await timedAssessmentStage('local_store', () => this.outbox.enqueueCheckpoint(document), operationId ?? null);
    return document;
    }, operationId ?? null);
  }

  async checkpoint(trigger: string, options: FlushOptions = {}) {
    if (!this.checkpointEndpoint || !this.fetch || !this.outbox.captureState) return null;
    if (this.captureBundle?.enabled && this.captureBundle.version === 1) {
      const operationId = options.operationId ?? this.operationId;
      const local = this.recordQueue.then(async () => ({ document: await this._createCheckpoint(trigger, operationId ?? null), boundary: options.boundary ?? await this.outbox.freezeBoundary(this.clientInstanceId) }));
      this.recordQueue = local.then(() => undefined);
      const { document, boundary } = await local;
      await this.flushConfirmed({ ...options, boundary, operationId });
      return this.checkpointUploads.confirmed.get(document.checkpoint_id) || null;
    }
    const document = await this._createCheckpoint(trigger);
    const pending = await this.outbox.pendingCheckpoints(false);
    const uploads = pending.map((checkpoint) => this._uploadCheckpoint(checkpoint, options));
    const ownIndex = pending.findIndex((checkpoint) => checkpoint.checkpoint_id === document.checkpoint_id);
    return ownIndex < 0 ? null : uploads[ownIndex];
  }

  recordCheckpoint(trigger: string) {
    if (!this.checkpointEndpoint) return Promise.resolve(null);
    const operationId = this.operationId;
    const pending = this.recordQueue.then(() => this._createCheckpoint(trigger, operationId ?? null));
    this.recordQueue = pending.then(() => undefined);
    void this.recordQueue.catch(() => {});
    return pending;
  }

  _uploadCheckpoint(checkpoint: CaptureCheckpoint, options: FlushOptions = {}) {
    const operationId = options.operationId ?? this.operationId;
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
          headers: this._requestHeaders(operationId ?? null),
          body: JSON.stringify(checkpoint),
          credentials: 'same-origin',
          keepalive: Boolean(options.keepalive) || checkpoint.trigger === 'pagehide' || checkpoint.trigger === 'session_end',
          timeoutMs: options.timeoutMs,
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
      if (events.some(event => event.attempt_id !== null && event.attempt_id !== this.attemptId)) {
        throw Object.assign(new Error('Earlier question evidence must be confirmed before navigation.'), { code: 'navigation_snapshot_backlog' });
      }
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

  freezeCaptureMutation(url: string, body: Record<string, unknown>, operationId: string | null = this.operationId ?? null): Promise<CaptureMutation> {
    const recordFence = this.recordQueue;
    const earlierUpload = this.flushPromise;
    const sessionId = this.sessionId;
    const attemptId = this.attemptId;
    const frozen = recordFence.then(async () => {
      if (earlierUpload) await earlierUpload;
      if (await this.outbox.readMetadata(`capture-flush:${sessionId}`)) {
        if (this.captureBundle?.version !== 1 || this.captureBundle.recovery_supported !== true
          || !await this._flushBundleLoop({ ignoreVisibility: true, operationId: operationId ?? undefined }, true)
          || await this.outbox.readMetadata(`capture-flush:${sessionId}`)) {
          throw new Error('先前轨迹上传尚未确认，请恢复原上传后再保存草稿。');
        }
      }
      const key = `capture-mutation:${sessionId}:${attemptId}`;
      const saved = await this.outbox.readMetadata<CaptureMutation>(key);
      if (saved) {
        if (saved.url !== url || JSON.stringify(saved.body) !== JSON.stringify(body)) {
          throw new Error('先前操作结果尚未确认，请先核验原操作。');
        }
        return saved;
      }
      const boundary = await this.outbox.freezeBoundary(this.clientInstanceId);
      const sequence = boundary[this.clientInstanceId]!;
      const state = await this.outbox.captureState(this.clientInstanceId);
      if (state.failed || state.missingLocalRanges.length) throw new Error('作答轨迹存在缺失或拒绝，请先核验原操作。');
      const events = (await this.outbox.pendingEvents(this.clientInstanceId, sequence)).filter(event => event.session_id === sessionId
        && (event.attempt_id === attemptId || event.attempt_id === null));
      const checkpoints = (await this.outbox.pendingCheckpoints(false)).filter(checkpoint => checkpoint.session_id === sessionId
        && checkpoint.client_instance_id === this.clientInstanceId && checkpoint.highest_emitted_sequence_no <= sequence);
      const processSnapshot: CaptureBundle = { version: 1, client_instance_id: this.clientInstanceId, event_boundary: sequence, events, checkpoints };
      const capability = this.captureBundle;
      if (events.length > (capability?.max_events ?? MAX_BATCH_SIZE) || checkpoints.length > (capability?.max_checkpoints ?? MAX_BATCH_SIZE)
        || new TextEncoder().encode(JSON.stringify({ ...body, capture_bundle: processSnapshot })).byteLength > (capability?.max_bytes ?? 8 * 1024 * 1024) - 64 * 1024) {
        throw Object.assign(new Error('待保存轨迹超出单次请求边界，请先同步后重试。'), { code: 'capture_bundle_too_large' });
      }
      const intent: CaptureMutation = { url, body: clone(body), sessionId, attemptId, operationId: operationId || undefined, processSnapshot };
      await timedAssessmentStage('local_store', () => this.outbox.writeMetadata(key, intent), operationId ?? null);
      return intent;
    });
    this.recordQueue = frozen.then(() => undefined, () => recordFence);
    return frozen;
  }

  async acknowledgeCaptureMutation(intent: CaptureMutation, response: ProcessConfirmation) {
    const key = `capture-mutation:${intent.sessionId}:${intent.attemptId}`;
    const saved = await this.outbox.readMetadata<CaptureMutation>(key);
    if (!saved || JSON.stringify(saved) !== JSON.stringify(intent)) throw new Error('Capture confirmation does not match the durable operation.');
    await timedAssessmentStage('ack', () => this._acknowledgeProcessSnapshot(intent.processSnapshot, response, true, intent.operationId ?? null), intent.operationId ?? null);
    await this.outbox.writeMetadata(key, null);
    const state = await this.outbox.captureState(this.clientInstanceId);
    if (state.highestContiguousAcked > this.lastCheckpointedContiguousAck) await this._createCheckpoint('ack_progress', intent.operationId ?? null);
    if (this.autoSchedule) this.scheduleUpload(this.uploadDelayMs);
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

  async _acknowledgeProcessSnapshot(snapshot: ProcessSnapshot, response: ProcessConfirmation, exactOrdinal = false, operationId = this.operationId ?? null) {
    const acknowledgements = response?.acknowledgements;
    const checkpointIds = response?.checkpoint_ids;
    const eventSequences = new Set(snapshot.events.map((event) => event.client_sequence_no));
    const validEvents = Array.isArray(acknowledgements)
      && acknowledgements.length === snapshot.events.length
      && new Set(acknowledgements.map((ack) => ack.client_sequence_no)).size === eventSequences.size
      && snapshot.events.every((event) => acknowledgements.some((ack) => ack.client_sequence_no === event.client_sequence_no
        && (!event.event_id || ack.event_id === event.event_id)
        && (!exactOrdinal || (typeof ack.event_id === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(ack.event_id)
          && Number.isSafeInteger(ack.server_ingest_sequence_no) && Number(ack.server_ingest_sequence_no) > 0))));
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
    measureConfirmedMaterial(snapshot, operationId);
  }

  async synchronizeClock({ createUrl, finalizeUrl }: SyncUrls = {}) {
    const operationId = this.operationId;
    if (!this.fetch || !this.isOnline() || !createUrl || !finalizeUrl) return null;
    const sentWall = this.wallNow().toISOString();
    const sentMonotonic = normalizeMonotonic(this.monotonicNow());
    try {
      const createdResponse = await this.fetch(createUrl, {
        method: 'POST',
        headers: this._requestHeaders(operationId ?? null),
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
        headers: this._requestHeaders(operationId ?? null),
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
