#!/usr/bin/env python3
"""Independent natural student lifecycles; browser-compatible immutable capture facts."""
import argparse
import concurrent.futures
from copy import deepcopy
import json
import math
import os
import re
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit
import uuid

from capture_protocol import BROWSER_ALIGNED_WORKLOAD, CONTRACT, digest, evidence_files
from natural_workload import CONFIDENCE_ONLY, NATURAL_WORKLOAD, confidence_decision, validate_workload
from school_synchronized_180 import Synchronized180Runner, InternalBudgetExceeded, AttemptJournal
from school_short_storm import StudentState, summary

LEASE_RECOVERY_PROFILE = 'same-client-lease-recovery-v1'
LEASE_RENEW_PATH = '/api/v1/assessment/process/attempts/:id/writer-lease/renew/'
LEASE_ACQUIRE_PATH = '/api/v1/assessment/process/attempts/:id/writer-lease/acquire/'


def lease_recovery_accounting(plan, attempts, markers, raw_error_count):
    """Classify only journal-linked, same-client recovery; preserve every failed row."""
    failed = [row for row in attempts if row.get('successful') is not True]
    by_id = {row.get('request_id'): (index, row) for index, row in enumerate(attempts)}
    valid = (type(raw_error_count) is int and raw_error_count == len(failed)
        and len(by_id) == len(attempts) and all(re.fullmatch('[a-f0-9]{32}', str(key)) for key in by_id))
    recovered, acquired, invalid = set(), set(), 0
    successful_attempts = []
    for marker in markers:
        try:
            renew_index, renew = by_id[marker['renew_request_id']]
            acquire_index, acquire = by_id[marker['acquire_request_id']]
            student_index = marker['student_index']
            student = plan['students'][student_index] if type(student_index) is int and student_index >= 0 else {}
            times = [renew['sent_at'], renew['completed_at'], acquire['sent_at'], acquire['completed_at'],
                     renew['lease_cached_valid_until'], renew['elapsed_seconds'], acquire['elapsed_seconds']]
            matches = (student.get('role') == 'protocol' and marker['username_sha256'] == digest(student['username'])
                and renew['student_index'] == acquire['student_index'] == student_index
                and renew['method'] == acquire['method'] == 'POST'
                and renew['path'] == LEASE_RENEW_PATH and acquire['path'] == LEASE_ACQUIRE_PATH
                and renew['successful'] is False and renew['http_status'] == 409 and renew.get('error_type') == 'HTTPError'
                and renew.get('server_error_code') == 'writer_lease_conflict'
                and acquire['successful'] is True and acquire['http_status'] == 201
                and acquire.get('error_type') is None and acquire.get('server_error_code') is None
                and all(type(value) in (int, float) and math.isfinite(value) for value in times)
                and renew['sent_at'] <= renew['completed_at'] <= acquire['sent_at'] <= acquire['completed_at']
                and renew['sent_at'] < renew['lease_cached_valid_until'] <= renew['sent_at'] + 5
                and renew['elapsed_seconds'] >= 0 and acquire['elapsed_seconds'] >= 0
                and renew_index < acquire_index
                and not any(row.get('student_index') == student_index for row in attempts[renew_index + 1:acquire_index])
                and marker['renew_request_id'] not in recovered and marker['acquire_request_id'] not in acquired)
            for field in ('writer_client_sha256', 'lease_scope_sha256'):
                matches = (matches and re.fullmatch('[a-f0-9]{64}', str(marker[field])) is not None
                    and marker[field] == renew.get(field) == acquire.get(field))
            if not matches:
                raise ValueError()
            recovered.add(marker['renew_request_id']); acquired.add(marker['acquire_request_id'])
            successful_attempts.append(deepcopy(acquire))
        except (KeyError, IndexError, TypeError, ValueError):
            invalid += 1
    return {'profile': LEASE_RECOVERY_PROFILE, 'valid': valid and invalid == 0,
        'journal_request_count': len(attempts), 'raw_http_error_count': raw_error_count,
        'handled_successful_recovery_count': len(recovered), 'unexpected_http_error_count': len(failed) - len(recovered),
        'invalid_recovery_marker_count': invalid, 'failed_attempts': deepcopy(failed),
        'successful_recovery_attempts': successful_attempts, 'recovery_markers': deepcopy(markers),
        'failed_attempt_latency': summary([row['elapsed_seconds'] for row in failed if type(row.get('elapsed_seconds')) in (int, float)])}


def peak_rss_bytes():
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)]
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.PeakWorkingSetSize
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if os.sys.platform == 'darwin' else 1024)


class IdentityAttemptJournal(AttemptJournal):
    def __init__(self, output, runner):
        super().__init__(output)
        self.runner = runner

    def append(self, row):
        row['student_index'] = getattr(self.runner.local, 'student_index', None)
        super().append(row)


