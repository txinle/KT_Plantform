import sys
import unittest
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

class NaturalWorkloadTests(unittest.TestCase):
    def plan(self, n=180):
        from natural_workload import build_workload
        return build_workload({'student_count': n, 'item_count': 20, 'task_id': 31,
            'usernames': [f'u{i:03}' for i in range(n)]}, seed=20261013)

    def test_plan_is_frozen_deterministic_and_partitioned(self):
        plan = self.plan()
        self.assertEqual(plan, self.plan())
        self.assertEqual(plan['workload_version'], 'natural-180-v1')
        self.assertEqual(Counter(s['role'] for s in plan['students']), {'browser': 2, 'protocol': 178})
        self.assertEqual(plan['students'][0]['username'], 'u000')

    def test_exact_selection_and_speed_distributions(self):
        students = self.plan()['students']
        self.assertEqual(Counter(s['speed_stratum'] for s in students), {'fast':72,'medium':81,'slow':27})
        for field, count in [('modify_items',54),('excursion_items',36),('optional_tool_items',36)]:
            self.assertEqual(sum(bool(s[field]) for s in students), count)
            self.assertTrue(all(not s[field] or len(set(s[field])) == 2 for s in students))
        self.assertTrue(all(max(s['excursion_items'], default=0) < 19 for s in students))

    def test_all_timings_are_bounded_and_every_item_has_a_dwell(self):
        bounds={'fast':(35,55),'medium':(45,75),'slow':(65,95)}
        for student in self.plan()['students']:
            low,high=bounds[student['speed_stratum']]
            self.assertTrue(0 <= student['arrival_offset_seconds'] <=60)
            self.assertEqual(len(student['item_dwell_seconds']),20)
            self.assertTrue(all(low<=v<=high for v in student['item_dwell_seconds']))

    def test_precheck_and_smaller_stages_have_exact_counts(self):
        for n in (2,10,50,80):
            students=self.plan(n)['students']
            self.assertEqual(len(students), n)
            self.assertEqual(sum(s['role']=='protocol' for s in students), n-2)
            self.assertEqual(sum(bool(s['modify_items']) for s in students), 1 if n==2 else round(n*.3))
            self.assertEqual(sum(bool(s['excursion_items']) for s in students), 1 if n==2 else round(n*.2))
            self.assertEqual(sum(bool(s['optional_tool_items']) for s in students), 1 if n==2 else round(n*.2))

    def test_frozen_plan_rejects_identity_or_schedule_drift(self):
        from natural_workload import validate_workload
        fixture={'student_count':180,'item_count':20,'task_id':31,'usernames':[f'u{i:03}' for i in range(180)]}
        plan=self.plan()
        validate_workload(fixture,plan)
        plan['students'][3]['item_dwell_seconds'][0]=0
        with self.assertRaises(ValueError): validate_workload(fixture,plan)

    def test_expected_inputs_and_visits_follow_frozen_plan(self):
        from natural_workload import expected_input_counts, visit_indices
        student={'modify_items':[0,1], 'excursion_items':[1,4]}
        self.assertEqual(expected_input_counts(student,['short_answer','single_choice']+['true_false']*18),
            {'transitions':23,'text_operations':3,'per_item':[3,2]+[1]*18})
        self.assertEqual(visit_indices(student),[0,1,2,1,2,3,4,5,4]+list(range(5,20)))

    def test_login_only_partition_uses_all_protocol_identities(self):
        from natural_workload import build_workload
        fixture={'student_count':180,'item_count':20,'task_id':1,'usernames':[f'u{i}' for i in range(180)]}
        fixture['protocol_usernames']=fixture['usernames'][:]
        self.assertTrue(all(s['role']=='protocol' for s in build_workload(fixture)['students']))

    def test_login_only_freezes_zero_arrivals_without_changing_other_material(self):
        from natural_workload import build_workload, validate_workload
        fixture={'student_count':180,'item_count':20,'task_id':1,'usernames':[f'u{i}' for i in range(180)]}
        natural=build_workload(fixture)
        login=build_workload({**fixture,'load_mode':'login-only'})
        self.assertTrue(all(s['arrival_offset_seconds']==0 for s in login['students']))
        self.assertEqual([{k:v for k,v in s.items() if k!='arrival_offset_seconds'} for s in login['students']],
            [{k:v for k,v in s.items() if k!='arrival_offset_seconds'} for s in natural['students']])
        validate_workload({**fixture,'load_mode':'login-only'},login)

    def test_login_only_runner_selects_178_protocol_students(self):
        from natural_workload import build_workload
        from school_natural_load import NaturalRunner, natural_result
        from unittest.mock import patch
        fixture={'student_count':180,'item_count':20,'task_id':31,'password':'unused',
            'usernames':[f'u{i}' for i in range(180)],'load_mode':'login-only'}
        plan=build_workload(fixture)
        runner=NaturalRunner('http://127.0.0.1:18080',31,35,plan=plan,start_at=0)
        with patch.object(runner,'lifecycle'):
            states=runner.run(fixture,'login-only')
        self.assertEqual(len(states),178)
        self.assertEqual({s.username for s in states},set(fixture['usernames'][2:]))
        result=natural_result(runner,states,0,'login-only')
        self.assertEqual(result['student_count'],178)
        self.assertEqual(result['full_operation_accounting']['login_to_home_ready']['planned_count'],178)
        self.assertIn('first_login_post_attempts',result)

    def test_login_only_prepares_before_shared_release_and_resets_login_timer(self):
        from school_natural_load import NaturalRunner, StudentState
        from unittest.mock import Mock, patch
        runner=NaturalRunner('http://127.0.0.1:18080',31,35,plan=self.plan(10),start_at=1000)
        state=StudentState('u002','unused');state.load_student_index=2
        calls=[]
        def prepare(s):calls.append('prepare');s.load_entry_started_at=-100
        def authenticate(s):calls.append(('authenticate',s.load_entry_started_at))
        runner.login_barrier=Mock();runner.login_barrier.wait.side_effect=lambda **_:calls.append('barrier')
        with (patch.object(runner,'prepare_login',prepare),patch.object(runner,'wait_until',lambda *_:calls.append('release')),
             patch.object(runner,'authenticate',authenticate),patch.object(runner,'_browser_request')):
            runner.lifecycle(state,runner.plan['students'][2],'login-only')
        self.assertEqual(calls[:3],['prepare','barrier','release'])
        self.assertGreater(calls[3][1],0)

    def test_missing_planned_operations_remain_in_denominator(self):
        from school_natural_load import NaturalRunner, natural_result
        runner=NaturalRunner('http://127.0.0.1:18080',31,35,plan=self.plan(10),start_at=0)
        result=natural_result(runner,list(range(8)),0,'natural')
        self.assertEqual(result['full_operation_accounting']['submit_next_ready']['planned_count'],160)
        self.assertEqual(result['full_operation_accounting']['submit_next_ready']['not_reached_count'],160)
        self.assertFalse(result['passed'])
        self.assertFalse(result['workflow_completed'])
        self.assertIn('max_rss_bytes',result['generator'])
        self.assertGreater(result['generator']['max_rss_bytes'],0)

    def test_login_only_reads_home_and_courses_after_authentication(self):
        from school_natural_load import NaturalRunner, StudentState
        from unittest.mock import Mock, patch
        runner=NaturalRunner('http://127.0.0.1:18080',31,35,plan=self.plan(10),start_at=0)
        state=StudentState('u002','unused')
        state.load_student_index=2
        runner.login_barrier=Mock()
        with patch.object(runner,'wait_until'), patch.object(runner,'prepare_login'), patch.object(runner,'authenticate'), patch.object(runner,'_browser_request') as request:
            runner.lifecycle(state,runner.plan['students'][2],'login-only')
        self.assertEqual([call.args[1] for call in request.call_args_list],['/student/','/api/v1/auth/session/','/api/v1/student/courses/'])
        self.assertTrue(runner.identity_outcomes[0]['workflow_completed'])
        self.assertIsNotNone(runner.identity_outcomes[0]['home_ready_at'])
        self.assertEqual(runner.metrics['login_to_home_ready'].__len__(),1)

    def test_bootstrap_profile_uses_one_authentication_request_per_page(self):
        from school_natural_load import NaturalRunner, StudentState
        from unittest.mock import patch
        runner = NaturalRunner('http://127.0.0.1:18080', 31, 35, plan=self.plan(10),
                               start_at=0, authentication_profile='bootstrap-v1')
        state = StudentState('u002', 'unused')
        state.load_student_index = 2
        with patch.object(runner, 'measured_request', return_value=(200, {}, {})) as request, \
                patch.object(runner, '_csrf', return_value='token'):
            runner.prepare_login(state)
        self.assertEqual([call.args[1] for call in request.call_args_list],
                         ['/login/', '/api/v1/auth/bootstrap/'])
        with patch.object(runner, '_browser_request') as request:
            runner.read_home(state, 0)
        self.assertEqual([call.args[1] for call in request.call_args_list],
                         ['/student/', '/api/v1/auth/bootstrap/', '/api/v1/student/courses/'])
        self.assertEqual(runner.metrics['login_to_home_ready'].__len__(),1)

    def test_failed_home_operation_is_counted_as_attempted_failure(self):
        from school_natural_load import NaturalRunner, StudentState
        from unittest.mock import Mock, patch
        runner=NaturalRunner('http://127.0.0.1:18080',31,35,plan=self.plan(10),start_at=0)
        state=StudentState('u002','unused')
        state.load_student_index=2
        runner.login_barrier=Mock()
        with patch.object(runner,'wait_until'), patch.object(runner,'prepare_login'), patch.object(runner,'authenticate'), patch.object(runner,'_browser_request',side_effect=RuntimeError('HTTP 503')):
            runner.lifecycle(state,runner.plan['students'][2],'login-only')
        rows=[r for r in runner.operation_measurements if r['metric']=='login_to_home_ready']
        self.assertEqual(len(rows),1)
        self.assertFalse(rows[0]['successful'])
        self.assertFalse(runner.identity_outcomes[0]['workflow_completed'])

    def test_stop_file_prevents_any_new_request(self):
        from school_natural_load import NaturalRunner, LoadStopped
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            stop=Path(directory)/'stop'
            runner=NaturalRunner('http://127.0.0.1:18080',31,35,plan=self.plan(10),start_at=0,stop_file=stop)
            stop.touch()
            with self.assertRaises(LoadStopped): runner.remaining_budget()

    def test_protocol_denominator_comes_from_plan_when_states_are_missing(self):
        from school_natural_load import NaturalRunner, natural_result
        runner=NaturalRunner('http://127.0.0.1:18080',31,35,plan=self.plan(10),start_at=0)
        result=natural_result(runner,[],0,'natural')
        self.assertEqual(result['full_operation_accounting']['start_to_first_question_ready']['planned_count'],8)
        self.assertEqual(result['full_operation_accounting']['start_to_first_question_ready']['not_reached_count'],8)
        self.assertFalse(result['passed'])

    def test_approved_latency_gates_reject_old_looser_limits(self):
        from school_natural_load import NaturalRunner, natural_result
        thresholds={'navigation_next_ready':(1,2),'navigation_return_ready':(1,2),
            'submit_next_ready':(1.5,3),'save_draft':(1,3),'finish_ready':(3,5),
            'login_entry_to_authentication_end':(5,10),'login_to_home_ready':(5,10),
            'start_to_first_question_ready':(5,10)}
        for metric,(p95,p99) in thresholds.items():
            for percentile,limit in [('p95',p95),('p99',p99)]:
                with self.subTest(metric=metric,percentile=percentile):
                    runner=NaturalRunner('http://127.0.0.1:18080',31,35,plan=self.plan(10),start_at=0)
                    runner.operation_measurements=[{'metric':metric,'elapsed_seconds':limit+.01,'successful':True}]
                    result=natural_result(runner,list(range(8)),0,'natural')
                    self.assertFalse(result['checks'][metric+'_'+percentile])
                    self.assertEqual(result['acceptance_thresholds_seconds'][metric],{'p95':p95,'p99':p99})

    def test_formal_seed_rounds_are_independent_without_changing_speed_or_arrival(self):
        from natural_workload import build_workload
        fixture={'student_count':180,'item_count':20,'task_id':31,'scenario':'confidence-only-v1',
            'usernames':[f'u{i:03}' for i in range(180)]}
        plans=[build_workload(fixture,seed) for seed in (20261013,20261014)]
        self.assertNotEqual(plans[0]['students'],plans[1]['students'])
        for plan in plans:
            self.assertEqual(Counter(s['speed_stratum'] for s in plan['students']),{'fast':72,'medium':81,'slow':27})
            for student in plan['students']:
                self.assertTrue(0<=student['arrival_offset_seconds']<=60)
                self.assertEqual(Counter(student['confidence_actions']),{'fill':16,'skip':4})
                self.assertEqual(student['optional_tool_items'],[])

    def test_overlap_requires_actual_distinct_frozen_identities_and_finite_intervals(self):
        from school_natural_load import observed_active_overlap
        from capture_protocol import digest
        plan=self.plan(2)
        rows=[{'username_sha256':digest(s['username']),'ready_at':10+i*10,'completed_at':700-i*50}
            for i,s in enumerate(plan['students'])]
        overlap=observed_active_overlap(plan,rows)
        self.assertEqual(overlap['seconds'],630)
        self.assertTrue(overlap['achieved'])
        for invalid in [rows[:1],[rows[0],rows[0]],[rows[0],{**rows[1],'username_sha256':'foreign'}],
                [rows[0],{**rows[1],'ready_at':float('nan')}],
                [rows[0],{**rows[1],'completed_at':float('inf')}],
                [rows[0],{**rows[1],'completed_at':19}],
                [rows[0],{**rows[1],'completed_at':619}]]:
            self.assertFalse(observed_active_overlap(plan,invalid)['achieved'])

