from django.contrib import admin
from .models import Attempt, DraftSaveEvent, ExperimentSession, QuestionVisitEvent, StudentQuestionAssignment, SubmissionEvent
admin.site.register(ExperimentSession)
admin.site.register(StudentQuestionAssignment)
admin.site.register(Attempt)
admin.site.register(QuestionVisitEvent)
admin.site.register(DraftSaveEvent)
admin.site.register(SubmissionEvent)
