from .models import ResearchConsent, User


CURRENT_RESEARCH_CONSENT_VERSION = "2026-09-14"

RESEARCH_CONSENT_COPY = {
    "title": "研究参与说明与知情同意",
    "paragraphs": [
        "你好！我们正在开展认知诊断与学习过程相关的学术研究，邀请你完成相关习题。",
        "本研究自愿参与。你可以选择不参与，也可以在作答过程中停止参与。",
        (
            "平台使用学生账号识别参与者，并记录作答内容、作答结果及相关答题过程数据，"
            "包括作答时间、答案修改，以及任务中设置的信心评价等。上述数据可能与账号中的"
            "姓名、学号关联。在最终数据处理时，将去除姓名、学号等敏感信息。"
        ),
        (
            "关闭网页可以停止当前操作，但已保存或提交的数据不会因此自动删除。在数据完成"
            "不可逆去标识化后，由于无法再次定位个人记录，将不能再单独删除该部分数据。如需"
            "了解研究数据的使用或退出研究相关事宜，请联系研究者。"
        ),
    ],
    "researcher_name": "唐鑫乐",
    "researcher_email": "txinle@163.com",
    "checkbox_label": (
        "我已阅读并理解上述说明，自愿参与本研究。（勾选并确认同意后方可进入答题）"
    ),
    "exit_label": "退出",
    "accept_label": "同意并继续",
}


def research_consent_required(user):
    return bool(
        user.is_authenticated
        and not user.is_superuser
        and user.role == User.Role.STUDENT
        and not ResearchConsent.objects.filter(
            student=user,
            version=CURRENT_RESEARCH_CONSENT_VERSION,
        ).exists()
    )


def accept_current_research_consent(*, student, version):
    consent, _ = ResearchConsent.objects.get_or_create(
        student=student,
        version=version,
    )
    return consent