class NaturalWriterLeaseTests(unittest.TestCase):
    def test_terminal_summary_waits_for_final_capture_drain_and_propagates_failure(self):
        from capture_protocol import BrowserAlignedStream
        from unittest.mock import patch
        for failed in (False, True):
            with self.subTest(failed=failed):
                runner,state=self.runner()
                state.session_id=11
                runner.ledger['11']={'answers':{}}
                runtime={'stream':BrowserAlignedStream(11,'client'),'head':'head','version':0,
                    'lease':{'writer_lease_token':'lease','writer_client_instance_id':'client'},
                    'dto':{'assignment':{'attempt_id':21},'question':{'question_type':'true_false'},
                        'actions':{'submit_url':'/submit/'},'process':{'capture_bundle':{'enabled':True,'version':1}}}}
                calls=[]
                def submit(_,__,body,metric):
                    bundle=body['atomic_submission']
                    return {'success':True,'committed':True,'status':'submitted','final_response_event_id':'head',
                        'next_url':'/student/sessions/11/complete/', 'checkpoint_ids':[row['checkpoint_id'] for row in bundle['checkpoints']],
                        'acknowledgements':[{'event_id':row['event_id'],'client_sequence_no':row['client_sequence_no'],
                            'server_ingest_sequence_no':row['client_sequence_no']} for row in bundle['events']]}
                def drain(*args,**kwargs):
                    self.assertEqual(kwargs,{'metric':'final_capture_drain'})
                    calls.append('drain')
                    if failed: raise RuntimeError('final checkpoint unconfirmed')
                def read(_,path,metric):
                    calls.append(metric)
                    return {'session_id':11,'status':'completed','submission_count':20,'attempt_count':20}
                with patch.object(runner,'_browser_ensure_lease'),patch.object(runner,'_browser_post',side_effect=submit),patch.object(runner,'_browser_close',side_effect=lambda *args:calls.append('close')),patch.object(runner,'_browser_flush',side_effect=drain),patch.object(runner,'_browser_request',side_effect=read):
                    if failed:
                        with self.assertRaisesRegex(RuntimeError,'final checkpoint unconfirmed'):
                            runner._submit_material(state,runtime,19,{'payload':{'after_state_hash':'hash'}},list(range(20)))
                        self.assertEqual(calls,['close','drain'])
                    else:
                        runner._submit_material(state,runtime,19,{'payload':{'after_state_hash':'hash'}},list(range(20)))
                        self.assertEqual(calls,['close','drain','completion_shell','completion_auth','finish','task_recovery'])

    def runtime(self, expires):
        from types import SimpleNamespace
        return {'lease':{'writer_lease_token':'old-token','writer_client_instance_id':'client'},
            'lease_valid_until':expires,'stream':SimpleNamespace(client_instance_id='client'),
            'dto':{'process':{'writer_lease_acquire_url':'/acquire/','writer_lease_renew_url':'/renew/'}}}

    def runner(self):
        from school_natural_load import NaturalRunner, StudentState
        runner=NaturalRunner('http://127.0.0.1:18080',31,35,plan=NaturalWorkloadTests().plan(10),start_at=0)
        return runner,StudentState('u002','unused')

    def response(self):
        return {'lease_token':'confirmed-token','client_instance_id':'client','expires_at':'1970-01-01T00:02:10+00:00'}

    def test_expired_lease_after_natural_think_is_acquired_without_renewing(self):
        from unittest.mock import patch
        runner,state=self.runner()
        runtime=self.runtime(99)
        with patch('school_natural_load.time.time',return_value=100), patch.object(runner,'_browser_post',return_value=self.response()) as post:
            runner._browser_ensure_lease(state,runtime)
        self.assertEqual(post.call_args.args[1],'/acquire/')
        self.assertEqual(post.call_args.args[2],{'client_instance_id':'client'})
        self.assertEqual(runtime['lease']['writer_lease_token'],'confirmed-token')

    def test_valid_lease_reuses_and_nearing_expiry_renews_before_ttl(self):
        from unittest.mock import patch
        runner,state=self.runner()
        with patch('school_natural_load.time.time',return_value=100), patch.object(runner,'_browser_post',return_value=self.response()) as post:
            runner._browser_ensure_lease(state,self.runtime(106))
            post.assert_not_called()
            runner._browser_ensure_lease(state,self.runtime(103))
        self.assertEqual(post.call_args.args[1],'/renew/')
        self.assertEqual(post.call_args.args[2],{'client_instance_id':'client','lease_token':'old-token'})

    def test_confirmed_renew_conflict_reacquires_as_browser_does(self):
        from unittest.mock import patch
        runner,state=self.runner()
        def respond(*args):
            if args[1]=='/renew/':
                state.browser_last_error_code='writer_lease_conflict'
                raise RuntimeError('HTTP 409')
            return self.response()
        with patch('school_natural_load.time.time',return_value=100), patch.object(runner,'_browser_post',side_effect=respond) as post:
            runner._browser_ensure_lease(state,self.runtime(103))
        self.assertEqual([call.args[1] for call in post.call_args_list],['/renew/','/acquire/'])

    def test_long_think_keeps_heartbeat_tools_refresh_and_reacquires_only_at_write(self):
        from unittest.mock import Mock, patch
        runner,state=self.runner()
        clock=[100.0]
        runtime=self.runtime(130)
        runtime.update(heartbeat_due=115,heartbeat_seconds=15,tools_refresh_due=110,last_interaction=100)
        runtime['dto']['assignment']={'attempt_id':8}
        runtime['dto']['actions']={'research_tools_url':'/tools/'}
        runtime['stream'].event=Mock()
        def sleep(seconds): clock[0]+=seconds
        with patch('school_natural_load.time.monotonic',side_effect=lambda:clock[0]), patch('school_natural_load.time.time',side_effect=lambda:clock[0]), patch('school_natural_load.time.sleep',side_effect=sleep), patch.object(runner,'_browser_post',return_value=self.response()) as post, patch.object(runner,'_browser_checkpoint') as checkpoint, patch.object(runner,'_browser_flush') as flush:
            runner.wait_until(180,runtime,state)
            self.assertEqual([call.args[1] for call in post.call_args_list],['/tools/']*7)
            self.assertEqual(checkpoint.call_count,5)
            self.assertEqual(flush.call_count,5)
            self.assertEqual(runtime['stream'].event.call_count,5)
            runner._browser_ensure_lease(state,runtime)
        self.assertEqual(post.call_args.args[1],'/acquire/')

    def test_acquire_conflict_is_not_hidden_or_retried(self):
        from unittest.mock import patch
        runner,state=self.runner()
        state.browser_last_error_code='writer_lease_conflict'
        with patch('school_natural_load.time.time',return_value=100), patch.object(runner,'_browser_post',side_effect=RuntimeError('HTTP 409')) as post:
            with self.assertRaisesRegex(RuntimeError,'HTTP 409'):
                runner._browser_ensure_lease(state,self.runtime(99))
        self.assertEqual(post.call_count,1)

