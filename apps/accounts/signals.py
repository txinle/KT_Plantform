from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ClassGroup, ClassStudentAnalyticsPolicy


@receiver(post_save, sender=ClassGroup)
def create_class_student_analytics_policy(sender, instance, created, raw, using, **kwargs):
    # raw=True is Django fixture deserialization's controlled exception: the
    # fixture load order may not have made related rows queryable yet.
    if created and not raw:
        ClassStudentAnalyticsPolicy.objects.using(using).get_or_create(
            class_group_id=instance.pk,
        )
