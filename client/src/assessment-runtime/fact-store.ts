import type { CaptureStore, CaptureEvent, CaptureCheckpoint, CaptureRejection, DurableIntent, FactRecord } from './capture-contract'
import { clone, submissionOwnership, missingSequenceRanges, MAX_BATCH_SIZE } from './process-types'
const DATABASE_NAME = 'kt-process-events-v1'
const DATABASE_VERSION = 2
interface Meta<T> { key: string; value: T }
function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error('IndexedDB request failed.'));
  });
}

function transactionResult(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onabort = () => reject(transaction.error || new Error('IndexedDB transaction aborted.'));
    transaction.onerror = () => reject(transaction.error || new Error('IndexedDB transaction failed.'));
  });
}

export class MemoryOutbox implements CaptureStore {
  metadata = new Map<string, unknown>()
  async readMetadata<T>(key: string): Promise<T | null> { return clone(this.metadata.get(key) as T ?? null) }
  async writeMetadata<T>(key: string, value: T | null): Promise<void> { if (value === null) this.metadata.delete(key); else this.metadata.set(key, clone(value)) }
  isFallback: boolean
  submissionIntents = new Map<string, DurableIntent>()
  navigationIntents = new Map<string, DurableIntent>()
  records: FactRecord[] = []
  nextSequences = new Map<string, number>()
  nextOrder = 1
  acknowledged = new Map<string, Set<number>>()
  checkpoints: CaptureCheckpoint[] = []
  deadLetters: Array<FactRecord & { rejection: CaptureRejection }> = []
  lock: Promise<unknown> = Promise.resolve()
  responseHeads = new Map<string, CaptureEvent>()

  constructor(options: { isFallback?: boolean } = {}) {
    this.isFallback = Boolean(options.isFallback);
    this.submissionIntents = new Map();
    this.navigationIntents = new Map();
    this.records = [];
    this.nextSequences = new Map();
    this.nextOrder = 1;
    this.acknowledged = new Map();
    this.checkpoints = [];
    this.deadLetters = [];
    this.lock = Promise.resolve();
    this.responseHeads = new Map();
  }

  _locked<T>(operation: () => T | Promise<T>): Promise<T> {
    const result = this.lock.then(operation);
    this.lock = result.catch(() => {});
    return result;
  }

