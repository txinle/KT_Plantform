"""Prepare route references in kt_e2e without depending on another browser test."""
import json
import os
import time
import uuid
from io import StringIO
from django.core.management import call_command
from django.db import connection, transaction
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import Course, ClassGroup, User
from apps.question_bank.models import QuestionBank
from apps.experiments.models import Experiment, ClassExperimentTask, PublishPlan
from apps.assessment.models import ExperimentSession, SubmissionEvent
from apps.assessment.services import save_draft
from apps.assessment.prepared_services import activate_prepared_session
from apps.assessment.capture_v2_services import ingest_capture_batch
from apps.assessment.response_snapshot_services import ResponseState
from apps.assessment.session_submission_models import SessionSubmission
from apps.assessment.session_submission_services import accept_session_submission, claim_session_submission, run_session_submission

if os.environ.get('POSTGRES_DB') != 'kt_e2e' or connection.settings_dict['NAME'] != 'kt_e2e':
    raise RuntimeError('Route fixtures are restricted to kt_e2e.')

call_command('seed_e2e_site_v2_all_types', stdout=StringIO())
grouped = Experiment.objects.get(course__code='SITE-V2-TYPES-E2E', name='Site v2 all question types')
group_student = User.objects.get(username='site-v2-types-student')
group_task = ClassExperimentTask.objects.get(experiment=grouped)
client_id = uuid.uuid4()
group_session, receipt = activate_prepared_session(student=group_student, task_id=group_task.pk,
    client_instance_id=client_id, navigation_id=uuid.uuid4())
first_attempt = group_session.attempts.select_related('assignment__snapshot').order_by('assignment__assigned_sequence_index').first()
snapshot = first_attempt.assignment.snapshot
assert snapshot.question_type == 'single_choice'
before, after = ResponseState(snapshot.question_type, None), ResponseState(snapshot.question_type, snapshot.correct_answer)
event_id = str(uuid.uuid4())
ingest_capture_batch(student=group_student, document={'client_instance_id': str(client_id), 'events': [{
    'event_id': event_id, 'event_type': 'response_transition', 'event_version': 1,
    'session_id': group_session.pk, 'attempt_id': first_attempt.pk, 'client_sequence_no': 1,
    'client_wall_time_utc': timezone.now().isoformat(), 'client_monotonic_ms': 100,
    'client_time_origin': timezone.now().isoformat(), 'quality_flags': [], 'app_version': 'route-fixture',
    'payload': {'capture_version': 'capture-v2', 'operation_id': str(uuid.uuid4()),
        'branch_id': str(client_id), 'parent_event_id': None, 'before_state': before.state_json,
        'after_state': after.state_json, 'before_state_hash': before.state_hash,
        'after_state_hash': after.state_hash, 'input_type': 'selectOption', 'source': 'student'},
}]})
save_draft(first_attempt.pk, None, target_event_id=event_id, expected_version=0,
    writer_lease_token=first_attempt.writer_lease_token, writer_client_instance_id=client_id,
    inline_confidence={'probability': 80, 'answer_event_id': event_id})
# This serial fixture must never consume another test's queued submission.
if SessionSubmission.objects.filter(status__in=('queued', 'running')).exists():
    raise RuntimeError('Run the route fixture with the submission queue idle.')
job = accept_session_submission(student=group_student, session_id=group_session.pk, document={
    'submission_token': str(uuid.uuid4()), 'client_clicked_at': timezone.now().isoformat(),
    'expected_versions': [{'attempt_id': attempt.pk, 'response_version': attempt.response_version,
        'target_event_id': str(attempt.active_response_event_id) if attempt.active_response_event_id else None}
        for attempt in group_session.attempts.order_by('pk')],
})
with transaction.atomic():
    claimed = claim_session_submission(worker_id='registered-route-fixture')
    if claimed is not None and claimed.pk != job.pk:
        raise RuntimeError('Route fixture submission claim was not isolated.')
if claimed is not None:
    run_session_submission(claimed)
# The E2E server's worker may have claimed this job first.
deadline = time.monotonic() + 15
while True:
    job.refresh_from_db()
    if job.status not in ('queued', 'running') or time.monotonic() >= deadline:
        break
    time.sleep(0.1)
