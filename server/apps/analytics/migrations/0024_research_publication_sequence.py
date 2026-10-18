from django.db import migrations, models


def initialize_publication_sequence(apps, schema_editor):
    materializations = apps.get_model("analytics", "ResearchSessionMaterialization")
    clock = apps.get_model("analytics", "ResearchPublicationClock")
    alias = schema_editor.connection.alias
    succeeded = materializations.objects.using(alias).filter(status="succeeded")
    succeeded.filter(is_current=True).update(activated_revision=1, superseded_revision=None)
    succeeded.filter(is_current=False).update(activated_revision=0, superseded_revision=1)
    clock.objects.using(alias).update_or_create(pk=1, defaults={"revision": 1})


class Migration(migrations.Migration):
    dependencies = [("analytics", "0023_comparison_source_signature")]
    operations = [
        migrations.CreateModel(
            name="ResearchPublicationClock",
            fields=[
                ("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ("revision", models.PositiveBigIntegerField(default=0)),
            ],
            options={"constraints": [models.CheckConstraint(condition=models.Q(pk=1), name="analytics_publication_clock_singleton")]},
        ),
        migrations.AddField("researchsessionmaterialization", "activated_revision", models.PositiveBigIntegerField(blank=True, null=True)),
        migrations.AddField("researchsessionmaterialization", "superseded_revision", models.PositiveBigIntegerField(blank=True, null=True)),
        migrations.RunPython(initialize_publication_sequence, migrations.RunPython.noop),
        migrations.AddIndex(
            "researchsessionmaterialization",
            models.Index(fields=["status", "activated_revision", "superseded_revision"], name="analytics_result_revision_idx"),
        ),
    ]
