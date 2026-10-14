"""Documentation-only shapes for the existing domain functions, never runtime validators."""
from ..atomic_submission_services import MAX_BYTES, MAX_CHECKPOINTS, MAX_EVENTS
from ..event_batch_services import CLIENT_BATCH_EVENT_TYPES, EVENT_REQUIRED_FIELDS
from ..models import ClientCaptureCheckpoint
from ..recording_capture_services import CHECKPOINT_FIELDS


def obj(properties, required=(), *, exact=True, description=None):
    schema = {'type': 'object', 'properties': properties, 'additionalProperties': not exact}
    if required:
        schema['required'] = sorted(required)
    if description:
        schema['description'] = description
    return schema


def array(items, **limits):
    return {'type': 'array', 'items': items, **limits}


def nullable(schema):
    return {**schema, 'nullable': True}


UUID = {'type': 'string', 'format': 'uuid'}
TIME = {'type': 'string', 'format': 'date-time', 'description': 'Timezone-aware timestamp.'}
STRING = {'type': 'string'}
BOOLEAN = {'type': 'boolean'}
POSITIVE = {'type': 'integer', 'minimum': 1}
COUNTER = {'type': 'integer', 'minimum': 0}
DECIMAL = {'oneOf': [{'type': 'number', 'minimum': 0}, {'type': 'string'}],
           'description': 'Finite nonnegative decimal, validated by the existing domain parser.'}
ANSWER = {'description': 'Question-type response JSON; the frozen question domain validates its shape.'}
SUCCESS = {'type': 'boolean', 'enum': [True]}
ERROR = obj({'error': obj({'code': STRING, 'message': STRING, 'fields': obj({}, exact=False),
                          'request_id': STRING}, ['code', 'message', 'fields', 'request_id'])}, ['error'])

EVENT = obj({
    'event_id': UUID, 'event_type': {'type': 'string', 'enum': sorted(CLIENT_BATCH_EVENT_TYPES)},
    'event_version': POSITIVE, 'session_id': POSITIVE, 'attempt_id': nullable(POSITIVE),
    'client_sequence_no': POSITIVE, 'correlation_id': nullable(UUID),
    'client_wall_time_utc': TIME, 'client_monotonic_ms': DECIMAL, 'client_time_origin': TIME,
    'time_sync_id': nullable(UUID), 'quality_flags': array(STRING), 'app_version': {'type': 'string', 'maxLength': 64},
    'payload': {'type': 'object', 'additionalProperties': True,
        'description': 'Versioned payload validated by apps.assessment.event_schema_registry; '
                       'the authoritative JSON schemas are assessment/event_schemas/<event_type>/v<event_version>.json.'},
}, EVENT_REQUIRED_FIELDS)
EVENT_BATCH = obj({'client_instance_id': UUID, 'events': array(EVENT, minItems=1, maxItems=MAX_EVENTS)},
                  ['client_instance_id', 'events'])
SEQUENCE_RANGE = array(POSITIVE, minItems=2, maxItems=2)
CHECKPOINT = obj({
    'checkpoint_id': UUID, 'session_id': POSITIVE, 'client_instance_id': UUID,
    **{name: COUNTER for name in ('highest_emitted_sequence_no', 'highest_acked_sequence_no',
          'highest_contiguous_acked_sequence_no', 'pending_event_count', 'failed_event_count')},
    'missing_local_sequence_ranges': array(SEQUENCE_RANGE), 'client_monotonic_ms': DECIMAL,
    'client_wall_time_utc': TIME, 'client_time_origin': TIME, 'time_sync_id': nullable(UUID),
    'trigger': {'type': 'string', 'enum': ClientCaptureCheckpoint.Trigger.values},
    'quality_flags': array(STRING), 'app_version': {'type': 'string', 'maxLength': 64},
}, CHECKPOINT_FIELDS)
BUNDLE = obj({
    'version': {'type': 'integer', 'enum': [1]}, 'client_instance_id': UUID,
    'event_boundary': COUNTER, 'events': array(EVENT, maxItems=MAX_EVENTS),
    'checkpoints': array(CHECKPOINT, maxItems=MAX_CHECKPOINTS), 'expected_version': COUNTER,
}, ['version', 'client_instance_id', 'event_boundary', 'events', 'checkpoints', 'expected_version'],
   description=f'Bounded durable evidence bundle; complete mutation body at most {MAX_BYTES} bytes. '
               'Event/session/attempt links, boundary and checkpoint counters are domain-validated.')
