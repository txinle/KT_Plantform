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
  const DATABASE_VERSION = 1;

  const PAYLOAD_FIELDS = Object.freeze({
    item_rendered: [],
    item_left: ['leave_reason'],
    response_input_started: ['question_type'],
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

  class MemoryOutbox {
    constructor(options = {}) {
      this.isFallback = Boolean(options.isFallback);
      this.records = [];
      this.nextSequences = new Map();
      this.nextOrder = 1;
      this.lock = Promise.resolve();
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
        return clone(event);
      });
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
        this.records = this.records.filter((record) => !(
          record.client_instance_id === clientInstanceId
          && acknowledged.has(record.client_sequence_no)
        ));
      });
    }

    count() {
      return this._locked(() => this.records.length);
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
      await completed;
      return clone(event);
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
      const transaction = this.database.transaction('events', 'readwrite');
      const completed = transactionResult(transaction);
      const store = transaction.objectStore('events');
      sequences.forEach((sequence) => store.delete([clientInstanceId, sequence]));
      await completed;
    }

    async count() {
      const transaction = this.database.transaction('events', 'readonly');
      const completed = transactionResult(transaction);
      const count = await requestResult(transaction.objectStore('events').count());
      await completed;
      return count;
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
      await record('pagehide', { persisted: Boolean(event.persisted) });
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
        });
      }, Math.max(15, heartbeatSeconds) * 1000);
    }

    const stopHeartbeat = () => {
      if (heartbeatTimer !== null && clearIntervalFunction) {
        clearIntervalFunction(heartbeatTimer);
      }
      heartbeatTimer = null;
    };
    const closeAttempt = async (reason) => {
      if (!attemptOpen) return;
      attemptOpen = false;
      stopHeartbeat();
      await record('item_left', { leave_reason: reason });
      collector.flush?.({ ignoreVisibility: true, keepalive: true });
    };
    const dispose = () => {
      stopHeartbeat();
      listeners.splice(0).forEach((remove) => remove());
    };

    return {
      ready: record('item_rendered', {}),
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
      this.retryTimer = null;
      this.syncTimer = null;
      this.flushPromise = null;
    }

    _qualityFlags() {
      const flags = [];
      if (!this.timeSyncId) flags.push('clock_unsynchronized');
      if (!this.isOnline()) flags.push('offline_buffered');
      if (this.outbox.isFallback) flags.push('payload_recovered');
      return flags;
    }

    async record(eventType, payload, options = {}) {
      const sanitized = sanitizePayload(eventType, payload);
      const wallTime = this.wallNow().toISOString();
      const monotonicMs = normalizeMonotonic(this.monotonicNow());
      const qualityFlags = [...new Set([
        ...this._qualityFlags(),
        ...(options.qualityFlags || []),
      ])];
      const event = await this.outbox.enqueue(
        this.clientInstanceId,
        (sequence) => {
          const document = {
            event_type: eventType,
            event_version: 1,
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
          if (this.timeSyncId) document.time_sync_id = this.timeSyncId;
          return document;
        },
      );
      if (this.autoSchedule) this.scheduleUpload(this.uploadDelayMs);
      return event;
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
            keepalive: Boolean(options.keepalive),
          });
          responseDocument = await response.json();
        } catch (_error) {
          completed = false;
          break;
        }
        if (!response.ok || !responseDocument?.success) {
          completed = false;
          break;
        }
        const available = new Set(batch.events.map((event) => event.client_sequence_no));
        const acknowledged = (responseDocument.acknowledgements || [])
          .map((item) => item.client_sequence_no)
          .filter((sequence) => available.has(sequence));
        if (!acknowledged.length) {
          completed = false;
          break;
        }
        await this.outbox.deleteAcknowledged(batch.clientInstanceId, acknowledged);
      }
      if (completed) {
        this.failureCount = 0;
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
