from django.urls import path
from .admin_workspace_views import AdminCatalogView, AdminCourseActionView, AdminUserActionView, AdminBankUpdateView, AdminAccessView

urlpatterns = [
    path('access/', AdminAccessView.as_view()),
    path('resources/banks/<int:bank_id>/update/', AdminBankUpdateView.as_view()),
    path('users/<int:user_id>/<str:action>/', AdminUserActionView.as_view()),
    path('courses/<int:course_id>/<str:action>/', AdminCourseActionView.as_view()),
    path('<str:section>/', AdminCatalogView.as_view()),
]