ACK = obj({'client_sequence_no': POSITIVE, 'event_id': UUID, 'server_ingest_sequence_no': POSITIVE},
          ['client_sequence_no', 'event_id', 'server_ingest_sequence_no'])
ACKS = array(ACK)
CAPTURE_BUNDLE = obj({name: shape for name, shape in BUNDLE['properties'].items() if name != 'expected_version'},
    ['version', 'client_instance_id', 'event_boundary', 'events', 'checkpoints'],
    description=f'Original bounded capture facts; maximum complete body {MAX_BYTES} bytes. '
                'Batch facts share one owned session; draft/tool facts belong to the current attempt or session.')
CAPTURE_RECEIPT = {'committed': SUCCESS, 'acknowledgements': ACKS, 'checkpoint_ids': array(UUID)}
REJECTION = obj({'client_sequence_no': {'description': 'Original rejected client sequence value, possibly absent/null or malformed.'},
                'code': STRING, 'permanent': BOOLEAN},
                ['client_sequence_no', 'code', 'permanent'])
LEASE_INPUT = {'writer_lease_token': nullable(UUID), 'writer_client_instance_id': nullable(UUID)}
CONFIDENCE = {'nullable': True, 'oneOf': [
    obj({'status': {'type': 'string', 'enum': ['skipped']}, 'answer_event_id': nullable(UUID),
         'probability': {'enum': [None]}}, ['status']),
    obj({'status': {'type': 'string', 'enum': ['committed']}, 'probability': {'type': 'integer'},
         'answer_event_id': nullable(UUID)},
        ['status', 'probability']),
], 'description': 'Only allowed for a server-assigned confidence prompt; probability uses its frozen scale.'}

DRAFT = obj({'answer': ANSWER, 'client_occurred_at': nullable(TIME), 'target_event_id': nullable(UUID),
             'expected_version': COUNTER, 'capture_bundle': CAPTURE_BUNDLE, 'capture_operation_id': UUID,
             **LEASE_INPUT}, exact=False,
             description='capture_operation_id is required with capture_bundle; it binds the original durable draft receipt.')
DRAFT['not'] = {'required': ['save_and_leave']}
DRAFT['allOf'] = [{'anyOf': [
    {'not': {'required': ['capture_bundle']}}, {'required': ['capture_operation_id']},
]}]
NEXT_ENTRY = obj({'assignment_id': POSITIVE, 'reason': {'type': 'string', 'enum': ['navigation_click', 'resume']},
    'navigation_id': UUID, 'client_occurred_at': TIME, 'client_monotonic_ms': DECIMAL, 'client_time_origin': TIME},
    ['assignment_id', 'reason', 'navigation_id', 'client_occurred_at', 'client_monotonic_ms', 'client_time_origin'],
    description='Frozen target entry for a session that selected transition protocol v1 at creation. '
                'Executed after the source mutation commits, using a separate transaction and current deadline checks.')
NEXT_ENTRY_RESULT = {'oneOf': [
    obj({'committed': SUCCESS, 'runtime': {'$ref': '#/components/schemas/StudentRuntimeQuestionEntry'}}, ['committed', 'runtime']),
    obj({'committed': {'type': 'boolean', 'enum': [False]},
         'error': obj({'code': STRING, 'message': STRING}, ['code', 'message'])}, ['committed', 'error']),
], 'description': 'The target outcome is independent of the confirmed source receipt. '
                  'Retry only the original frozen target material after partial success.'}
