from django.db import migrations


# Eligibility changes include queryset updates, so signals cannot preserve the
# shared summary boundary. These narrow PostgreSQL triggers dirty only task
# comparison revisions, never personal or research evidence revisions.
SQL = """
CREATE FUNCTION kt_student_comparison_enrollment_changed() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'UPDATE' AND OLD.class_group_id IS NOT DISTINCT FROM NEW.class_group_id
      AND OLD.course_id IS NOT DISTINCT FROM NEW.course_id
      AND OLD.student_id IS NOT DISTINCT FROM NEW.student_id
      AND OLD.is_active IS NOT DISTINCT FROM NEW.is_active THEN
    RETURN NEW;
  END IF;
  IF TG_OP <> 'INSERT' THEN
    UPDATE experiments_classexperimenttask SET result_revision = result_revision + 1
      WHERE class_group_id = OLD.class_group_id;
  END IF;
  IF TG_OP <> 'DELETE' THEN
    UPDATE experiments_classexperimenttask SET result_revision = result_revision + 1
      WHERE class_group_id = NEW.class_group_id;
    RETURN NEW;
  END IF;
  RETURN OLD;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER kt_student_comparison_enrollment
AFTER INSERT OR UPDATE OR DELETE ON accounts_courseenrollment
FOR EACH ROW EXECUTE FUNCTION kt_student_comparison_enrollment_changed();

CREATE FUNCTION kt_student_comparison_account_changed() RETURNS trigger AS $$
BEGIN
  IF OLD.is_active IS DISTINCT FROM NEW.is_active OR OLD.role IS DISTINCT FROM NEW.role THEN
    UPDATE experiments_classexperimenttask SET result_revision = result_revision + 1
    WHERE class_group_id IN (SELECT class_group_id FROM accounts_courseenrollment WHERE student_id = NEW.id)
       OR class_snapshot_id IN (SELECT class_snapshot_id FROM experiments_experimentversionparticipantsnapshot WHERE student_id = NEW.id);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER kt_student_comparison_account
AFTER UPDATE OF is_active, role ON accounts_user
FOR EACH ROW EXECUTE FUNCTION kt_student_comparison_account_changed();
"""
REVERSE = """
DROP TRIGGER kt_student_comparison_account ON accounts_user;
DROP FUNCTION kt_student_comparison_account_changed();
DROP TRIGGER kt_student_comparison_enrollment ON accounts_courseenrollment;
DROP FUNCTION kt_student_comparison_enrollment_changed();
"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute(SQL)


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute(REVERSE)


class Migration(migrations.Migration):
    dependencies = [('analytics', '0016_student_comparison')]
    operations = [migrations.RunPython(install, uninstall)]
