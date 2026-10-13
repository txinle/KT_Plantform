import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace as Row
from unittest.mock import patch
from uuid import UUID

from django.core.management.base import CommandError
from django.test import SimpleTestCase

from .management.commands.verify_school_browser_witness import js_json, js_digest, verify_witness, require_isolated_database


STAMP = "2026-10-11T00:00:00.000Z"
NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)
def uid(number):
    return str(UUID(int=number))


def fixture_rows():
    source = {key: str(index)*64 for index, key in enumerate(('source_sha256', 'frontend_sha256', 'config_sha256'), 1)}
    fixture = {'database': 'kt_e2e', 'run_id': 'private-browser-run', 'student_count': 3,
        'usernames': ['fixture-1', 'fixture-2', 'fixture-3'], 'task_id': 9, 'course_id': 8, 'class_id': 7,
        'experiment_id': 6, 'source_config': source}
    facts = {'task': Row(pk=9, experiment_id=6, experiment=Row(course_id=8), class_group_id=7),
        'sessions': [], 'events': [], 'checkpoints': [], 'attempts': [], 'visits': [], 'navigations': [], 'submissions': []}
    probes = []
    for number in range(1, 4):
        session, attempt, assignment = number, number+10, number+20
        client, event_id, checkpoint_id, navigation_id, entry_id, token = [uid(number*100+index) for index in range(1,7)]
        facts['sessions'].append(Row(pk=session, student_id=number, student=Row(username=f'fixture-{number}'),
            class_task_id=9, status='completed', terminal_at=NOW))
        payload = {'value': 1.0, 'tiny': 1e-7, 'label': '中文'}
        envelope = {'session_id': session, 'attempt_id': attempt, 'event_type': 'response_transition', 'event_version': 1,
            'client_sequence_no': 1, 'client_wall_time_utc': STAMP, 'client_monotonic_ms': 1.2345,
            'client_time_origin': STAMP, 'time_sync_id': None, 'correlation_id': None}
        event = Row(pk=event_id, student_id=number, client_instance_id=client,
            server_ingest_sequence_no=2, payload=payload, app_version='test', quality_flags=[], **envelope)
        event.client_wall_time_utc=NOW; event.client_time_origin=NOW; event.client_monotonic_ms=Decimal('1.235')
        facts['events'].append(event)
        witness = {'event_id': event_id, **envelope, 'app_version_sha256': js_digest('test'),
            'quality_flags_sha256': js_digest([]), 'payload_fields': sorted(payload), 'payload_sha256': js_digest(payload),
            'material_sha256': js_digest({'payload': payload, **envelope})}
        checkpoint = {'checkpoint_id': checkpoint_id, 'session_id': session, 'client_instance_id': client,
            'highest_emitted_sequence_no': 1, 'highest_acked_sequence_no': 1,
            'highest_contiguous_acked_sequence_no': 1, 'pending_event_count': 0, 'failed_event_count': 0,
            'missing_local_sequence_ranges': [], 'client_monotonic_ms': 1.2345,
            'client_wall_time_utc': STAMP, 'client_time_origin': STAMP, 'time_sync_id': None,
            'trigger': 'commit', 'quality_flags': [], 'app_version': 'test'}
        cp = Row(pk=checkpoint_id, student_id=number, **checkpoint)
        cp.client_monotonic_ms=Decimal('1.235'); cp.client_wall_time_utc=NOW; cp.client_time_origin=NOW
        facts['checkpoints'].append(cp)
        cp_witness = {key: value for key, value in checkpoint.items() if key not in ('app_version','quality_flags','missing_local_sequence_ranges')}
        cp_witness.update(app_version_sha256=js_digest('test'), quality_flags_sha256=js_digest([]),
            missing_local_sequence_ranges_sha256=js_digest([]), material_fields=sorted(checkpoint), material_sha256=js_digest(checkpoint))
        visit = Row(pk=number+30, session_id=session, attempt_id=attempt, visit_index=1,
            entry_reason='navigation_click', client_occurred_at=NOW)
        facts['visits'].append(visit)
        facts['events'].append(Row(pk=entry_id, student_id=number, session_id=session, attempt_id=attempt,
            assignment_id=assignment, event_type='item_entered', event_version=3, client_instance_id=None,
            client_sequence_no=None, server_ingest_sequence_no=1, correlation_id=navigation_id,
            client_wall_time_utc=NOW, client_time_origin=NOW, client_monotonic_ms=Decimal('1.235'),
            payload={'entry_reason': 'navigation_click', 'from_assignment_id': None, 'visit_id': visit.pk}, server_received_at=NOW))
        receipt = {'navigation_id': navigation_id, 'session_id': session, 'assignment_id': assignment,
            'attempt_id': attempt, 'visit_id': visit.pk, 'visit_index': 1, 'entry_event_id': entry_id, 'entered_at': STAMP}
        entry = {'navigation_id': navigation_id, 'reason': 'navigation_click', 'client_occurred_at': STAMP,
            'client_monotonic_ms': 1.2345, 'client_time_origin': STAMP, 'material_sha256': 'e'*64}
        ack = {'event_id': event_id, 'client_sequence_no': 1, 'server_ingest_sequence_no': 2}
        stored = {'committed': True, 'acknowledgements': [], 'checkpoint_ids': [],
            'final_response_event_id': event_id, 'response_version': 3}
        facts['attempts'].append(Row(pk=attempt, session_id=session, assignment_id=assignment, status='submitted',
            submission_token=token, final_response_event_id=event_id, active_response_event_id=event_id,
            submission_receipt=stored, response_version=3, submission_fingerprint='a'*64))
        facts['submissions'].append(Row(attempt_id=attempt, submission_token=token))
        requests = [
            {'path': f'/api/v1/student/sessions/{session}/questions/{assignment}/', 'entry': entry, 'entry_receipt': receipt,
             'status': 200, 'events': [], 'checkpoints': []},
            {'path': '/api/v1/assessment/process/capture-v2/batch/', 'client_instance_id': client,
             'events': [witness], 'checkpoints': [cp_witness], 'acknowledgements': [ack], 'checkpoint_ids': [checkpoint_id], 'status': 200},
            {'path': f'/api/v1/student/attempts/{attempt}/submit/', 'submission': {'token': token, 'target_event_id': event_id,
             'expected_version': 2, 'material_sha256': 'b'*64}, 'status': 200,
             'receipt': {'committed': True, 'final_response_event_id': event_id, 'response_version': 3,
              'persisted_material_sha256': js_digest(stored)}, 'events': [], 'checkpoints': [],
              'acknowledgements': [], 'checkpoint_ids': []},
        ]
        for index, request in enumerate(requests):
            request.update(request_index=index, material_sha256='c'*64, material_fields=[], rejection_count=0)
        probes.append({'completed': True, 'protocol_witness': {'schema_version': 1,
            'evidence_kind': 'real_browser_network_witness', 'candidate': copy.deepcopy(source), 'task_id': 9, 'probe': number,
            'session_ids': [session], 'requests': requests, 'settlement': {'complete': True,
                'pending_response_count': 0, 'unfinished_request_count': 0}}})
    for probe in probes:
        evidence=probe['protocol_witness']; session=evidence['session_ids'][0]
        client=uid(session*100+1)
        evidence['local_storage']={'schema_version':1, 'read_complete':True, 'scope_complete':True,
            'status':'observed', 'reason':None, 'snapshot_cutoff_utc':STAMP,
            'database':{'name':'kt-process-events-v1','version':2},
            'session_ids':[session], 'client_instance_ids':[client], 'events':[], 'checkpoints':[], 'intents':[],
            'streams':[{'client_instance_id':client,'highest_emitted_sequence_no':1,'highest_acked_sequence_no':1,
                'highest_contiguous_acked_sequence_no':1,'acknowledged_sequence_nos':[1],
                'pending_event_count':0,'failed_event_count':0,'missing_local_sequence_ranges':[]}],
            'boundary':{'event_count':0,'checkpoint_count':0,'intent_count':0,
                'pending_event_count':0,'failed_event_count':0,'unscoped_client_count':0}}
    return fixture, {'completed': True, 'probes': probes}, facts


