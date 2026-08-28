from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ClassGroup, ClassStudentAnalyticsPolicy


@receiver(post_save, sender=ClassGroup)
def create_class_student_analytics_policy(sender, instance, created, raw, using, **kwargs):
    # raw=True is Django fixture deserialization's controlled exception: the
    # fixture must include its policy, or be repaired after import by a
    # migration/future controlled importer. Deploy migrations before traffic;
    # normal creates must not hide a missing policy table.
    if created and not raw:
        ClassStudentAnalyticsPolicy.objects.using(using).get_or_create(
            class_group_id=instance.pk,
        )
