import django.db.models.deletion
import re

from django.db import migrations, models


LEGACY_KC_CODE = re.compile(r"^KC0*([1-9][0-9]*)$")


def backfill_course_kc_codes(apps, schema_editor):
    Concept = apps.get_model("question_bank", "Concept")
    course_ids = (
        Concept.objects.exclude(course_id=None)
        .order_by("course_id")
        .values_list("course_id", flat=True)
        .distinct()
    )
    for course_id in course_ids:
        concepts = list(Concept.objects.filter(course_id=course_id).order_by("pk"))
        used_codes = set()
        pending = []
        for concept in concepts:
            match = LEGACY_KC_CODE.fullmatch(concept.code or "")
            canonical = f"KC{int(match.group(1))}" if match else None
            if canonical is None or canonical in used_codes:
                pending.append(concept)
                continue
            Concept.objects.filter(pk=concept.pk).update(code=canonical)
            used_codes.add(canonical)

        next_sequence = 1
        for concept in pending:
            while f"KC{next_sequence}" in used_codes:
                next_sequence += 1
            canonical = f"KC{next_sequence}"
            Concept.objects.filter(pk=concept.pk).update(code=canonical)
            used_codes.add(canonical)
            next_sequence += 1


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("question_bank", "0005_alter_question_question_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="concept",
            name="parent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="children",
                to="question_bank.concept",
            ),
        ),
        migrations.AddField(
            model_name="concept",
            name="code",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="concept",
            name="description",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="concept",
            name="status",
            field=models.CharField(
                choices=[("active", "可用"), ("archived", "归档")],
                default="active",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="questionconcept",
            name="display_order",
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="questionconcept",
            name="parent_link",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="child_links",
                to="question_bank.questionconcept",
            ),
        ),
        migrations.RunPython(backfill_course_kc_codes, noop_reverse),
        migrations.AddConstraint(
            model_name="concept",
            constraint=models.UniqueConstraint(
                condition=models.Q(("course__isnull", False)),
                fields=("course", "code"),
                name="unique_course_kc_code",
            ),
        ),
        migrations.AddConstraint(
            model_name="questionconcept",
            constraint=models.CheckConstraint(
                condition=models.Q(("display_order__gt", 0)),
                name="question_concept_display_order_positive",
            ),
        ),
    ]
