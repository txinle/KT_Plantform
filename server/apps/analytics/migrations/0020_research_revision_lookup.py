from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class AddIndexConcurrentlyCompat(AddIndexConcurrently):
    """Keep production PostgreSQL migrations concurrent while supporting test backends."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, self.model_name)
        if self.allow_migrate_model(schema_editor.connection.alias, model):
            if schema_editor.connection.vendor == 'postgresql':
                schema_editor.add_index(model, self.index, concurrently=True)
            else:
                schema_editor.add_index(model, self.index)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        model = from_state.apps.get_model(app_label, self.model_name)
        if self.allow_migrate_model(schema_editor.connection.alias, model):
            if schema_editor.connection.vendor == 'postgresql':
                schema_editor.remove_index(model, self.index, concurrently=True)
            else:
                schema_editor.remove_index(model, self.index)


class Migration(migrations.Migration):
    atomic = False
    dependencies = [('analytics', '0019_student_learning_comparison_jobs')]
    operations = [AddIndexConcurrentlyCompat(
        model_name='researchsessionmaterialization',
        index=models.Index(fields=['session', 'projection_version', 'source_revision', 'status'], name='analytics_revision_job_idx'),
    )]