assert job.status == 'completed', (job.status, job.error_code)
assert SubmissionEvent.objects.filter(attempt=first_attempt).exists()

teacher = User.objects.get(username='teacher')
student = User.objects.get(username='S002')
course = Course.objects.get(code='KT101', term='2026春')
class_group = ClassGroup.objects.get(course=course, name='认知追踪实验班')
bank = QuestionBank.objects.get(course=course, name='演示题库')
standalone_bank = QuestionBank.objects.filter(course=None, owner=teacher).first()
feedback = Experiment.objects.get(course=course, name='完整反馈视觉验证')
feedback_session = ExperimentSession.objects.get(version__experiment=feedback, student=student)
question = feedback_session.assignments.order_by('assigned_sequence_index').first().snapshot
experiment = Experiment.objects.get(course=course, name='演示交互数据采集')
manual = Experiment.objects.get(course=course, name='人工简答题批改验收')
runtime_session = ExperimentSession.objects.get(version__experiment=experiment, student__username='S001')
source_course, _ = Course.objects.get_or_create(owner=teacher, code='ROUTE-SOURCE-E2E', term='2026', defaults={'name': 'Route copy source'})
QuestionBank.objects.update_or_create(course=source_course, owner=teacher, name='Route shared bank',
    defaults={'created_by': teacher, 'visibility': QuestionBank.Visibility.SHARED})
with open('../contracts/frontend-routes.json', encoding='utf-8') as source:
    routes = json.load(source)['routes']
result = []
for route in routes:
    name, path = route['name'], route['path']
    persona = 'teacher'
    facts = dict(course_pk=course.pk, course_id=course.pk, class_pk=class_group.pk,
        student_pk=student.pk, source_course_pk=source_course.pk,
        creator_pk=teacher.pk, bank_pk=bank.pk, question_pk=bank.questions.order_by('pk').first().pk,
        pk=experiment.pk, experiment_pk=feedback.pk, experiment_id=feedback.pk,
        task_pk=ClassExperimentTask.objects.get(experiment=manual, class_group=class_group).pk,
        plan_pk=PublishPlan.objects.filter(experiment=experiment).first().pk, step=1,
        session_pk=feedback_session.pk, session_id=feedback_session.pk, question_id=question.pk,
        assignment_id=runtime_session.assignments.order_by('assigned_sequence_index').first().pk)
    if name in ('home', 'login'):
        persona = 'anonymous'
    if name.startswith('question_bank:') and '<int:' in path:
        facts.update(pk=standalone_bank.pk, bank_pk=standalone_bank.pk,
            question_pk=standalone_bank.questions.order_by('pk').first().pk)
    if name.startswith('courses:') and '/banks/' in path:
        facts['pk'] = bank.pk
    if name.startswith('courses:class_question_'):
        facts['question_pk'] = question.pk
    if name == 'courses:experiment_grading':
        facts['pk'] = manual.pk
    if name in ('courses:experiment_results', 'courses:experiment_session_detail'):
        facts['pk'] = feedback.pk
    if name.startswith('experiments:'):
        facts['pk'] = experiment.pk
    if name.startswith('assessment:') or name == 'password-change':
        persona = 'student'
    if name == 'assessment:question':
        persona, facts['session_id'] = 'runtime', runtime_session.pk
    if name == 'assessment:complete':
        persona, facts['session_id'] = 'groupstudent', group_session.pk
    if name in ('assessment:learning', 'assessment:learning_experiment', 'assessment:learning_question'):
        persona = 'groupstudent'
        facts.update(course_id=grouped.course_id, experiment_id=grouped.pk, question_id=snapshot.pk)
    import re
    kwargs = {key: facts[key] for key in re.findall(r'<int:(\w+)>', path)}
    result.append(dict(name=name, pattern='/' + path, path=reverse(name, kwargs=kwargs), persona=persona))
print(json.dumps(dict(routes=result, group_result=f'/student/sessions/{group_session.pk}/complete/'), ensure_ascii=False))