class LoadStopped(InternalBudgetExceeded):
    pass


class NaturalRunner(Synchronized180Runner):
    def __init__(self,*args,plan,start_at,stop_file=None,total_budget_seconds=2700,
                 authentication_profile='legacy',load_evidence_profile='legacy',**kwargs):
        if authentication_profile not in ('legacy', 'bootstrap-v1'):
            raise ValueError('Unsupported authentication profile')
        self.authentication_profile = authentication_profile
        if load_evidence_profile not in ('legacy', LEASE_RECOVERY_PROFILE):
            raise ValueError('Unsupported load evidence profile')
        self.load_evidence_profile = load_evidence_profile
        self.lease_recoveries = []
        super().__init__(*args,workload_version=BROWSER_ALIGNED_WORKLOAD,**kwargs)
        self.plan,self.start_at,self.stop_file=plan,start_at,Path(stop_file) if stop_file else None
        self.confidence_only = plan.get('scenario') == CONFIDENCE_ONLY
        self.budget_deadline=time.monotonic()+max(0,start_at-time.time())+total_budget_seconds
        self.internal_budget_seconds=total_budget_seconds
        self.require_student_slice_capabilities=True
        self.students=[]
        self.identity_progress_path=None
        self.identity_outcomes=[]
        self.local=threading.local()

    @property
    def authentication_context_path(self):
        return '/api/v1/auth/bootstrap/' if self.authentication_profile == 'bootstrap-v1' else '/api/v1/auth/session/'

    @property
    def released_at(self):
        return getattr(self.local,'released_at',None)

    @released_at.setter
    def released_at(self,value):
        if not hasattr(self,'local'): self.local=threading.local()
        self.local.released_at=value

    def enable_attempt_journal(self, output):
        super().enable_attempt_journal(output)
        self.request_attempts = IdentityAttemptJournal(output, self)

    def publish_identities(self):
        if self.identity_progress_path is None:
            return
        with self.lock:
            temporary = self.identity_progress_path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'workload_version': NATURAL_WORKLOAD, 'task_id': self.task_id,
                'plan_sha256': digest(self.plan), 'observed_at': time.time(), 'students': self.identity_outcomes}), encoding='utf-8')
            os.replace(temporary, self.identity_progress_path)

    def remaining_budget(self):
        if self.stop_file and self.stop_file.exists():
            self.budget_interrupted=True
            raise LoadStopped('External stop file requested cooperative cancellation')
        return super().remaining_budget()

    def wait_until(self,deadline,runtime=None,state=None):
        while time.monotonic()<deadline:
            self.remaining_budget()
            now=time.monotonic()
            if runtime:
                if runtime['dto']['actions'].get('research_tools_url') and now>=runtime['tools_refresh_due']:
                    self._browser_post(state,runtime['dto']['actions']['research_tools_url'],{},'tools_refresh')
                    runtime['tools_refresh_due']=time.monotonic()+10
                if now>=runtime['heartbeat_due']:
                    runtime['stream'].event(runtime['dto']['assignment']['attempt_id'],'activity_heartbeat',{
                        'attempt_id':runtime['dto']['assignment']['attempt_id'],'document_visible':True,'window_focused':True,
                        'last_device_interaction_age_ms':max(0,round((now-runtime['last_interaction'])*1000))})
                    self._browser_checkpoint(state,runtime,'heartbeat')
                    self._browser_flush(state,runtime)
                    runtime['heartbeat_due']=time.monotonic()+runtime['heartbeat_seconds']
                if now>=getattr(state,'browser_sync_due',float('inf')):
                    self._browser_sync(state,runtime)
                    state.browser_sync_due=time.monotonic()+900
            time.sleep(min(.25,max(0,deadline-time.monotonic())))
        self.remaining_budget()

    def _browser_ensure_lease(self, state, runtime):
        # Match writer-lease.ts beforePointerWrite after a natural thinking pause.
        cached_valid = bool(runtime['lease']) and runtime['lease_valid_until'] > time.time()
        state.browser_lease_cached_valid_until = runtime.get('lease_valid_until') if cached_valid else None
        if runtime['lease'] and not cached_valid:
            runtime['lease'] = {}
            runtime.pop('lease_valid_until', None)
        try:
            return super()._browser_ensure_lease(state, runtime)
        except RuntimeError:
            if not cached_valid or getattr(state, 'browser_last_error_code', None) != 'writer_lease_conflict':
                raise
            renew_attempt = deepcopy(getattr(state, 'browser_last_request_attempt', None))
            runtime['lease'] = {}
            runtime.pop('lease_valid_until', None)
            result = super()._browser_ensure_lease(state, runtime)
            acquire_attempt = getattr(state, 'browser_last_request_attempt', None)
            if (self.load_evidence_profile == LEASE_RECOVERY_PROFILE and renew_attempt and acquire_attempt
                    and renew_attempt.get('http_status') == 409 and renew_attempt.get('server_error_code') == 'writer_lease_conflict'
                    and acquire_attempt.get('successful') is True and acquire_attempt.get('http_status') == 201):
                with self.lock:
                    self.lease_recoveries.append({'renew_request_id': renew_attempt['request_id'],
                        'acquire_request_id': acquire_attempt['request_id'], 'student_index': state.load_student_index,
                        'username_sha256': digest(state.username),
                        'writer_client_sha256': renew_attempt.get('writer_client_sha256'),
                        'lease_scope_sha256': renew_attempt.get('lease_scope_sha256')})
            return result
        finally:
            state.browser_lease_cached_valid_until = None

    def browser_open_question(self, state, assignment_id, reason='direct_open', dto=None):
        if not self.confidence_only:
            return super().browser_open_question(state, assignment_id, reason, dto)
        if dto is None:
            material = getattr(state, 'browser_entry_material', None) or self.entry_material(state)
            state.browser_entry_material = deepcopy(material)
            dto = self._browser_post(state, f'/api/v1/student/sessions/{state.session_id}/questions/{assignment_id}/', material, 'enter_item')
        tools = dto.get('research_tools') or {}
        if dto['actions'].get('research_tools_url') or tools.get('rules') or tools.get('hints') or any(
                tools.get(key) for key in ('doubt_enabled', 'check_enabled')):
            raise RuntimeError('Confidence-only fixture exposed a research tool')
        # An empty local presentation prevents querying tools disabled by this fixture.
        projected = {**dto, 'research_tools': {'rules': [], 'hints': []}}
        runtime = super().browser_open_question(state, assignment_id, reason, projected)
        runtime['tools_refresh_due'] = float('inf')
        return runtime

    def input_value(self,state,runtime,value,index):
        self._browser_start_action(state,'answer_input',index)
        dto,stream=runtime['dto'],runtime['stream']
        kind,attempt=dto['question']['question_type'],dto['assignment']['attempt_id']
        stream.event(attempt,'response_input_started',{'question_type':kind})
        target=stream.answer_transition(attempt,kind,runtime['answer'],runtime['head'],value)
        runtime.update(head=target['event_id'],answer=value,last_interaction=time.monotonic())
        self._browser_save(state,runtime,target)
        self._browser_logical_action(state,'answer_input',index)
        return target

    def navigate(self,state,runtime,to,index,action):
        self._browser_start_action(state,action,index)
        dto=self._browser_close(state,runtime,'navigation',to)
        runtime=self.browser_open_question(state,to,'navigation',dto)
        self._browser_logical_action(state,action,index)
        return runtime

    def natural_tools(self,state,runtime,index,student):
        if self.confidence_only:
            return
        for rule in runtime['tools'].get('rules',[]):
            if rule['required']:
                for stage in ('started','finished'): self._browser_tool_action(state,runtime,rule['tool'],stage,rule=rule)
        if index in student['optional_tool_items']:
            hints=runtime['tools'].get('hints',[])
            if hints:
                data={'hint_id':hints[0]['id']}
                source=self._browser_tool_action(state,runtime,'hint','requested',data)
                self._browser_tool_observed(runtime,source,'hint','presented',data,'self_initiated')
                self._browser_tool_action(state,runtime,'hint','presented',data)
                self._browser_tool_observed(runtime,source,'hint','closed',data,'self_initiated')
            else:
                for stage in ('requested','presented','started','finished'):
                    self._browser_tool_action(state,runtime,'check',stage)

    def _submit_material(self,state,runtime,index,target,assignments):
        dto,stream=runtime['dto'],runtime['stream']
        attempt=dto['assignment']['attempt_id']
        kind=dto['question']['question_type']
        if self.confidence_only:
            confidence=state.natural_confidence_decision
        else:
            self._browser_start_action(state,'submit_confirmation_ready',index)
            confidence,contract=None,dto.get('pending_confidence')
            if contract:
                for stage in ('presented','skipped'):
                    stream.event(attempt,'confidence_observed',{'capture_version':'capture-v2','operation_id':str(uuid.uuid4()),
                        'confidence_assignment_id':contract['confidence_assignment_id'],'stage':stage,'answer_event_id':runtime['head'],
                        'probability':None,'feedback_phase':'before_feedback'},event_version=2 if contract['scale_version']=='v2' else 1)
                    if stage=='presented': self._browser_post(state,contract['prompt_shown_url'],{},'confidence_prompt')
                confidence={'status':'skipped','answer_event_id':runtime['head']}
            self._browser_logical_action(state,'submit_confirmation_ready',index)
            self._browser_start_action(state,'submit_next_ready',index)
        token=str(uuid.uuid4())
        commit=stream.event(attempt,'response_commit_requested',{'submission_token':token})
        self._browser_ensure_lease(state,runtime)
        body={'submission_token':token,'target_event_id':runtime['head'],'client_clicked_at':commit['client_wall_time_utc'],**runtime['lease']}
        if confidence: body['confidence']=confidence
        stream.checkpoint('commit')
        body['atomic_submission']={'version':1,'client_instance_id':stream.client_instance_id,'event_boundary':stream.sequence,
            'events':stream.pending_events(),'checkpoints':self._browser_freeze_bundle(runtime)['checkpoints'],'expected_version':runtime['version']}
        result=self._browser_post(state,dto['actions']['submit_url'],body,'submit_item')
        if result.get('status')!='submitted' or result.get('committed') is not True or result.get('final_response_event_id')!=runtime['head']:
            raise RuntimeError('Atomic submission receipt mismatch')
        if self.confidence_only:
            state.natural_submission_ack_at=time.perf_counter()
            with self.lock:
                self.ledger[str(state.session_id)]['confidence'][str(attempt)].update(acknowledged=True,
                    submission_token=token, submission_receipt_sha256=digest({key:result[key] for key in
                        ('committed','acknowledgements','checkpoint_ids','final_response_event_id','response_version')}))
        stream.accept_acknowledgements(result['acknowledgements'])
        stream.acked_checkpoint_ids.update(result['checkpoint_ids'])
        self._browser_checkpoint(state,runtime,'ack_progress')
        stream.event(attempt,'feedback_shown',{'feedback_mode':result.get('feedback_mode','none'),
            'included_correctness':'is_correct' in result,'included_correct_answer':'correct_answer' in result,
            'included_explanation':bool(result.get('explanation'))})
        if result.get('explanation'): stream.event(attempt,'explanation_shown',{'source':'submission_feedback'})
        next_path=urlsplit(result['next_url']).path
        if index<19 and next_path!=f'/student/sessions/{state.session_id}/questions/{assignments[index+1]}/':
            raise RuntimeError('Submission next question drifted')
        entry_dto=self._browser_close(state,runtime,'submit',assignments[index+1]) if index<19 else self._browser_close(state,runtime,'submit')
        with self.lock:
            self.ledger[str(state.session_id)]['answers'][str(attempt)]={'state_sha256':target['payload']['after_state_hash'],
                'target_event_id':runtime['head'],'submission_token':token,'question_type':kind,
                'response_version':result.get('response_version',runtime['version'])}
        if index<19:
            runtime=self.browser_open_question(state,assignments[index+1],'submit_next',entry_dto)
        else:
            self._browser_flush(state,runtime,metric='final_capture_drain')
            self._browser_request(state,next_path,'completion_shell')
            self._browser_request(state,self.authentication_context_path,'completion_auth')
            state.finish_payload=self._browser_request(state,f'/api/v1/student/sessions/{state.session_id}/summary/','finish')
            if (state.finish_payload.get('session_id')!=state.session_id or state.finish_payload.get('status')!='completed'
                    or state.finish_payload.get('submission_count')!=20 or state.finish_payload.get('attempt_count')!=20):
                raise RuntimeError('Authoritative completion summary mismatch')
            recovery=self._browser_request(state,f'/api/v1/student/tasks/{self.task_id}/recovery/','task_recovery')
            if recovery.get('status')!='completed': raise RuntimeError('Terminal task completion was not confirmed')
        if not self.confidence_only:
            self._browser_logical_action(state,'submit_next_ready',index)
        return runtime

    def prepare_confidence(self, state, runtime, index):
        began=time.perf_counter()
        successful=False
        error_type=None
        try:
            contract=runtime['dto'].get('pending_confidence')
            if not contract or contract['scale_version'] != self.plan['confidence_scale_version'] or any(
                    contract[key] != value for key, value in self.plan['confidence_scale'].items()):
                raise RuntimeError('Confidence-only assignment/scale differs from frozen plan')
            decision=confidence_decision(state.natural_student_plan,index,contract,runtime['head'])
            attempt=runtime['dto']['assignment']['attempt_id']
            observed={'capture_version':'capture-v2','confidence_assignment_id':contract['confidence_assignment_id'],
                'answer_event_id':runtime['head'],'probability':None,'feedback_phase':'before_feedback'}
            runtime['stream'].event(attempt,'confidence_observed',dict(observed,operation_id=str(uuid.uuid4()),stage='presented'),
                event_version=2 if contract['scale_version']=='v2' else 1)
            response=self._browser_post(state,contract['prompt_shown_url'],{},'confidence_prompt')
            think_began=time.perf_counter()
            think_success=False
            think_error_type=None
            try:
                self.wait_until(time.monotonic()+state.natural_student_plan['confidence_think_seconds'][index],runtime,state)
                think_success=True
            except Exception as error:
                think_error_type=type(error).__name__
                raise
            finally:
                self.record_outcome(state,'confidence_think',think_began,think_success,200 if think_success else None,think_error_type)
            if decision['status']=='skipped':
                runtime['stream'].event(attempt,'confidence_observed',dict(observed,operation_id=str(uuid.uuid4()),stage='skipped'),
                    event_version=2 if contract['scale_version']=='v2' else 1)
            with self.lock:
                self.ledger[str(state.session_id)].setdefault('confidence',{})[str(attempt)]={
                    'confidence_assignment_id':contract['confidence_assignment_id'],'scale_version':contract['scale_version'],
                    'response':deepcopy(decision),'synthetic':True,'item_index':index,
                    'prompt_response_sha256':digest(response),'acknowledged':False,'submission_receipt_sha256':None}
            successful=True
            return decision
        except Exception as error:
            error_type=type(error).__name__
            raise
        finally:
            self.record_outcome(state,'confidence_entry',began,successful,200 if successful else None,error_type)

    def submit(self, state, runtime, index, target, assignments):
        if not self.confidence_only:
            return self._submit_material(state,runtime,index,target,assignments)
        state.natural_confidence_decision=self.prepare_confidence(state,runtime,index)
        began=time.perf_counter()
        state.natural_submission_ack_at=None
        successful=False
        failure=None
        try:
            runtime=self._submit_material(state,runtime,index,target,assignments)
            successful=True
            return runtime
        except Exception as error:
            failure=error
            raise
        finally:
            ended=time.perf_counter()
            ack=state.natural_submission_ack_at
            ack_metric='submit_ack' if index<19 else 'finish_submit_ack'
            self.record_boundary(state,ack_metric,index,began,ack if ack is not None else ended,
                ack is not None,None if ack is not None else failure)
            if ack is not None:
                next_metric='submit_ack_to_next_ready' if index<19 else 'finish_ack_to_terminal_ready'
                self.record_boundary(state,next_metric,index,ack,ended,successful,failure)
            total_metric='submit_next_ready' if index<19 else 'finish_ready'
            self.record_boundary(state,total_metric,index,began,ended,successful,failure)

    def record_boundary(self,state,metric,index,began,ended,successful,error=None):
        elapsed=ended-began
        self.record(metric,elapsed)
        with self.lock:
            self.operation_measurements.append({'metric':metric,'student_index':state.load_student_index,
                'item_index':index,'elapsed_seconds':elapsed,'http_status':200 if successful else None,
                'error_type':type(error).__name__ if error else None,
                'server_error_code':getattr(state,'browser_last_error_code',None) if error else None,
                'timeout':isinstance(error,TimeoutError) or isinstance(getattr(error,'reason',None),TimeoutError),
                'successful':successful,'began_monotonic':began,'ended_monotonic':ended})

    def natural_answer(self,state,student):
        runtime=state.browser_current
        assignments=[int(row['assignment_id']) for row in runtime['dto']['navigation']]
        if len(assignments)!=20 or len(set(assignments))!=20: raise RuntimeError('Exactly20 assignments required')
        for index,assignment in enumerate(assignments):
            ready=time.monotonic()
            ready_wall=time.time()
            if runtime['dto']['assignment']['assignment_id']!=assignment: raise RuntimeError('Assigned order drifted')
            dwell=student['item_dwell_seconds'][index]
            with self.lock:
                self.item_arrivals.append({'student_index':state.load_student_index,'item_index':index,'ready_at':ready_wall,
                    'planned_submit_at':ready_wall+dwell,'planned_dwell_seconds':dwell})
            kind=runtime['dto']['question']['question_type']
            options=runtime['dto']['question'].get('options',[])
            answer=[options[0]['key']] if kind=='multiple_choice' else(options[0]['key'] if kind in('single_choice','true_false') else'自然负载分段作答')
            self.wait_until(ready+dwell*.3,runtime,state)
            if kind in('fill_blank','short_answer'):
                self.input_value(state,runtime,answer[:3],index)
                self.wait_until(ready+dwell*.6,runtime,state)
            target=self.input_value(state,runtime,answer,index)
            if index in student['modify_items']:
                self.wait_until(ready+dwell*.75,runtime,state)
                modified=([options[-1]['key']] if kind=='multiple_choice' else(options[-1]['key'] if kind in('single_choice','true_false') else answer+'修订'))
                target=self.input_value(state,runtime,modified,index)
            if index in student['excursion_items']:
                runtime=self.navigate(state,runtime,assignments[index+1],index,'navigation_next_ready')
                runtime=self.navigate(state,runtime,assignment,index,'navigation_return_ready')
            self.natural_tools(state,runtime,index,student)
            self.wait_until(ready+dwell,runtime,state)
            runtime=self.submit(state,runtime,index,target,assignments)
        with self.lock: self.ledger[str(state.session_id)]['complete']=True

    def read_home(self,state,login_started):
        successful,status,error_type=False,None,None
        try:
            self._browser_request(state,'/student/','home_shell')
            self._browser_request(state,self.authentication_context_path,'home_auth')
            self._browser_request(state,'/api/v1/student/courses/','home_courses')
            successful,status=True,200
        except Exception as error:
            error_type=type(error).__name__
            if str(error).startswith('HTTP ') and str(error)[5:].isdigit(): status=int(str(error)[5:])
            raise
        finally:
            self.record_outcome(state,'login_to_home_ready',login_started,successful,status,error_type)

    def lifecycle(self,state,student,mode):
        outcome={'username_sha256':digest(state.username),'student_index':state.load_student_index,
            'planned_arrival_at':self.start_at+(0 if mode=='login-only' else student['arrival_offset_seconds']),
            'actual_arrival_at':None,'authenticated_at':None,'home_ready_at':None,'ready_at':None,'completed_at':None,'workflow_completed':False}
        self.local.student_index = state.load_student_index
        state.natural_student_plan = student
        with self.lock: self.identity_outcomes.append(outcome)
        self.publish_identities()
        try:
            if mode=='login-only':
                preparation_started=time.monotonic()
                outcome['auth_preparation_started_at']=time.time()
                self.prepare_login(state)
                outcome.update(auth_preparation_completed_at=time.time(),auth_preparation_seconds=time.monotonic()-preparation_started)
                self.login_barrier.wait(timeout=min(40,self.remaining_budget()))
            self.wait_until(time.monotonic()+max(0,outcome['planned_arrival_at']-time.time()))
            outcome['actual_arrival_at']=time.time()
            if mode!='login-only': self.prepare_login(state)
            login_started=time.perf_counter()
            if mode=='login-only':state.load_entry_started_at=login_started
            self.authenticate(state)
            outcome['authenticated_at']=time.time()
            self.publish_identities()
            self.read_home(state,login_started)
            outcome['home_ready_at']=time.time()
            if mode=='login-only':
                outcome['workflow_completed']=True
                return
            self.released_at=time.perf_counter()
            self.start_and_first_question(state)
            outcome['ready_at']=time.time()
            self.publish_identities()
            with self.lock:
                self.ledger[str(state.session_id)].update(username_sha256=digest(state.username),natural_plan=deepcopy(student),
                    **({'synthetic_confidence': True, 'scenario': CONFIDENCE_ONLY} if self.confidence_only else {}))
            self.natural_answer(state,student)
            outcome.update(completed_at=time.time(),workflow_completed=True)
        except Exception as error:
            state.workflow_failed=True
            self._browser_finish_action(state,False,error)
            with self.lock:
                self.errors.append({'phase':'natural','student_index':state.load_student_index,'error':type(error).__name__,
                    'reason':str(error) if isinstance(error,LoadStopped) else type(error).__name__})
        finally:
            self.publish_identities()

    def run(self,fixture,mode='natural'):
        selected=[(i,s) for i,s in enumerate(self.plan['students']) if s['role']=='protocol']
        states=[StudentState(s['username'],fixture['password']) for _,s in selected]
        self.students=states
        if mode=='login-only': self.login_barrier=threading.Barrier(len(states))
        for state,(i,_) in zip(states,selected): state.load_student_index=i
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1,len(states))) as pool:
            futures=[pool.submit(self.lifecycle,state,s,mode) for state,(_,s) in zip(states,selected)]
            for future in futures: future.result()
        self.execution_completed=not self.budget_interrupted
        return states


