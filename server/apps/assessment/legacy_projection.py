from django.db import transaction

from .event_services import append_assessment_event
from .models import AssessmentEvent, DraftSaveEvent, ExperimentSession, QuestionVisitEvent, SubmissionEvent


def project_legacy_events(*, session_queryset=None):
    sessions = session_queryset or ExperimentSession.objects.all()
    created = 0
    for session in sessions.select_related("version"):
        with transaction.atomic():
            created += _project_one(
                session=session,
                event_type="legacy_session_start_observed",
                payload={"legacy_session_id": session.pk},
                occurred_at=session.started_at,
            )
            for visit in session.visit_events.select_related("attempt", "from_assignment"):
                created += _project_one(
                    session=session,
                    event_type="legacy_visit_observed",
                    attempt=visit.attempt,
                    assignment=visit.attempt.assignment,
                    payload={"legacy_visit_event_id": visit.pk, "visit_index": visit.visit_index, "event_sequence": visit.event_sequence, "entry_reason": visit.entry_reason, "from_assignment_id": visit.from_assignment_id},
                    occurred_at=visit.client_occurred_at or visit.server_received_at,
                )
            for draft in DraftSaveEvent.objects.filter(attempt__session=session):
                created += _project_one(session=session, event_type="legacy_draft_observed", attempt=draft.attempt, assignment=draft.attempt.assignment, payload={"legacy_draft_event_id": draft.pk, "answer_hash": draft.answer_hash}, occurred_at=draft.client_occurred_at or draft.server_received_at)
            for submission in SubmissionEvent.objects.filter(attempt__session=session):
                created += _project_one(session=session, event_type="legacy_submission_observed", attempt=submission.attempt, assignment=submission.attempt.assignment, payload={"legacy_submission_event_id": submission.attempt_id, "submission_token": str(submission.submission_token), "score": float(submission.score), "is_correct": submission.is_correct}, occurred_at=submission.client_clicked_at or submission.server_received_at)
            if session.status in (ExperimentSession.Status.COMPLETED, ExperimentSession.Status.EXPIRED, ExperimentSession.Status.INVALIDATED):
                created += _project_one(session=session, event_type="legacy_session_end_observed", payload={"legacy_session_id": session.pk, "status": session.status}, occurred_at=session.completed_at or session.last_activity_at)
    return created


def _project_one(*, session, event_type, payload, occurred_at, attempt=None, assignment=None):
    if AssessmentEvent.objects.filter(session=session, event_type=event_type, payload=payload, capture_mode=AssessmentEvent.CaptureMode.LEGACY_PROJECTION).exists():
        return 0
    append_assessment_event(event_type=event_type, event_version=1, event_domain=AssessmentEvent.EventDomain.ASSESSMENT, actor_source=AssessmentEvent.ActorSource.SYSTEM, session=session, attempt=attempt, assignment=assignment, payload=payload, client_wall_time_utc=occurred_at, capture_mode=AssessmentEvent.CaptureMode.LEGACY_PROJECTION, quality_flags=["mid_session_capture"])
    return 1
