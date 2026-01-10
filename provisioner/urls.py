"""
Provisioner URL Configuration
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings

# Admin customization
admin.site.site_header = settings.ADMIN_SITE_HEADER
admin.site.site_title = settings.ADMIN_SITE_TITLE
admin.site.index_title = settings.ADMIN_INDEX_TITLE

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('core.urls')),
]
