import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const {
  MemoryOutbox,
  ProcessEventCollector,
  backoffDelay,
  bindLifecycle,
  defaultOutbox,
  sanitizePayload,
} = require('../../../static/js/process-event-collector.js');

const INSTANCE_A = '11111111-1111-4111-8111-111111111111';
const INSTANCE_B = '22222222-2222-4222-8222-222222222222';

test('submission intent survives collector replacement and is scoped to one attempt', async () => {
  const outbox = new MemoryOutbox();
  const intent = { token: INSTANCE_A, answer: 'B', confidence: { status: 'skipped' } };
  await outbox.saveSubmissionIntent(7, 11, intent);
  assert.deepEqual(await outbox.restoreSubmissionIntent(7, 11), intent);
  assert.equal(await outbox.restoreSubmissionIntent(7, 12), null);
  await outbox.saveSubmissionIntent(7, 11, null);
  assert.equal(await outbox.restoreSubmissionIntent(7, 11), null);
});

test('interactive presentation survives payload sanitization for the v2 schema', async () => {
  const collector = new ProcessEventCollector(eventPayload());
  const event = await collector.record('item_rendered', { presentation_state: 'interactive' });
  assert.equal(event.event_version, 2);
  assert.deepEqual(event.payload, { presentation_state: 'interactive' });
});

test('an explicit event version preserves versioned confidence schemas', async () => {
  const collector = new ProcessEventCollector(eventPayload());
  const event = await collector.record('confidence_observed', {
    capture_version: 'capture-v2',
    operation_id: INSTANCE_A,
    confidence_assignment_id: INSTANCE_B,
    stage: 'responded',
    answer_event_id: null,
    probability: 57,
    feedback_phase: 'before_feedback',
  }, { eventVersion: 2 });
  assert.equal(event.event_version, 2);
});

test('atomic capture-v2 rejection retains all evidence instead of dead-lettering valid neighbours', async () => {
  const outbox = new MemoryOutbox();
  const collector = new ProcessEventCollector(eventPayload({
    outbox, endpoint: '/assessment/process/capture-v2/batch/',
    fetch: async () => ({ ok: false, status: 400, json: async () => ({ error: { code: 'invalid_event' } }) }),
  }));
  await collector.record('document_visible', {});
  await collector.record('item_rendered', { presentation_state: 'interactive' });
  assert.equal(await collector.flush(), false);
  assert.equal(await outbox.count(), 2);
});

function eventPayload(overrides = {}) {
  return {
    sessionId: 7,
    attemptId: 11,
    endpoint: '/assessment/process/events/batch/',
    csrfToken: 'csrf',
    appVersion: 'test-v1',
    outbox: new MemoryOutbox(),
    uuid: () => INSTANCE_A,
    wallNow: () => new Date('2026-08-26T00:00:00.000Z'),
    monotonicNow: () => 125.5,
    timeOrigin: '2026-08-25T23:59:59.874Z',
    isOnline: () => true,
    isVisible: () => true,
    autoSchedule: false,
    ...overrides,
  };
}

function okResponse(body) {
  return {
    ok: true,
    json: async () => body,
  };
}

test('a refreshed page receives a new UUID instance and its own sequence', async () => {
  const outbox = new MemoryOutbox();
  const first = new ProcessEventCollector(eventPayload({ outbox, uuid: () => INSTANCE_A }));
  const second = new ProcessEventCollector(eventPayload({ outbox, uuid: () => INSTANCE_B }));

  const firstEvent = await first.record('item_rendered', {});
  const secondEvent = await second.record('item_rendered', {});

  assert.equal(first.clientInstanceId, INSTANCE_A);
  assert.equal(second.clientInstanceId, INSTANCE_B);
  assert.equal(firstEvent.client_sequence_no, 1);
  assert.equal(secondEvent.client_sequence_no, 1);
});

test('concurrent records allocate unique sequences in the same atomic enqueue', async () => {
  const outbox = new MemoryOutbox();
  const collector = new ProcessEventCollector(eventPayload({ outbox }));

  const events = await Promise.all(
    Array.from({ length: 20 }, () => collector.record('document_visible', {})),
  );

  assert.deepEqual(
    events.map((event) => event.client_sequence_no).sort((a, b) => a - b),
    Array.from({ length: 20 }, (_, index) => index + 1),
  );
  assert.equal(await outbox.count(), 20);
});

