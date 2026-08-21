from django.contrib import admin
from .models import Attempt, DraftSaveEvent, ExperimentSession, QuestionVisitEvent, StudentQuestionAssignment, SubmissionEvent
ExperimentSession._meta.verbose_name = "实验会话"
ExperimentSession._meta.verbose_name_plural = "实验会话"
StudentQuestionAssignment._meta.verbose_name = "学生题目分配"
StudentQuestionAssignment._meta.verbose_name_plural = "学生题目分配"
Attempt._meta.verbose_name = "作答记录"
Attempt._meta.verbose_name_plural = "作答记录"
QuestionVisitEvent._meta.verbose_name = "题目访问事件"
QuestionVisitEvent._meta.verbose_name_plural = "题目访问事件"
DraftSaveEvent._meta.verbose_name = "草稿保存事件"
DraftSaveEvent._meta.verbose_name_plural = "草稿保存事件"
SubmissionEvent._meta.verbose_name = "提交事件"
SubmissionEvent._meta.verbose_name_plural = "提交事件"
admin.site.register(ExperimentSession)
admin.site.register(StudentQuestionAssignment)
admin.site.register(Attempt)
admin.site.register(QuestionVisitEvent)
admin.site.register(DraftSaveEvent)
admin.site.register(SubmissionEvent)