class BrowserWitnessTests(SimpleTestCase):
    def confidence_browser_rows(self):
        plan={'scenario':'confidence-only-v1','confidence_scale_version':'v2',
            'confidence_scale':{'minimum':0,'maximum':100,'step':1}, 'students':[]}
        report={'probes':[]}; sessions=[]; attempts=[]; confidence=[]; events=[]
        for student in range(2):
            session=student+1; name=f'confidence-browser-{student}'
            sessions.append(Row(pk=session,student=Row(username=name)))
            plan['students'].append({'username':name,'role':'browser',
                'confidence_actions':['skip' if item%5==0 else 'fill' for item in range(20)],
                'confidence_values':[None if item%5==0 else 57 for item in range(20)]})
            probe={'protocol_witness':{'session_ids':[session],'requests':[]},
                'waterfall':[{'endpoint':'/api/v1/student/attempts/:id/submit/'}]}
            report['probes'].append(probe)
            for item in range(20):
                attempt=student*20+item+1; head=uid(5000+attempt); token=uid(6000+attempt)
                skipped=item%5==0; probability=None if skipped else 57; assignment=uid(7000+attempt)
                receipt={'committed':True,'acknowledgements':[],'checkpoint_ids':[],
                    'final_response_event_id':head,'response_version':1}
                attempts.append(Row(pk=attempt,session_id=session,assignment=Row(assigned_sequence_index=item+1),
                    final_response_event_id=head,active_response_event_id=head,submission_token=token,
                    submission_receipt=receipt,submission_fingerprint='a'*64))
                confidence.append(Row(pk=assignment,attempt_id=attempt,condition='prompt',
                    response_status='skipped' if skipped else 'committed',probability=probability,
                    prompt_shown_at=NOW,committed_at=None if skipped else NOW))
                probe['protocol_witness']['requests'].append({'path':f'/api/v1/student/attempts/{attempt}/submit/',
                    'status':200,'submission':{'token':token,'target_event_id':head,'material_sha256':'a'*64},
                    'receipt':{'committed':True,'final_response_event_id':head,'persisted_material_sha256':js_digest(receipt)}})
                for kind,payload,client in [('confidence_prompt_shown',{'confidence_assignment_id':assignment,'scale_version':'v2'},None),
                    ('confidence_skipped' if skipped else 'confidence_committed',{'confidence_assignment_id':assignment,
                        **({'reason_code':'student_skipped'} if skipped else {'probability':probability})},None),
                    *[('confidence_observed',{'confidence_assignment_id':assignment,'stage':stage,
                        'answer_event_id':head,'probability':None},uid(8000+attempt))
                        for stage in (['presented','skipped'] if skipped else ['presented'])]]:
                    events.append(Row(attempt_id=attempt,session_id=session,event_type=kind,payload=payload,client_instance_id=client))
        return plan,report,sessions,attempts,confidence,events

    def test_confidence_browser_gate_checks_all_forty_frozen_final_answer_associations(self):
        from .management.commands.verify_school_browser_witness import browser_confidence_checks
        rows=self.confidence_browser_rows()
        checks=browser_confidence_checks(*rows)
        self.assertTrue(all(checks.values()),checks)
        for mutation in (lambda r:setattr(r[4][1],'probability',58),
                lambda r:r[5].append(copy.deepcopy(r[5][0])),
                lambda r:r[5][2].payload.update(answer_event_id=uid(999)),
                lambda r:r[1]['probes'][0]['protocol_witness']['requests'][0]['receipt'].update(persisted_material_sha256='b'*64)):
            changed=copy.deepcopy(rows);mutation(changed)
            self.assertFalse(all(browser_confidence_checks(*changed).values()))

    def test_confidence_browser_zero_tools_uses_actual_journal_and_events_not_metrics(self):
        from .management.commands.verify_school_browser_witness import browser_confidence_checks
        for mutation in (lambda r:r[1]['probes'][0]['waterfall'].append({'endpoint':'/api/v1/assessment/process/capture-v2/attempts/:id/tools/'}),
                lambda r:r[1]['probes'][0]['protocol_witness']['requests'].append({'path':'/api/v1/assessment/process/capture-v2/attempts/1/resolve/'}),
                lambda r:r[5].append(Row(attempt_id=1,session_id=1,event_type='research_tool_requested',payload={},client_instance_id=uid(888))),
                lambda r:r[1]['probes'][0].pop('waterfall')):
            rows=self.confidence_browser_rows();rows[1]['probes'][0]['tools_request_count']=0;mutation(rows)
            self.assertFalse(all(browser_confidence_checks(*rows).values()))

    def test_confidence_browser_uncertain_submission_can_be_confirmed_by_same_material_replay(self):
        from .management.commands.verify_school_browser_witness import browser_confidence_checks
        rows=self.confidence_browser_rows()
        original=copy.deepcopy(rows[1]['probes'][0]['protocol_witness']['requests'][0])
        original.pop('receipt');original['status']=None
        rows[1]['probes'][0]['protocol_witness']['requests'].insert(0,original)
        self.assertTrue(all(browser_confidence_checks(*rows).values()))

    def test_explicit_natural_browser_subset_binds_two_of_a_larger_fixture(self):
        fixture, report, facts = fixture_rows()
        fixture.update(browser_usernames=fixture['usernames'][:2], item_count=20)
        report['workload_version']='natural-180-v1'
        report['probes']=report['probes'][:2]
        for key in ('sessions','attempts','events','checkpoints','visits','navigations','submissions'):
            facts[key]=[row for row in facts[key] if getattr(row,'session_id',getattr(row,'pk',None) if key=='sessions' else None) in (1,2)
                or key=='submissions' and row.attempt_id in (11,12)]
        result=verify_witness(fixture,report,**facts)
        self.assertTrue(result['evidence_binding_passed'],result)
        self.assertFalse(result['passed'])  # One synthetic response is not a twenty-item natural completion.

    def test_natural_behavior_counts_come_from_the_frozen_sparse_plan(self):
        from .management.commands.verify_school_browser_witness import natural_behavior_matches
        plan={'modify_items':[3,9], 'excursion_items':[5,15], 'optional_tool_items':[2,12],
            'item_dwell_seconds':[45]*20}
        actions=[{'action':kind,'sequence':item+1,'completed':True} for kind,key in (
            ('answer_modify','modify_items'),('navigation_next_ready','excursion_items'),
            ('navigation_return_ready','excursion_items'),('optional_tool','optional_tool_items')) for item in plan[key]]
        probe={'natural_plan':plan,'actions':actions,'submitted_count':20,
            'think_waits':[{'item':i,'started_at':0,'completed_at':45,'planned_seconds':45} for i in range(20)]}
        attempts=[Row(pk=i,status='submitted',final_response_event_id=uid(i+1)) for i in range(20)]
        events=[Row(event_type='response_transition',attempt_id=i%20) for i in range(22)]
        self.assertTrue(natural_behavior_matches(probe,attempts,events))
        probe['actions'].append({'action':'navigation_next_ready','sequence':1,'completed':True})
        self.assertFalse(natural_behavior_matches(probe,attempts,events))

    def verify(self, report_change=None, facts_change=None):
        fixture, report, facts = fixture_rows()
        if report_change: report_change(report)
        if facts_change: facts_change(facts)
        return verify_witness(fixture, report, **facts)

    def test_js_numeric_unicode_and_sorted_key_vectors(self):
        vectors = [(1.0, '1'), (1e-7, '1e-7'), (1e-6, '0.000001'), (1e20, '100000000000000000000'),
            (-0.0, '0'), ({'z': '中文', 'a': [1.0, -0.0]}, '{"a":[1,0],"z":"中文"}'),
            ({'10': 1, '2': 2, 'a': 3}, '{"2":2,"10":1,"a":3}')]
        for value, serialized in vectors:
            self.assertEqual(js_json(value), serialized)
            self.assertEqual(js_digest(value), hashlib.sha256(serialized.encode()).hexdigest())

    def test_complete_actual_browser_material_matches_model_shaped_rows(self):
        result = self.verify()
        self.assertTrue(result['passed'], result)
        self.assertTrue(result['known_fact_integrity_passed'])
        self.assertTrue(result['workflow_completed'])

    def test_no_empty_or_simulated_evidence_can_pass(self):
        for mutation in [lambda r: r.update(probes=[]),
                lambda r: r['probes'][0]['protocol_witness'].update(requests=[]),
                lambda r: r['probes'][0]['protocol_witness'].update(evidence_kind='http_protocol_persistence')]:
            self.assertFalse(self.verify(mutation)['passed'])

    def test_candidate_session_student_and_attempt_boundaries(self):
        cases = [lambda r: r['probes'][0]['protocol_witness']['candidate'].update(source_sha256='0'*64),
            lambda r: r['probes'][1]['protocol_witness'].update(session_ids=[1]),
            lambda r: r['probes'][0]['protocol_witness']['requests'][1]['events'][0].update(attempt_id=12)]
        for mutation in cases:
            self.assertFalse(self.verify(mutation)['passed'])
        self.assertFalse(self.verify(facts_change=lambda f: setattr(f['sessions'][0].student, 'username', 'outside'))['passed'])

    def test_event_every_material_field_and_extra_enrichment_are_checked(self):
        mutations = {'event_version': 2, 'client_monotonic_ms': Decimal('1.234'), 'client_time_origin': NOW.replace(hour=1),
            'correlation_id': uid(888), 'app_version': 'changed', 'quality_flags': ['changed'],
            'payload': {'value': 1, 'tiny': 1e-7, 'label': '中文', 'unexpected': 1}}
        for field, value in mutations.items():
            self.assertFalse(self.verify(facts_change=lambda f: setattr(f['events'][0], field, value))['known_fact_integrity_passed'], field)
        result = self.verify(facts_change=lambda f: f['events'][0].payload.update(before_snapshot_id=uid(555)))
        self.assertTrue(result['passed'])

    def test_checkpoint_all_fields_and_missing_ranges_are_checked(self):
        for field, value in {'pending_event_count': 1, 'highest_emitted_sequence_no': 2, 'trigger': 'resume',
                'client_instance_id': uid(888), 'quality_flags': ['changed'], 'app_version': 'changed',
                'missing_local_sequence_ranges': [[2, 3]]}.items():
            self.assertFalse(self.verify(facts_change=lambda f: setattr(f['checkpoints'][0], field, value))['known_fact_integrity_passed'], field)

    def test_acked_missing_is_corruption_but_unacked_missing_is_unknown(self):
        self.assertFalse(self.verify(facts_change=lambda f: f['checkpoints'].pop(0))['known_fact_integrity_passed'])
        def unack(r):
            row=r['probes'][0]['protocol_witness']['requests'][1]
            row['checkpoint_ids']=[]
            row['acknowledgements']=[]
            row['status']=None
        result=self.verify(unack, lambda f: f['checkpoints'].pop(0))
        self.assertTrue(result['known_fact_integrity_passed'], result)
        self.assertFalse(result['passed'])
        self.assertEqual(result['counts']['unknown_checkpoints_missing'], 1)

    def test_extra_client_rows_and_replay_material_or_ingest_changes_fail(self):
        def replay(report):
            requests=report['probes'][0]['protocol_witness']['requests']
            row=copy.deepcopy(requests[1]); row['request_index']=len(requests); requests.append(row)
        self.assertTrue(self.verify(replay)['passed'])
        for field, value in [('material_sha256', '0'*64), ('server_ingest_sequence_no', 99)]:
            def changed(r):
                replay(r)
                row=r['probes'][0]['protocol_witness']['requests'][-1]
                target=row['events'][0] if field=='material_sha256' else row['acknowledgements'][0]
                target[field]=value
            self.assertFalse(self.verify(changed)['known_fact_integrity_passed'])
        self.assertFalse(self.verify(facts_change=lambda f: f['events'].append(copy.copy(f['events'][0])))['known_fact_integrity_passed'])

    def test_entry_and_submission_receipts_are_persisted_immutable_subsets(self):
        self.assertFalse(self.verify(facts_change=lambda f: setattr(f['visits'][0], 'visit_index', 2))['known_fact_integrity_passed'])
        self.assertFalse(self.verify(facts_change=lambda f: f['attempts'][0].submission_receipt.update(response_version=9))['known_fact_integrity_passed'])
        self.assertFalse(self.verify(facts_change=lambda f: f['submissions'].pop(0))['known_fact_integrity_passed'])
        def change(r): r['probes'][0]['protocol_witness']['requests'][2]['receipt']['success']=True
        self.assertTrue(self.verify(change)['passed'])

    def test_same_iso_instant_is_accepted_and_incomplete_is_not_corruption(self):
        def change(r):
            r['probes'][0]['protocol_witness']['requests'][1]['events'][0]['client_wall_time_utc']='2026-10-11T08:00:00+08:00'
            r['probes'][0]['completed']=False
            r['completed']=False
        result=self.verify(change)
        self.assertTrue(result['known_fact_integrity_passed'])
        self.assertFalse(result['workflow_completed'])
        self.assertFalse(result['passed'])

    def test_database_guard_requires_explicit_settings_and_actual_isolated_pg(self):
        fake=Row(vendor='postgresql', settings_dict={'NAME':'kt_e2e'})
        cursor=Row(execute=lambda sql: None, fetchone=lambda: ('production',))
        from contextlib import nullcontext
        fake.cursor=lambda: nullcontext(cursor)
        with patch.dict(os.environ, {'POSTGRES_DB': 'kt_e2e'}), patch('apps.experiments.management.commands.verify_school_browser_witness.connection', fake):
            with self.assertRaises(CommandError): require_isolated_database()
        with patch.dict(os.environ, {'POSTGRES_DB': 'production'}), patch('apps.experiments.management.commands.verify_school_browser_witness.connection', fake):
            with self.assertRaises(CommandError): require_isolated_database()

    def test_empty_or_unbound_evidence_is_not_a_raw_database_corruption_claim(self):
        result=self.verify(lambda r: r.update(probes=[]))
        self.assertFalse(result['passed'])
        self.assertFalse(result['raw_fact_integrity_failed'])
        self.assertFalse(result['evidence_binding_passed'])

    def test_event_and_checkpoint_unacked_missing_and_changed_unknown_material(self):
        def unack(r):
            requests=r['probes'][0]['protocol_witness']['requests']
            row=copy.deepcopy(requests[1])
            row['request_index']=len(requests)
            row['events'][0]['event_id']=uid(888)
            row['events'][0]['client_sequence_no']=2
            row['events'][0]['material_sha256']='0'*64
            row['acknowledgements']=[]; row['checkpoint_ids']=[]; row['status']=None
            requests[1]['checkpoint_ids']=[]
            requests.append(row)
        result=self.verify(unack, lambda f: f['checkpoints'].pop(0))
        self.assertTrue(result['known_fact_integrity_passed'], result)
        self.assertEqual(result['counts']['unknown_events_missing'], 1)
        self.assertFalse(result['passed'])
        self.assertFalse(self.verify(unack, lambda f: setattr(f['events'][0], 'event_version', 9))['known_fact_integrity_passed'])

    def test_navigation_receipt_material_and_token_replay(self):
        fixture, report, facts=fixture_rows()
        session=facts['sessions'][0]; attempt=facts['attempts'][0]
        token=uid(900); leave_id=uid(901); client=uid(101)
        destination=44
        facts['attempts'].append(Row(pk=55, session_id=session.pk, assignment_id=destination))
        receipt={'success': True, 'committed': True, 'navigation_token': token,
            'acknowledgements': [], 'checkpoint_ids': [], 'response_version': 2,
            'active_response_event_id': str(attempt.final_response_event_id), 'saved_at': STAMP,
            'status': 'in_progress', 'writer_lease_released': True, 'to_assignment_id': destination,
            'next_url': f'/student/sessions/{session.pk}/questions/{destination}/'}
        facts['navigations'].append(Row(attempt_id=attempt.pk, token=token, receipt=receipt))
        left=Row(pk=leave_id, student_id=session.student_id, session_id=session.pk, attempt_id=attempt.pk,
            event_type='item_left', event_version=1, client_instance_id=client, client_sequence_no=2,
            server_ingest_sequence_no=3, client_wall_time_utc=NOW, client_time_origin=NOW,
            client_monotonic_ms=Decimal('2'), time_sync_id=None, correlation_id=token,
            app_version='test', quality_flags=[], payload={'leave_reason':'navigation','to_assignment_id':destination})
        facts['events'].append(left)
        event={'event_id':leave_id, 'session_id':session.pk,'attempt_id':attempt.pk,'event_type':'item_left','event_version':1,
            'client_sequence_no':2,'client_wall_time_utc':STAMP,'client_time_origin':STAMP,'client_monotonic_ms':2,
            'time_sync_id':None,'correlation_id':token,'app_version_sha256':js_digest('test'),'quality_flags_sha256':js_digest([]),
            'payload_fields':sorted(left.payload),'payload_sha256':js_digest(left.payload),'material_sha256':'d'*64}
        request={'path': f'/api/v1/student/attempts/{attempt.pk}/draft/','request_index':3,'material_sha256':'c'*64,
            'client_instance_id':client,'events':[event],'checkpoints':[],'status':200,
            'acknowledgements':[{'event_id':leave_id,'client_sequence_no':2,'server_ingest_sequence_no':3}], 'checkpoint_ids':[],
            'navigation':{'token':token,'leave_event_id':leave_id,'target_event_id':str(attempt.final_response_event_id),
                'to_assignment_id':destination,'expected_version':1,'material_sha256':'a'*64},
            'receipt':{'committed':True,'persisted_material_sha256':js_digest(receipt)}}
        requests=report['probes'][0]['protocol_witness']['requests']; requests.append(request)
        report['probes'][0]['protocol_witness']['local_storage']['streams'][0].update(
            highest_emitted_sequence_no=2,highest_acked_sequence_no=2,highest_contiguous_acked_sequence_no=2,acknowledged_sequence_nos=[1,2])
        result=verify_witness(fixture,report,**facts)
        self.assertTrue(result['passed'],result)
        replay=copy.deepcopy(request); replay['request_index']=4; requests.append(replay)
        self.assertTrue(verify_witness(fixture,report,**facts)['passed'])
        for field,value in [('response_version',9),('writer_lease_released',False),('to_assignment_id',999)]:
            original=receipt[field]; receipt[field]=value
            self.assertFalse(verify_witness(fixture,report,**facts)['known_fact_integrity_passed'])
            receipt[field]=original
        replay['navigation']['material_sha256']='0'*64
        self.assertFalse(verify_witness(fixture,report,**facts)['known_fact_integrity_passed'])

    def test_unacknowledged_submission_then_successful_same_token_replay(self):
        def replay(r):
            requests=r['probes'][0]['protocol_witness']['requests']
            acked=copy.deepcopy(requests[2]); acked['request_index']=3
            requests[2]['receipt']=None; requests[2]['status']=None
            requests.append(acked)
        result=self.verify(replay)
        self.assertTrue(result['passed'], result)

    def test_command_is_explicit_actual_pg_read_only_and_outputs_no_private_facts(self):
        from contextlib import nullcontext
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from unittest.mock import MagicMock
        from contextlib import ExitStack
        from .management.commands import verify_school_browser_witness as module
        fixture, report, facts=fixture_rows()
        fake=MagicMock(vendor='postgresql', settings_dict={'NAME':'kt_e2e'})
        fake.cursor.return_value.__enter__.return_value.fetchone.return_value=('kt_e2e',)
        models={'ClassExperimentTask':facts['task'],'ExperimentSession':facts['sessions'],
            'AssessmentEvent':facts['events'],'ClientCaptureCheckpoint':facts['checkpoints'],
            'Attempt':facts['attempts'],'QuestionVisitEvent':facts['visits'],
            'NavigationReceipt':facts['navigations'],'SubmissionEvent':facts['submissions'],
            'StudentQuestionAssignment':[Row(pk=row.assignment_id,session_id=row.session_id) for row in facts['attempts']]}
        with TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'POSTGRES_DB':'kt_e2e'}))
            stack.enter_context(patch.object(module,'connection',fake))
            stack.enter_context(patch.object(module.transaction,'atomic',lambda: nullcontext()))
            for model, rows in models.items():
                manager=stack.enter_context(patch.object(getattr(module,model),'objects'))
                if model=='ClassExperimentTask': manager.select_related.return_value.filter.return_value.first.return_value=rows
                elif model=='ExperimentSession':
                    manager.filter.return_value.exclude.return_value.exists.return_value=False
                    manager.filter.return_value.filter.return_value.select_related.return_value.__iter__.return_value=iter(rows)
                else: manager.filter.return_value.__iter__.return_value=iter(rows)
            manifest=Path(directory)/'fixture.json'; browser=Path(directory)/'browser.json'; output=Path(directory)/'result.json'
            manifest.write_text(json.dumps(fixture),encoding='utf-8'); browser.write_text(json.dumps(report),encoding='utf-8')
            stream=StringIO(); module.Command(stdout=stream).handle(manifest=str(manifest),browser_report=str(browser),output=str(output))
            result=json.loads(output.read_text(encoding='utf-8')); self.assertTrue(result['passed'], result)
            statements=[call.args[0] for call in fake.cursor.return_value.__enter__.return_value.execute.call_args_list]
            self.assertEqual(statements,['SELECT current_database()', 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'])
            for name in fixture['usernames']: self.assertNotIn(name,stream.getvalue())
            self.assertNotIn('中文',stream.getvalue())

    def test_extra_checkpoint_and_tampered_entry_or_submission_replay_fail(self):
        def extra(f):
            checkpoint=copy.copy(f['checkpoints'][0]); checkpoint.pk=uid(999); f['checkpoints'].append(checkpoint)
        self.assertFalse(self.verify(facts_change=extra)['known_fact_integrity_passed'])
        for operation, index in [('entry',0),('submission',2)]:
            def changed(r):
                requests=r['probes'][0]['protocol_witness']['requests']
                replay=copy.deepcopy(requests[index]); replay['request_index']=3
                replay[operation]['material_sha256']='0'*64; requests.append(replay)
            self.assertFalse(self.verify(changed)['known_fact_integrity_passed'])

    def test_database_guard_rejects_wrong_vendor_or_settings_before_any_cursor(self):
        for vendor, name in [('sqlite','kt_e2e'),('postgresql','production')]:
            from unittest.mock import MagicMock
            fake=MagicMock(vendor=vendor, settings_dict={'NAME':name})
            with patch.dict(os.environ, {'POSTGRES_DB':'kt_e2e'}), patch('apps.experiments.management.commands.verify_school_browser_witness.connection',fake):
                with self.assertRaises(CommandError): require_isolated_database()
            fake.cursor.assert_not_called()

    def test_null_candidate_cannot_leak_untrusted_values_or_pass(self):
        fixture, report, facts=fixture_rows()
        fixture['source_config']={'source_sha256':{'private':'should-not-be-output'}}
        result=verify_witness(fixture,report,**facts)
        self.assertFalse(result['passed'])
        self.assertNotIn('should-not-be-output', json.dumps(result))

    def test_network_pass_does_not_certify_unsent_indexeddb_material(self):
        result=self.verify(lambda r: [probe['protocol_witness'].pop('local_storage') for probe in r['probes']])
        self.assertFalse(result['passed'])
        self.assertTrue(result['network_witness_passed'])
        self.assertEqual(result['local_storage_boundary']['unsent_indexeddb_events'], 'unknown')
        self.assertEqual(result['local_storage_boundary']['unsent_indexeddb_checkpoints'], 'unknown')
        self.assertIsNone(result['local_storage_boundary']['unsent_material_count'])
        self.assertFalse(result['full_browser_capture_chain_passed'])

    def test_missing_local_storage_cannot_pass_the_full_acceptance_command(self):
        result=self.verify(lambda r: [probe['protocol_witness'].pop('local_storage') for probe in r['probes']])
        self.assertTrue(result['network_witness_passed'])
        self.assertFalse(result['full_browser_capture_chain_passed'])
        self.assertFalse(result['passed'])

    def test_complete_readonly_storage_boundary_and_network_can_pass_full_chain(self):
        result=self.verify()
        self.assertTrue(result['local_storage_read_complete'],result)
        self.assertTrue(result['local_boundary_reconciled'],result)
        self.assertTrue(result['full_browser_capture_chain_passed'],result)
        self.assertTrue(result['passed'],result)

    def test_storage_unreadable_unscoped_and_hidden_sequence_gaps_never_full_pass(self):
        for mutation in [lambda r: r['probes'][0]['protocol_witness']['local_storage'].update(read_complete=False),
            lambda r: r['probes'][0]['protocol_witness']['local_storage'].update(scope_complete=False),
            lambda r: r['probes'][0]['protocol_witness']['local_storage']['boundary'].update(unscoped_client_count=1),
            lambda r: r['probes'][0]['protocol_witness']['local_storage']['streams'][0].update(highest_emitted_sequence_no=2),
            lambda r: r['probes'][0]['protocol_witness']['local_storage']['streams'][0].update(acknowledged_sequence_nos=[1,2]),
            lambda r: r['probes'][0]['protocol_witness']['local_storage']['boundary'].update(event_count=1)]:
            result=self.verify(mutation)
            self.assertFalse(result['full_browser_capture_chain_passed'],result)
            self.assertFalse(result['passed'])
            self.assertFalse(result['raw_fact_integrity_failed'])

    def add_unsent(self,report):
        evidence=report['probes'][0]['protocol_witness']; local=evidence['local_storage']
        event=copy.deepcopy(evidence['requests'][1]['events'][0]);event.update(event_id=uid(777),client_sequence_no=2,
            client_instance_id=evidence['requests'][1]['client_instance_id'], delivery_status='pending',material_sha256='7'*64)
        local['events']=[event];local['boundary'].update(event_count=1,pending_event_count=1)
        local['streams'][0].update(highest_emitted_sequence_no=2,pending_event_count=1)
        return event

    def test_unsent_unacknowledged_event_missing_is_known_local_material_not_corruption(self):
        result=self.verify(self.add_unsent)
        self.assertTrue(result['local_storage_read_complete'],result)
        self.assertTrue(result['local_boundary_reconciled'],result)
        self.assertTrue(result['known_fact_integrity_passed'],result)
        self.assertFalse(result['raw_fact_integrity_failed'])
        self.assertEqual(result['counts']['local_events_unacknowledged_missing'],1)
        self.assertFalse(result['passed'])

    def test_unsent_present_material_is_checked_and_acked_eviction_is_allowed(self):
        fixture,report,facts=fixture_rows();self.add_unsent(report)
        event=copy.copy(facts['events'][0]);event.pk=uid(777);event.client_sequence_no=2;event.server_ingest_sequence_no=3
        facts['events'].append(event)
        result=verify_witness(fixture,report,**facts)
        self.assertTrue(result['known_fact_integrity_passed'],result)
        self.assertEqual(result['counts']['local_events_unacknowledged_persisted'],1)
        self.assertFalse(result['passed'])
        event.app_version='changed'
        self.assertFalse(verify_witness(fixture,report,**facts)['known_fact_integrity_passed'])
        self.assertTrue(self.verify()['full_browser_capture_chain_passed'])

    def test_local_intent_same_material_confirmed_receipt_and_unknown_material(self):
        def intent(r):
            evidence=r['probes'][0]['protocol_witness'];request=evidence['requests'][0];local=evidence['local_storage']
            local['intents']=[{'kind':'entry','session_id':1,'attempt_id':11,'assignment_id':21,
                'token':request['entry']['navigation_id'],'delivery_status':'unconfirmed','document_sha256':'a'*64,
                'material_sha256':request['entry']['material_sha256'],'material':copy.deepcopy(request['entry']),
                'snapshot':None}]
            local['boundary']['intent_count']=1
        result=self.verify(intent)
        self.assertTrue(result['full_browser_capture_chain_passed'],result)
        def unknown(r):
            intent(r);r['probes'][0]['protocol_witness']['local_storage']['intents'][0].update(material=None,material_sha256=None)
        result=self.verify(unknown)
        self.assertFalse(result['passed'])
        self.assertFalse(result['raw_fact_integrity_failed'])
        self.assertEqual(result['counts']['local_intents_unknown_material'],1)

    def test_pending_storage_snapshot_then_later_observed_ack_does_not_corrupt(self):
        def pending(r):
            evidence=r['probes'][0]['protocol_witness'];local=evidence['local_storage']
            event=copy.deepcopy(evidence['requests'][1]['events'][0]);event.update(client_instance_id=uid(101),delivery_status='pending')
            local['events']=[event];local['boundary'].update(event_count=1,pending_event_count=1)
            local['streams'][0].update(acknowledged_sequence_nos=[],highest_acked_sequence_no=0,
                highest_contiguous_acked_sequence_no=0,pending_event_count=1)
        result=self.verify(pending)
        self.assertTrue(result['passed'],result)
        self.assertEqual(result['counts']['local_events_acknowledged'],1)

    def test_post_snapshot_network_event_is_reconciled_without_changing_snapshot_highwater(self):
        fixture,report,facts=fixture_rows()
        evidence=report['probes'][0]['protocol_witness'];request=copy.deepcopy(evidence['requests'][1])
        request.update(request_index=3,sent_at='2026-10-11T00:00:01.000Z',checkpoint_ids=[],checkpoints=[])
        request['events'][0].update(event_id=uid(777),client_sequence_no=2,material_sha256='7'*64)
        request['acknowledgements'][0].update(event_id=uid(777),client_sequence_no=2,server_ingest_sequence_no=3)
        evidence['requests'].append(request)
        event=copy.copy(facts['events'][0]);event.pk=uid(777);event.client_sequence_no=2;event.server_ingest_sequence_no=3
        facts['events'].append(event)
        result=verify_witness(fixture,report,**facts)
        self.assertTrue(result['passed'],result)

    def test_snapshot_cutoff_is_required_and_unsent_count_excludes_observed_replays(self):
        result=self.verify(lambda r:r['probes'][0]['protocol_witness']['local_storage'].pop('snapshot_cutoff_utc'))
        self.assertFalse(result['passed'],result)
        self.assertFalse(result['raw_fact_integrity_failed'])
        def retained(r):
            evidence=r['probes'][0]['protocol_witness'];local=evidence['local_storage']
            checkpoint=copy.deepcopy(evidence['requests'][1]['checkpoints'][0]);checkpoint['delivery_status']='pending'
            local['checkpoints']=[checkpoint];local['boundary']['checkpoint_count']=1
        result=self.verify(retained)
        self.assertTrue(result['passed'],result)
        self.assertEqual(result['local_storage_boundary']['unsent_material_count'],0)

    def test_unsent_checkpoint_and_failed_event_missing_are_unacknowledged_not_corruption(self):
        def pending(r):
            event=self.add_unsent(r);event['delivery_status']='failed'
            evidence=r['probes'][0]['protocol_witness'];local=evidence['local_storage']
            local['boundary'].update(pending_event_count=0,failed_event_count=1,checkpoint_count=1)
            local['streams'][0].update(pending_event_count=0,failed_event_count=1)
            checkpoint=copy.deepcopy(evidence['requests'][1]['checkpoints'][0])
            checkpoint.update(checkpoint_id=uid(778),delivery_status='pending',material_sha256='8'*64)
            local['checkpoints']=[checkpoint]
        result=self.verify(pending)
        self.assertTrue(result['local_boundary_reconciled'],result)
        self.assertFalse(result['raw_fact_integrity_failed'])
        self.assertEqual(result['counts']['local_events_unacknowledged_missing'],1)
        self.assertEqual(result['counts']['local_checkpoints_unacknowledged_missing'],1)
        self.assertFalse(result['passed'])

    def test_retained_intent_snapshot_event_and_checkpoint_boundaries_are_required(self):
        def intent(r):
            evidence=r['probes'][0]['protocol_witness'];request=evidence['requests'][2];local=evidence['local_storage']
            local['intents']=[{'kind':'submission','session_id':1,'attempt_id':11,
                'token':request['submission']['token'],'delivery_status':'unconfirmed','document_sha256':'a'*64,
                'material_sha256':request['submission']['material_sha256'],'material':copy.deepcopy(request['submission']),
                'snapshot':{'client_instance_id':uid(101),'event_boundary':1,
                    'events':copy.deepcopy(evidence['requests'][1]['events']),
                    'checkpoints':copy.deepcopy(evidence['requests'][1]['checkpoints'])}}]
            local['boundary']['intent_count']=1
        self.assertTrue(self.verify(intent)['passed'])
        for boundary in (None,-1,0,2):
            def changed(r):
                intent(r);r['probes'][0]['protocol_witness']['local_storage']['intents'][0]['snapshot']['event_boundary']=boundary
            result=self.verify(changed)
            self.assertFalse(result['passed'],result)
            self.assertFalse(result['raw_fact_integrity_failed'])

    def test_real_probe_numbering_is_one_to_three(self):
        self.assertTrue(self.verify()['passed'])
        def zero_based(r):
            for probe in r['probes']:
                probe['protocol_witness']['probe']-=1
        result=self.verify(zero_based)
        self.assertFalse(result['evidence_binding_passed'])
        self.assertFalse(result['passed'])

    def without_emitted_event_ids(self, report):
        for probe in report['probes']:
            for request in probe['protocol_witness']['requests']:
                for event in request['events']:
                    event.pop('event_id',None)

    def test_server_assigned_uuid_is_bound_by_client_and_sequence_with_optional_emitted_id(self):
        self.assertTrue(self.verify(self.without_emitted_event_ids)['passed'])
        def retained(r):
            self.without_emitted_event_ids(r)
            evidence=r['probes'][0]['protocol_witness'];local=evidence['local_storage']
            event=copy.deepcopy(evidence['requests'][1]['events'][0])
            event.update(event_id=None,client_instance_id=uid(101),delivery_status='pending')
            local['events']=[event];local['boundary'].update(event_count=1,pending_event_count=1)
            local['streams'][0].update(pending_event_count=1)
        result=self.verify(retained)
        self.assertTrue(result['passed'],result)
        self.assertEqual(result['counts']['local_events_acknowledged'],1)

    def test_ack_uuid_must_equal_durable_pk_without_an_emitted_uuid(self):
        def changed(r):
            self.without_emitted_event_ids(r)
            r['probes'][0]['protocol_witness']['requests'][1]['acknowledgements'][0]['event_id']=uid(999)
        result=self.verify(changed)
        self.assertTrue(result['evidence_binding_passed'],result)
        self.assertFalse(result['known_fact_integrity_passed'])
        self.assertTrue(result['raw_fact_integrity_failed'])

    def test_optional_emitted_uuid_and_duplicate_sequence_material_are_verified(self):
        def changed_id(r):
            r['probes'][0]['protocol_witness']['requests'][1]['events'][0]['event_id']=uid(999)
        result=self.verify(changed_id)
        self.assertTrue(result['evidence_binding_passed'],result)
        self.assertFalse(result['known_fact_integrity_passed'])
        def replay(r):
            self.without_emitted_event_ids(r)
            requests=r['probes'][0]['protocol_witness']['requests'];row=copy.deepcopy(requests[1])
            row['request_index']=len(requests);requests.append(row)
        self.assertTrue(self.verify(replay)['passed'])
        def divergent(r):
            replay(r);r['probes'][0]['protocol_witness']['requests'][-1]['events'][0]['material_sha256']='0'*64
        self.assertFalse(self.verify(divergent)['known_fact_integrity_passed'])

    def test_local_unsent_uuid_absent_keeps_missing_and_persisted_unacknowledged_separate(self):
        fixture,report,facts=fixture_rows();self.without_emitted_event_ids(report)
        event=self.add_unsent(report);event.pop('event_id')
        result=verify_witness(fixture,report,**facts)
        self.assertTrue(result['local_boundary_reconciled'],result)
        self.assertTrue(result['known_fact_integrity_passed'])
        self.assertFalse(result['raw_fact_integrity_failed'])
        self.assertEqual(result['counts']['local_events_unacknowledged_missing'],1)
        actual=copy.copy(facts['events'][0]);actual.pk=uid(777);actual.client_sequence_no=2;actual.server_ingest_sequence_no=3
        facts['events'].append(actual)
        result=verify_witness(fixture,report,**facts)
        self.assertTrue(result['known_fact_integrity_passed'],result)
        self.assertEqual(result['counts']['local_events_unacknowledged_persisted'],1)
        self.assertFalse(result['passed'])

    def test_entry_decimal_string_compares_numerically_without_rehashing_serialization(self):
        def decimal_string(r):
            for probe in r['probes']:
                entry=probe['protocol_witness']['requests'][0]['entry']
                entry['client_monotonic_ms']='1.235'
                entry['material_sha256']=js_digest({key:value for key,value in entry.items() if key!='material_sha256'})
        self.assertTrue(self.verify(decimal_string)['passed'])
        def divergent(r):
            decimal_string(r)
            requests=r['probes'][0]['protocol_witness']['requests'];row=copy.deepcopy(requests[0])
            row['request_index']=len(requests);row['entry']['client_monotonic_ms']=1.235
            row['entry']['material_sha256']=js_digest({key:value for key,value in row['entry'].items() if key!='material_sha256'})
            requests.append(row)
        self.assertFalse(self.verify(divergent)['known_fact_integrity_passed'])

    def test_missing_original_entry_clock_is_invalid_evidence_not_database_corruption(self):
        for field in ('client_monotonic_ms','client_occurred_at','client_time_origin'):
            def unavailable(r):
                self.without_emitted_event_ids(r)
                r['probes'][0]['protocol_witness']['requests'][0]['entry'][field]=None
            result=self.verify(unavailable)
            self.assertFalse(result['passed'])
            self.assertFalse(result['evidence_binding_passed'],result)
            self.assertFalse(result['raw_fact_integrity_failed'])
            self.assertEqual(result['status'],'invalid_evidence')
        def supplied_mismatch(r):
            r['probes'][0]['protocol_witness']['requests'][0]['entry']['client_monotonic_ms']='2.000'
        result=self.verify(supplied_mismatch)
        self.assertTrue(result['evidence_binding_passed'])
        self.assertTrue(result['raw_fact_integrity_failed'])

    def test_event_and_ack_identity_require_client_uuid_and_positive_integer_sequence(self):
        for field,value in [('client_sequence_no',0),('client_sequence_no',True),('client_sequence_no','1')]:
            def malformed(r):
                self.without_emitted_event_ids(r)
                r['probes'][0]['protocol_witness']['requests'][1]['events'][0][field]=value
            self.assertFalse(self.verify(malformed)['passed'])
        for value in (0,True,'1'):
            def malformed_ack(r):
                self.without_emitted_event_ids(r)
                r['probes'][0]['protocol_witness']['requests'][1]['acknowledgements'][0]['client_sequence_no']=value
            self.assertFalse(self.verify(malformed_ack)['passed'])
