import { setImmediate } from 'node:timers'
import { TextEncoder } from 'node:util'
import assert from 'node:assert/strict';
import { test } from 'vitest';

import {
  ProcessEventCollector,
} from './process-sender';
import { MemoryOutbox, defaultOutbox } from './fact-store';
import { bindLifecycle } from './session-lifecycle';
import { backoffDelay, sanitizePayload } from './process-types';

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

test('a stale capture-v2 event link is dead-lettered without blocking the current client instance', async () => {
  const outbox = new MemoryOutbox();
  const bodies = [];
  const fetch = async (_url, options) => {
    const body = JSON.parse(options.body);
    bodies.push(body);
    if (body.client_instance_id === INSTANCE_A) {
      return {
        ok: false,
        status: 400,
        json: async () => ({
          success: false,
          error: { code: 'invalid_event_link' },
        }),
      };
    }
    return okResponse({
      success: true,
      acknowledgements: body.events.map((event) => ({
        client_sequence_no: event.client_sequence_no,
        event_id: INSTANCE_A,
        server_ingest_sequence_no: event.client_sequence_no,
      })),
    });
  };
  const stale = new ProcessEventCollector(eventPayload({
    outbox,
    endpoint: '/assessment/process/capture-v2/batch/',
    fetch,
    uuid: () => INSTANCE_A,
  }));
  await stale.record('document_visible', {});
  const current = new ProcessEventCollector(eventPayload({
    outbox,
    endpoint: '/assessment/process/capture-v2/batch/',
    fetch,
    uuid: () => INSTANCE_B,
  }));
  await current.record('item_rendered', { presentation_state: 'interactive' });

  assert.equal(await current.flush(), true);
  assert.deepEqual(
    bodies.map((body) => body.client_instance_id),
    [INSTANCE_A, INSTANCE_B],
  );
  assert.equal(await outbox.count(), 0);
  assert.equal((await outbox.captureState(INSTANCE_A)).failed, 1);
  assert.equal((await outbox.captureState(INSTANCE_B)).failed, 0);
});

test('a terminated capture-v2 task backlog is dead-lettered', async () => {
  const outbox = new MemoryOutbox();
  const collector = new ProcessEventCollector(eventPayload({
    outbox,
    endpoint: '/assessment/process/capture-v2/batch/',
    fetch: async () => ({
      ok: false,
      status: 409,
      json: async () => ({
        success: false,
        error: { code: 'task_terminated' },
      }),
    }),
  }));
  await collector.record('document_visible', {});

  assert.equal(await collector.flush(), true);
  assert.equal(await outbox.count(), 0);
  assert.equal((await outbox.captureState(INSTANCE_A)).failed, 1);
});