NAVIGATION = obj({
    'navigation_token': UUID, 'leave_event_id': UUID, 'target_event_id': nullable(UUID),
    'to_assignment_id': POSITIVE, 'client_clicked_at': nullable(TIME), **LEASE_INPUT,
    'save_and_leave': BUNDLE, 'next_entry': NEXT_ENTRY,
}, ['navigation_token', 'leave_event_id', 'target_event_id', 'to_assignment_id', 'save_and_leave'])
DRAFT_RECEIPT = obj({
    **CAPTURE_RECEIPT,
    'success': SUCCESS, 'saved_at': TIME, 'response_version': COUNTER,
    'active_response_event_id': nullable(UUID), 'response_snapshot_id': UUID,
    'content_hash': STRING, 'content_length': COUNTER, 'state_hash': STRING,
    'response_state_after': {'nullable': True, 'type': 'object', 'additionalProperties': True},
}, ['success', 'saved_at', 'response_version', 'active_response_event_id'])
NAVIGATION_RECEIPT = obj({
    'success': SUCCESS, 'committed': SUCCESS, 'navigation_token': UUID, 'acknowledgements': ACKS,
    'checkpoint_ids': array(UUID), 'response_version': COUNTER, 'active_response_event_id': nullable(UUID),
    'saved_at': nullable(TIME), 'status': STRING, 'writer_lease_released': SUCCESS, 'next_entry': NEXT_ENTRY_RESULT,
    'to_assignment_id': POSITIVE, 'next_url': STRING,
}, ['success', 'committed', 'navigation_token', 'acknowledgements', 'checkpoint_ids', 'response_version',
    'active_response_event_id', 'saved_at', 'status', 'writer_lease_released', 'to_assignment_id', 'next_url'])
SUBMISSION = obj({'submission_token': UUID, 'target_event_id': nullable(UUID),
                  'client_clicked_at': nullable(TIME), 'confidence': CONFIDENCE, **LEASE_INPUT},
                 ['submission_token'], exact=False)
SUBMISSION['not'] = {'required': ['atomic_submission']}
ATOMIC_SUBMISSION = obj({**SUBMISSION['properties'], 'target_event_id': UUID, 'atomic_submission': BUNDLE},
                       ['submission_token', 'target_event_id', 'atomic_submission'])
SUBMISSION_RECEIPT = obj({
    'success': SUCCESS, 'status': STRING, 'next_url': STRING, 'confidence_required': {'type': 'boolean', 'enum': [False]},
    'feedback_mode': STRING, 'feedback_phase': STRING,
    'is_correct': nullable(BOOLEAN), 'correct_answer': ANSWER, 'explanation': STRING,
}, ['success', 'status', 'next_url', 'confidence_required', 'feedback_mode', 'feedback_phase'],
   description='Feedback fields appear only when the server policy permits them.')
ATOMIC_SUBMISSION_RECEIPT = obj({**SUBMISSION_RECEIPT['properties'], 'committed': SUCCESS,
    'acknowledgements': ACKS, 'checkpoint_ids': array(UUID), 'final_response_event_id': UUID,
    'response_version': COUNTER},
    [*SUBMISSION_RECEIPT['required'], 'committed', 'acknowledgements', 'checkpoint_ids',
     'final_response_event_id', 'response_version'],
    description='Receipt returned only after the atomic evidence/response transaction commits; '
                'feedback fields appear only when the server policy permits them.')
NEXT_RECEIPT = obj({'success': SUCCESS, 'next_url': STRING}, ['success', 'next_url'])


def spec(request, success=None, *, status=200, errors=(), description='', body_required=True):
    responses = {code: (ERROR, message) for code, message in (
        (401, 'Authentication required.'), (403, 'Role/CSRF authorization rejected.'),
        (405, 'Only POST is allowed.'))}
    responses.update({code: (ERROR, 'Rejected by the existing domain contract.') for code in errors})
    if success is not None:
        responses[status] = (success, 'Confirmed domain response; wire envelope is added once by middleware.')
    return {'request': request, 'responses': responses, 'description': description,
            'body_required': body_required}


MUTATIONS = {
    'save_draft': spec({'oneOf': [DRAFT, NAVIGATION]}, {'oneOf': [DRAFT_RECEIPT, NAVIGATION_RECEIPT]},
        errors=(400, 404, 409, 413), body_required=False,
        description='Save a draft or atomically save-and-leave. navigation_token binds immutable material; '
                    'a confirmed retry returns the persisted navigation receipt. Domain CAS and writer lease remain authoritative.'),
    'submit': spec({'oneOf': [SUBMISSION, ATOMIC_SUBMISSION]},
        {'oneOf': [SUBMISSION_RECEIPT, ATOMIC_SUBMISSION_RECEIPT]},
        errors=(400, 404, 409, 413), description='Submit the existing frozen response; optionally ingest the bounded '
        'atomic_submission evidence in the same durable transaction. submission_token/material replay preserves the original receipt.'),
    'skip': spec(None, NEXT_RECEIPT, errors=(404, 409), body_required=False,
                 description='Skip using the existing server-authorized attempt transition.'),
    'finish': spec(None, NEXT_RECEIPT, errors=(404,), body_required=False,
                   description='Finish the owned session using the existing domain service.'),
}