def observed_active_overlap(plan,students):
    """Require observed intervals for exactly the frozen identities in this plan."""
    expected={digest(student['username']) for student in plan['students']}
    observed=[row.get('username_sha256') for row in students]
    valid=len(students)==len(expected) and set(observed)==expected and len(set(observed))==len(observed)
    valid=valid and all(type(row.get(key)) in (int,float) and math.isfinite(row[key])
        for row in students for key in ('ready_at','completed_at'))
    valid=valid and all(row['completed_at']>row['ready_at'] for row in students)
    seconds=max(0,min(row['completed_at'] for row in students)-max(row['ready_at'] for row in students)) if valid and students else 0
    return {'expected':len(expected),'observed':len(students),'seconds':seconds,'valid_intervals':valid,
        'achieved':valid and seconds>=600,'boundary':'actual first interactive entry to actual completion'}


def natural_result(runner,states,started,mode):
    selected=[s for s in runner.plan['students'] if s['role']=='protocol']
    n=len(selected)
    rows=list(runner.operation_measurements)+list(runner.protocol_operation_measurements)
    request_attempts=list(runner.request_attempts)
    error_accounting = lease_recovery_accounting(runner.plan, request_attempts, runner.lease_recoveries, runner.request_errors)
    tool_attempts=[row for row in request_attempts if row['path'].split('?',1)[0].endswith('/tools/')]
    latency={metric:{'all_operations':summary([r['elapsed_seconds'] for r in rows if r['metric']==metric]),
        'successful_only':summary([r['elapsed_seconds'] for r in rows if r['metric']==metric and r['successful']]),
        'failed_count':sum(r['metric']==metric and not r['successful'] for r in rows)} for metric in {r['metric'] for r in rows}}
    thresholds={'login_entry_to_authentication_end':(5,10),'login_to_home_ready':(5,10),'start_to_first_question_ready':(5,10),
        'navigation_next_ready':(1,2),'navigation_return_ready':(1,2),'submit_next_ready':(1.5,3),'finish_ready':(3,5),'save_draft':(1,3)}
    planned={'login_entry_to_authentication_end':n,'login_to_home_ready':n}
    if mode!='login-only':
        planned.update(start_to_first_question_ready=n,navigation_next_ready=sum(len(s['excursion_items']) for s in selected),
            navigation_return_ready=sum(len(s['excursion_items']) for s in selected),submit_next_ready=n*20,finish_ready=n,
            save_draft=sum(28+len(s['modify_items']) for s in selected))
        if runner.confidence_only:
            planned.update(submit_next_ready=n*19, submit_ack=n*19, submit_ack_to_next_ready=n*19,
                finish_submit_ack=n,finish_ack_to_terminal_ready=n,
                confidence_entry=n*20,confidence_think=n*20,confidence_prompt=n*20)
    accounting={key:{'planned_count':count,'attempted_count':len([r for r in rows if r['metric']==key]),
        'successful_count':sum(r['metric']==key and r['successful'] for r in rows),
        'failed_count':sum(r['metric']==key and not r['successful'] for r in rows),
        'timeout_count':sum(r['metric']==key and (r.get('timeout') or r.get('error_type') in ('TimeoutError','timeout')
            or r.get('server_error_code')=='transport_timeout') for r in rows),
        'not_reached_count':max(0,count-len([r for r in rows if r['metric']==key]))} for key,count in planned.items()}
    checks={'complete_workflow':n>0 and len(states)==n and len(runner.identity_outcomes)==n and all(s['workflow_completed'] for s in runner.identity_outcomes),
        'zero_workflow_errors':not runner.errors,'bounded_execution':not runner.budget_interrupted,
        'all_planned_operations_reached':all(v['attempted_count']==v['planned_count'] and v['failed_count']==0 for v in accounting.values())}
    if runner.load_evidence_profile == LEASE_RECOVERY_PROFILE:
        checks['all_http_errors_accounted'] = error_accounting['valid'] and error_accounting['unexpected_http_error_count'] == 0
    else:
        checks['zero_http_errors'] = runner.request_errors == 0
    if runner.confidence_only:
        checks['confidence_only_zero_tool_requests']=not tool_attempts and not any(row['metric'] in ('research_tool','tools_configuration','tools_refresh') for row in rows)
    for metric,(p95,p99) in thresholds.items():
        if metric not in planned or planned[metric]==0: continue
        aggregate=latency.get(metric,{}).get('all_operations',{})
        checks[metric+'_p95']=aggregate.get('p95_seconds') is not None and aggregate['p95_seconds']<=p95
        checks[metric+'_p99']=aggregate.get('p99_seconds') is not None and aggregate['p99_seconds']<=p99
    login_attempts=[row for row in request_attempts if row['path']=='/api/v1/auth/login/' and row['method']=='POST' and row['attempt']==1]
    first_login_posts=[{**row,'username_sha256':digest(runner.plan['students'][row['student_index']]['username'])}
        for row in login_attempts]
    sends=[row['sent_at'] for row in login_attempts]
    send_window=max(sends)-min(sends) if sends else None
    if mode=='login-only':
        checks['all_first_login_posts_observed']=len(login_attempts)==n
        checks['login_send_window_within_one_second']=send_window is not None and send_window<=1
        checks['all_first_login_posts_successful']=sum(row['successful'] for row in login_attempts)==n
    protocol=runner.protocol_summary()
    return {'workload_version':NATURAL_WORKLOAD,'mode':mode,'student_count':n,'total_students':runner.plan['total_students'],
        **({'scenario':CONFIDENCE_ONLY,'synthetic_confidence':True} if runner.confidence_only else {}),
        'task_id':runner.task_id,'plan_sha256':digest(runner.plan),'start_at':runner.start_at,'started':started,'ended':time.time(),
        'students':runner.identity_outcomes,'metrics':{k:summary(v) for k,v in runner.metrics.items()},'operation_latency':latency,
        'acceptance_thresholds_seconds':{metric:{'p95':p95,'p99':p99} for metric,(p95,p99) in thresholds.items()},
        'protocol_active_overlap':observed_active_overlap({'students':selected},runner.identity_outcomes),
        'formal_overlap_state':'pending_combined_protocol_and_browser_evidence' if mode!='login-only' else 'not_applicable',
        'full_operation_accounting':accounting,'operation_measurements':rows,'request_attempts':runner.attempt_summary(),
        'actual_tool_request_count':len(tool_attempts),'tool_request_journal_boundary':'All attempted normalized /tools/ requests, including failures and retries',
        'login_post_send_window_seconds':send_window,'first_login_post_count':len(login_attempts),
        'first_login_post_attempts':first_login_posts,
        **({'authentication_preparation_seconds':summary([row['auth_preparation_seconds'] for row in runner.identity_outcomes if 'auth_preparation_seconds' in row]),
            'login_measurement_boundary':'Shared login POST release through authentication/home; auth preparation and release waiting excluded'} if mode=='login-only' else {}),
        'first_login_post_success_count':sum(row['successful'] for row in login_attempts),'http_request_count':runner.request_count,'http_error_count':runner.request_errors,'errors':runner.errors,
        'load_evidence_profile': runner.load_evidence_profile,
        **({'http_error_accounting': error_accounting} if runner.load_evidence_profile == LEASE_RECOVERY_PROFILE else {}),
        'item_arrivals':runner.item_arrivals,'capture_protocol':protocol,'authentication_success_count':sum(s['authenticated_at'] is not None for s in runner.identity_outcomes),
        'execution_completed':runner.execution_completed,'workflow_completed':checks['complete_workflow'],'performance_passed':all(checks.values()),
        'passed':all(checks.values()),'checks':checks,'budget_interrupted':runner.budget_interrupted,'safety_interrupted':runner.budget_interrupted,
        'incomplete':not checks['complete_workflow'],'formal_capacity_accepted':False,'data_integrity_passed':None,
        'data_integrity_state':'pending_external_verification','generator':{'cpu_seconds':time.process_time(),
            'max_rss_bytes':peak_rss_bytes(),'cpu_scope':'whole generator process; since process initialization',
            'arrival_lateness':summary([max(0,s['actual_arrival_at']-s['planned_arrival_at']) for s in runner.identity_outcomes if s['actual_arrival_at'] is not None])}}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--manifest',required=True)
    parser.add_argument('--plan',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--base-url',default='http://127.0.0.1:18080')
    parser.add_argument('--start-at',type=float,required=True)
    parser.add_argument('--stop-file')
    parser.add_argument('--mode',choices=('natural','login-only'),default='natural')
    parser.add_argument('--timeout',type=float,default=35)
    parser.add_argument('--total-budget-seconds',type=float,default=2700)
    args=parser.parse_args()
    if args.base_url!='http://127.0.0.1:18080' or args.timeout!=35 or args.total_budget_seconds!=2700 or not math.isfinite(args.start_at):
        parser.error('Only approved loopback18080 /35s request /2700s total budget allowed')
    fixture=json.loads(Path(args.manifest).read_text(encoding='utf-8-sig'))
    if fixture.get('database')!='kt_e2e' or fixture.get('capture_protocol')!=CONTRACT: parser.error('Dedicated kt_e2e capture fixture required')
    plan=validate_workload(fixture,json.loads(Path(args.plan).read_text(encoding='utf-8-sig')))
    runner=NaturalRunner(args.base_url,fixture['task_id'],args.timeout,plan=plan,start_at=args.start_at,stop_file=args.stop_file,
        authentication_profile='bootstrap-v1' if fixture.get('source_config', {}).get('pool_profile') == 'six-pools-180-v1' else 'legacy',
        load_evidence_profile=fixture.get('source_config', {}).get('load_evidence_profile', 'legacy'))
    runner.browser_shell_url=fixture['source_config']['shell_url']
    runner.protocol_course_id,runner.protocol_experiment_id=fixture['course_id'],fixture['experiment_id']
    runner.identity_progress_path = Path(args.output).with_suffix('.students.json')
    if runner.identity_progress_path.exists():
        parser.error('Identity evidence already exists; no automatic repeat allowed')
    started=time.time()
    with evidence_files(args.manifest,args.output) as files:
        files['runner']=runner
        runner.enable_attempt_journal(files['attempts'])
        states=runner.run(fixture,args.mode)
        result=natural_result(runner,states,started,args.mode)
        result['students_progress_file'] = runner.identity_progress_path.name
        runner.workload_version=NATURAL_WORKLOAD
        files['complete']=result['workflow_completed']
        json.dump(result,files['result'],indent=2)
        files['result_ready']=True
    print(json.dumps({k:result[k] for k in ('student_count','authentication_success_count','workflow_completed','performance_passed','checks')}))
    raise SystemExit(0 if result['performance_passed'] else 1)

if __name__=='__main__': main()