test('checkpoint state reports locally missing sequence ranges', async () => {
  const outbox = new MemoryOutbox();
  const collector = new ProcessEventCollector(eventPayload({ outbox }));
  await collector.record('document_visible', {});
  await collector.record('window_focused', {});
  await collector.record('item_rendered', {});
  outbox.records = outbox.records.filter((row) => row.client_sequence_no !== 2);

  const state = await outbox.captureState(INSTANCE_A);

  assert.equal(state.highestEmitted, 3);
  assert.deepEqual(state.missingLocalRanges, [[2, 2]]);
});

test('an event is durably queued before upload starts', async () => {
  const outbox = new MemoryOutbox();
  let observedCount = 0;
  const collector = new ProcessEventCollector(eventPayload({
    outbox,
    fetch: async (_url, options) => {
      observedCount = await outbox.count();
      const body = JSON.parse(options.body);
      return okResponse({
        success: true,
        acknowledgements: body.events.map((event) => ({
          client_sequence_no: event.client_sequence_no,
          event_id: INSTANCE_B,
          server_ingest_sequence_no: event.client_sequence_no,
        })),
      });
    },
  }));

  await collector.record('item_rendered', {});
  await collector.flush();

  assert.equal(observedCount, 1);
  assert.equal(await outbox.count(), 0);
});

test('failed or incomplete acknowledgement never deletes an outbox row', async () => {
  const outbox = new MemoryOutbox();
  let response = { ok: false, json: async () => ({ success: false }) };
  const collector = new ProcessEventCollector(eventPayload({
    outbox,
    fetch: async () => response,
  }));
  const event = await collector.record('item_rendered', {});

  await collector.flush();
  assert.equal(await outbox.count(), 1);

  response = okResponse({ success: true, acknowledgements: [] });
  await collector.flush();
  assert.equal(await outbox.count(), 1);

  response = okResponse({
    success: true,
    acknowledgements: [{
      client_sequence_no: event.client_sequence_no,
      event_id: INSTANCE_B,
      server_ingest_sequence_no: 1,
    }],
  });
  await collector.flush();
  assert.equal(await outbox.count(), 0);
});

test('an all-invalid 4xx batch moves every event to dead letter', async () => {
  const outbox = new MemoryOutbox();
  const collector = new ProcessEventCollector(eventPayload({
    outbox,
    fetch: async () => ({
      ok: false,
      status: 400,
      json: async () => ({
        success: false,
        error: { code: 'invalid_event' },
      }),
    }),
  }));
  await collector.record('item_rendered', { presentation_state: 'interactive' });

  await collector.flush();
  const state = await outbox.captureState(INSTANCE_A);

  assert.equal(await outbox.count(), 0);
  assert.equal(state.failed, 1);
});

test('ack high-water does not conceal a non-contiguous acknowledgement gap', async () => {
  const outbox = new MemoryOutbox();
  const collector = new ProcessEventCollector(eventPayload({ outbox }));
  await collector.record('document_visible', {});
  await collector.record('window_focused', {});
  await collector.record('item_rendered', { presentation_state: 'interactive' });

  await outbox.deleteAcknowledged(INSTANCE_A, [1, 3]);
  const state = await outbox.captureState(INSTANCE_A);

  assert.equal(state.highestEmitted, 3);
  assert.equal(state.highestAcked, 3);
  assert.equal(state.highestContiguousAcked, 1);
  assert.equal(state.pending, 1);
});

test('v2 navigation close carries the destination assignment', async () => {
  const collector = new ProcessEventCollector(eventPayload());
  const lifecycle = bindLifecycle(collector, {
    document: null,
    window: null,
    setInterval: null,
  });
  await lifecycle.ready;
  await lifecycle.closeAttempt('navigation', 42);
  const batch = await collector.outbox.peekBatch();
  const left = batch.events.find((event) => event.event_type === 'item_left');
  assert.equal(left.event_version, 2);
  assert.deepEqual(left.payload, {
    leave_reason: 'navigation',
    to_assignment_id: 42,
  });
});