SYNC_RECEIPT = obj({'success': SUCCESS, 'time_sync_id': UUID, 'server_received_at': TIME,
    'server_responded_at': TIME, 'sync_quality': STRING, 'estimated_clock_offset_ms': nullable({'type': 'integer'}),
    'estimated_rtt_ms': nullable(COUNTER)},
    ['success', 'time_sync_id', 'server_received_at', 'server_responded_at', 'sync_quality',
     'estimated_clock_offset_ms', 'estimated_rtt_ms'])
LEASE_RECEIPT = obj({'success': SUCCESS, 'lease_token': UUID, 'client_instance_id': UUID, 'expires_at': TIME},
                    ['success', 'lease_token', 'client_instance_id', 'expires_at'])
CLOSE_BUNDLE = obj({'lease_token': UUID, 'client_instance_id': UUID, 'capture_bundle': CAPTURE_BUNDLE,
    'capture_operation_id': UUID, 'leave_event_id': UUID, 'next_entry': NEXT_ENTRY},
    ['lease_token', 'client_instance_id', 'capture_bundle', 'capture_operation_id', 'leave_event_id'])
CLOSE_RECEIPT = obj({'success': SUCCESS, **CAPTURE_RECEIPT, 'capture_operation_id': UUID,
    'writer_lease_released': SUCCESS, 'status': STRING, 'response_version': COUNTER,
    'active_response_event_id': nullable(UUID), 'final_response_event_id': nullable(UUID), 'next_entry': NEXT_ENTRY_RESULT},
    ['success', 'committed', 'acknowledgements', 'checkpoint_ids', 'capture_operation_id',
     'writer_lease_released', 'status', 'response_version', 'active_response_event_id', 'final_response_event_id'])
PROCESS_ERRORS = (400, 409, 413, 415)
TOOL_INPUT = obj({'tool': STRING, 'stage': STRING, 'operation_id': UUID, 'data': obj({}, exact=False),
                  'answer_event_id': nullable(UUID), 'initiation_type': STRING, 'capture_bundle': CAPTURE_BUNDLE}, ['tool', 'stage', 'operation_id'])
TOOL_RESULT = obj({'success': SUCCESS, 'event_id': UUID, 'content_version': STRING, 'text': STRING, **CAPTURE_RECEIPT},
                  ['success', 'event_id', 'content_version'])
TOOLS_AVAILABLE = obj({'success': SUCCESS, 'state': obj({'doubt': BOOLEAN, 'pending_check': BOOLEAN,
    'checking': BOOLEAN, 'doubt_note': STRING}, ['doubt', 'pending_check', 'checking', 'doubt_note']),
    'doubt_enabled': BOOLEAN, 'check_enabled': BOOLEAN, 'kc_visible': BOOLEAN, 'content_version': STRING,
    'hints': array(obj({'id': STRING, 'level': COUNTER}, ['id', 'level'])),
    'rules': array(obj({}, exact=False)), 'confidence_scale': obj({}, exact=False)},
    ['success', 'state', 'doubt_enabled', 'check_enabled', 'kc_visible', 'content_version', 'hints', 'rules'])

