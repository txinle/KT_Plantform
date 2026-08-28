from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ClassGroup, ClassStudentAnalyticsPolicy


@receiver(post_save, sender=ClassGroup)
def create_class_student_analytics_policy(sender, instance, created, raw, using, **kwargs):
    if created and not raw:
        ClassStudentAnalyticsPolicy.objects.using(using).get_or_create(
            class_group_id=instance.pk,
        )