test('a retry sends the identical persisted batch body', async () => {
  const bodies = [];
  const collector = new ProcessEventCollector(eventPayload({
    fetch: async (_url, options) => {
      bodies.push(options.body);
      if (bodies.length === 1) return { ok: false, json: async () => ({}) };
      const body = JSON.parse(options.body);
      return okResponse({
        success: true,
        acknowledgements: body.events.map((event) => ({
          client_sequence_no: event.client_sequence_no,
          event_id: INSTANCE_B,
          server_ingest_sequence_no: 1,
        })),
      });
    },
  }));
  await collector.record('pageshow', { persisted: false });

  await collector.flush();
  await collector.flush();

  assert.equal(bodies.length, 2);
  assert.equal(bodies[0], bodies[1]);
});

test('backoff is exponential and capped', () => {
  assert.equal(backoffDelay(0), 1000);
  assert.equal(backoffDelay(1), 2000);
  assert.equal(backoffDelay(5), 32000);
  assert.equal(backoffDelay(20), 60000);
});

test('online recovery flushes retained events', async () => {
  let online = false;
  let uploads = 0;
  const collector = new ProcessEventCollector(eventPayload({
    isOnline: () => online,
    fetch: async (_url, options) => {
      uploads += 1;
      const body = JSON.parse(options.body);
      return okResponse({
        success: true,
        acknowledgements: body.events.map((event) => ({
          client_sequence_no: event.client_sequence_no,
          event_id: INSTANCE_B,
          server_ingest_sequence_no: 1,
        })),
      });
    },
  }));
  await collector.record('network_offline', {});
  await collector.flush();
  assert.equal(uploads, 0);

  online = true;
  await collector.handleOnline({ synchronize: false });
  assert.equal(uploads, 1);
});

test('a previous client instance backlog is uploaded before the current instance', async () => {
  const outbox = new MemoryOutbox();
  const bodies = [];
  const fetch = async (_url, options) => {
    const body = JSON.parse(options.body);
    bodies.push(body);
    return okResponse({
      success: true,
      acknowledgements: body.events.map((event) => ({
        client_sequence_no: event.client_sequence_no,
        event_id: INSTANCE_B,
        server_ingest_sequence_no: event.client_sequence_no,
      })),
    });
  };
  const previous = new ProcessEventCollector(eventPayload({ outbox, fetch, uuid: () => INSTANCE_A }));
  await previous.record('item_rendered', {});
  const current = new ProcessEventCollector(eventPayload({ outbox, fetch, uuid: () => INSTANCE_B }));
  await current.record('item_rendered', {});

  await current.flush();

  assert.deepEqual(bodies.map((body) => body.client_instance_id), [INSTANCE_A, INSTANCE_B]);
});

test('uploads are split at the server maximum of 100 events', async () => {
  const sizes = [];
  const collector = new ProcessEventCollector(eventPayload({
    fetch: async (_url, options) => {
      const body = JSON.parse(options.body);
      sizes.push(body.events.length);
      return okResponse({
        success: true,
        acknowledgements: body.events.map((event) => ({
          client_sequence_no: event.client_sequence_no,
          event_id: INSTANCE_B,
          server_ingest_sequence_no: event.client_sequence_no,
        })),
      });
    },
  }));
  await Promise.all(
    Array.from({ length: 105 }, () => collector.record('document_visible', {})),
  );

  await collector.flush();

  assert.deepEqual(sizes, [100, 5]);
});

