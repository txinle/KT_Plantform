from django.urls import path
from .admin_choice_views import AdminChoiceView
from .admin_workspace_views import AdminCatalogView, AdminCourseActionView, AdminUserActionView, AdminBankUpdateView, AdminAccessView, AdminRecycleView, AdminRecycleRestoreView

urlpatterns = [
    path('choices/<str:kind>/', AdminChoiceView.as_view()),
    path('recycle/', AdminRecycleView.as_view()),
    path('recycle/<int:entry_id>/restore/', AdminRecycleRestoreView.as_view()),
    path('access/', AdminAccessView.as_view()),
    path('resources/banks/<int:bank_id>/update/', AdminBankUpdateView.as_view()),
    path('users/<int:user_id>/<str:action>/', AdminUserActionView.as_view()),
    path('courses/<int:course_id>/<str:action>/', AdminCourseActionView.as_view()),
    path('<str:section>/', AdminCatalogView.as_view()),
]