function eventPayload(overrides = {}) {
  const transport = overrides.fetch;
  if (transport) overrides = { ...overrides, fetch: async (...args) => {
    const response = await transport(...args);
    return { ...response, status: response.status ?? (response.ok ? 200 : 503),
      json: async () => { const body = await response.json(); return response.ok ? { data: body, meta: { request_id: 'test-request' } } : body; } };
  } };
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

test('concurrent bounded drains of the same durable queue share one flush', async () => {
  let releaseUpload;
  const pending = new Promise(resolve => { releaseUpload = resolve; });
  const collector = new ProcessEventCollector(eventPayload({
    fetch: async (_url, options) => {
      await pending;
      const body = JSON.parse(options.body);
      return okResponse({ success: true, acknowledgements: body.events.map(e => ({ client_sequence_no: e.client_sequence_no })) });
    },
  }));
  let loops = 0;
  const flushLoop = collector._flushLoop.bind(collector);
  collector._flushLoop = (...args) => { loops++; return flushLoop(...args); };
  await collector.record('item_left', { leave_reason: 'navigation' });
  const first = collector.flushBounded(1500, { ignoreVisibility: true, keepalive: true });
  const second = collector.flushBounded(1500, { ignoreVisibility: true, keepalive: true });
  releaseUpload();
  assert.deepEqual(await Promise.all([first, second]), [true, true]);
  assert.equal(loops, 1);
});

test('a timed-out bounded drain does not restart its wait for the same in-flight evidence', async () => {
  let releaseUpload;
  const pending = new Promise(resolve => { releaseUpload = resolve; });
  const timers = [];
  const collector = new ProcessEventCollector(eventPayload({
    setTimeout: (callback) => { timers.push(callback); return timers.length; }, clearTimeout: () => {},
    fetch: async (_url, options) => {
      await pending;
      const body = JSON.parse(options.body);
      return okResponse({ success: true, acknowledgements: body.events.map(e => ({ client_sequence_no: e.client_sequence_no })) });
    },
  }));
  await collector.record('item_left', { leave_reason: 'navigation' });
  const first = collector.flushBounded(1500, { ignoreVisibility: true, keepalive: true });
  await new Promise(resolve => setImmediate(resolve));
  timers[0]();
  assert.equal(await first, false);
  const second = collector.flushBounded(1500, { ignoreVisibility: true, keepalive: true });
  assert.equal(timers.length, 1);
  assert.equal(await second, false);
  releaseUpload();
  await collector.flushPromise;
  assert.equal(await collector.outbox.count(), 0);
});

test('a failed bounded drain retries the retained evidence on the next call', async () => {
  let uploads = 0;
  const collector = new ProcessEventCollector(eventPayload({
    fetch: async (_url, options) => {
      if (++uploads === 1) throw new Error('offline');
      const body = JSON.parse(options.body);
      return okResponse({ success: true, acknowledgements: body.events.map(e => ({ client_sequence_no: e.client_sequence_no })) });
    },
  }));
  await collector.record('item_left', { leave_reason: 'navigation' });
  assert.equal(await collector.flushBounded(1500), false);
  assert.equal(await collector.outbox.count(), 1);
  assert.equal(await collector.flushBounded(1500), true);
  assert.equal(uploads, 2);
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
  const background = collector.flushBounded(1500, { ignoreVisibility: true, keepalive: true });
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

test('concurrent checkpoints upload each immutable fact once and await their own confirmation', async () => {
  const bodies = [];
  const releases = [];
  const collector = new ProcessEventCollector(eventPayload({
    checkpointEndpoint: '/checkpoint/',
    fetch: async (_url, options) => {
      const body = JSON.parse(options.body);
      bodies.push(body);
      await new Promise(resolve => releases.push(resolve));
      return okResponse({ success: true, checkpoint_id: body.checkpoint_id });
    },
  }));
  const first = collector.checkpoint('heartbeat');
  const second = collector.checkpoint('navigation');
  let secondFinished = false;
  second.then(() => { secondFinished = true; });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(bodies.length, 1);
  releases[0]();
  const firstResult = await first;
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(secondFinished, false);
  assert.equal(bodies.length, 2);
  releases[1]();
  const secondResult = await second;
  assert.equal(firstResult.checkpoint_id, bodies[0].checkpoint_id);
  assert.equal(secondResult.checkpoint_id, bodies[1].checkpoint_id);
  assert.deepEqual(bodies.map(body => body.trigger), ['heartbeat', 'navigation']);
  assert.equal(new Set(bodies.map(body => body.checkpoint_id)).size, 2);
  assert.equal((await collector.outbox.pendingCheckpoints()).length, 0);
});

test('failed checkpoint confirmation retains its immutable fact and ACK marker for recovery', async () => {
  let succeeds = false;
  const bodies = [];
  const collector = new ProcessEventCollector(eventPayload({
    checkpointEndpoint: '/checkpoint/',
    fetch: async (url, options) => {
      const body = JSON.parse(options.body);
      if (url === '/checkpoint/') {
        bodies.push(body);
        return okResponse({ success: succeeds, checkpoint_id: body.checkpoint_id });
      }
      return okResponse({ success: true, acknowledgements: body.events.map(e => ({ client_sequence_no: e.client_sequence_no })) });
    },
  }));
  await collector.record('item_left', { leave_reason: 'navigation' });
  assert.equal(await collector.flushBounded(1500), false);
  assert.equal(collector.lastCheckpointedContiguousAck, 0);
  const failed = (await collector.outbox.pendingCheckpoints())[0];
  assert.equal(failed.checkpoint_id, bodies[0].checkpoint_id);
  succeeds = true;
  await collector.checkpoint('resume');
  assert.deepEqual(bodies[1], failed);
  assert.equal(collector.lastCheckpointedContiguousAck, 1);
  assert.equal((await collector.outbox.pendingCheckpoints()).length, 0);
});

test('bounded flush confirms its frozen sequence without draining later heartbeats', async () => {
  let releaseUpload;
  const gate = new Promise(resolve => { releaseUpload = resolve; });
  const bodies = [];
  const collector = new ProcessEventCollector(eventPayload({
    fetch: async (_url, options) => {
      const body = JSON.parse(options.body);
      bodies.push(body);
      await gate;
      return okResponse({ success: true, acknowledgements: body.events.map(e => ({ client_sequence_no: e.client_sequence_no })) });
    },
  }));
  await collector.record('item_left', { leave_reason: 'navigation' });
  const leaving = collector.flushBounded(1500);
  await new Promise(resolve => setImmediate(resolve));
  await collector.record('activity_heartbeat', { document_visible: true });
  releaseUpload();
  assert.equal(await leaving, true);
  assert.deepEqual(bodies.flatMap(body => body.events.map(e => e.client_sequence_no)), [1]);
  assert.equal(await collector.outbox.count(), 1);
});

test('foreground flush awaits the frozen confirmation after a short bounded wait expires', async () => {
  let releaseUpload;
  const gate = new Promise(resolve => { releaseUpload = resolve; });
  const bodies = [];
  const collector = new ProcessEventCollector(eventPayload({
    fetch: async (_url, options) => {
      const body = JSON.parse(options.body);
      bodies.push(body);
      await gate;
      return okResponse({ success: true, acknowledgements: body.events.map(e => ({ client_sequence_no: e.client_sequence_no })) });
    },
  }));
  await collector.record('item_left', { leave_reason: 'navigation' });
  assert.equal(await collector.flushBounded(10), false);
  try {
    const foreground = collector.flushConfirmed();
    await new Promise(resolve => setImmediate(resolve));
    await collector.record('activity_heartbeat', { document_visible: true });
    releaseUpload();
    assert.equal(await foreground, true);
    assert.deepEqual(bodies.flatMap(body => body.events.map(e => e.client_sequence_no)), [1]);
    assert.equal(await collector.outbox.count(), 1);
  } finally { releaseUpload(); }
});

test('concurrent foreground confirmations drain each frozen boundary after joining an older upload', async () => {
  let releaseUpload;
  const gate = new Promise(resolve => { releaseUpload = resolve; });
  const bodies = [];
  const collector = new ProcessEventCollector(eventPayload({
    fetch: async (_url, options) => {
      const body = JSON.parse(options.body);
      bodies.push(body);
      if (bodies.length === 1) await gate;
      return okResponse({ success: true, acknowledgements: body.events.map(e => ({ client_sequence_no: e.client_sequence_no })) });
    },
  }));
  await collector.record('activity_heartbeat', { document_visible: true });
  const background = collector.flush();
  await new Promise(resolve => setImmediate(resolve));
  await collector.record('research_tool_requested', { tool: 'doubt', stage: 'mark_added' });
  const first = collector.flushConfirmed();
  await new Promise(resolve => setImmediate(resolve));
  await collector.record('research_tool_requested', { tool: 'check', stage: 'started' });
  const second = collector.flushConfirmed();
  await new Promise(resolve => setImmediate(resolve));
  await collector.record('activity_heartbeat', { document_visible: true });
  releaseUpload();
  assert.deepEqual(await Promise.all([background, first, second]), [true, true, true]);
  assert.deepEqual(bodies.flatMap(body => body.events.map(e => e.client_sequence_no)), [1, 2, 3]);
  assert.equal(await collector.outbox.count(), 1);
});

test('bounded flush reports a rejected event and retains its failure evidence', async () => {
  const collector = new ProcessEventCollector(eventPayload({
    fetch: async (_url, options) => {
      const body = JSON.parse(options.body);
      return okResponse({ success: true, rejections: body.events.map(e => ({ client_sequence_no: e.client_sequence_no, permanent: true, code: 'invalid_event' })) });
    },
  }));
  await collector.record('item_left', { leave_reason: 'navigation' });
  assert.equal(await collector.flushBounded(1500), false);
  assert.equal(collector.outbox.deadLetters.length, 1);
});

test('bounded flush does not report success across a locally missing sequence', async () => {
  const collector = new ProcessEventCollector(eventPayload({
    fetch: async (_url, options) => {
      const body = JSON.parse(options.body);
      return okResponse({ success: true, acknowledgements: body.events.map(e => ({ client_sequence_no: e.client_sequence_no })) });
    },
  }));
  await collector.record('item_left', { leave_reason: 'navigation' });
  collector.outbox.records = [];
  assert.equal(await collector.flushBounded(1500), false);
  assert.deepEqual((await collector.outbox.captureState(collector.clientInstanceId)).missingLocalRanges, [[1, 1]]);
});

test('atomic submission freezes and persists all A to B to A events before any send', async () => {
  const collector = new ProcessEventCollector(eventPayload({ checkpointEndpoint: '/checkpoint/', fetch: async () => { throw new Error('must not upload'); } }));
  for (const value of ['A', 'B', 'A']) {
    await collector.record('response_transition', { after_state: { value } }, { eventId: globalThis.crypto.randomUUID() });
  }
  const intent = { token: INSTANCE_B, clickedAt: '2026-08-26T00:00:01.000Z', answer: 'A', head: 'head' };
  const frozen = await collector.freezeSubmissionSnapshot(intent);
  assert.deepEqual(await collector.outbox.restoreSubmissionIntent(7, 11), frozen);
  assert.equal(frozen.processSnapshot.event_boundary, 3);
  assert.deepEqual(frozen.processSnapshot.events.map(e => e.payload.after_state.value), ['A', 'B', 'A']);
  assert.equal(frozen.processSnapshot.checkpoints.length, 1);
  assert.equal(frozen.processSnapshot.checkpoints[0].trigger, 'commit');
  assert.equal(frozen.processSnapshot.checkpoints[0].highest_contiguous_acked_sequence_no, 0);
  await collector.record('activity_heartbeat', { document_visible: true });
  const refreshed = new ProcessEventCollector(eventPayload({ outbox: collector.outbox, uuid: () => INSTANCE_B }));
  assert.deepEqual(await refreshed.freezeSubmissionSnapshot(intent), frozen);
  assert.equal(await collector.outbox.count(), 4);
  await assert.rejects(refreshed.freezeSubmissionSnapshot({ ...intent, answer: 'B' }), /immutable/);
});

test('atomic submission rejects an oversized envelope without truncating pending facts', async () => {
  const collector = new ProcessEventCollector(eventPayload({ checkpointEndpoint: '/checkpoint/' }));
  for (let i = 0; i < 101; i++) await collector.record('document_visible', {});
  await assert.rejects(collector.freezeSubmissionSnapshot({ token: INSTANCE_B, answer: 'A' }), error => error.code === 'submission_snapshot_too_large');
  assert.equal(await collector.outbox.count(), 101);
});

test('atomic submission requires complete committed ACKs before deleting any evidence', async () => {
  const collector = new ProcessEventCollector(eventPayload({ checkpointEndpoint: '/checkpoint/' }));
  await collector.record('response_transition', { after_state: { value: 'A' } }, { eventId: INSTANCE_B });
  const frozen = await collector.freezeSubmissionSnapshot({ token: INSTANCE_B, answer: 'A' });
  const snapshot = frozen.processSnapshot;
  const response = { committed: true, acknowledgements: [{ client_sequence_no: 1, event_id: INSTANCE_B }], checkpoint_ids: snapshot.checkpoints.map(c => c.checkpoint_id) };
  for (const invalid of [
    { ...response, committed: false },
    { ...response, acknowledgements: [] },
    { ...response, acknowledgements: [{ client_sequence_no: 1, event_id: INSTANCE_A }] },
    { ...response, checkpoint_ids: [] },
  ]) {
    await assert.rejects(collector.acknowledgeSubmission(snapshot, invalid), /confirmation/);
    assert.equal(await collector.outbox.count(), 1);
    assert.equal((await collector.outbox.pendingCheckpoints()).length, 1);
  }
  await collector.record('document_visible', {});
  await collector.acknowledgeSubmission(snapshot, response);
  assert.equal(await collector.outbox.count(), 1);
  assert.equal((await collector.outbox.pendingCheckpoints()).length, 0);
  assert.equal((await collector.outbox.captureState(INSTANCE_A)).highestContiguousAcked, 1);
  assert.deepEqual((await collector.outbox.peekBatch()).events.map(event => event.client_sequence_no), [2]);
});

test('checkpoint confirmation stays single flight across old and replacement runtimes', async () => {
  const outbox = new MemoryOutbox();
  const bodies = [];
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const fetch = async (_url, options) => {
    const body = JSON.parse(options.body);
    bodies.push(body);
    await gate;
    return okResponse({ success: true, checkpoint_id: body.checkpoint_id });
  };
  const oldRuntime = new ProcessEventCollector(eventPayload({ outbox, checkpointEndpoint: '/checkpoint/', fetch }));
  const oldCheckpoint = oldRuntime.checkpoint('navigation');
  await new Promise(resolve => setImmediate(resolve));
  const replacement = new ProcessEventCollector(eventPayload({ outbox, checkpointEndpoint: '/checkpoint/', fetch, uuid: () => INSTANCE_B }));
  const resumed = replacement.checkpoint('resume');
  await new Promise(resolve => setImmediate(resolve));
  const concurrentBodies = [...bodies];
  release();
  await Promise.all([oldCheckpoint, resumed]);
  assert.equal(concurrentBodies.length, 1);
  assert.equal(bodies.length, 2);
  assert.equal(new Set(bodies.map(body => body.checkpoint_id)).size, 2);
});

test('atomic submission ACK cannot clean a snapshot different from the durable submission', async () => {
  const collector = new ProcessEventCollector(eventPayload({ checkpointEndpoint: '/checkpoint/' }));
  await collector.record('document_visible', {});
  const frozen = await collector.freezeSubmissionSnapshot({ token: INSTANCE_B, answer: 'A' });
  const altered = { ...frozen.processSnapshot, event_boundary: 2 };
  await assert.rejects(collector.acknowledgeSubmission(altered, {
    committed: true,
    acknowledgements: [{ client_sequence_no: 1 }],
    checkpoint_ids: altered.checkpoints.map(checkpoint => checkpoint.checkpoint_id),
  }), /durable submission/);
  assert.equal(await collector.outbox.count(), 1);
  assert.equal((await collector.outbox.pendingCheckpoints()).length, 1);
});

test('collector requests carry the selected operation UUID and a distinct request UUID', async () => {
  const requests = [];
  const collector = new ProcessEventCollector(eventPayload({
    checkpointEndpoint: '/checkpoint/',
    fetch: async (url, options) => {
      requests.push(options);
      const body = JSON.parse(options.body);
      return url === '/checkpoint/' ? okResponse({ success: true, checkpoint_id: body.checkpoint_id })
        : okResponse({ success: true, acknowledgements: body.events.map(event => ({ client_sequence_no: event.client_sequence_no })) });
    },
  }));
  collector.setOperationId(INSTANCE_B);
  await collector.record('document_visible', {});
  await collector.flush();
  assert.equal(requests.length, 2);
  assert.ok(requests.every(request => request.headers['X-KT-Operation-ID'] === INSTANCE_B));
  assert.equal(new Set(requests.map(request => request.headers['X-Request-ID'])).size, 2);
  collector.setOperationId(undefined);
  await collector.checkpoint('navigation');
  assert.equal(requests.at(-1).headers['X-KT-Operation-ID'], undefined);
});

test('bounded flush recovers pending checkpoints even with no new event ACK progress', async () => {
  let succeeds = false;
  const bodies = [];
  const collector = new ProcessEventCollector(eventPayload({
    checkpointEndpoint: '/checkpoint/',
    fetch: async (_url, options) => {
      const body = JSON.parse(options.body);
      bodies.push(body);
      return okResponse({ success: succeeds, checkpoint_id: body.checkpoint_id });
    },
  }));
  await collector.checkpoint('heartbeat');
  const original = (await collector.outbox.pendingCheckpoints())[0];
  assert.equal(await collector.flushBounded(1500), false);
  succeeds = true;
  assert.equal(await collector.flushBounded(1500), true);
  assert.equal((await collector.outbox.pendingCheckpoints()).length, 0);
  assert.ok(bodies.every(body => JSON.stringify(body) === JSON.stringify(original)));
});

test('atomic snapshot ownership prevents background transport while later heartbeat facts upload', async () => {
  const sent = [];
  const collector = new ProcessEventCollector(eventPayload({
    checkpointEndpoint: '/checkpoint/',
    fetch: async (url, options) => {
      const body = JSON.parse(options.body);
      sent.push({ url, body });
      return url === '/checkpoint/' ? okResponse({ success: true, checkpoint_id: body.checkpoint_id })
        : okResponse({ success: true, acknowledgements: body.events.map(event => ({ client_sequence_no: event.client_sequence_no })) });
    },
  }));
  await collector.record('response_transition', { after_state: { value: 'A' } }, { eventId: INSTANCE_B });
  const frozen = await collector.freezeSubmissionSnapshot({ token: INSTANCE_B, answer: 'A' });
  await collector.record('activity_heartbeat', { document_visible: true });
  await collector.checkpoint('heartbeat');
  assert.equal(await collector.flushBounded(1500), false);
  assert.deepEqual(sent.filter(request => request.body.events).flatMap(request => request.body.events.map(event => event.client_sequence_no)), [2]);
  assert.ok(!sent.some(request => request.body.checkpoint_id === frozen.processSnapshot.checkpoints[0].checkpoint_id));
  assert.equal(await collector.outbox.count(), 1);
  assert.equal((await collector.outbox.pendingCheckpoints()).length, 1);
});

test('atomic snapshot ownership survives replacement and explicit cancellation releases retained rows', async () => {
  const outbox = new MemoryOutbox();
  const collector = new ProcessEventCollector(eventPayload({ outbox, checkpointEndpoint: '/checkpoint/' }));
  await collector.record('document_visible', {});
  const frozen = await collector.freezeSubmissionSnapshot({ token: INSTANCE_B, answer: 'A' });
  const sent = [];
  const replacement = new ProcessEventCollector(eventPayload({
    outbox, uuid: () => INSTANCE_B, checkpointEndpoint: '/checkpoint/',
    fetch: async (url, options) => {
      const body = JSON.parse(options.body);
      sent.push({ url, body });
      return url === '/checkpoint/' ? okResponse({ success: true, checkpoint_id: body.checkpoint_id })
        : okResponse({ success: true, acknowledgements: body.events.map(event => ({ client_sequence_no: event.client_sequence_no })) });
    },
  }));
  await replacement.checkpoint('resume');
  assert.equal(await replacement.flushBounded(1500), false);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].body.trigger, 'resume');
  await assert.rejects(collector.acknowledgeSubmission(frozen.processSnapshot, { committed: false }), /confirmation/);
  await outbox.saveSubmissionIntent(7, 11, null);
  assert.equal(await outbox.count(), 1);
  assert.equal(await replacement.flushBounded(1500), true);
  assert.equal(sent.filter(request => request.body.events).length, 1);
  assert.ok(sent.some(request => request.body.checkpoint_id === frozen.processSnapshot.checkpoints[0].checkpoint_id));
});

test('freezing submission coordinates existing uploads before claiming pending evidence', async () => {
  let releaseUpload;
  const gate = new Promise(resolve => { releaseUpload = resolve; });
  const collector = new ProcessEventCollector(eventPayload({
    fetch: async (_url, options) => {
      const body = JSON.parse(options.body);
      await gate;
      return okResponse({ success: true, acknowledgements: body.events.map(event => ({ client_sequence_no: event.client_sequence_no })) });
    },
  }));
  await collector.record('document_visible', {});
  const upload = collector.flush();
  await new Promise(resolve => setImmediate(resolve));
  const freezing = collector.freezeSubmissionSnapshot({ token: INSTANCE_B, answer: 'A' });
  let frozen = false;
  freezing.then(() => { frozen = true; });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(frozen, false);
  releaseUpload();
  await upload;
  const snapshot = (await freezing).processSnapshot;
  assert.deepEqual(snapshot.events, []);
  assert.equal(snapshot.checkpoints[0].highest_contiguous_acked_sequence_no, 1);
});

test('replacing a durable submission with a plain intent cannot silently release its owned rows', async () => {
  const collector = new ProcessEventCollector(eventPayload());
  await collector.record('document_visible', {});
  const intent = { token: INSTANCE_B, answer: 'A' };
  await collector.freezeSubmissionSnapshot(intent);
  await assert.rejects(collector.outbox.saveSubmissionIntent(7, 11, intent), /immutable/);
  assert.equal(await collector.outbox.peekBatch(), null);
  await collector.outbox.saveSubmissionIntent(7, 11, null);
  assert.equal((await collector.outbox.peekBatch()).events.length, 1);
});

test('navigation freezes its evidence without waiting for an older upload or unrelated checkpoints', async () => {
  let releaseUpload;
  const gate = new Promise(resolve => { releaseUpload = resolve; });
  const collector = new ProcessEventCollector(eventPayload({
    checkpointEndpoint: '/checkpoint/',
    fetch: async (_url, options) => {
      const body = JSON.parse(options.body);
      await gate;
      return okResponse({ success: true, acknowledgements: body.events?.map(event => ({ client_sequence_no: event.client_sequence_no })) });
    },
  }));
  await collector.record('document_visible', {});
  const olderUpload = collector.flush();
  await new Promise(resolve => setImmediate(resolve));
  const left = await collector.record('item_left', { leave_reason: 'navigation', to_assignment_id: 17 },
    { eventId: INSTANCE_B, correlationId: INSTANCE_A });
  const intent = { token: INSTANCE_A, leaveEventId: left.event_id, toAssignmentId: 17, head: null };
  const frozen = await collector.freezeNavigationSnapshot(intent);
  assert.equal(frozen.processSnapshot.event_boundary, 2);
  assert.deepEqual(frozen.processSnapshot.events.map(event => event.client_sequence_no), [1, 2]);
  assert.equal(frozen.processSnapshot.checkpoints.at(-1).trigger, 'navigation');
  assert.deepEqual(await collector.outbox.restoreNavigationIntent(7, 11), frozen);
  assert.equal(await collector.outbox.peekBatch(), null, 'Claimed navigation evidence must remain owned until confirmation');
  releaseUpload();
  await olderUpload;
});

test('navigation confirmation validates every frozen event and checkpoint before deleting local facts', async () => {
  const collector = new ProcessEventCollector(eventPayload({ checkpointEndpoint: '/checkpoint/' }));
  const left = await collector.record('item_left', { leave_reason: 'navigation', to_assignment_id: 17 }, { eventId: INSTANCE_B });
  const frozen = await collector.freezeNavigationSnapshot({ token: INSTANCE_A, leaveEventId: left.event_id, toAssignmentId: 17 });
  await assert.rejects(collector.acknowledgeNavigation(frozen.processSnapshot, { committed: true }), /confirmation/);
  assert.equal(await collector.outbox.count(), 1);
  await collector.acknowledgeNavigation(frozen.processSnapshot, {
    navigation_token: INSTANCE_A, to_assignment_id: 17, writer_lease_released: true,
    committed: true, acknowledgements: [{ client_sequence_no: 1, event_id: INSTANCE_B }],
    checkpoint_ids: frozen.processSnapshot.checkpoints.map(row => row.checkpoint_id),
  });
  assert.equal(await collector.outbox.count(), 0);
  assert.deepEqual(await collector.outbox.pendingCheckpoints(), []);
});