PROCESS = {
    'research_tools': spec({'oneOf': [obj({}), TOOL_INPUT]}, {'oneOf': [TOOLS_AVAILABLE, TOOL_RESULT]},
        errors=(400, 404, 409, 413, 415), description='Empty JSON object reads authorized tools/rules; action JSON '
        'records the existing research-tool operation. Optional capture_bundle commits original evidence with the action. '
        'Frozen tool configuration validates stage and data.'),
    'resolve_branch': spec(obj({'candidate_event_ids': array(UUID), 'selected_event_id': UUID,
        'expected_version': COUNTER, 'operation_id': UUID, **LEASE_INPUT},
        ['candidate_event_ids', 'selected_event_id', 'expected_version', 'operation_id']),
        obj({'success': SUCCESS, 'response_version': COUNTER, 'active_response_event_id': UUID,
             'answer': ANSWER}, ['success', 'response_version', 'active_response_event_id', 'answer']),
        errors=(409,), description='Resolve the existing response branch using domain CAS and lease rules.'),
    'capture_v2_batch': spec({'oneOf': [EVENT_BATCH, obj({'capture_bundle': CAPTURE_BUNDLE}, ['capture_bundle'])]},
        obj({'success': SUCCESS, 'acknowledgements': ACKS, 'rejections': array(REJECTION), **CAPTURE_RECEIPT},
            ['success', 'acknowledgements', 'rejections']), errors=PROCESS_ERRORS,
        description=f'Ingest capture-v2 raw facts under the existing idempotency and lock rules; maximum body {MAX_BYTES} bytes.'),
    'event_batch': spec(EVENT_BATCH, obj({'success': SUCCESS, 'acknowledgements': ACKS,
        'rejections': array(REJECTION), 'highest_received_sequence_no': COUNTER,
        'highest_contiguous_received_sequence_no': COUNTER, 'missing_sequence_ranges': array(SEQUENCE_RANGE)},
        ['success', 'acknowledgements', 'rejections', 'highest_received_sequence_no',
         'highest_contiguous_received_sequence_no', 'missing_sequence_ranges']), errors=PROCESS_ERRORS,
        description='Ingest 1–100 legacy process events with per-event rejections and sequence receipts; maximum body 262144 bytes.'),
    'capture_checkpoint': spec(CHECKPOINT, obj({'success': SUCCESS, 'checkpoint_id': UUID, 'server_received_at': TIME},
        ['success', 'checkpoint_id', 'server_received_at']), status=201, errors=(400, 413, 415),
        description='Record the original bounded capture checkpoint, including idempotency and counter invariants.'),
    'time_sync_create': spec(obj({'session_id': POSITIVE, 'client_instance_id': UUID,
        'client_wall_time_utc': TIME, 'client_monotonic_ms': DECIMAL, 'client_time_origin': TIME},
        ['session_id', 'client_instance_id', 'client_wall_time_utc', 'client_monotonic_ms', 'client_time_origin']),
        SYNC_RECEIPT, status=201, errors=(400, 413, 415), description='Create the existing pending clock-correlation sample.'),
    'time_sync_finalize': spec(obj({'time_sync_id': UUID, 'client_instance_id': UUID,
        'client_received_wall_time_utc': TIME, 'client_received_monotonic_ms': DECIMAL},
        ['time_sync_id', 'client_instance_id', 'client_received_wall_time_utc', 'client_received_monotonic_ms']),
        SYNC_RECEIPT, errors=PROCESS_ERRORS, description='Finalize the owned clock-correlation sample.'),
    'writer_lease_acquire': spec(obj({'client_instance_id': UUID}, ['client_instance_id']),
        LEASE_RECEIPT, status=201, errors=PROCESS_ERRORS, description='Acquire the owned attempt writer lease.'),
    'writer_lease_renew': spec(obj({'lease_token': UUID, 'client_instance_id': UUID}, ['lease_token', 'client_instance_id']),
        LEASE_RECEIPT, errors=PROCESS_ERRORS, description='Renew the matching attempt writer lease.'),
    'writer_lease_release': spec({'oneOf': [obj({'lease_token': UUID, 'client_instance_id': UUID},
        ['lease_token', 'client_instance_id']), CLOSE_BUNDLE]},
        {'oneOf': [obj({'success': SUCCESS}, ['success']), CLOSE_RECEIPT]}, errors=PROCESS_ERRORS,
        description='Release the matching lease; optional close bundle commits original recording evidence, '
                    'checkpoints and release together. capture_operation_id replays its immutable durable receipt.'),
    'confidence_prompt_shown': spec(obj({}), obj({'success': SUCCESS, 'response_status': STRING},
        ['success', 'response_status']), errors=PROCESS_ERRORS, description='Mark the server-assigned confidence prompt as shown.'),
    'confidence_commit': spec(None, errors=(409,), body_required=False,
        description='Always rejects with final_confirmation_required. Confidence is confirmed in the formal submit request.'),
    'confidence_skip': spec(None, errors=(409,), body_required=False,
        description='Always rejects with final_confirmation_required. Confidence is confirmed in the formal submit request.'),
}