test('a response transition larger than 64 KiB remains intact and avoids keepalive truncation', async () => {
  const outbox = new MemoryOutbox();
  const uploads = [];
  const longText = 'x'.repeat((64 * 1024) + 1);
  const collector = new ProcessEventCollector(eventPayload({
    outbox,
    fetch: async (_url, options) => {
      const body = JSON.parse(options.body);
      uploads.push({
        body,
        bytes: new TextEncoder().encode(options.body).byteLength,
        keepalive: options.keepalive,
      });
      return okResponse({
        success: true,
        acknowledgements: body.events.map((event) => ({
          client_sequence_no: event.client_sequence_no,
          event_id: INSTANCE_B,
          server_ingest_sequence_no: event.client_sequence_no,
        })),
      });
    },
  }));
  const event = await collector.record('response_transition', {
    capture_version: 'capture-v2',
    operation_id: 'long-text-edit',
    branch_id: 'branch-long-text',
    parent_event_id: null,
    before_state: { question_type: 'short_answer', value: '' },
    after_state: { question_type: 'short_answer', value: longText },
    before_state_hash: 'a'.repeat(64),
    after_state_hash: 'b'.repeat(64),
    input_type: 'insertText',
    source: 'student',
  });

  assert.equal(await outbox.count(), 1);
  await collector.flush({ keepalive: true });

  assert.equal(uploads.length, 1);
  assert.equal(uploads[0].body.events.length, 1);
  assert.equal(uploads[0].body.events[0].event_id, event.event_id);
  assert.equal(uploads[0].body.events[0].payload.after_state.value, longText);
  assert.ok(uploads[0].bytes > 64 * 1024);
  assert.equal(uploads[0].keepalive, false);
  assert.equal(await outbox.count(), 0);
});

test('a thousand offline response transitions restore in ordered batches without loss or coalescing', async () => {
  const outbox = new MemoryOutbox();
  const uploaded = [];
  const batchSizes = [];
  let online = false;
  const collector = new ProcessEventCollector(eventPayload({
    outbox,
    isOnline: () => online,
    fetch: async (_url, options) => {
      const body = JSON.parse(options.body);
      batchSizes.push(body.events.length);
      uploaded.push(...body.events);
      return okResponse({
        success: true,
        acknowledgements: body.events.map((event) => ({
          client_sequence_no: event.client_sequence_no,
          event_id: INSTANCE_B,
          server_ingest_sequence_no: event.client_sequence_no,
        })),
      });
    },
  }));
  const emitted = await Promise.all(Array.from({ length: 1000 }, (_, index) => collector.record(
    'response_transition',
    {
      capture_version: 'capture-v2',
      operation_id: `offline-edit-${index + 1}`,
      branch_id: 'branch-offline',
      parent_event_id: index === 0 ? null : `offline-event-${index}`,
      before_state: { question_type: 'short_answer', value: String(index) },
      after_state: { question_type: 'short_answer', value: String(index + 1) },
      before_state_hash: String(index).padStart(64, '0'),
      after_state_hash: String(index + 1).padStart(64, '0'),
      input_type: 'insertText',
      source: 'student',
    },
    { eventId: `offline-event-${index + 1}` },
  )));

  assert.deepEqual(
    emitted.map((event) => event.client_sequence_no),
    Array.from({ length: 1000 }, (_, index) => index + 1),
  );
  assert.equal(await outbox.count(), 1000);
  assert.equal(await collector.flush(), false);
  assert.equal(uploaded.length, 0);

  online = true;
  assert.equal(await collector.handleOnline({ synchronize: false }), true);

  assert.ok(batchSizes.every((size) => size <= 100));
  assert.deepEqual(
    uploaded.map((event) => event.client_sequence_no),
    Array.from({ length: 1000 }, (_, index) => index + 1),
  );
  assert.deepEqual(
    uploaded.map((event) => event.payload.operation_id),
    Array.from({ length: 1000 }, (_, index) => `offline-edit-${index + 1}`),
  );
  assert.equal(await outbox.count(), 0);
});

test('payload sanitization retains declared fields and drops tracking detail', () => {
  assert.deepEqual(
    sanitizePayload('activity_heartbeat', {
      attempt_id: 11,
      document_visible: true,
      window_focused: false,
      last_device_interaction_age_ms: 1200,
      mouse_x: 99,
      key_code: 'KeyA',
      raw_text: 'private answer',
    }),
    {
      attempt_id: 11,
      document_visible: true,
      window_focused: false,
      last_device_interaction_age_ms: 1200,
    },
  );
  assert.deepEqual(
    sanitizePayload('response_state_changed', {
      question_type: 'single_choice',
      response_state_after: 'A',
      change_source: 'student',
      raw_text: 'A because...',
    }),
    {
      question_type: 'single_choice',
      response_state_after: 'A',
      change_source: 'student',
    },
  );
});

