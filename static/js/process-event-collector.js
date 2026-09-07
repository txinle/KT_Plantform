(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.KTProcessData = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const MAX_BATCH_SIZE = 100;
  const MAX_BACKOFF_MS = 60000;
  const DEFAULT_SYNC_INTERVAL_MS = 15 * 60 * 1000;
  const DATABASE_NAME = 'kt-process-events-v1';
  const DATABASE_VERSION = 2;
  const V2_EVENT_TYPES = new Set([
    'item_rendered', 'item_left', 'response_state_changed', 'text_edit_observed',
  ]);

  const PAYLOAD_FIELDS = Object.freeze({
    feedback_exposure: ['capture_version', 'operation_id', 'feedback_mode', 'stage', 'answer_event_id', 'snapshot_id',
      'included_correctness', 'included_correct_answer', 'included_explanation'],
    research_tool_requested: ['capture_version', 'operation_id', 'tool', 'stage', 'initiation_type',
      'answer_event_id', 'content_version', 'data'],
    confidence_observed: ['capture_version', 'operation_id', 'confidence_assignment_id', 'stage',
      'answer_event_id', 'probability', 'feedback_phase'],
    research_tool_observed: ['capture_version', 'operation_id', 'server_event_id', 'tool', 'stage',
      'initiation_type', 'answer_event_id', 'content_version', 'data'],
    response_transition: ['capture_version', 'operation_id', 'branch_id', 'parent_event_id',
      'before_state', 'after_state', 'before_state_hash', 'after_state_hash', 'input_type', 'source',
      'option_key', 'option_action'],
    input_operation: ['capture_version', 'operation_id', 'branch_id', 'parent_event_id',
      'input_type', 'composition', 'selection_start', 'selection_end'],
    item_rendered: ['presentation_state'],
    item_left: ['leave_reason', 'to_assignment_id'],
    response_input_started: ['question_type'],
    text_edit_observed: [
      'question_type',
      'edit_operation',
      'content_hash_before',
      'content_hash_after',
      'content_length_before',
      'content_length_after',
      'input_method',
      'batch_start_client_monotonic_ms',
      'batch_end_client_monotonic_ms',
      'is_ime',
    ],
    response_state_changed: [
      'question_type',
      'response_state_before',
      'response_state_after',
      'response_snapshot_id',
      'content_hash',
      'content_length',
      'state_hash',
      'change_source',
      'stable_input_observation',
    ],
    response_commit_requested: ['submission_token'],
    feedback_shown: [
      'feedback_mode',
      'included_correctness',
      'included_correct_answer',
      'included_explanation',
    ],
    explanation_shown: ['source'],
    hint_opened: ['hint_id'],
    hint_closed: ['hint_id'],
    resource_opened: ['resource_id', 'resource_type'],
    resource_closed: ['resource_id', 'resource_type'],
    document_visible: [],
    document_hidden: [],
    window_focused: [],
    window_blurred: [],
    pageshow: ['persisted'],
    pagehide: ['persisted'],
    network_online: [],
    network_offline: [],
    activity_heartbeat: [
      'attempt_id',
      'document_visible',
      'window_focused',
      'last_device_interaction_age_ms',
    ],
    simulation_parameter_changed: ['simulation_snapshot_id', 'parameter_keys'],
    simulation_run_started: ['simulation_snapshot_id', 'run_id'],
    simulation_run_completed: [
      'simulation_snapshot_id',
      'run_id',
      'status',
    ],
    simulation_error_returned: [
      'simulation_snapshot_id',
      'run_id',
      'error_code',
    ],
  });

  function clone(value) {
    if (typeof structuredClone === 'function') return structuredClone(value);
    return JSON.parse(JSON.stringify(value));
  }

  function sanitizePayload(eventType, payload) {
    const allowed = PAYLOAD_FIELDS[eventType];
    if (!allowed) throw new Error(`Unsupported process event type: ${eventType}`);
    const source = payload && typeof payload === 'object' && !Array.isArray(payload)
      ? payload
      : {};
    return Object.fromEntries(
      allowed
        .filter((field) => Object.prototype.hasOwnProperty.call(source, field))
        .map((field) => [field, clone(source[field])]),
    );
  }

  function backoffDelay(failureCount) {
    const exponent = Math.max(0, Number(failureCount) || 0);
    return Math.min(MAX_BACKOFF_MS, 1000 * (2 ** exponent));
  }

  function normalizeMonotonic(value) {
    return Math.round(Number(value) * 1000) / 1000;
  }

  function requestResult(request) {
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error('IndexedDB request failed.'));
    });
  }

  function transactionResult(transaction) {
    return new Promise((resolve, reject) => {
      transaction.oncomplete = () => resolve();
      transaction.onabort = () => reject(transaction.error || new Error('IndexedDB transaction aborted.'));
      transaction.onerror = () => reject(transaction.error || new Error('IndexedDB transaction failed.'));
    });
  }

  function missingSequenceRanges(highestEmitted, knownSequences) {
    const known = new Set(knownSequences);
    const ranges = [];
    let start = null;
    for (let sequence = 1; sequence <= highestEmitted; sequence += 1) {
      if (!known.has(sequence) && start === null) start = sequence;
      if (known.has(sequence) && start !== null) {
        ranges.push([start, sequence - 1]);
        start = null;
      }
    }
    if (start !== null) ranges.push([start, highestEmitted]);
    return ranges;
  }

  class MemoryOutbox {
    constructor(options = {}) {
      this.isFallback = Boolean(options.isFallback);
      this.records = [];
      this.nextSequences = new Map();
      this.nextOrder = 1;
      this.acknowledged = new Map();
      this.checkpoints = [];
      this.deadLetters = [];
      this.lock = Promise.resolve();
      this.responseHeads = new Map();
    }

    _locked(operation) {
      const result = this.lock.then(operation);
      this.lock = result.catch(() => {});
      return result;
    }

    enqueue(clientInstanceId, eventFactory) {
      return this._locked(() => {
        const sequence = this.nextSequences.get(clientInstanceId) || 1;
        const event = eventFactory(sequence);
        this.nextSequences.set(clientInstanceId, sequence + 1);
        this.records.push({
          client_instance_id: clientInstanceId,
          client_sequence_no: sequence,
          outbox_order: this.nextOrder,
          event: clone(event),
        });
        this.nextOrder += 1;
        if (event.event_type === 'response_transition') {
          this.responseHeads.set(`${event.session_id}:${event.attempt_id}`, clone(event));
        }
        return clone(event);
      });
    }

    async restoreResponse(sessionId, attemptId) {
      return clone(this.responseHeads.get(`${sessionId}:${attemptId}`) || null);
    }
    async clearResponseRecovery(sessionId, attemptId) {
      this.responseHeads.delete(`${sessionId}:${attemptId}`);
    }

    peekBatch(limit = MAX_BATCH_SIZE) {
      return this._locked(() => {
        if (!this.records.length) return null;
        const ordered = [...this.records].sort((left, right) => (
          left.outbox_order - right.outbox_order
        ));
        const clientInstanceId = ordered[0].client_instance_id;
        const records = ordered
          .filter((record) => record.client_instance_id === clientInstanceId)
          .slice(0, Math.min(limit, MAX_BATCH_SIZE));
        return {
          clientInstanceId,
          events: records.map((record) => clone(record.event)),
        };
      });
    }

    deleteAcknowledged(clientInstanceId, sequences) {
      const acknowledged = new Set(sequences);
      return this._locked(() => {
        const prior = this.acknowledged.get(clientInstanceId) || new Set();
        sequences.forEach((sequence) => prior.add(sequence));
        this.acknowledged.set(clientInstanceId, prior);
        this.records = this.records.filter((record) => !(
          record.client_instance_id === clientInstanceId
          && acknowledged.has(record.client_sequence_no)
        ));
      });
    }

    count() {
      return this._locked(() => this.records.length);
    }

    captureState(clientInstanceId) {
      return this._locked(() => {
        const emitted = (this.nextSequences.get(clientInstanceId) || 1) - 1;
        const acknowledged = this.acknowledged.get(clientInstanceId) || new Set();
        const highestAcked = acknowledged.size ? Math.max(...acknowledged) : 0;
        let contiguous = 0;
        while (acknowledged.has(contiguous + 1)) contiguous += 1;
        const pending = this.records.filter(
          (record) => record.client_instance_id === clientInstanceId,
        );
        const failedRows = this.deadLetters.filter(
          (row) => row.client_instance_id === clientInstanceId,
        );
        const known = new Set(acknowledged);
        pending.forEach((record) => known.add(record.client_sequence_no));
        failedRows.forEach((record) => known.add(record.client_sequence_no));
        return {
          highestEmitted: emitted,
          highestAcked,
          highestContiguousAcked: contiguous,
          pending: pending.length,
          failed: failedRows.length,
          missingLocalRanges: missingSequenceRanges(emitted, known),
        };
      });
    }

    enqueueCheckpoint(document) {
      return this._locked(() => this.checkpoints.push(clone(document)));
    }

    pendingCheckpoints() {
      return this._locked(() => this.checkpoints.map(clone));
    }

    deleteCheckpoint(checkpointId) {
      return this._locked(() => {
        this.checkpoints = this.checkpoints.filter((row) => row.checkpoint_id !== checkpointId);
      });
    }

    moveToDeadLetter(clientInstanceId, sequence, rejection) {
      return this._locked(() => {
        const record = this.records.find((row) => row.client_instance_id === clientInstanceId && row.client_sequence_no === sequence);
        if (record) this.deadLetters.push({ ...record, rejection: clone(rejection) });
        this.records = this.records.filter((row) => !(row.client_instance_id === clientInstanceId && row.client_sequence_no === sequence));
      });
    }
  }

  class IndexedDbOutbox {
    constructor(database) {
      this.database = database;
      this.isFallback = false;
    }

    static open(indexedDb = globalThis.indexedDB) {
      if (!indexedDb) return Promise.reject(new Error('IndexedDB is unavailable.'));
      return new Promise((resolve, reject) => {
        const request = indexedDb.open(DATABASE_NAME, DATABASE_VERSION);
        request.onupgradeneeded = () => {
          const database = request.result;
          if (!database.objectStoreNames.contains('events')) {
            const events = database.createObjectStore('events', {
              keyPath: ['client_instance_id', 'client_sequence_no'],
            });
            events.createIndex('outbox_order', 'outbox_order', { unique: true });
          }
          if (!database.objectStoreNames.contains('meta')) {
            database.createObjectStore('meta', { keyPath: 'key' });
          }
          if (!database.objectStoreNames.contains('checkpoints')) {
            database.createObjectStore('checkpoints', { keyPath: 'checkpoint_id' });
          }
          if (!database.objectStoreNames.contains('dead_letters')) {
            database.createObjectStore('dead_letters', { keyPath: 'dead_letter_id' });
          }
        };
        request.onsuccess = () => resolve(new IndexedDbOutbox(request.result));
        request.onerror = () => reject(request.error || new Error('Unable to open IndexedDB.'));
        request.onblocked = () => reject(new Error('IndexedDB upgrade is blocked.'));
      });
    }

    async enqueue(clientInstanceId, eventFactory) {
      const transaction = this.database.transaction(['events', 'meta'], 'readwrite');
      const completed = transactionResult(transaction);
      const events = transaction.objectStore('events');
      const meta = transaction.objectStore('meta');
      const sequenceKey = `next-sequence:${clientInstanceId}`;
      const sequenceRequest = meta.get(sequenceKey);
      const orderRequest = meta.get('next-outbox-order');
      const [sequenceRow, orderRow] = await Promise.all([
        requestResult(sequenceRequest),
        requestResult(orderRequest),
      ]);
      const sequence = sequenceRow ? sequenceRow.value : 1;
      const outboxOrder = orderRow ? orderRow.value : 1;
      const event = eventFactory(sequence);
      meta.put({ key: sequenceKey, value: sequence + 1 });
      meta.put({ key: 'next-outbox-order', value: outboxOrder + 1 });
      events.add({
        client_instance_id: clientInstanceId,
        client_sequence_no: sequence,
        outbox_order: outboxOrder,
        event: clone(event),
      });
      if (event.event_type === 'response_transition') {
        meta.put({ key: `response-head:${event.session_id}:${event.attempt_id}`, value: clone(event) });
        meta.put({ key: `response-state:${event.attempt_id}:${event.payload.after_state_hash}`, value: clone(event.payload.after_state) });
      }
      await completed;
      return clone(event);
    }

    async restoreResponse(sessionId, attemptId) {
      const transaction = this.database.transaction('meta', 'readonly');
      const completed = transactionResult(transaction);
      const row = await requestResult(transaction.objectStore('meta').get(`response-head:${sessionId}:${attemptId}`));
      await completed;
      return row?.value || null;
    }
    async clearResponseRecovery(sessionId, attemptId) {
      const transaction = this.database.transaction('meta', 'readwrite');
      const completed = transactionResult(transaction);
      transaction.objectStore('meta').delete(`response-head:${sessionId}:${attemptId}`);
      await completed;
    }

    async peekBatch(limit = MAX_BATCH_SIZE) {
      const transaction = this.database.transaction('events', 'readonly');
      const completed = transactionResult(transaction);
      const records = await requestResult(transaction.objectStore('events').getAll());
      await completed;
      if (!records.length) return null;
      records.sort((left, right) => left.outbox_order - right.outbox_order);
      const clientInstanceId = records[0].client_instance_id;
      return {
        clientInstanceId,
        events: records
          .filter((record) => record.client_instance_id === clientInstanceId)
          .slice(0, Math.min(limit, MAX_BATCH_SIZE))
          .map((record) => clone(record.event)),
      };
    }

    async deleteAcknowledged(clientInstanceId, sequences) {
      const transaction = this.database.transaction(['events', 'meta'], 'readwrite');
      const completed = transactionResult(transaction);
      const store = transaction.objectStore('events');
      sequences.forEach((sequence) => store.delete([clientInstanceId, sequence]));
      const meta = transaction.objectStore('meta');
      const key = `acknowledged:${clientInstanceId}`;
      const prior = await requestResult(meta.get(key));
      const values = new Set(prior?.value || []);
      sequences.forEach((sequence) => values.add(sequence));
      meta.put({ key, value: [...values].sort((a, b) => a - b) });
      await completed;
    }

    async count() {
      const transaction = this.database.transaction('events', 'readonly');
      const completed = transactionResult(transaction);
      const count = await requestResult(transaction.objectStore('events').count());
      await completed;
      return count;
    }

    async captureState(clientInstanceId) {
      const transaction = this.database.transaction(['events', 'meta', 'dead_letters'], 'readonly');
      const completed = transactionResult(transaction);
      const events = transaction.objectStore('events');
      const meta = transaction.objectStore('meta');
      const [sequenceRow, ackRow, records, deadLetters] = await Promise.all([
        requestResult(meta.get(`next-sequence:${clientInstanceId}`)),
        requestResult(meta.get(`acknowledged:${clientInstanceId}`)),
        requestResult(events.getAll()),
        requestResult(transaction.objectStore('dead_letters').getAll()),
      ]);
      await completed;
      const emitted = (sequenceRow?.value || 1) - 1;
      const values = new Set(ackRow?.value || []);
      const highestAcked = values.size ? Math.max(...values) : 0;
      let contiguous = 0;
      while (values.has(contiguous + 1)) contiguous += 1;
      const pendingRows = records.filter(
        (row) => row.client_instance_id === clientInstanceId,
      );
      const failedRows = deadLetters.filter(
        (row) => row.client_instance_id === clientInstanceId,
      );
      const known = new Set(values);
      pendingRows.forEach((row) => known.add(row.client_sequence_no));
      failedRows.forEach((row) => known.add(row.client_sequence_no));
      return {
        highestEmitted: emitted,
        highestAcked,
        highestContiguousAcked: contiguous,
        pending: pendingRows.length,
        failed: failedRows.length,
        missingLocalRanges: missingSequenceRanges(emitted, known),
      };
    }

    async enqueueCheckpoint(document) {
      const transaction = this.database.transaction('checkpoints', 'readwrite');
      const completed = transactionResult(transaction);
      transaction.objectStore('checkpoints').put(clone(document));
      await completed;
    }

    async pendingCheckpoints() {
      const transaction = this.database.transaction('checkpoints', 'readonly');
      const completed = transactionResult(transaction);
      const rows = await requestResult(transaction.objectStore('checkpoints').getAll());
      await completed;
      return rows.map(clone);
    }

    async deleteCheckpoint(checkpointId) {
      const transaction = this.database.transaction('checkpoints', 'readwrite');
      const completed = transactionResult(transaction);
      transaction.objectStore('checkpoints').delete(checkpointId);
      await completed;
    }

    async moveToDeadLetter(clientInstanceId, sequence, rejection) {
      const transaction = this.database.transaction(['events', 'dead_letters'], 'readwrite');
      const completed = transactionResult(transaction);
      const events = transaction.objectStore('events');
      const record = await requestResult(events.get([clientInstanceId, sequence]));
      if (record) {
        transaction.objectStore('dead_letters').put({
          ...record,
          dead_letter_id: `${clientInstanceId}:${sequence}`,
          rejection: clone(rejection),
        });
        events.delete([clientInstanceId, sequence]);
      }
      await completed;
    }
  }

  async function defaultOutbox(
    indexedDb = globalThis.indexedDB,
    openTimeoutMs = 1500,
  ) {
    let timeout = null;
    try {
      const opening = IndexedDbOutbox.open(indexedDb);
      const deadline = new Promise((_, reject) => {
        timeout = globalThis.setTimeout?.(
          () => reject(new Error('IndexedDB open timed out.')),
          openTimeoutMs,
        );
      });
      return await Promise.race([opening, deadline]);
    } catch (_error) {
      return new MemoryOutbox({ isFallback: true });
    } finally {
      if (timeout !== null) globalThis.clearTimeout?.(timeout);
    }
  }

  function bindLifecycle(collector, options = {}) {
    const documentTarget = options.document || globalThis.document;
    const windowTarget = options.window || globalThis.window;
    const monotonicNow = options.monotonicNow || (() => globalThis.performance.now());
    const setIntervalFunction = options.setInterval || globalThis.setInterval?.bind(globalThis);
    const clearIntervalFunction = options.clearInterval || globalThis.clearInterval?.bind(globalThis);
    const heartbeatSeconds = Number(options.heartbeatIntervalSeconds) || 30;
    let visible = documentTarget?.visibilityState !== 'hidden';
    let focused = documentTarget?.hasFocus ? documentTarget.hasFocus() : true;
    let attemptOpen = options.attemptOpen !== false;
    let lastInteractionAt = monotonicNow();
    let heartbeatTimer = null;
    const listeners = [];

    const record = (eventType, payload) => Promise.resolve(
      collector.record(eventType, payload),
    ).catch(() => null);
    const listen = (target, eventType, handler, listenerOptions) => {
      if (!target?.addEventListener) return;
      target.addEventListener(eventType, handler, listenerOptions);
      listeners.push(() => target.removeEventListener(eventType, handler, listenerOptions));
    };
    const noteInteraction = () => {
      lastInteractionAt = monotonicNow();
    };

    listen(documentTarget, 'visibilitychange', () => {
      visible = documentTarget.visibilityState !== 'hidden';
      const recorded = record(visible ? 'document_visible' : 'document_hidden', {});
      if (visible) recorded.then(() => collector.flush?.());
      return recorded;
    });
    listen(windowTarget, 'focus', () => {
      focused = true;
      return record('window_focused', {});
    });
    listen(windowTarget, 'blur', () => {
      focused = false;
      return record('window_blurred', {});
    });
    listen(windowTarget, 'pageshow', (event) => record('pageshow', {
      persisted: Boolean(event.persisted),
    }));
    listen(windowTarget, 'pagehide', async (event) => {
      if (attemptOpen) await closeAttempt('pagehide', null, false, false);
      await record('pagehide', { persisted: Boolean(event.persisted) });
      await collector.checkpoint?.('pagehide');
      return collector.flush?.({ ignoreVisibility: true, keepalive: true });
    });
    listen(windowTarget, 'online', async () => {
      await record('network_online', {});
      return collector.handleOnline?.(options.onlineOptions || {});
    });
    listen(windowTarget, 'offline', () => record('network_offline', {}));
    ['pointerdown', 'touchstart', 'input'].forEach((eventType) => {
      listen(documentTarget, eventType, noteInteraction, { passive: true });
    });

    if (setIntervalFunction && attemptOpen) {
      heartbeatTimer = setIntervalFunction(() => {
        if (!attemptOpen || !visible || !focused) return Promise.resolve();
        return record('activity_heartbeat', {
          attempt_id: collector.attemptId,
          document_visible: true,
          window_focused: true,
          last_device_interaction_age_ms: Math.max(
            0,
            Math.round(monotonicNow() - lastInteractionAt),
          ),
        }).then(() => collector.checkpoint?.('heartbeat'));
      }, Math.max(15, heartbeatSeconds) * 1000);
    }

    const stopHeartbeat = () => {
      if (heartbeatTimer !== null && clearIntervalFunction) {
        clearIntervalFunction(heartbeatTimer);
      }
      heartbeatTimer = null;
    };
    const closeAttempt = async (
      reason, toAssignmentId = null, flush = true, createCheckpoint = true,
    ) => {
      if (!attemptOpen) return;
      attemptOpen = false;
      stopHeartbeat();
      await record('item_left', {
        leave_reason: reason,
        to_assignment_id: toAssignmentId,
      });
      if (createCheckpoint) {
        await collector.checkpoint?.(reason === 'navigation' ? 'navigation' : 'session_end');
      }
      if (flush) await collector.flushBounded?.(1500, { ignoreVisibility: true, keepalive: true });
    };
    const dispose = () => {
      stopHeartbeat();
      listeners.splice(0).forEach((remove) => remove());
    };

    return {
      // item_rendered is the compatibility event name; the payload makes its
      // measurement boundary explicit: stem and controls are interactive.
      ready: (async () => {
        if (!options.deferRendered) await record('item_rendered', { presentation_state: 'interactive' });
        await record('pageshow', { persisted: false });
        await record(visible ? 'document_visible' : 'document_hidden', {});
        await record(focused ? 'window_focused' : 'window_blurred', {});
        await collector.checkpoint?.('resume');
      })(),
      closeAttempt,
      dispose,
      isAttemptOpen: () => attemptOpen,
    };
  }

  class ProcessEventCollector {
    constructor(options) {
      if (!options || !options.outbox) throw new Error('An outbox is required.');
      this.sessionId = options.sessionId;
      this.attemptId = options.attemptId;
      this.endpoint = options.endpoint;
      this.checkpointEndpoint = options.checkpointEndpoint || '';
      this.csrfToken = options.csrfToken || '';
      this.appVersion = options.appVersion || '';
      this.outbox = options.outbox;
      this.fetch = options.fetch || globalThis.fetch?.bind(globalThis);
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
      this.recordQueue = Promise.resolve();
    }

    _qualityFlags() {
      const flags = [];
      if (!this.timeSyncId) flags.push('clock_unsynchronized');
      if (!this.isOnline()) flags.push('offline_buffered');
      if (this.outbox.isFallback) flags.push('persistent_outbox_unavailable');
      return flags;
    }

    async record(eventType, payload, options = {}) {
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
          const document = {
            event_type: eventType,
            event_version: V2_EVENT_TYPES.has(eventType) ? 2 : 1,
            session_id: this.sessionId,
            attempt_id: this.attemptId,
            client_sequence_no: sequence,
            client_wall_time_utc: wallTime,
            client_monotonic_ms: monotonicMs,
            client_time_origin: this.timeOrigin,
            payload: sanitized,
            quality_flags: qualityFlags,
            app_version: this.appVersion,
          };
          if (options.correlationId) document.correlation_id = options.correlationId;
          if (options.eventId) document.event_id = options.eventId;
          if (this.timeSyncId) document.time_sync_id = this.timeSyncId;
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

    async _flushLoop(options = {}) {
      if (!this.fetch || !this.isOnline()) return false;
      if (!options.ignoreVisibility && !this.isVisible()) return false;
      let completed = true;
      while (this.isOnline() && (options.ignoreVisibility || this.isVisible())) {
        const batch = await this.outbox.peekBatch(MAX_BATCH_SIZE);
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
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': this.csrfToken,
            },
            body,
            credentials: 'same-origin',
            keepalive: Boolean(options.keepalive) && new TextEncoder().encode(body).byteLength < 60 * 1024,
          });
          responseDocument = await response.json();
        } catch (_error) {
          completed = false;
          break;
        }
        if (!response.ok) {
          const permanentResponse = response.status >= 400
            && response.status < 500
            && response.status !== 408
            && response.status !== 429
            && !this.endpoint.includes('/capture-v2/');
          if (permanentResponse) {
            for (const event of batch.events) {
              await this.outbox.moveToDeadLetter?.(
                batch.clientInstanceId,
                event.client_sequence_no,
                {
                  client_sequence_no: event.client_sequence_no,
                  code: responseDocument?.error?.code || 'permanent_rejection',
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
          this.lastCheckpointedContiguousAck = state.highestContiguousAcked;
          await this.checkpoint('ack_progress', { keepalive: Boolean(options.keepalive) });
        }
      } else {
        const delay = backoffDelay(this.failureCount);
        this.failureCount += 1;
        if (this.autoSchedule) this.scheduleUpload(delay);
      }
      return completed;
    }

    flush(options = {}) {
      if (this.flushPromise) return this.flushPromise;
      this.flushPromise = this._flushLoop(options).finally(() => {
        this.flushPromise = null;
      });
      return this.flushPromise;
    }

    async flushBounded(timeoutMs = 1500, options = {}) {
      const flushLatest = async () => {
        await this.recordQueue;
        if (this.flushPromise) await this.flushPromise;
        return this.flush(options);
      };
      if (!this.setTimeout) return flushLatest();
      let timeoutId;
      return Promise.race([
        flushLatest(),
        new Promise((resolve) => {
          timeoutId = this.setTimeout(() => resolve(false), timeoutMs);
        }),
      ]).finally(() => {
        if (timeoutId && this.clearTimeout) this.clearTimeout(timeoutId);
      });
    }

    async checkpoint(trigger, options = {}) {
      if (!this.checkpointEndpoint || !this.fetch || !this.outbox.captureState) return null;
      const state = await this.outbox.captureState(this.clientInstanceId);
      const document = {
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
      const pending = await this.outbox.pendingCheckpoints();
      try {
        let result = null;
        for (const checkpoint of pending) {
          const response = await this.fetch(this.checkpointEndpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken },
            body: JSON.stringify(checkpoint),
            credentials: 'same-origin',
            keepalive: Boolean(options.keepalive) || trigger === 'pagehide' || trigger === 'session_end',
          });
          if (!response.ok) break;
          result = await response.json();
          await this.outbox.deleteCheckpoint(checkpoint.checkpoint_id);
        }
        return result;
      } catch (_error) {
        return null;
      }
    }

    async synchronizeClock({ createUrl, finalizeUrl } = {}) {
      if (!this.fetch || !this.isOnline() || !createUrl || !finalizeUrl) return null;
      const sentWall = this.wallNow().toISOString();
      const sentMonotonic = normalizeMonotonic(this.monotonicNow());
      try {
        const createdResponse = await this.fetch(createUrl, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': this.csrfToken,
          },
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
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': this.csrfToken,
          },
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
        this.timeSyncId = finalized.time_sync_id;
        return finalized;
      } catch (_error) {
        this.timeSyncId = null;
        return null;
      }
    }

    startSynchronization(
      urls,
      intervalMs = DEFAULT_SYNC_INTERVAL_MS,
      runImmediately = true,
    ) {
      const run = () => this.synchronizeClock(urls);
      if (runImmediately) run();
      if (this.setInterval) this.syncTimer = this.setInterval(run, intervalMs);
    }

    async handleOnline(options = {}) {
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

  return {
    DEFAULT_SYNC_INTERVAL_MS,
    IndexedDbOutbox,
    MAX_BATCH_SIZE,
    MemoryOutbox,
    ProcessEventCollector,
    backoffDelay,
    bindLifecycle,
    defaultOutbox,
    sanitizePayload,
  };
});