  enqueue(clientInstanceId: string, eventFactory: (sequence: number) => CaptureEvent) {
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

  async restoreResponse(sessionId: number, attemptId: number) {
    return clone(this.responseHeads.get(`${sessionId}:${attemptId}`) || null);
  }
  async restoreSubmissionIntent(sessionId: number, attemptId: number) {
    return clone(this.submissionIntents.get(`${sessionId}:${attemptId}`) || null);
  }
  async saveSubmissionIntent(sessionId: number, attemptId: number, intent: DurableIntent | null) {
    const key = `${sessionId}:${attemptId}`;
    const prior = this.submissionIntents.get(key);
    if (intent !== null && prior?.processSnapshot
      && JSON.stringify(prior.processSnapshot) !== JSON.stringify(intent.processSnapshot)) {
      throw new Error('Submission snapshot is immutable until explicitly cancelled.');
    }
    if (intent === null) this.submissionIntents.delete(key);
    else this.submissionIntents.set(key, clone(intent));
  }
  async restoreNavigationIntent(sessionId: number, attemptId: number) {
    return clone(this.navigationIntents.get(`${sessionId}:${attemptId}`) || null);
  }
  async saveNavigationIntent(sessionId: number, attemptId: number, intent: DurableIntent | null) {
    const key = `${sessionId}:${attemptId}`;
    const prior = this.navigationIntents.get(key);
    if (intent !== null && prior?.processSnapshot && JSON.stringify(prior) !== JSON.stringify(intent)) {
      throw new Error('Navigation snapshot is immutable until confirmed or explicitly cancelled.');
    }
    if (intent === null) this.navigationIntents.delete(key);
    else this.navigationIntents.set(key, clone(intent));
  }
  async clearResponseRecovery(sessionId: number, attemptId: number) {
    this.responseHeads.delete(`${sessionId}:${attemptId}`);
  }

  freezeBoundary(clientInstanceId: string) {
    return this._locked(() => {
      const boundary = { [clientInstanceId]: (this.nextSequences.get(clientInstanceId) || 1) - 1 };
      for (const record of this.records) {
        boundary[record.client_instance_id] = Math.max(boundary[record.client_instance_id] || 0, record.client_sequence_no);
      }
      return boundary;
    });
  }

  pendingEvents(clientInstanceId: string, sequence: number) {
    return this._locked(() => this.records
      .filter((record) => record.client_instance_id === clientInstanceId && record.client_sequence_no <= sequence)
      .sort((left, right) => left.client_sequence_no - right.client_sequence_no)
      .map((record) => clone(record.event)));
  }

  peekBatch(limit = MAX_BATCH_SIZE, boundary: Record<string, number> | null = null) {
    return this._locked(() => {
      const owned = submissionOwnership([...this.submissionIntents.values(), ...this.navigationIntents.values(),
        ...[...this.metadata].filter(([key]) => key.startsWith('capture-flush:') || key.startsWith('capture-mutation:')).map(([, intent]) => intent as DurableIntent)]);
      const ordered = this.records.filter((record) => !owned.events.has(`${record.client_instance_id}:${record.client_sequence_no}`)
        && (!boundary || record.client_sequence_no <= (boundary[record.client_instance_id] ?? -1))).sort((left, right) => (
        left.outbox_order - right.outbox_order
      ));
      if (!ordered.length) return null;
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

  deleteAcknowledged(clientInstanceId: string, sequences: number[]) {
    const acknowledged = new Set(sequences);
    return this._locked(() => {
      const prior = this.acknowledged.get(clientInstanceId) || new Set<number>();
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

  captureState(clientInstanceId: string) {
    return this._locked(() => {
      const emitted = (this.nextSequences.get(clientInstanceId) || 1) - 1;
      const acknowledged = this.acknowledged.get(clientInstanceId) || new Set<number>();
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

  enqueueCheckpoint(document: CaptureCheckpoint) {
    return this._locked(() => this.checkpoints.push(clone(document)));
  }

  pendingCheckpoints(includeOwned = true) {
    return this._locked(() => {
      const owned = submissionOwnership([...this.submissionIntents.values(), ...this.navigationIntents.values(),
        ...[...this.metadata].filter(([key]) => key.startsWith('capture-flush:') || key.startsWith('capture-mutation:')).map(([, intent]) => intent as DurableIntent)]);
      return this.checkpoints.filter((checkpoint) => includeOwned || !owned.checkpoints.has(checkpoint.checkpoint_id)).map(clone);
    });
  }

  deleteCheckpoint(checkpointId: string) {
    return this._locked(() => {
      this.checkpoints = this.checkpoints.filter((row) => row.checkpoint_id !== checkpointId);
    });
  }

  moveToDeadLetter(clientInstanceId: string, sequence: number, rejection: CaptureRejection) {
    return this._locked(() => {
      const record = this.records.find((row) => row.client_instance_id === clientInstanceId && row.client_sequence_no === sequence);
      if (record) this.deadLetters.push({ ...record, rejection: clone(rejection) });
      this.records = this.records.filter((row) => !(row.client_instance_id === clientInstanceId && row.client_sequence_no === sequence));
    });
  }
}

export class IndexedDbOutbox implements CaptureStore {
  async readMetadata<T>(key: string): Promise<T | null> {
    const transaction = this.database.transaction('meta', 'readonly')
    const completed = transactionResult(transaction)
    const row = await requestResult<Meta<T> | undefined>(transaction.objectStore('meta').get(key))
    await completed
    return row?.value ?? null
  }
  async writeMetadata<T>(key: string, value: T | null): Promise<void> {
    const transaction = this.database.transaction('meta', 'readwrite')
    const completed = transactionResult(transaction)
    if (value === null) transaction.objectStore('meta').delete(key)
    else transaction.objectStore('meta').put({ key, value: clone(value) })
    await completed
  }
  database: IDBDatabase
  isFallback: boolean
  constructor(database: IDBDatabase) {
    this.database = database;
    this.isFallback = false;
  }

  static open(indexedDb = globalThis.indexedDB): Promise<IndexedDbOutbox> {
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

  async enqueue(clientInstanceId: string, eventFactory: (sequence: number) => CaptureEvent) {
    const transaction = this.database.transaction(['events', 'meta'], 'readwrite');
    const completed = transactionResult(transaction);
    const events = transaction.objectStore('events');
    const meta = transaction.objectStore('meta');
    const sequenceKey = `next-sequence:${clientInstanceId}`;
    const sequenceRequest = meta.get(sequenceKey);
    const orderRequest = meta.get('next-outbox-order');
    const [sequenceRow, orderRow] = await Promise.all([
      requestResult<Meta<number> | undefined>(sequenceRequest),
      requestResult<Meta<number> | undefined>(orderRequest),
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

  async restoreResponse(sessionId: number, attemptId: number) {
    const transaction = this.database.transaction('meta', 'readonly');
    const completed = transactionResult(transaction);
    const row = await requestResult<Meta<CaptureEvent> | undefined>(transaction.objectStore('meta').get(`response-head:${sessionId}:${attemptId}`));
    await completed;
    return row?.value || null;
  }
  async restoreSubmissionIntent(sessionId: number, attemptId: number) {
    const transaction = this.database.transaction('meta', 'readonly');
    const completed = transactionResult(transaction);
    const row = await requestResult<Meta<DurableIntent> | undefined>(transaction.objectStore('meta').get(`submission-intent:${sessionId}:${attemptId}`));
    await completed;
    return row?.value || null;
  }
  async saveSubmissionIntent(sessionId: number, attemptId: number, intent: DurableIntent | null) {
    const transaction = this.database.transaction('meta', 'readwrite');
    const completed = transactionResult(transaction);
    const store = transaction.objectStore('meta');
    const key = `submission-intent:${sessionId}:${attemptId}`;
    const prior = await requestResult<Meta<DurableIntent> | undefined>(store.get(key));
    if (intent !== null && prior?.value?.processSnapshot
      && JSON.stringify(prior.value.processSnapshot) !== JSON.stringify(intent.processSnapshot)) {
      throw new Error('Submission snapshot is immutable until explicitly cancelled.');
    }
    if (intent === null) store.delete(key);
    else store.put({ key, value: clone(intent) });
    await completed;
  }
  async restoreNavigationIntent(sessionId: number, attemptId: number) {
    const transaction = this.database.transaction('meta', 'readonly');
    const completed = transactionResult(transaction);
    const row = await requestResult<Meta<DurableIntent> | undefined>(transaction.objectStore('meta').get(`navigation-intent:${sessionId}:${attemptId}`));
    await completed;
    return row?.value || null;
  }
  async saveNavigationIntent(sessionId: number, attemptId: number, intent: DurableIntent | null) {
    const transaction = this.database.transaction('meta', 'readwrite');
    const completed = transactionResult(transaction);
    const store = transaction.objectStore('meta');
    const key = `navigation-intent:${sessionId}:${attemptId}`;
    const prior = await requestResult<Meta<DurableIntent> | undefined>(store.get(key));
    if (intent !== null && prior?.value?.processSnapshot && JSON.stringify(prior.value) !== JSON.stringify(intent)) {
      throw new Error('Navigation snapshot is immutable until confirmed or explicitly cancelled.');
    }
    if (intent === null) store.delete(key);
    else store.put({ key, value: clone(intent) });
    await completed;
  }
  async clearResponseRecovery(sessionId: number, attemptId: number) {
    const transaction = this.database.transaction('meta', 'readwrite');
    const completed = transactionResult(transaction);
    transaction.objectStore('meta').delete(`response-head:${sessionId}:${attemptId}`);
    await completed;
  }

  async freezeBoundary(clientInstanceId: string) {
    const transaction = this.database.transaction(['events', 'meta'], 'readonly');
    const completed = transactionResult(transaction);
    const [records, sequenceRow] = await Promise.all([
      requestResult<FactRecord[]>(transaction.objectStore('events').getAll()),
      requestResult(transaction.objectStore('meta').get(`next-sequence:${clientInstanceId}`)),
    ]);
    await completed;
    const boundary = { [clientInstanceId]: (sequenceRow?.value || 1) - 1 };
    for (const record of records) {
      boundary[record.client_instance_id] = Math.max(boundary[record.client_instance_id] || 0, record.client_sequence_no);
    }
    return boundary;
  }

  async pendingEvents(clientInstanceId: string, sequence: number) {
    const transaction = this.database.transaction('events', 'readonly');
    const completed = transactionResult(transaction);
    const records = await requestResult<FactRecord[]>(transaction.objectStore('events').getAll());
    await completed;
    return records.filter((record) => record.client_instance_id === clientInstanceId && record.client_sequence_no <= sequence)
      .sort((left, right) => left.client_sequence_no - right.client_sequence_no)
      .map((record) => clone(record.event));
  }

  async peekBatch(limit = MAX_BATCH_SIZE, boundary: Record<string, number> | null = null) {
    const transaction = this.database.transaction(['events', 'meta'], 'readonly');
    const completed = transactionResult(transaction);
    const [allRecords, metadata] = await Promise.all([
      requestResult<FactRecord[]>(transaction.objectStore('events').getAll()),
      requestResult<Meta<DurableIntent>[]>(transaction.objectStore('meta').getAll()),
    ]);
    await completed;
    const owned = submissionOwnership(metadata.filter((row) => row.key.startsWith('submission-intent:') || row.key.startsWith('navigation-intent:') || row.key.startsWith('capture-flush:') || row.key.startsWith('capture-mutation:')).map((row) => row.value));
    const records = allRecords.filter((record) => !owned.events.has(`${record.client_instance_id}:${record.client_sequence_no}`)
      && (!boundary || record.client_sequence_no <= (boundary[record.client_instance_id] ?? -1)));
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

  async deleteAcknowledged(clientInstanceId: string, sequences: number[]) {
    const transaction = this.database.transaction(['events', 'meta'], 'readwrite');
    const completed = transactionResult(transaction);
    const store = transaction.objectStore('events');
    sequences.forEach((sequence) => store.delete([clientInstanceId, sequence]));
    const meta = transaction.objectStore('meta');
    const key = `acknowledged:${clientInstanceId}`;
    const prior = await requestResult<Meta<number[]> | undefined>(meta.get(key));
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

  async captureState(clientInstanceId: string) {
    const transaction = this.database.transaction(['events', 'meta', 'dead_letters'], 'readonly');
    const completed = transactionResult(transaction);
    const events = transaction.objectStore('events');
    const meta = transaction.objectStore('meta');
    const [sequenceRow, ackRow, records, deadLetters] = await Promise.all([
      requestResult<Meta<number> | undefined>(meta.get(`next-sequence:${clientInstanceId}`)),
      requestResult<Meta<number[]> | undefined>(meta.get(`acknowledged:${clientInstanceId}`)),
      requestResult<FactRecord[]>(events.getAll()),
      requestResult<FactRecord[]>(transaction.objectStore('dead_letters').getAll()),
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

  async enqueueCheckpoint(document: CaptureCheckpoint) {
    const transaction = this.database.transaction('checkpoints', 'readwrite');
    const completed = transactionResult(transaction);
    transaction.objectStore('checkpoints').put(clone(document));
    await completed;
  }

  async pendingCheckpoints(includeOwned = true) {
    const transaction = this.database.transaction(['checkpoints', 'meta'], 'readonly');
    const completed = transactionResult(transaction);
    const [rows, metadata] = await Promise.all([
      requestResult<CaptureCheckpoint[]>(transaction.objectStore('checkpoints').getAll()),
      requestResult<Meta<DurableIntent>[]>(transaction.objectStore('meta').getAll()),
    ]);
    await completed;
    const owned = submissionOwnership(metadata.filter((row) => row.key.startsWith('submission-intent:') || row.key.startsWith('navigation-intent:') || row.key.startsWith('capture-flush:') || row.key.startsWith('capture-mutation:')).map((row) => row.value));
    return rows.filter((checkpoint) => includeOwned || !owned.checkpoints.has(checkpoint.checkpoint_id)).map(clone);
  }

  async deleteCheckpoint(checkpointId: string) {
    const transaction = this.database.transaction('checkpoints', 'readwrite');
    const completed = transactionResult(transaction);
    transaction.objectStore('checkpoints').delete(checkpointId);
    await completed;
  }

  async moveToDeadLetter(clientInstanceId: string, sequence: number, rejection: CaptureRejection) {
    const transaction = this.database.transaction(['events', 'dead_letters'], 'readwrite');
    const completed = transactionResult(transaction);
    const events = transaction.objectStore('events');
    const record = await requestResult<FactRecord | undefined>(events.get([clientInstanceId, sequence]));
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

export async function defaultOutbox(
  indexedDb = globalThis.indexedDB,
  openTimeoutMs = 1500,
) {
  let timeout: ReturnType<typeof globalThis.setTimeout> | null = null;
  try {
    const opening = IndexedDbOutbox.open(indexedDb);
    const deadline = new Promise<never>((_, reject) => {
      timeout = globalThis.setTimeout?.(
        () => reject(new Error('IndexedDB open timed out.')),
        openTimeoutMs,
      );
    });
    return await Promise.race([opening, deadline]);
  } catch {
    return new MemoryOutbox({ isFallback: true });
  } finally {
    if (timeout !== null) globalThis.clearTimeout?.(timeout);
  }
}