class ConfidenceOnlyWorkloadTests(unittest.TestCase):
    def fixture(self):
        return {'student_count':10,'item_count':20,'task_id':31,'scenario':'confidence-only-v1',
            'confidence_scale_version':'v2','usernames':[f'u{i:03}' for i in range(10)]}

    def plan(self):
        from natural_workload import build_workload
        return build_workload(self.fixture())

    def test_confidence_plan_has_legal_values_skips_and_no_optional_tools(self):
        plan=self.plan()
        self.assertEqual(plan['scenario'],'confidence-only-v1')
        self.assertTrue(plan['synthetic_confidence'])
        self.assertEqual(plan['confidence_scale'],{'minimum':0,'maximum':100,'step':1})
        for student in plan['students']:
            self.assertEqual(student['optional_tool_items'],[])
            self.assertEqual(Counter(student['confidence_actions']),{'fill':16,'skip':4})
            self.assertEqual(len(student['confidence_values']),20)
            self.assertEqual(len(student['confidence_think_seconds']),20)
            for action,value,think in zip(student['confidence_actions'],student['confidence_values'],student['confidence_think_seconds']):
                self.assertTrue(1<=think<=3)
                self.assertTrue(value is None if action=='skip' else isinstance(value,int) and 0<=value<=100)

    def test_confidence_decision_is_bound_to_actual_head_and_scale(self):
        from natural_workload import confidence_decision
        student=self.plan()['students'][0]
        index=student['confidence_actions'].index('fill')
        contract={'minimum':0,'maximum':100,'step':1}
        self.assertEqual(confidence_decision(student,index,contract,'actual-head'),
            {'status':'committed','probability':student['confidence_values'][index],'answer_event_id':'actual-head'})
        index=student['confidence_actions'].index('skip')
        self.assertEqual(confidence_decision(student,index,contract,'actual-head'),
            {'status':'skipped','answer_event_id':'actual-head'})
        student['confidence_values'][student['confidence_actions'].index('fill')]=101
        with self.assertRaises(ValueError): confidence_decision(student,student['confidence_actions'].index('fill'),contract,'head')

    def test_last_item_is_excluded_from_submit_next_metrics(self):
        from school_natural_load import NaturalRunner,natural_result
        runner=NaturalRunner('http://127.0.0.1:18080',31,35,plan=self.plan(),start_at=0)
        result=natural_result(runner,list(range(8)),0,'natural')
        for key in ('submit_ack','submit_ack_to_next_ready','submit_next_ready'):
            self.assertEqual(result['full_operation_accounting'][key]['planned_count'],152)
        self.assertEqual(result['full_operation_accounting']['finish_ready']['planned_count'],8)
        self.assertEqual(result['full_operation_accounting']['finish_submit_ack']['planned_count'],8)
        self.assertEqual(result['full_operation_accounting']['finish_ack_to_terminal_ready']['planned_count'],8)
        self.assertEqual(result['full_operation_accounting']['confidence_entry']['planned_count'],160)
        self.assertTrue(result['synthetic_confidence'])
        self.assertNotIn('submit_ack_p95',result['checks'])
        self.assertNotIn('submit_ack_to_next_ready_p95',result['checks'])

    def test_submit_timeout_remains_failed_attempt_and_missing_ack_ready_is_not_reached(self):
        from school_natural_load import NaturalRunner,StudentState,natural_result
        from unittest.mock import patch
        runner=NaturalRunner('http://127.0.0.1:18080',31,35,plan=self.plan(),start_at=0)
        state=StudentState('u002','unused')
        state.load_student_index=2
        with patch.object(runner,'prepare_confidence',return_value={'status':'skipped'}),patch.object(runner,'_submit_material',side_effect=TimeoutError()),patch('school_natural_load.time.perf_counter',side_effect=[100,135]):
            with self.assertRaises(TimeoutError): runner.submit(state,{},0,{},list(range(20)))
        result=natural_result(runner,list(range(8)),0,'natural')
        accounting=result['full_operation_accounting']
        self.assertEqual(accounting['submit_next_ready']['attempted_count'],1)
        self.assertEqual(accounting['submit_next_ready']['failed_count'],1)
        self.assertEqual(accounting['submit_next_ready']['timeout_count'],1)
        self.assertEqual(accounting['submit_next_ready']['not_reached_count'],151)
        self.assertEqual(accounting['submit_ack_to_next_ready']['not_reached_count'],152)
        self.assertEqual(result['operation_latency']['submit_next_ready']['all_operations']['max_seconds'],35)
        self.assertFalse(result['passed'])

    def test_confidence_wait_timeout_retains_failure_type_in_both_boundaries(self):
        from school_natural_load import NaturalRunner,StudentState,natural_result
        from unittest.mock import Mock,patch
        runner=NaturalRunner('http://127.0.0.1:18080',31,35,plan=self.plan(),start_at=0)
        state=StudentState('u002','unused')
        state.load_student_index=2
        state.natural_student_plan=runner.plan['students'][2]
        runtime={'head':'head','stream':Mock(),'dto':{'assignment':{'attempt_id':8},'pending_confidence':{
            'confidence_assignment_id':'assignment','scale_version':'v2','minimum':0,'maximum':100,'step':1,
            'prompt_shown_url':'/confidence/prompt/'}}}
        with patch.object(runner,'_browser_post',return_value={}),patch.object(runner,'wait_until',side_effect=TimeoutError()):
            with self.assertRaises(TimeoutError): runner.prepare_confidence(state,runtime,0)
        accounting=natural_result(runner,list(range(8)),0,'natural')['full_operation_accounting']
        for key in ('confidence_entry','confidence_think'):
            self.assertEqual(accounting[key]['failed_count'],1)
            self.assertEqual(accounting[key]['timeout_count'],1)

    def test_prepare_confidence_fills_and_skips_using_atomic_response_material(self):
        from school_natural_load import NaturalRunner,StudentState
        from unittest.mock import Mock,patch
        runner=NaturalRunner('http://127.0.0.1:18080',31,35,plan=self.plan(),start_at=0)
        state=StudentState('u002','unused')
        state.load_student_index=2
        state.session_id=77
        state.natural_student_plan=runner.plan['students'][2]
        runner.ledger['77']={'events':{},'answers':{}}
        stream=Mock()
        runtime={'head':'actual-head','stream':stream,'dto':{'assignment':{'attempt_id':8},
            'pending_confidence':{'confidence_assignment_id':'assignment-id','scale_version':'v2','minimum':0,'maximum':100,'step':1,'prompt_shown_url':'/confidence/prompt/'}}}
        for action in ('fill','skip'):
            index=state.natural_student_plan['confidence_actions'].index(action)
            stream.reset_mock()
            with patch.object(runner,'wait_until'),patch.object(runner,'_browser_post',return_value={'success':True}) as post:
                result=runner.prepare_confidence(state,runtime,index)
            self.assertEqual(result['status'],'committed' if action=='fill' else 'skipped')
            self.assertEqual(result['answer_event_id'],'actual-head')
            self.assertEqual([call.args[2]['stage'] for call in stream.event.call_args_list],['presented']+(['skipped'] if action=='skip' else []))
            self.assertEqual([call.args[1] for call in post.call_args_list],['/confidence/prompt/'])
        self.assertEqual(len(runner.metrics['confidence_entry']),2)

    def test_submission_ack_and_next_ready_use_distinct_clock_boundaries(self):
        from school_natural_load import NaturalRunner,StudentState
        from unittest.mock import patch
        runner=NaturalRunner('http://127.0.0.1:18080',31,35,plan=self.plan(),start_at=0)
        state=StudentState('u002','unused')
        state.load_student_index=2
        def material(*args):
            state.natural_submission_ack_at=102
            return {'interactive':True}
        with patch.object(runner,'prepare_confidence',return_value={'status':'skipped'}),patch.object(runner,'_submit_material',side_effect=material),patch('school_natural_load.time.perf_counter',side_effect=[100,105]):
            runner.submit(state,{},0,{},list(range(20)))
        self.assertEqual(runner.metrics['submit_ack'],[2])
        self.assertEqual(runner.metrics['submit_ack_to_next_ready'],[3])
        self.assertEqual(runner.metrics['submit_next_ready'],[5])
        self.assertNotIn('finish_ready',runner.metrics)

    def test_closed_tool_dto_enters_without_querying_tool_configuration(self):
        from school_natural_load import NaturalRunner,Synchronized180Runner,StudentState
        from unittest.mock import patch
        runner=NaturalRunner('http://127.0.0.1:18080',31,35,plan=self.plan(),start_at=0)
        with patch.object(Synchronized180Runner,'browser_open_question',return_value={'tools_refresh_due':10}) as enter:
            runtime=runner.browser_open_question(StudentState('u002','unused'),12,dto={'actions':{},'research_tools':None})
        self.assertEqual(runtime['tools_refresh_due'],float('inf'))
        self.assertEqual(enter.call_args.args[3]['research_tools'],{'rules':[],'hints':[]})
        with self.assertRaisesRegex(RuntimeError,'exposed a research tool'):
            runner.browser_open_question(StudentState('u002','unused'),12,dto={'actions':{'research_tools_url':'/tools/'}})

    def test_readonly_kc_display_without_tool_entry_does_not_mount_tools(self):
        from school_natural_load import NaturalRunner,Synchronized180Runner,StudentState
        from unittest.mock import patch
        runner=NaturalRunner('http://127.0.0.1:18080',31,35,plan=self.plan(),start_at=0)
        dto={'actions':{},'research_tools':{'kc_visible':True,'doubt_enabled':False,'check_enabled':False,'hints':[]}}
        with patch.object(Synchronized180Runner,'browser_open_question',return_value={}) as enter:
            value=runner.browser_open_question(StudentState('u002','unused'),12,dto=dto)
        self.assertEqual(value['tools_refresh_due'],float('inf'))
        self.assertEqual(enter.call_args.args[3]['research_tools'],{'rules':[],'hints':[]})

    def test_last_item_has_terminal_metrics_without_submit_next_sample(self):
        from school_natural_load import NaturalRunner,StudentState
        from unittest.mock import patch
        runner=NaturalRunner('http://127.0.0.1:18080',31,35,plan=self.plan(),start_at=0)
        state=StudentState('u002','unused')
        state.load_student_index=2
        def material(*args):
            state.natural_submission_ack_at=102
            return {'terminal':True}
        with patch.object(runner,'prepare_confidence',return_value={'status':'skipped'}),patch.object(runner,'_submit_material',side_effect=material),patch('school_natural_load.time.perf_counter',side_effect=[100,105]):
            runner.submit(state,{},19,{},list(range(20)))
        self.assertEqual(runner.metrics['finish_submit_ack'],[2])
        self.assertEqual(runner.metrics['finish_ack_to_terminal_ready'],[3])
        self.assertEqual(runner.metrics['finish_ready'],[5])
        self.assertNotIn('submit_next_ready',runner.metrics)
        self.assertNotIn('submit_ack',runner.metrics)

    def test_actual_tool_request_journal_rejects_unnamed_refresh(self):
        from school_natural_load import NaturalRunner,natural_result
        runner=NaturalRunner('http://127.0.0.1:18080',31,35,plan=self.plan(),start_at=0)
        runner.request_attempts.append({'path':'/api/v1/assessment/process/capture-v2/attempts/:id/tools/',
            'method':'POST','attempt':1,'sent_at':1,'elapsed_seconds':.1,'successful':True,'operation_id':'tool-refresh'})
        result=natural_result(runner,list(range(8)),0,'natural')
        self.assertEqual(result['actual_tool_request_count'],1)
        self.assertFalse(result['checks']['confidence_only_zero_tool_requests'])

    def test_no_tools_refresh_while_confidence_only_student_thinks(self):
        from school_natural_load import NaturalRunner,StudentState
        from unittest.mock import Mock,patch
        runner=NaturalRunner('http://127.0.0.1:18080',31,35,plan=self.plan(),start_at=0)
        clock=[100.0]
        runtime={'heartbeat_due':1000,'heartbeat_seconds':30,'tools_refresh_due':100,
            'last_interaction':100,'dto':{'actions':{},'assignment':{'attempt_id':8}},'stream':Mock()}
        with patch('school_natural_load.time.monotonic',side_effect=lambda:clock[0]),patch('school_natural_load.time.sleep',side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)),patch.object(runner,'_browser_post') as post:
            runner.wait_until(111,runtime,StudentState('u002','unused'))
        post.assert_not_called()

