from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False
    dependencies = [('analytics', '0019_student_learning_comparison_jobs')]
    operations = [AddIndexConcurrently(
        model_name='researchsessionmaterialization',
        index=models.Index(fields=['session', 'projection_version', 'source_revision', 'status'], name='analytics_revision_job_idx'),
    )]
