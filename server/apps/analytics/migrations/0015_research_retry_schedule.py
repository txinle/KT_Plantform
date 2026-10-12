import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models
from django.db.models.functions import Now


class Migration(migrations.Migration):
    dependencies = [('analytics', '0014_studentexperimentresultsnapshot')]
    operations = [
        migrations.AddField('researchsessionmaterialization', 'next_attempt_at', models.DateTimeField(default=django.utils.timezone.now, db_default=Now())),
        migrations.AddField('researchsessionmaterialization', 'priority', models.PositiveSmallIntegerField(default=0, db_default=0)),
        migrations.AddIndex('researchsessionmaterialization', models.Index(fields=['status', 'next_attempt_at', 'priority', 'created_at'], name='analytics_retry_claim_idx', condition=models.Q(status__in=('queued', 'running')))),
        migrations.CreateModel(
            name='ResearchSessionQuality',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ordinal', models.PositiveIntegerField()),
                ('attempt_id', models.PositiveBigIntegerField(blank=True, db_index=True, null=True)),
                ('question_ref', models.CharField(blank=True, default='', max_length=32)),
                ('rule', models.CharField(max_length=128)),
                ('state', models.CharField(max_length=64)),
                ('payload', models.JSONField(default=dict)),
                ('materialization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='quality_entries', to='analytics.researchsessionmaterialization')),
            ],
            options={
                'ordering': ['ordinal'],
                'indexes': [models.Index(fields=['materialization', 'state', 'ordinal'], name='analytics_quality_state_idx')],
                'constraints': [models.UniqueConstraint(fields=('materialization', 'ordinal'), name='unique_research_quality_ordinal')],
            },
        ),
    ]