class ConfidencePersistenceTests(unittest.TestCase):
    def test_exact_confidence_value_head_receipt_and_unique_records_are_required(self):
        import os
        from types import SimpleNamespace as Row
        os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
        sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
        import django
        django.setup()
        from apps.experiments.management.commands.verify_school_protocol_load import confidence_persistence_checks
        from capture_protocol import digest
        from natural_workload import confidence_decision
        plan=ConfidenceOnlyWorkloadTests().plan()
        student=plan['students'][2]
        index=student['confidence_actions'].index('fill')
        # This isolated helper fixture has one assignment; make item0 a committed response.
        student['confidence_actions'][0]='fill'
        student['confidence_values'][0]=73
        session=Row(pk=77,student=Row(username=student['username']))
        receipt={'committed':True,'final_response_event_id':'head'}
        attempt=Row(pk=8,session_id=77,assignment=Row(assigned_sequence_index=1),final_response_event_id='head',submission_token='token',submission_receipt=receipt)
        assigned=Row(pk='assignment',attempt_id=8,condition='prompt',response_status='committed',probability=73,prompt_shown_at=1,committed_at=2)
        response=confidence_decision(student,0,plan['confidence_scale'],'head')
        ledger={'sessions':{'77':{'confidence':{'8':{'confidence_assignment_id':'assignment','scale_version':'v2',
            'response':response,'synthetic':True,'item_index':0,'acknowledged':True,'submission_token':'token',
            'submission_receipt_sha256':digest(receipt)}}}}}
        events=[Row(attempt_id=8,event_type='confidence_observed',client_instance_id='client',payload={'stage':'presented','confidence_assignment_id':'assignment','answer_event_id':'head','probability':None}),
            Row(attempt_id=8,event_type='confidence_prompt_shown',client_instance_id=None,payload={'confidence_assignment_id':'assignment'}),
            Row(attempt_id=8,event_type='confidence_committed',client_instance_id=None,payload={'confidence_assignment_id':'assignment','probability':73})]
        checks=confidence_persistence_checks(plan,ledger,[session],[attempt],[assigned],events)
        self.assertTrue(all(checks.values()))
        assigned.probability=72
        self.assertFalse(all(confidence_persistence_checks(plan,ledger,[session],[attempt],[assigned],events).values()))
        assigned.probability=73
        self.assertFalse(all(confidence_persistence_checks(plan,ledger,[session],[attempt],[assigned,assigned],events).values()))
        attempt.submission_receipt={'committed':True,'final_response_event_id':'wrong'}
        self.assertFalse(all(confidence_persistence_checks(plan,ledger,[session],[attempt],[assigned],events).values()))
        attempt.submission_receipt=receipt
        self.assertFalse(all(confidence_persistence_checks(plan,ledger,[session],[attempt],[assigned],events+[events[1]]).values()))
        student['confidence_actions'][0]='skip'
        student['confidence_values'][0]=None
        assigned.response_status='skipped'
        assigned.probability=None
        assigned.committed_at=None
        ledger['sessions']['77']['confidence']['8']['response']=confidence_decision(student,0,plan['confidence_scale'],'head')
        events[-1].event_type='confidence_skipped'
        events[-1].payload={'confidence_assignment_id':'assignment','reason_code':'student_skipped'}
        events.append(Row(attempt_id=8,event_type='confidence_observed',client_instance_id='client',payload={'stage':'skipped','confidence_assignment_id':'assignment','answer_event_id':'head','probability':None}))
        self.assertTrue(all(confidence_persistence_checks(plan,ledger,[session],[attempt],[assigned],events).values()))

