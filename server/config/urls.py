from django.contrib import admin
from django.urls import include, path

from apps.accounts.health import health
from apps.api.frontend_entry import page_urlpatterns

urlpatterns = [
    path('api/v1/', include('apps.api.urls')),
    path('api/v1/student/', include('apps.assessment.api.mutation_urls')),
    path('api/v1/assessment/process/', include('apps.assessment.process_urls')),
    path('health/', health, name='health'),
    path('admin/', admin.site.urls),
    *page_urlpatterns(),
]

admin.site.site_header = 'KT Data Lab 管理后台'
admin.site.site_title = 'KT Data Lab 管理后台'
admin.site.index_title = '平台数据管理'
