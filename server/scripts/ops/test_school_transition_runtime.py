"""Local acceptance tests; synthetic evidence never contacts the school host."""
import copy
import hashlib
import importlib
import json
import math
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path

OPS = Path(__file__).resolve().parent
sys.path.insert(0, str(OPS))
sys.path.insert(0, str(OPS.parent / 'load'))
try:
    transition = importlib.import_module('school_transition_runtime')
except ModuleNotFoundError as error:
    if error.name != 'school_transition_runtime':
        raise
    transition = None
from natural_workload import build_workload
from capture_protocol import digest


def evidence(count=10, seed=20261013, login=False):
    names = [f'synthetic-{i}' for i in range(count)]
    fixture = {'database': 'kt_e2e', 'run_id': f'fresh-{count}-{seed}', 'task_id': seed,
        'student_count': count, 'item_count': 20, 'usernames': names,
        'browser_usernames': names[:2], 'load_mode':'login-only' if login else 'natural',
        'protocol_usernames': names[2:], 'scenario': 'confidence-only-v1',
        'confidence_scale_version': 'v2', 'source_config': {'phase': 'candidate'}}
    plan = build_workload(fixture, seed)
    selected = [row for row in plan['students'] if row['role'] == 'protocol']
    students = [{'username_sha256': digest(row['username']),
        'ready_at': 100., 'completed_at': 1100., 'workflow_completed': True} for row in selected]
    counts = {'login_entry_to_authentication_end': len(selected), 'login_to_home_ready': len(selected)}
    if not login:
        counts.update(start_to_first_question_ready=len(selected),
            navigation_next_ready=sum(len(row['excursion_items']) for row in selected),
            navigation_return_ready=sum(len(row['excursion_items']) for row in selected),
            submit_next_ready=len(selected)*19, finish_ready=len(selected),
            save_draft=sum(28+len(row['modify_items']) for row in selected),
            submit_ack=len(selected)*19, submit_ack_to_next_ready=len(selected)*19,
            finish_submit_ack=len(selected), finish_ack_to_terminal_ready=len(selected),
            confidence_entry=len(selected)*20, confidence_think=len(selected)*20,
            confidence_prompt=len(selected)*20)
    operations = [{'metric': key, 'elapsed_seconds': .1, 'successful': True} for key, n in counts.items() for _ in range(n)]
    accounting = {key: {'planned_count': n, 'attempted_count': n, 'successful_count': n,
        'failed_count': 0, 'timeout_count': 0, 'not_reached_count': 0} for key, n in counts.items()}
    http = {'workflow_completed': True, 'execution_completed': True, 'errors': [], 'http_error_count': 0,
        'student_count': len(selected), 'students': students, 'mode': 'login-only' if login else 'natural',
        'task_id': fixture['task_id'], 'plan_sha256': hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'operation_measurements': operations, 'full_operation_accounting': accounting,
        'actual_tool_request_count': 0, 'started': 90., 'ended': 1200.,
        'first_login_post_count': len(selected), 'first_login_post_success_count': len(selected),
        'first_login_post_attempts':[{'username_sha256':digest(row['username']),'sent_at':100.1+i*.001,
            'successful':True,'http_status':200,'request_id':hashlib.sha256(str(i).encode()).hexdigest()[:32]}
            for i,row in enumerate(selected)],
        'login_post_send_window_seconds': .5}
    probes = []
    for row in plan['students'][:2]:
        actions = [{'action': key, 'duration_ms': 100., 'completed': True, 'start_ms': i*100., 'sequence': i}
            for key, n in {'login': 1, 'task_list': 1, 'start_first_ready': 1,
                'navigation_next_ready': len(row['excursion_items']), 'navigation_return_ready': len(row['excursion_items']),
                'submit_next_ready': 19, 'finish_ready': 1, 'submit_request_confidence_ready': 20,
                'confidence_input_ready': 16, 'confidence_skip_ready': 4, 'answer_input':20,
                'answer_input_segment':8,'answer_modify':len(row['modify_items']),
                'submit_ack':20,'next_question_ready':19,'last_session_ready':1}.items() for i in range(n)]
        probes.append({'username_sha256': digest(row['username']),
            'ready_at': 100., 'completed_at': 1100., 'completed': True, 'session_completed': True,
            'submitted_count': 20, 'evidence_complete': True, 'page_error_count': 0,
            'disabled_tools_passed': True, 'tools_request_count': 0, 'actions': actions,
            'waterfall': [{'endpoint': '/api/v1/student/attempts/1/draft/', 'duration_ms': 100.,
                'status': 200, 'request_id': f'request-{i}', 'start_ms': i*100.} for i in range(20)]})
        if login:
            probes[-1].update(mode='login-only',submitted_count=0,session_completed=False,started_at=90.,home_ready_at=100.4,
                actions=[{'action':key,'duration_ms':100.,'completed':True} for key in ('login','task_list')],
                waterfall=[{'endpoint':'/api/v1/auth/login/','method':'POST','sent_at':100.,'status':200,
                    'request_id':hashlib.sha256(row['username'].encode()).hexdigest()[:32]}])
    sample={'sample_id':'sample-1','browser_t0':99.98,'browser_t1':100.02,'server_epoch':100.}
    browser = {'completed': True, 'probes': probes, 'mode':'login-only' if login else 'natural',
        'clock_calibration':{'selected_sample_id':'sample-1','server_minus_browser_lower_seconds':-.02,
            'server_minus_browser_upper_seconds':.02,'samples':[sample,{**sample,'sample_id':'sample-2'},
                {**sample,'sample_id':'sample-3'}]}}
    observation = {'state': 'completed', 'oom_kills': 0, 'started_at': 80., 'ended_at': 1300.}
    independent = {'passed': True}
    return fixture, plan, http, browser, observation, independent


class TransitionGatesTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(transition, 'The staged coordinator has not been implemented')

    def redis_runtime(self):
        policies={'redis-admission':{'appendonly':'no','appendfsync':'everysec','maxmemory-policy':'allkeys-lru','save':''},
            'redis-session':{'appendonly':'yes','appendfsync':'always','maxmemory-policy':'noeviction','save':'3600 1 300 100 60 10000'}}
        runtime=transition.TransitionRuntime.__new__(transition.TransitionRuntime)
        runtime.config={'source_config':{'roles':{role:{'redis_policy':policy.copy()} for role,policy in policies.items()}}}
        roles={role:{'Id':role+'-owned'} for role in policies}
        calls=[]
        def capture(command):
            calls.append(command)
            self.assertIn('--json',command)
            role=command[2].removesuffix('-owned');key=command[-1]
            return json.dumps([key,policies[role][key]])+'\n'
        runtime.capture=capture
        return runtime,roles,policies,calls

    def test_redis_checker_accepts_distinct_frozen_original_policies_and_empty_save(self):
        self.assertTrue(hasattr(transition.TransitionRuntime,'verify_redis_policies'),
            'Admission and session Redis must be checked against their separate frozen policies')
        runtime,roles,policies,calls=self.redis_runtime()
        observed=runtime.verify_redis_policies(roles)
        self.assertEqual(observed,policies)
        self.assertEqual(observed['redis-admission']['save'],'')
        self.assertEqual(len(calls),8)

    def test_redis_checker_rejects_policy_drift_or_missing_frozen_policy(self):
        self.assertTrue(hasattr(transition.TransitionRuntime,'verify_redis_policies'))
        for role,key,value in (('redis-admission','appendonly','yes'),('redis-session','appendfsync','everysec'),
                              ('redis-admission','save','3600 1')):
            runtime,roles,policies,calls=self.redis_runtime()
            policies[role][key]=value
            with self.subTest(role=role,key=key),self.assertRaises(transition.SafetyInterrupted):
                runtime.verify_redis_policies(roles)
        runtime,roles,policies,calls=self.redis_runtime()
        del runtime.config['source_config']['roles']['redis-admission']['redis_policy']['save']
        with self.assertRaises(transition.SafetyInterrupted):runtime.verify_redis_policies(roles)

    def test_bridge_start_preserves_directory_owner_and_private_record_contract(self):
        self.assertTrue(hasattr(transition,'write_bridge_start'),
            'Published bridge start must retain the launcher-readable directory owner/group')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);bridge=root/'bridge';bridge.mkdir()
            private=root/'private';private.mkdir()
            class Bridge:
                def __truediv__(self,name):return bridge/name
                def stat(self):return SimpleNamespace(st_uid=1000,st_gid=1001)
            changes=[]
            def chown(path,uid,gid):
                self.assertTrue(path.exists())
                changes.append((path,uid,gid))
            platform=SimpleNamespace(name='posix',chown=chown)
            value={'stage':'baseline10','start_at':123,'fixture_binding':{'bound':True}}
            private_value={'started_at':120,'start_at':123,'stage':'baseline10'}
            with patch.object(transition,'os',platform),patch('school_diagnostic_sweep.os.chmod',wraps=__import__('os').chmod) as chmod:
                transition.write_once(private/'started.json',private_value)
                transition.write_bridge_start(Bridge(),value)
            self.assertEqual(changes,[(bridge/'start.json',1000,1001)])
            self.assertEqual(json.loads((bridge/'start.json').read_text()),value)
            self.assertEqual(json.loads((private/'started.json').read_text()),private_value)
            self.assertEqual([call.args[1] for call in chmod.call_args_list],[0o600,0o600])
            with patch.object(transition,'os',platform),self.assertRaises(FileExistsError):
                transition.write_bridge_start(Bridge(),value)
            self.assertEqual(len(changes),1)

    def score(self, stage='candidate10', count=10, seed=20261013, **changes):
        f, p, h, b, o, v = evidence(count, seed, stage == 'login180')
        return transition.score_stage(stage, f, p, h, b, o,
            persistence=changes.pop('persistence', v), browser_persistence=v, architecture=v,
            drain={'passed': True, 'budget_seconds': 300}, load_exit_code=changes.pop('load_exit_code',0),
            observer_exit_code=0, stop_reason=changes.pop('stop_reason',None),
            teacher_low={'passed': True}, browser_binding_passed=True,login_session_count=0, **changes)

    def test_initial_authentication_discovery_401_is_accounted_separately(self):
        f,p,h,b,o,v=evidence()
        for probe in b['probes']:
            probe['waterfall'] += [
                {'endpoint':'/api/v1/auth/session/','method':'GET','phase':'login','status':401,'sent_at':99},
                {'endpoint':'/api/v1/auth/login/','method':'POST','phase':'login','status':200,'sent_at':100},
            ]
        scores,complete,passed=transition.browser_score(p,b)
        self.assertTrue(complete)
        self.assertTrue(passed)
        self.assertTrue(all(row['expected_auth_discovery_401_count']==1 for row in scores.values()))
        for method,phase,sent in (('POST','login',99),('GET','submit_next_ready',99),('GET','login',101)):
            other=copy.deepcopy(b)
            other['probes'][0]['waterfall'][-2].update(method=method,phase=phase,sent_at=sent)
            with self.subTest(method=method,phase=phase,sent=sent):
                self.assertFalse(transition.browser_score(p,other)[1])

    def test_user_requested_single_180_diagnostic_does_not_create_formal_acceptance(self):
        f,p,h,b,o,v=evidence(180)
        f['source_config']['diagnostic_180_authorized']=True
        transition.require_previous('diagnostic180',{},f['source_config'])
        with self.assertRaises(transition.SafetyInterrupted):
            transition.require_previous('diagnostic180',{}, {'phase':'candidate'})
        result=transition.score_stage('diagnostic180',f,p,h,b,o,persistence=v,browser_persistence=v,
            architecture=v,drain={'passed':True,'budget_seconds':300},load_exit_code=0,
            observer_exit_code=0,stop_reason=None,teacher_low=v,browser_binding_passed=True)
        self.assertTrue(result['stage_passed'])
        self.assertFalse(result['escalation_allowed'])
        self.assertFalse(result['formal_capacity_accepted'])
        self.assertTrue(result['single_round_diagnostic'])
        self.assertFalse(transition.formal_acceptance({'diagnostic180':result}))
        with self.assertRaises(transition.SafetyInterrupted):
            transition.require_previous('candidate50',{'diagnostic180':result},f['source_config'])

    def test_failed_candidate_cannot_escalate(self):
        f,p,h,b,o,v=evidence()
        h['operation_measurements'][0]['elapsed_seconds']=11
        result=transition.score_stage('candidate10',f,p,h,b,o,persistence=v,browser_persistence=v,
            architecture=v,drain={'passed':True,'budget_seconds':300},load_exit_code=1,
            observer_exit_code=0,stop_reason=None,teacher_low=v,browser_binding_passed=True)
        self.assertFalse(result['escalation_allowed'])
        baseline={**self.score('baseline10'),'source_config':f['source_config']}
        with self.assertRaises(transition.SafetyInterrupted):
            transition.require_previous('candidate50', {'baseline10':baseline,'candidate10':result}, f['source_config'])

    def test_baseline_performance_failure_allows_candidate_only_with_workflow_and_persistence(self):
        result=self.score('baseline10',load_exit_code=1)
        self.assertTrue(result['escalation_allowed'])
        result=self.score('baseline10',persistence={'passed':False},load_exit_code=1)
        self.assertFalse(result['escalation_allowed'])

    def test_error_and_timeout_and_missing_persistence_stop(self):
        for changes in ({'stop_reason':'stage_budget'}, {'load_exit_code':124}, {'persistence':{}},
                        {'stop_reason':'unrecovered_protocol_workflow_error'}):
            with self.subTest(changes=changes):
                self.assertFalse(self.score(**changes)['escalation_allowed'])

    def test_mixed_180_and_rounds_must_each_pass(self):
        first=self.score('natural180-r1',180)
        second=self.score('natural180-r2',180,20261014)
        self.assertEqual(first['overlap']['observed'],180)
        self.assertTrue(first['overlap']['achieved'])
        self.assertFalse(transition.formal_acceptance({'natural180-r1':first}))
        self.assertTrue(transition.formal_acceptance({'natural180-r1':first,'natural180-r2':second}))
        second['stage_passed']=False
        self.assertFalse(transition.formal_acceptance({'natural180-r1':first,'natural180-r2':second}))

    def test_wrong_identity_or_infinite_interval_cannot_count_for_overlap(self):
        for mutate in ('duplicate','infinite'):
            f,p,h,b,o,v=evidence(180)
            if mutate=='duplicate': b['probes'][0]['username_sha256']=h['students'][0]['username_sha256']
            else: b['probes'][0]['completed_at']=math.inf
            result=transition.score_stage('natural180-r1',f,p,h,b,o,persistence=v,browser_persistence=v,
                architecture=v,drain={'passed':True,'budget_seconds':300},load_exit_code=0,
                observer_exit_code=0,stop_reason=None,teacher_low=v,browser_binding_passed=True)
            self.assertFalse(result['escalation_allowed'])

    def test_successful_only_summary_cannot_hide_failed_operation(self):
        f,p,h,b,o,v=evidence()
        h['operation_measurements'][0]['successful']=False
        result=transition.score_stage('candidate10',f,p,h,b,o,persistence=v,browser_persistence=v,
            architecture=v,drain={'passed':True,'budget_seconds':300},load_exit_code=0,
            observer_exit_code=0,stop_reason=None,teacher_low=v,browser_binding_passed=True)
        self.assertFalse(result['escalation_allowed'])
        self.assertEqual(result['performance']['protocol']['login_entry_to_authentication_end']['failed_count'],1)

    def test_observer_must_cover_browser_and_protocol(self):
        f,p,h,b,o,v=evidence()
        o['ended_at']=1050
        result=transition.score_stage('candidate10',f,p,h,b,o,persistence=v,browser_persistence=v,
            architecture=v,drain={'passed':True,'budget_seconds':300},load_exit_code=0,
            observer_exit_code=0,stop_reason=None,teacher_low=v,browser_binding_passed=True)
        self.assertFalse(result['checks']['observer_covers_load'])

    def test_login180_combines_exactly_178_protocol_and_two_browsers(self):
        result=self.score('login180',180)
        self.assertTrue(result['stage_passed'])
        self.assertEqual(result['protocol_students'],178)
        self.assertEqual(result['browser_students'],2)
        self.assertEqual(result['combined_login']['first_post_count'],180)

    def test_login180_rejects_missing_browser_or_late_actual_post(self):
        for kind in ('missing','late','nan'):
            f,p,h,b,o,v=evidence(180,login=True)
            if kind=='missing':b['probes']=[]
            else:b['probes'][0]['waterfall'][0]['sent_at']=102 if kind=='late' else math.nan
            result=transition.score_stage('login180',f,p,h,b,o,persistence={},browser_persistence={},architecture={},
                drain={'passed':True,'budget_seconds':300},load_exit_code=0,observer_exit_code=0,stop_reason=None,
                teacher_low={},browser_binding_passed=True,login_session_count=0)
            self.assertFalse(result['escalation_allowed'])

    def test_login_clock_uncertainty_cannot_be_hidden_by_raw_send_window(self):
        f,p,h,b,o,v=evidence(180,login=True)
        calibration=b['clock_calibration']
        calibration.update(server_minus_browser_lower_seconds=-1,server_minus_browser_upper_seconds=1)
        for row in calibration['samples']:row.update(browser_t0=99,browser_t1=101)
        result=transition.score_stage('login180',f,p,h,b,o,persistence={},browser_persistence={},architecture={},
            drain={'passed':True,'budget_seconds':300},load_exit_code=0,observer_exit_code=0,stop_reason=None,
            teacher_low={},browser_binding_passed=True,login_session_count=0)
        self.assertFalse(result['checks']['login_post_send_window_passed'])
        self.assertGreater(result['combined_login']['send_window_upper_bound_seconds'],1)

    def test_failed_browser_with_no_completion_still_yields_failed_stage_evidence(self):
        f,p,h,b,o,v=evidence()
        b['probes'][0].update(completed=False,completed_at=None)
        result=transition.score_stage('candidate10',f,p,h,b,o,persistence=v,browser_persistence=v,
            architecture=v,drain={'passed':True,'budget_seconds':300},load_exit_code=0,
            observer_exit_code=0,stop_reason=None,teacher_low=v,browser_binding_passed=True)
        self.assertFalse(result['escalation_allowed'])

    def test_memory_grace_and_health_and_failed_request_windows(self):
        guard=transition.StageGuard()
        sample={'memory_available_bytes':1,'production_healthy':True,'disk_error':False}
        self.assertIsNone(guard.update(sample,now=0))
        self.assertIsNone(guard.update(sample,now=14))
        self.assertEqual(guard.update(sample,now=15),'host_memory_below_2_gib_15_seconds')
        guard=transition.StageGuard()
        sample.update(memory_available_bytes=4*1024**3,production_healthy=False)
        self.assertIsNone(guard.update(sample,now=0))
        self.assertIsNone(guard.update(sample,now=1))
        self.assertEqual(guard.update(sample,now=2),'production_health_failed_three_times')
        guard=transition.StageGuard()
        for at in (1,31):guard.add_request({'completed_at':at,'successful':False})
        self.assertEqual(guard.update({**sample,'production_healthy':True},now=60),'business_failure_two_windows')

    def test_drain_is_independent_bounded_budget(self):
        class Clock:
            value=0
            def now(self):return self.value
            def sleep(self,seconds):self.value+=seconds
        clock=Clock()
        result=transition.bounded_drain(lambda:{'outbox':1},lambda:None,clock=clock.now,sleep=clock.sleep)
        self.assertFalse(result['passed'])
        self.assertEqual(result['elapsed_seconds'],300)
        self.assertEqual(result['budget_seconds'],300)

    def test_drain_deadline_interrupt_is_preserved_as_failed_evidence(self):
        def interrupted():raise transition.DrainBudgetExpired()
        result=transition.bounded_drain(interrupted,lambda:None)
        self.assertFalse(result['passed'])
        self.assertEqual(result['budget_seconds'],300)

    def test_private_docker_load_timeout_stops_owned_load(self):
        stopped=[]
        class Runtime(transition.TransitionRuntime):
            def stop_load(self):stopped.append(self.load_name)
        runtime=Runtime.__new__(Runtime)
        runtime.load_name='previous'
        with patch.object(transition.ArchitectureRuntime,'run_private',return_value=124):
            result=runtime.run_private(['docker','run','--name','owned-teacher'],Path('.'),'teacher',420)
        self.assertEqual(result,124)
        self.assertEqual(stopped,['owned-teacher'])
        self.assertEqual(runtime.load_name,'previous')

    def test_source_role_change_blocks_predecessor(self):
        source={'source_sha256':'a','frontend_sha256':'b','config_sha256':'c','roles':{'web':{'workers':16}}}
        previous={**self.score(), 'source_config':copy.deepcopy(source)}
        baseline={**self.score('baseline10'),'source_config':copy.deepcopy(source)}
        transition.require_previous('candidate50',{'baseline10':baseline,'candidate10':previous},source)
        source['roles']['web']['workers']=12
        with self.assertRaises(transition.SafetyInterrupted):
            transition.require_previous('candidate50',{'baseline10':baseline,'candidate10':previous},source)

    def test_fresh_baseline_and_candidate_allowlists_can_differ(self):
        source={'roles':{},'admission':{'enabled':True,'capacity':8,'task_ids':[2]},
            'session_timeout_seconds':5,'diagnostics_mode':'light'}
        baseline={**self.score('baseline10'),'source_config':copy.deepcopy(source)}
        baseline['source_config']['admission']['task_ids']=[1]
        transition.require_previous('candidate10',{'baseline10':baseline},source)
        source['admission']['capacity']=9
        with self.assertRaises(transition.SafetyInterrupted):
            transition.require_previous('candidate10',{'baseline10':baseline},source)

    def test_missing_browser_input_is_not_hidden_by_completed_flag(self):
        f,p,h,b,o,v=evidence()
        b['probes'][0]['actions']=[row for row in b['probes'][0]['actions'] if row['action']!='answer_input']
        result=transition.score_stage('candidate10',f,p,h,b,o,persistence=v,browser_persistence=v,
            architecture=v,drain={'passed':True,'budget_seconds':300},load_exit_code=0,
            observer_exit_code=0,stop_reason=None,teacher_low=v,browser_binding_passed=True)
        self.assertFalse(result['checks']['browser_workflow_completed'])

    def test_run_adapter_freezes_cli_and_retains_independent_evidence(self):
        fixture,plan,http,browser,observation,verified=evidence()
        calls=[]
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);attempt=root/'candidate10';attempt.mkdir()
            bridge=root/'bridge'/'candidate10';bridge.mkdir(parents=True)
            binding={'synthetic':'bound'}
            (attempt/'fixture-binding.json').write_text(json.dumps(binding))
            (attempt/'fixture.ledger.json').write_text(json.dumps({'sessions':{},'incomplete':False}))
            (attempt/'fixture.json').write_text(json.dumps(fixture))
            (bridge/'browser-binding.json').write_text(json.dumps(binding))
            class FakeRuntime(transition.TransitionRuntime):
                def __init__(self):
                    self.private=root;self.bridge_root=root/'bridge';self.history_root=root/'history'
                    self.children=[];self.compose=['docker','compose'];self.config={'run_id':'unit','images':{},'source_config':{}}
                    self.guard=transition.StageGuard();self.load_name=None;self.native_probe=None
                @property
                def observer_script(self):return root/'observer.py'
                @property
                def runtime_roles(self):return ('web',)
                def assert_prepared(self,stage,directory):return fixture,plan
                def verify_runtime(self):pass
                def verify_gateway_routing(self,directory):pass
                def db_json(self,sql):return 0
                def load_command(self,directory,identity,args):return ['docker','run','--name','unit-load',*args]
                def teacher_command(self,directory,mode):return ['docker','run','--name','unit-teacher',mode]
                def direct_command(self,directory,args):return args
                def safety_sample(self,directory):return {'memory_available_bytes':4*1024**3,'production_healthy':True,'disk_error':False}
                def stop_load(self):calls.append('stop-own-load')
                def drain(self,directory,task):return {'passed':True,'budget_seconds':300}
                def history(self):return {}
                def run_private(self,command,directory,label,timeout):
                    calls.append(label)
                    if label in ('persistence','browser-persistence','architecture-results','teacher-low'):
                        (directory/(label+'.json')).write_text(json.dumps(verified))
                    elif label=='services':(directory/'services.log').write_text('')
                    return 0
            runtime=FakeRuntime()
            class Process:
                def __init__(self,command,**kwargs):
                    self.command=command;self.returncode=None;calls.append(command)
                    if 'scripts/load/school_natural_load.py' in command:
                        (attempt/'http.json').write_text(json.dumps(http))
                        (bridge/'browser.json').write_text(json.dumps(browser));self.returncode=0
                    elif str(root/'observer.py') in command:
                        (attempt/'observation.json').write_text(json.dumps(observation))
                def poll(self):return self.returncode
                def wait(self,timeout):self.returncode=0;return 0
            class Teacher:
                def stop_load(self):calls.append('stop-own-teacher')
            with patch.object(transition.subprocess,'Popen',Process),patch.object(transition.time,'sleep',lambda _:None),patch.object(transition,'TransitionRuntime',lambda _:Teacher()):
                result=runtime.run_stage('candidate10',transition.time.time()+60)
            self.assertTrue(result['stage_passed'])
            command=next(row for row in calls if isinstance(row,list) and 'scripts/load/school_natural_load.py' in row)
            self.assertEqual(command[command.index('--timeout')+1],'35')
            self.assertEqual(command[command.index('--total-budget-seconds')+1],'2700')
            self.assertTrue({'persistence','browser-persistence','architecture-results','teacher-low'}.issubset(set(row for row in calls if isinstance(row,str))))
            self.assertTrue((root/'history'/'candidate10.json').exists())

    def test_slow_browser_operation_keeps_fast_request_ids_and_server_trace(self):
        browser={'probes':[{'username_sha256':'identity','actions':[{'action':'submit_next_ready',
            'start_ms':10,'duration_ms':4000,'completed':True}], 'waterfall':[{'request_id':'fast-request',
            'start_ms':20,'end_ms':200,'duration_ms':180,'status':200}]}]}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'services.log').write_text('web | '+json.dumps({'request_id':'fast-request','phase':'request_complete'}))
            result=transition.slow_operations({},browser,root)
        self.assertEqual(result['operations'][0]['request_ids'],['fast-request'])
        self.assertEqual(result['server_traces'][0]['request_id'],'fast-request')

    def test_seed_fixture_includes_actual_teacher_account_for_probe(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            task=SimpleNamespace(experiment=SimpleNamespace(course_id=10,owner=SimpleNamespace(username='actual-owner')),
                experiment_id=20,class_group_id=30)
            manager=SimpleNamespace(select_related=lambda *_:SimpleNamespace(get=lambda **_:task))
            models=SimpleNamespace(ClassExperimentTask=SimpleNamespace(objects=manager))
            def call_command(name,**kwargs):kwargs['stdout'].write(json.dumps({'task_id':40}))
            class Runtime(transition.TransitionRuntime):
                def direct_command(self,attempt,args):return args
                def run_private(self,command,attempt,label,timeout):
                    script=command[-1].replace('/attempt/',str(attempt).replace('\\','/')+'/')
                    with patch.dict(sys.modules,{'django.core.management':SimpleNamespace(call_command=call_command),
                        'django.db':SimpleNamespace(connection=SimpleNamespace(settings_dict={'NAME':'kt_e2e'})),
                        'apps.experiments.models':models}):exec(script,{})
                    return 0
            runtime=Runtime.__new__(Runtime);runtime.private=root;runtime.config={'run_id':'seed-test'}
            runtime.seed_stage('candidate10')
            fixture=json.loads((root/'candidate10'/'unbound-fixture.json').read_text())
            self.assertEqual(fixture['teacher_username'],'actual-owner')


class SixPoolProfileTests(unittest.TestCase):
    def source(self, six=True):
        source = {'phase': 'candidate' if six else 'baseline', 'baseline_commit': 'e64535f',
            'host_budget': {'vcpu': 16, 'memory_bytes': 16 * 1024**3},
            'durability': {'fsync': 'on', 'synchronous_commit': 'on', 'full_page_writes': 'on'},
            'workload_profile': 'natural-180-v1', 'session_timeout_seconds': 5, 'diagnostics_mode': 'light',
            'admission': {'enabled': True, 'capacity': 8, 'task_ids': [1]},
            'source_sha256': 'a', 'frontend_sha256': 'b', 'config_sha256': 'c',
            'roles': {'web': {'workers': 16}, 'student-read': {'workers': 2, 'cpus': 2},
                      'teacher-read': {'workers': 1}}}
        if six:
            source['pool_profile'] = 'six-pools-180-v1'
            source['roles'] = {role: {'workers': workers, 'memory_bytes': memory, 'cpus': 0,
                'cpu_shares': shares} for role, workers, memory, shares in (
                ('web', 1, 512 * 1024**2, 512), ('auth', 4, 1024**3, 2048),
                ('assessment', 24, 4 * 1024**3, 4096), ('capture', 4, 1280 * 1024**2, 2048),
                ('student-read', 2, 768 * 1024**2, 512), ('teacher-read', 1, 512 * 1024**2, 256))}
        policy = {'appendonly': 'yes', 'appendfsync': 'always', 'maxmemory-policy': 'noeviction', 'save': ''}
        source['roles'].update({'redis-session': {'memory_bytes': 512 * 1024**2, 'cpus': 1, 'redis_policy': policy.copy()},
            'redis-admission': {'memory_bytes': 256 * 1024**2, 'cpus': .25, 'redis_policy': policy.copy()}})
        return source

    def record(self, stage, source):
        return {'stage': stage, 'escalation_allowed': True, 'source_config': copy.deepcopy(source)}

    def test_six_pool_predecessors_skip_login_storm_but_require_candidate_checks(self):
        source = self.source()
        history = {stage: self.record(stage, source) for stage in ('candidate10', 'candidate50')}
        history['baseline10'] = self.record('baseline10', self.source(False))
        transition.require_previous('natural180-r1', history, source)
        del history['candidate50']
        with self.assertRaises(transition.SafetyInterrupted):
            transition.require_previous('natural180-r1', history, source)

    def test_profile_exception_still_binds_host_durability_and_workload(self):
        source = self.source()
        for key, value in (('host_budget', {'vcpu': 32, 'memory_bytes': 16 * 1024**3}),
                           ('durability', {'fsync': 'off'}), ('workload_profile', 'easier')):
            baseline = self.source(False)
            baseline[key] = value
            with self.subTest(key=key), self.assertRaises(transition.SafetyInterrupted):
                transition.require_previous('candidate10', {'baseline10': self.record('baseline10', baseline)}, source)

    def test_baseline_exception_keeps_redis_resources_and_durability_identical(self):
        source = self.source()
        for role in ('redis-admission', 'redis-session'):
            for field, value in (('memory_bytes', 1), ('cpus', 2), ('redis_policy', {'appendfsync': 'everysec'})):
                baseline = self.source(False)
                baseline['roles'][role][field] = value
                with self.subTest(role=role, field=field), self.assertRaises(transition.SafetyInterrupted):
                    transition.require_previous('candidate10', {'baseline10': self.record('baseline10', baseline)}, source)

    def test_new_profile_baseline_may_use_independent_task_allowlist(self):
        source = self.source()
        source['admission']['task_ids'] = [2]
        transition.require_previous('candidate10', {'baseline10': self.record('baseline10', self.source(False))}, source)

    def test_profile_candidate_still_requires_exact_frozen_source(self):
        source = self.source()
        history = {'baseline10': self.record('baseline10', self.source(False)),
                   'candidate10': self.record('candidate10', source)}
        history['candidate10']['source_config']['pool_profile'] = None
        with self.assertRaises(transition.SafetyInterrupted):
            transition.require_previous('candidate50', history, source)

    def test_new_profile_validates_fixed_workers_memory_and_cpu_priority(self):
        transition.validate_transition_source(self.source())
        transition.validate_transition_source(self.source(False))
        for field, value in (('workers', 16), ('memory_bytes', 1024**3), ('memory_bytes', 2 * 1024**3), ('cpus', 2), ('cpu_shares', 0)):
            source = self.source()
            source['roles']['assessment'][field] = value
            with self.subTest(field=field), self.assertRaises(transition.SafetyInterrupted):
                transition.validate_transition_source(source)

    def test_gateway_routes_use_safe_methods_and_new_roles(self):
        runtime = transition.TransitionRuntime.__new__(transition.TransitionRuntime)
        runtime.config = {'source_config': self.source()}
        self.assertIn(('auth', 'GET', '/api/v1/auth/csrf/', (200,)), runtime.gateway_routes)
        self.assertIn(('assessment', 'GET', '/api/v1/student/tasks/1/recovery/', (401, 403)), runtime.gateway_routes)
        self.assertIn(('capture', 'POST', '/api/v1/assessment/process/capture-v2/batch/', (401, 403)), runtime.gateway_routes)

    def test_pgbouncer_budget_requires_fixed_user_exact_aliases_and_reserves(self):
        pools = {'kt_core': (1, 0), 'kt_auth': (4, 0), 'kt_assessment': (8, 2),
                 'kt_capture': (4, 1), 'kt_student': (2, 0), 'kt_teacher': (1, 0), 'kt_background': (3, 0)}
        config = '[databases]\n' + '\n'.join(f'{alias} = host=school-test-postgres port=5432 dbname=kt_e2e user=kt_e2e_user pool_size={size} reserve_pool_size={reserve}'
            for alias, (size, reserve) in pools.items()) + '\n[pgbouncer]\npool_mode=transaction\ndefault_pool_size=0\n'
        transition.validate_pool_budget(config)
        for invalid in (config.replace('pool_size=8', 'pool_size=16'),
                        config.replace(' user=kt_e2e_user', ''),
                        config.replace('reserve_pool_size=2', 'reserve_pool_size=3'),
                        config.replace('pool_mode=transaction', 'pool_mode=session')):
            with self.assertRaises(transition.SafetyInterrupted):
                transition.validate_pool_budget(invalid)

    def container_runtime(self):
        from school_diagnostic_runtime import DiagnosticRuntime, ROLES, REDIS_ROLE, SIX_POOL_ALIASES
        source = self.source()
        source['roles'][REDIS_ROLE] = {'memory_bytes': 128 * 1024**2, 'cpus': .25}
        roles = ROLES + ('auth', 'assessment', 'capture', REDIS_ROLE)
        runtime = DiagnosticRuntime.__new__(DiagnosticRuntime)
        runtime.config = {'source_config': source, 'images': {role: 'sha256:' + 'a' * 64 for role in roles}}
        items = []
        for role in roles:
            expected = source['roles'].get(role, {})
            env = {'POSTGRES_DB': 'kt_e2e', 'POSTGRES_USER': 'kt_e2e_user',
                'POSTGRES_HOST': 'school-test-postgres' if role == 'scheduler' else 'pgbouncer',
                'POSTGRES_POOL_ALIAS': SIX_POOL_ALIASES.get(role, ''),
                'PGBOUNCER_SERVER_HOST': 'school-test-postgres', 'CAPTURE_V2_ENABLED': '1',
                'CAPTURE_V2_COURSE_IDS': '*'}
            if 'workers' in expected:
                env.update(GUNICORN_WORKERS=str(expected['workers']), ASSESSMENT_ADMISSION_ENABLED='1',
                    ASSESSMENT_ADMISSION_TASK_IDS='1', ASSESSMENT_ADMISSION_CAPACITY='8',
                    ASSESSMENT_ADMISSION_REDIS_URL='redis://redis-admission:6379/0')
            items.append({'Id': role, 'Image': runtime.config['images'][role], 'State': {'Running': True},
                'Config': {'Env': [f'{key}={value}' for key, value in env.items()], 'Labels': {
                    'com.docker.compose.project': transition.PROJECT, 'com.docker.compose.service': role}},
                'HostConfig': {'Memory': expected.get('memory_bytes', 1),
                    'NanoCpus': round(expected.get('cpus', 0) * 1e9), 'CpuShares': expected.get('cpu_shares', 0),
                    'PortBindings': {'80/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18080'}]} if role == 'nginx' else {}},
                'NetworkSettings': {'Networks': {transition.PROJECT + '_default': {}}}})
        return runtime, items

    def test_runtime_requires_all_six_roles_aliases_and_resource_priorities(self):
        runtime, items = self.container_runtime()
        self.assertEqual(len(runtime.validate_containers(items)), 14)
        self.assertEqual(set(runtime.http_workers), {'web', 'auth', 'assessment', 'capture', 'student-read', 'teacher-read'})
        with self.assertRaises(transition.SafetyInterrupted):
            runtime.validate_containers([item for item in items if item['Id'] != 'capture'])
        for field, value in (('Memory', 1024**3), ('NanoCpus', 2 * 10**9), ('CpuShares', 256)):
            changed = copy.deepcopy(items)
            next(item for item in changed if item['Id'] == 'assessment')['HostConfig'][field] = value
            with self.subTest(field=field), self.assertRaises(transition.SafetyInterrupted):
                runtime.validate_containers(changed)
        changed = copy.deepcopy(items)
        env = next(item for item in changed if item['Id'] == 'capture')['Config']['Env']
        env[env.index('POSTGRES_POOL_ALIAS=kt_capture')] = 'POSTGRES_POOL_ALIAS=kt_core'
        with self.assertRaises(transition.SafetyInterrupted):
            runtime.validate_containers(changed)

    def test_legacy_runtime_does_not_accept_extra_roles(self):
        runtime, items = self.container_runtime()
        runtime.config['source_config'].pop('pool_profile')
        with self.assertRaises(transition.SafetyInterrupted):
            runtime.validate_containers(items)

    def test_six_pool_gateway_witness_checks_each_method_and_correlated_role(self):
        import school_architecture_runtime as architecture
        runtime = architecture.ArchitectureRuntime.__new__(architecture.ArchitectureRuntime)
        routes = ('auth', 'assessment', 'capture', 'student-read', 'teacher-read')
        runtime.config = {'source_config': self.source(), 'images': {role: 'sha256:' + 'a' * 64 for role in routes}}
        seen = {}
        containers = [{'Id': role + '-id', 'Image': runtime.config['images'][role], 'State': {'Running': True},
            'Config': {'Labels': {'com.docker.compose.project': transition.PROJECT, 'com.docker.compose.service': role}}}
            for role in routes]
        def request(req, timeout):
            role, method, _, statuses = next(route for route in runtime.gateway_routes if req.full_url.endswith(route[2]))
            self.assertEqual(req.method, method)
            self.assertEqual(req.data, b'{}' if role == 'capture' else None)
            seen[req.get_header('X-request-id')] = role
            raise architecture.urllib.error.HTTPError(req.full_url, statuses[0], '', {}, None)
        def docker(command, **kwargs):
            if command[1] == 'ps':
                output = '\n'.join(role + '-id' for role in routes)
            elif command[1] == 'inspect':
                output = json.dumps(containers)
            else:
                role = command[-1].removesuffix('-id')
                output = '\n'.join(json.dumps({'phase': 'gunicorn_request_complete', 'request_id': identity})
                    for identity, expected in seen.items() if expected == role)
            return SimpleNamespace(returncode=0, stdout=output, stderr='')
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(architecture.subprocess, 'run', side_effect=docker), \
                patch.object(architecture.urllib.request, 'build_opener', return_value=SimpleNamespace(open=request)):
            self.assertTrue(runtime.verify_gateway_routing(Path(directory))['passed'])
        self.assertEqual(len(seen), 5)

    def owned_postgres(self):
        runtime, items = self.container_runtime()
        source = runtime.config['source_config']
        source['postgres_profile'] = 'isolated-pg17-v1'
        source['database_host'] = 'school-pool-postgres'
        source['roles']['db'] = {'memory_bytes': 4 * 1024**3, 'cpus': 0, 'cpu_shares': 3122}
        runtime.config['database_container'] = 'kt-school-e2e-db-1'
        runtime.config['images']['db'] = 'sha256:' + 'b' * 64
        db = {'Id': 'owned-postgres', 'Name': '/kt-school-e2e-db-1', 'Image': runtime.config['images']['db'], 'State': {'Running': True},
            'Config': {'Env': ['POSTGRES_DB=kt_e2e', 'POSTGRES_USER=kt_e2e_user'], 'Labels': {
                'com.docker.compose.project': transition.PROJECT, 'com.docker.compose.service': 'db'}},
            'HostConfig': {'Memory': 4 * 1024**3, 'NanoCpus': 0, 'CpuShares': 3122, 'PortBindings': {}},
            'NetworkSettings': {'Networks': {transition.PROJECT + '_default': {
                'Aliases': ['db', 'school-pool-postgres'], 'IPAddress': '172.18.0.20'}}},
            'Mounts': [{'Type': 'volume', 'Name': 'kt-school-e2e_pool_postgres_data',
                'Destination': '/var/lib/postgresql/data', 'RW': True}]}
        for item in items:
            env = item['Config']['Env']
            env[:] = [value.replace('=school-test-postgres', '=school-pool-postgres') for value in env]
        return runtime, items + [db]

    def test_owned_postgres_profile_adds_only_owned_database_and_alias(self):
        runtime, items = self.owned_postgres()
        self.assertEqual(runtime.database_container, 'kt-school-e2e-db-1')
        self.assertEqual(runtime.database_host, 'school-pool-postgres')
        self.assertIn('db', runtime.runtime_roles)
        self.assertEqual(len(runtime.validate_containers(items)), 15)
        for mutate in (
            lambda db: db['Config']['Labels'].update({'com.docker.compose.project': 'kt_plantform'}),
            lambda db: db['HostConfig'].update(Memory=8 * 1024**3),
            lambda db: db['HostConfig'].update(NanoCpus=2 * 10**9),
            lambda db: db['HostConfig'].update(PortBindings={'5432/tcp': [{}]}),
            lambda db: db['Mounts'][0].update(Name='kt_plantform_postgres_data'),
            lambda db: db['NetworkSettings']['Networks'].update({'production_default': {}}),
        ):
            changed = copy.deepcopy(items)
            mutate(changed[-1])
            with self.assertRaises(transition.SafetyInterrupted):
                runtime.validate_containers(changed)
        runtime.config['database_container'] = 'kt_plantform-db-1'
        with self.assertRaises(transition.SafetyInterrupted):
            _ = runtime.database_container

    def test_legacy_database_binding_still_uses_designated_shared_postgres(self):
        runtime, _ = self.container_runtime()
        self.assertEqual(runtime.database_container, 'kt_plantform-db-1')
        self.assertEqual(runtime.database_host, 'school-test-postgres')
        self.assertNotIn('db', runtime.runtime_roles)

    def test_owned_pg17_settings_reject_tuning_version_or_durability_changes(self):
        expected = {'server_version_num': 170011, 'max_connections': 64, 'shared_buffers': 1024**3,
            'effective_cache_size': 3 * 1024**3, 'work_mem': 4 * 1024**2,
            'maintenance_work_mem': 128 * 1024**2, 'autovacuum_work_mem': 64 * 1024**2,
            'max_wal_size': 2 * 1024**3, 'checkpoint_completion_target': .9,
            'fsync': 'on', 'synchronous_commit': 'on', 'full_page_writes': 'on'}
        transition.validate_owned_postgres_settings(expected)
        for key, value in (('server_version_num', 180001), ('shared_buffers', 2 * 1024**3),
                           ('max_connections', 100), ('full_page_writes', 'off')):
            with self.subTest(key=key), self.assertRaises(transition.SafetyInterrupted):
                transition.validate_owned_postgres_settings({**expected, key: value})

    def test_owned_pg_direct_operations_and_probe_use_owned_target(self):
        runtime, _ = self.owned_postgres()
        runtime.compose = ['docker', 'compose', '-p', transition.PROJECT]
        runtime.private = Path('private')
        self.assertIn('POSTGRES_HOST=school-pool-postgres', runtime.direct_command(Path('attempt'), ['manage.py', 'check']))
        calls = []
        runtime.capture = lambda command, **kwargs: calls.append(command) or '{"ok":true}'
        self.assertTrue(runtime.db_json("SELECT json_build_object('ok',true)")['ok'])
        self.assertEqual(calls[0][2], 'kt-school-e2e-db-1')