class SameClientLeaseEvidenceTests(unittest.TestCase):
    def evidence(self):
        from capture_protocol import digest
        plan = NaturalWorkloadTests().plan(10)
        common = {'student_index': 2, 'method': 'POST', 'attempt': 1,
            'writer_client_sha256': 'c' * 64, 'lease_scope_sha256': 'd' * 64, 'elapsed_seconds': .1}
        renew = {**common, 'request_id': 'a' * 32, 'operation_id': 'a' * 32,
            'path': '/api/v1/assessment/process/attempts/:id/writer-lease/renew/',
            'http_status': 409, 'successful': False, 'error_type': 'HTTPError',
            'server_error_code': 'writer_lease_conflict', 'sent_at': 100., 'completed_at': 100.1,
            'lease_cached_valid_until': 103.}
        acquire = {**common, 'request_id': 'b' * 32, 'operation_id': 'b' * 32,
            'path': '/api/v1/assessment/process/attempts/:id/writer-lease/acquire/',
            'http_status': 201, 'successful': True, 'error_type': None,
            'server_error_code': None, 'sent_at': 100.1, 'completed_at': 100.2}
        marker = {'renew_request_id': renew['request_id'], 'acquire_request_id': acquire['request_id'],
            'student_index': 2, 'username_sha256': digest(plan['students'][2]['username']),
            'writer_client_sha256': common['writer_client_sha256'], 'lease_scope_sha256': common['lease_scope_sha256']}
        return plan, [renew, acquire], [marker]

    def test_linked_successful_recovery_keeps_raw_failure_and_latency(self):
        from school_natural_load import lease_recovery_accounting
        plan, attempts, markers = self.evidence()
        result = lease_recovery_accounting(plan, attempts, markers, 1)
        self.assertTrue(result['valid'])
        self.assertEqual(result['raw_http_error_count'], 1)
        self.assertEqual(result['handled_successful_recovery_count'], 1)
        self.assertEqual(result['unexpected_http_error_count'], 0)
        self.assertEqual(result['failed_attempts'], [attempts[0]])
        self.assertEqual(result['failed_attempt_latency']['max_seconds'], .1)

    def test_unrecovered_or_forged_markers_cannot_account_for_error(self):
        from copy import deepcopy
        from school_natural_load import lease_recovery_accounting
        plan, attempts, markers = self.evidence()
        variants = []
        for key, value in (('server_error_code', 'unrecognized_error_code'), ('http_status', 500),
                           ('lease_cached_valid_until', 99.)):
            changed = deepcopy(attempts); changed[0][key] = value
            variants.append((changed, markers))
        for key, value in (('http_status', 409), ('successful', False), ('student_index', 3),
                           ('writer_client_sha256', 'e' * 64), ('lease_scope_sha256', 'e' * 64), ('sent_at', 99.)):
            changed = deepcopy(attempts); changed[1][key] = value
            variants.append((changed, markers))
        variants += [(attempts, []), (attempts, markers * 2), (attempts[:1], markers)]
        forged = deepcopy(markers); forged[0]['renew_request_id'] = 'f' * 32
        variants.append((attempts, forged))
        for rows, declared in variants:
            with self.subTest(rows=rows, declared=declared):
                result = lease_recovery_accounting(plan, rows, declared, 1)
                self.assertFalse(result['valid'] and result['unexpected_http_error_count'] == 0)
        self.assertFalse(lease_recovery_accounting(plan, attempts, markers, 0)['valid'])

    def test_real_request_metadata_and_successful_reacquire_create_marker_without_tokens(self):
        from io import BytesIO
        from types import SimpleNamespace
        from unittest.mock import patch
        from urllib.error import HTTPError
        import json
        from school_natural_load import NaturalRunner, StudentState, lease_recovery_accounting
        plan = NaturalWorkloadTests().plan(10)
        runner = NaturalRunner('http://127.0.0.1:18080', 31, 35, plan=plan, start_at=0,
            load_evidence_profile='same-client-lease-recovery-v1')
        state = StudentState(plan['students'][2]['username'], 'unused'); state.load_student_index = 2
        runtime = NaturalWriterLeaseTests().runtime(103)
        runtime['dto']['process'] = {
            'writer_lease_renew_url': '/api/v1/assessment/process/attempts/9/writer-lease/renew/',
            'writer_lease_acquire_url': '/api/v1/assessment/process/attempts/9/writer-lease/acquire/'}
        def open_request(request, **kwargs):
            if request.full_url.endswith('/renew/'):
                raise HTTPError(request.full_url, 409, '', {}, BytesIO(b'{"error":{"code":"writer_lease_conflict"}}'))
            identity = request.get_header('X-request-id')
            response = BytesIO(json.dumps({'data': NaturalWriterLeaseTests().response(), 'meta': {'request_id': identity}}).encode())
            response.status = 201
            response.headers = {'Content-Type': 'application/json', 'X-Request-ID': identity}
            return response
        state.opener = SimpleNamespace(open=open_request)
        with patch('school_natural_load.time.time', return_value=100):
            runner._browser_ensure_lease(state, runtime)
        self.assertEqual(runner.request_errors, 1)
        self.assertEqual(len(runner.lease_recoveries), 1)
        accounting = lease_recovery_accounting(plan, list(runner.request_attempts), runner.lease_recoveries, 1)
        self.assertTrue(accounting['valid'])
        self.assertEqual(accounting['unexpected_http_error_count'], 0)
        material = json.dumps({'rows': list(runner.request_attempts), 'markers': runner.lease_recoveries})
        self.assertNotIn('old-token', material)
        self.assertNotIn('confirmed-token', material)

    def test_natural_output_preserves_raw_count_and_separates_unexpected_errors(self):
        from school_natural_load import NaturalRunner, natural_result
        plan, attempts, markers = self.evidence()
        for profile in ('legacy', 'same-client-lease-recovery-v1'):
            runner = NaturalRunner('http://127.0.0.1:18080', 31, 35, plan=plan, start_at=0, load_evidence_profile=profile)
            runner.request_attempts = attempts
            runner.request_count = 2
            runner.request_errors = 1
            runner.lease_recoveries = markers
            result = natural_result(runner, [], 0, 'natural')
            self.assertEqual(result['http_error_count'], 1)
            self.assertEqual(result['request_attempts']['attempt_latency']['count'], 2)
            if profile == 'legacy':
                self.assertFalse(result['checks']['zero_http_errors'])
            else:
                self.assertTrue(result['checks']['all_http_errors_accounted'])
                self.assertEqual(result['http_error_accounting']['handled_successful_recovery_count'], 1)
                self.assertEqual(result['http_error_accounting']['unexpected_http_error_count'], 0)
            self.assertFalse(result['passed'])  # Missing primary planned operations still fail.

    def test_load_exit_gate_accepts_complete_new_profile_but_keeps_legacy_failure(self):
        from capture_protocol import digest
        from school_natural_load import NaturalRunner, natural_result
        plan, attempts, markers = self.evidence()
        for profile, expected in (('legacy', False), ('same-client-lease-recovery-v1', True)):
            runner = NaturalRunner('http://127.0.0.1:18080', 31, 35, plan=plan, start_at=0, load_evidence_profile=profile)
            runner.request_attempts = attempts; runner.request_count = 2; runner.request_errors = 1
            runner.lease_recoveries = markers; runner.execution_completed = True
            runner.identity_outcomes = [{'username_sha256': digest(student['username']), 'workflow_completed': True,
                'ready_at': 100, 'completed_at': 1000, 'authenticated_at': 99,
                'actual_arrival_at': 90, 'planned_arrival_at': 90} for student in plan['students'] if student['role'] == 'protocol']
            initial = natural_result(runner, list(range(8)), 0, 'natural')
            runner.operation_measurements = [{'metric': metric, 'elapsed_seconds': .1, 'successful': True}
                for metric, item in initial['full_operation_accounting'].items() for _ in range(item['planned_count'])]
            result = natural_result(runner, list(range(8)), 0, 'natural')
            self.assertEqual(result['performance_passed'], expected)
            self.assertEqual(result['http_error_count'], 1)
            if expected:
                self.assertTrue(all(result['checks'].values()))

    def test_failed_reacquire_wrong_client_unknown_code_and_expired_cache_emit_no_marker(self):
        from io import BytesIO
        from types import SimpleNamespace
        from unittest.mock import patch
        from urllib.error import HTTPError
        import json
        from school_natural_load import NaturalRunner, StudentState
        plan = NaturalWorkloadTests().plan(10)
        for variant in ('failed_reacquire', 'wrong_client', 'unknown_code', 'expired_cache'):
            runner = NaturalRunner('http://127.0.0.1:18080', 31, 35, plan=plan, start_at=0,
                load_evidence_profile='same-client-lease-recovery-v1')
            state = StudentState(plan['students'][2]['username'], 'unused'); state.load_student_index = 2
            runtime = NaturalWriterLeaseTests().runtime(99 if variant == 'expired_cache' else 103)
            runtime['dto']['process'] = {
                'writer_lease_renew_url': '/api/v1/assessment/process/attempts/9/writer-lease/renew/',
                'writer_lease_acquire_url': '/api/v1/assessment/process/attempts/9/writer-lease/acquire/'}
            def open_request(request, **kwargs):
                if request.full_url.endswith('/renew/') or variant in ('failed_reacquire', 'expired_cache'):
                    code = 'unknown_code' if variant == 'unknown_code' else 'writer_lease_conflict'
                    status = 500 if variant == 'failed_reacquire' and request.full_url.endswith('/acquire/') else 409
                    raise HTTPError(request.full_url, status, '', {}, BytesIO(json.dumps({'error': {'code': code}}).encode()))
                identity = request.get_header('X-request-id')
                payload = {**NaturalWriterLeaseTests().response(), 'client_instance_id': 'different-client'}
                response = BytesIO(json.dumps({'data': payload, 'meta': {'request_id': identity}}).encode())
                response.status = 201; response.headers = {'Content-Type': 'application/json', 'X-Request-ID': identity}
                return response
            state.opener = SimpleNamespace(open=open_request)
            with self.subTest(variant=variant), patch('school_natural_load.time.time', return_value=100):
                with self.assertRaises(RuntimeError):
                    runner._browser_ensure_lease(state, runtime)
                self.assertEqual(runner.lease_recoveries, [])
                self.assertGreaterEqual(runner.request_errors, 1)


if __name__ == '__main__': unittest.main()