test('response state payload retains before/after refs and stable observation', () => {
  assert.deepEqual(
    sanitizePayload('response_state_changed', {
      question_type: 'short_answer',
      response_state_before: null,
      response_state_after: { snapshot_id: 's', state_hash: 'h', content_length: 2 },
      stable_input_observation: { is_composing: false, text_length: 2 },
    }),
    {
      question_type: 'short_answer',
      response_state_before: null,
      response_state_after: { snapshot_id: 's', state_hash: 'h', content_length: 2 },
      stable_input_observation: { is_composing: false, text_length: 2 },
    },
  );
});

test('text edit payload never retains answer text', () => {
  assert.deepEqual(
    sanitizePayload('text_edit_observed', {
      question_type: 'short_answer',
      edit_operation: 'insert',
      content_hash_before: 'a'.repeat(64),
      content_hash_after: 'b'.repeat(64),
      content_length_before: 0,
      content_length_after: 3,
      input_method: 'keyboard',
      raw_text: 'private answer',
    }),
    {
      question_type: 'short_answer',
      edit_operation: 'insert',
      content_hash_before: 'a'.repeat(64),
      content_hash_after: 'b'.repeat(64),
      content_length_before: 0,
      content_length_after: 3,
      input_method: 'keyboard',
    },
  );
});

test('lifecycle capture emits approved events and heartbeat stops after close', async () => {
  class Target {
    constructor() { this.listeners = new Map(); }
    addEventListener(name, listener) {
      if (!this.listeners.has(name)) this.listeners.set(name, []);
      this.listeners.get(name).push(listener);
    }
    removeEventListener(name, listener) {
      this.listeners.set(
        name,
        (this.listeners.get(name) || []).filter((item) => item !== listener),
      );
    }
    dispatch(name, detail = {}) {
      return Promise.all(
        (this.listeners.get(name) || []).map((listener) => listener(detail)),
      );
    }
  }

  const documentTarget = new Target();
  documentTarget.visibilityState = 'visible';
  documentTarget.hasFocus = () => true;
  const windowTarget = new Target();
  const records = [];
  const checkpoints = [];
  let intervalCallback;
  let intervalCleared = false;
  let monotonic = 5000;
  const collector = {
    attemptId: 11,
    record: async (eventType, payload) => { records.push({ eventType, payload }); },
    flush: async () => true,
    checkpoint: async (trigger) => { checkpoints.push(trigger); },
  };

  const lifecycle = bindLifecycle(collector, {
    document: documentTarget,
    window: windowTarget,
    heartbeatIntervalSeconds: 20,
    monotonicNow: () => monotonic,
    setInterval: (callback) => { intervalCallback = callback; return 17; },
    clearInterval: (identifier) => { intervalCleared = identifier === 17; },
  });
  await lifecycle.ready;
  assert.equal(records[0].eventType, 'item_rendered');
  assert.deepEqual(
    records.slice(1, 4).map((item) => item.eventType),
    ['pageshow', 'document_visible', 'window_focused'],
  );

  documentTarget.visibilityState = 'hidden';
  await documentTarget.dispatch('visibilitychange');
  await windowTarget.dispatch('blur');
  await windowTarget.dispatch('offline');
  assert.deepEqual(
    records.slice(4, 7).map((item) => item.eventType),
    ['document_hidden', 'window_blurred', 'network_offline'],
  );

  documentTarget.visibilityState = 'visible';
  await documentTarget.dispatch('visibilitychange');
  await windowTarget.dispatch('focus');
  monotonic = 6200;
  await documentTarget.dispatch('pointerdown');
  monotonic = 8000;
  await intervalCallback();
  const heartbeat = records.find((item) => item.eventType === 'activity_heartbeat');
  assert.deepEqual(heartbeat.payload, {
    attempt_id: 11,
    document_visible: true,
    window_focused: true,
    last_device_interaction_age_ms: 1800,
  });
  assert.ok(checkpoints.includes('heartbeat'));

  await windowTarget.dispatch('pagehide', { persisted: true });
  const heartbeatCount = records.filter((item) => item.eventType === 'activity_heartbeat').length;
  await intervalCallback();
  assert.equal(intervalCleared, true);
  const left = records.find((item) => item.eventType === 'item_left');
  assert.equal(left.payload.leave_reason, 'pagehide');
  assert.equal(left.payload.to_assignment_id, null);
  const leftIndex = records.findIndex((item) => item.eventType === 'item_left');
  const pagehideIndex = records.findIndex((item) => item.eventType === 'pagehide');
  assert.ok(leftIndex < pagehideIndex);
  assert.equal(checkpoints.at(-1), 'pagehide');
  assert.equal(
    records.filter((item) => item.eventType === 'activity_heartbeat').length,
    heartbeatCount,
  );
});

