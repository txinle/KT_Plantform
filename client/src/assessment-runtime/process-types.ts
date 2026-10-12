import type { DurableIntent } from './capture-contract'
export const MAX_BATCH_SIZE = 100;
const MAX_BACKOFF_MS = 60000;
export const DEFAULT_SYNC_INTERVAL_MS = 15 * 60 * 1000;
export const PERMANENT_CAPTURE_V2_REJECTIONS = new Set([
  'invalid_event_link',
  'task_terminated',
  'deadline_expired',
  'course_access_revoked',
]);
export const V2_EVENT_TYPES = new Set([
  'item_rendered', 'item_left', 'response_state_changed', 'text_edit_observed',
]);

const PAYLOAD_FIELDS: Readonly<Record<string, string[]>> = Object.freeze({
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

export function clone<T>(value: T): T {
  if (typeof structuredClone === 'function') return structuredClone(value);
  return JSON.parse(JSON.stringify(value));
}

export function submissionOwnership(intents: Array<DurableIntent | null>) {
  const events = new Set<string>();
  const checkpoints = new Set<string>();
  for (const intent of intents) {
    const snapshot = intent?.processSnapshot;
    if (!snapshot) continue;
    snapshot.events.forEach((event) => events.add(`${snapshot.client_instance_id}:${event.client_sequence_no}`));
    snapshot.checkpoints.forEach((checkpoint) => checkpoints.add(checkpoint.checkpoint_id));
  }
  return { events, checkpoints };
}

export function sanitizePayload(eventType: string, payload: Record<string, unknown>) {
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

export function backoffDelay(failureCount: number) {
  const exponent = Math.max(0, Number(failureCount) || 0);
  return Math.min(MAX_BACKOFF_MS, 1000 * (2 ** exponent));
}

export function normalizeMonotonic(value: number) {
  return Math.round(Number(value) * 1000) / 1000;
}

export function missingSequenceRanges(highestEmitted: number, knownSequences: Iterable<number>) {
  const known = new Set(knownSequences);
  const ranges = [];
  let start: number | null = null;
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
