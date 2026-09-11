from django.urls import include, path

from .views import (
    TeacherClassCenterView,
    TeacherClassCollectionView,
    TeacherDataCenterAdvancedView,
    TeacherDataCenterAdvancedStudentSearchView,
    TeacherDataCenterExportCatalogView,
    TeacherDataCenterExportHistoryView,
    TeacherDataCenterExportJobCreateView,
    TeacherDataCenterExportJobDetailView,
    TeacherDataCenterOverviewView,
    TeacherDataCenterQualityView,
    TeacherDataCenterScopeView,
)


urlpatterns = [
    path("", include("apps.analytics.api.teaching_legacy_urls")),
    path("", include("apps.analytics.api.drilldown_urls")),
    path(
        "courses/<int:course_id>/data/scope/",
        TeacherDataCenterScopeView.as_view(),
        name="teacher-data-center-scope",
    ),
    path(
        "courses/<int:course_id>/data/overview/",
        TeacherDataCenterOverviewView.as_view(),
        name="teacher-data-center-overview",
    ),
    path(
        "courses/<int:course_id>/data/exports/",
        TeacherDataCenterExportCatalogView.as_view(),
        name="teacher-data-center-exports",
    ),
    path(
        "courses/<int:course_id>/data/quality/",
        TeacherDataCenterQualityView.as_view(),
        name="teacher-data-center-quality",
    ),
    path(
        "courses/<int:course_id>/data/exports/history/",
        TeacherDataCenterExportHistoryView.as_view(),
        name="teacher-data-center-export-history",
    ),
    path(
        "courses/<int:course_id>/data/export-jobs/",
        TeacherDataCenterExportJobCreateView.as_view(),
        name="teacher-data-center-export-job-create",
    ),
    path(
        "courses/<int:course_id>/data/export-jobs/<uuid:job_id>/",
        TeacherDataCenterExportJobDetailView.as_view(),
        name="teacher-data-center-export-job-detail",
    ),
    path(
        "courses/<int:course_id>/data/advanced/",
        TeacherDataCenterAdvancedView.as_view(),
        name="teacher-data-center-advanced",
    ),
    path(
        "courses/<int:course_id>/data/advanced/students/",
        TeacherDataCenterAdvancedStudentSearchView.as_view(),
        name="teacher-data-center-advanced-students",
    ),
    path(
        "courses/<int:course_id>/classes/",
        TeacherClassCollectionView.as_view(),
        name="teacher-class-collection",
    ),
    path(
        "courses/<int:course_id>/classes/<int:class_id>/center/",
        TeacherClassCenterView.as_view(),
        name="teacher-class-center",
    ),
]