test('successful contiguous ACK emits an ack-progress checkpoint', async () => {
  const checkpointBodies = [];
  const collector = new ProcessEventCollector(eventPayload({
    checkpointEndpoint: '/checkpoint/',
    fetch: async (url, options) => {
      const body = JSON.parse(options.body);
      if (url === '/checkpoint/') {
        checkpointBodies.push(body);
        return okResponse({ success: true, checkpoint_id: body.checkpoint_id });
      }
      return okResponse({
        success: true,
        acknowledgements: body.events.map((event) => ({
          client_sequence_no: event.client_sequence_no,
          event_id: INSTANCE_B,
          server_ingest_sequence_no: event.client_sequence_no,
        })),
      });
    },
  }));
  await collector.record('item_rendered', { presentation_state: 'interactive' });

  await collector.flush();

  assert.equal(checkpointBodies.length, 1);
  assert.equal(checkpointBodies[0].trigger, 'ack_progress');
  assert.equal(checkpointBodies[0].highest_contiguous_acked_sequence_no, 1);
});

test('IndexedDB unavailability is explicit in the in-memory fallback quality', async () => {
  const outbox = await defaultOutbox(undefined);
  const collector = new ProcessEventCollector(eventPayload({ outbox }));

  const event = await collector.record('item_rendered', {});

  assert.equal(outbox.isFallback, true);
  assert.ok(event.quality_flags.includes('persistent_outbox_unavailable'));
});

test('visit correlation is the default while explicit operation correlation is preserved', async () => {
  const visitId = '00000000-0000-4000-8000-000000000041';
  const submissionId = '00000000-0000-4000-8000-000000000042';
  const collector = new ProcessEventCollector(eventPayload({ correlationId: visitId }));

  const rendered = await collector.record('item_rendered', { presentation_state: 'interactive' });
  const commit = await collector.record(
    'response_commit_requested',
    { submission_token: submissionId },
    { correlationId: submissionId },
  );

  assert.equal(rendered.correlation_id, visitId);
  assert.equal(commit.correlation_id, submissionId);
});

test('two-step time synchronization uses one instance and links later events', async () => {
  const requests = [];
  const walls = [
    new Date('2026-08-26T00:00:00.000Z'),
    new Date('2026-08-26T00:00:00.080Z'),
    new Date('2026-08-26T00:00:00.100Z'),
  ];
  const monotonic = [100, 180, 200];
  const collector = new ProcessEventCollector(eventPayload({
    wallNow: () => walls.shift(),
    monotonicNow: () => monotonic.shift(),
    fetch: async (url, options) => {
      requests.push({ url, body: JSON.parse(options.body) });
      if (url === '/sync/create/') {
        return okResponse({
          success: true,
          time_sync_id: INSTANCE_B,
          sync_quality: 'pending',
        });
      }
      return okResponse({
        success: true,
        time_sync_id: INSTANCE_B,
        sync_quality: 'valid',
      });
    },
  }));

  await collector.synchronizeClock({
    createUrl: '/sync/create/',
    finalizeUrl: '/sync/finalize/',
  });
  const event = await collector.record('item_rendered', {});

  assert.equal(requests[0].body.client_instance_id, INSTANCE_A);
  assert.equal(requests[1].body.client_instance_id, INSTANCE_A);
  assert.equal(requests[1].body.client_received_monotonic_ms, 180);
  assert.equal(event.time_sync_id, INSTANCE_B);
  assert.ok(!event.quality_flags.includes('clock_unsynchronized'));
});

