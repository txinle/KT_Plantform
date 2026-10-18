from django.test import TestCase, override_settings

from apps.api.test_support import domain_payload
from . import test_teacher_data_center_api as fixtures
from .models import ResearchSessionMaterialization
from .research_materialization import PROJECTION_VERSION


@override_settings(ADVANCED_DATA_MATERIALIZED_ENABLED=True,
                   ADVANCED_DATA_MATERIALIZED_COURSE_IDS=frozenset({"*"}))
class StudentSuggestionScopeTests(TestCase):
    def setUp(self):
        fixtures.TeacherDataCenterApiTests.setUp(self)
        ResearchSessionMaterialization.objects.create(
            session=self.session, status="succeeded", is_current=True,
            projection_version=PROJECTION_VERSION,
            source_revision=self.session.research_source_revision,
        )

    def query(self, **values):
        return {"experiment_id": self.experiment.pk, "class_id": self.class_group.pk,
                "q": self.student.student_number, **values}

    def test_detail_filters_are_projected_before_scope_validation(self):
        response = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/data/advanced/students/",
            self.query(session_ref="stale-reference", student_id=99999999,
                       session_id=99999999, search="unrelated", page="bad", cursor="bad"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in domain_payload(response)["items"]], [self.student.pk])

    def test_saved_detail_filters_are_projected_before_scope_validation(self):
        session = self.client.session
        session[f"data_center_scope_v1:{self.course.pk}"] = {
            "experiment_id": self.experiment.pk, "class_id": self.class_group.pk,
            "session_ref": "stale-reference", "student_id": 99999999,
        }
        session.save()
        response = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/data/advanced/students/",
            {"q": self.student.student_number},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in domain_payload(response)["items"]], [self.student.pk])
        self.assertIn(f"data_center_scope_v1:{self.course.pk}", self.client.session)

    def test_time_and_condition_filters_remain_authoritative(self):
        response = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/data/advanced/students/",
            self.query(started_from_utc="2100-01-01T00:00:00Z"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(domain_payload(response)["items"], [])
        response = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/data/advanced/students/",
            self.query(condition_id=99999999),
        )
        self.assertEqual(response.status_code, 404)
