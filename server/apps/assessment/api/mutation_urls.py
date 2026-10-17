from django.urls import path
from .session_submission import StudentSessionSubmissionView

from .. import views
from .domain_adapter import documented_domain_view
from .mutation_schema import MUTATIONS


app_name = 'assessment_api'
urlpatterns = [
    path('sessions/<int:session_id>/submission/', StudentSessionSubmissionView.as_view(), name='student-session-submission'),
    path('attempts/<int:attempt_id>/draft/', documented_domain_view(views.save_draft_api, **MUTATIONS['save_draft']), name='save_draft'),
    path('attempts/<int:attempt_id>/submit/', documented_domain_view(views.submit_api, **MUTATIONS['submit']), name='submit'),
    path('attempts/<int:attempt_id>/skip/', documented_domain_view(views.skip_api, **MUTATIONS['skip']), name='skip'),
    path('sessions/<int:session_id>/finish/', documented_domain_view(views.finish, **MUTATIONS['finish']), name='finish'),
]