test('hidden pages retain queued data unless a bounded lifecycle flush is requested', async () => {
  let uploads = 0;
  const collector = new ProcessEventCollector(eventPayload({
    isVisible: () => false,
    fetch: async (_url, options) => {
      uploads += 1;
      const body = JSON.parse(options.body);
      return okResponse({
        success: true,
        acknowledgements: body.events.map((event) => ({
          client_sequence_no: event.client_sequence_no,
          event_id: INSTANCE_B,
          server_ingest_sequence_no: 1,
        })),
      });
    },
  }));
  await collector.record('pagehide', { persisted: false });

  await collector.flush();
  assert.equal(uploads, 0);
  await collector.flush({ ignoreVisibility: true, keepalive: true });
  assert.equal(uploads, 1);
});


test('bounded navigation flush waits for pending durable event materialization', async () => {
  let materialize;
  const bodyReady = new Promise(resolve => { materialize = resolve; });
  const uploaded = [];
  const collector = new ProcessEventCollector(eventPayload({
    fetch: async (_url, options) => {
      const body = JSON.parse(options.body); uploaded.push(...body.events);
      return okResponse({ success: true, acknowledgements: body.events.map(e => ({ client_sequence_no: e.client_sequence_no })) });
    },
  }));
  const record = collector.record('item_left', {}, { materialize: () => bodyReady });
  const flushed = collector.flushBounded(1500, { ignoreVisibility: true, keepalive: true });
  await new Promise(resolve => setImmediate(resolve));
  materialize({ leave_reason: 'navigation' });
  await record;
  assert.equal(await flushed, true);
  assert.equal(uploaded.length, 1);
  assert.equal(await collector.outbox.count(), 0);
});

test('bounded navigation flush drains events added during an earlier ACK checkpoint', async () => {
  let completeCheckpoint, checkpointStarted;
  const started = new Promise(resolve => { checkpointStarted = resolve; });
  const pending = new Promise(resolve => { completeCheckpoint = resolve; });
  const uploaded = [];
  let checkpoints = 0;
  const collector = new ProcessEventCollector(eventPayload({
    checkpointEndpoint: '/checkpoint/',
    fetch: async (url, options) => {
      const body = JSON.parse(options.body);
      if (url === '/checkpoint/') {
        if (++checkpoints === 1) { checkpointStarted(); await pending; }
        return okResponse({ success: true });
      }
      uploaded.push(...body.events);
      return okResponse({ success: true, acknowledgements: body.events.map(e => ({ client_sequence_no: e.client_sequence_no })) });
    },
  }));
  await collector.record('item_left', { leave_reason: 'submit' });
  const background = collector.flush();
  await started;
  await collector.record('feedback_shown', { feedback_mode: 'correctness' });
  const leaving = collector.flushBounded(1500, { ignoreVisibility: true, keepalive: true });
  completeCheckpoint();
  await background;
  assert.equal(await leaving, true);
  assert.deepEqual(uploaded.map(e => e.event_type), ['item_left', 'feedback_shown']);
  assert.equal(await collector.outbox.count(), 0);
});

test('bounded keepalive uploads preserve the final ACK checkpoint during page departure', async () => {
  const checkpoints = [];
  const collector = new ProcessEventCollector(eventPayload({
    checkpointEndpoint: '/checkpoint/',
    fetch: async (url, options) => {
      const body = JSON.parse(options.body);
      if (url === '/checkpoint/') { checkpoints.push(options); return okResponse({ success: true }); }
      return okResponse({ success: true, acknowledgements: body.events.map(e => ({ client_sequence_no: e.client_sequence_no })) });
    },
  }));
  await collector.record('item_left', { leave_reason: 'submit' });
  await collector.flushBounded(1500, { ignoreVisibility: true, keepalive: true });
  assert.equal(checkpoints.length, 1);
  assert.equal(checkpoints[0].keepalive, true);
  assert.equal(JSON.parse(checkpoints[0].body).pending_event_count, 0);
});