class LeaseRecoveryScoringTests(unittest.TestCase):
    def prepared(self):
        from school_natural_load import lease_recovery_accounting
        from test_natural_workload import SameClientLeaseEvidenceTests
        fixture, plan, http, browser, observation, verified = evidence()
        _, attempts, markers = SameClientLeaseEvidenceTests().evidence()
        markers[0]['username_sha256'] = digest(plan['students'][2]['username'])
        fixture['source_config']['load_evidence_profile'] = 'same-client-lease-recovery-v1'
        http.update(load_evidence_profile='same-client-lease-recovery-v1', http_error_count=1,
            http_request_count=len(attempts), http_error_accounting=lease_recovery_accounting(plan, attempts, markers, 1))
        return fixture, plan, http, browser, observation, verified, attempts

    def score(self, fixture, plan, http, browser, observation, verified, attempts):
        return transition.score_stage('candidate10', fixture, plan, http, browser, observation,
            persistence=verified, browser_persistence=verified, architecture=verified,
            drain={'passed': True, 'budget_seconds': 300}, load_exit_code=0, observer_exit_code=0,
            stop_reason=None, teacher_low=verified, browser_binding_passed=True, request_attempts=attempts)

    def test_new_profile_accepts_exact_journal_bound_recovery_without_hiding_raw_error(self):
        values = self.prepared()
        result = self.score(*values)
        self.assertTrue(result['stage_passed'])
        self.assertEqual(values[2]['http_error_count'], 1)

    def test_legacy_unknown_profile_missing_journal_and_forged_accounting_fail(self):
        for variant in ('legacy', 'unknown', 'missing_journal', 'forged_count', 'changed_journal', 'failed_primary_operation'):
            fixture, plan, http, browser, observation, verified, attempts = self.prepared()
            if variant == 'legacy':
                fixture['source_config'].pop('load_evidence_profile')
            elif variant == 'unknown':
                fixture['source_config']['load_evidence_profile'] = 'ignore-conflicts'
            elif variant == 'missing_journal':
                attempts = None
            elif variant == 'forged_count':
                http['http_error_accounting']['raw_http_error_count'] = 0
            elif variant == 'changed_journal':
                attempts[0]['server_error_code'] = 'unrecognized_error_code'
            else:
                http['full_operation_accounting']['save_draft']['failed_count'] = 1
            with self.subTest(variant=variant):
                self.assertFalse(self.score(fixture, plan, http, browser, observation, verified, attempts)['stage_passed'])

    def test_evidence_profile_is_frozen_across_predecessors_and_unknown_rejected(self):
        source = SixPoolProfileTests().source()
        source['load_evidence_profile'] = 'same-client-lease-recovery-v1'
        baseline = SixPoolProfileTests().source(False)
        record = {'stage': 'baseline10', 'escalation_allowed': True, 'source_config': baseline}
        with self.assertRaises(transition.SafetyInterrupted):
            transition.require_previous('candidate10', {'baseline10': record}, source)
        baseline['load_evidence_profile'] = source['load_evidence_profile']
        transition.require_previous('candidate10', {'baseline10': record}, source)
        source['load_evidence_profile'] = 'ignore-conflicts'
        with self.assertRaises(transition.SafetyInterrupted):
            transition.validate_transition_source(source)


if __name__=='__main__': unittest.main()
