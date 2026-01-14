"""
Provisioner URL Configuration
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.views.generic import TemplateView

# Admin customization
admin.site.site_header = settings.ADMIN_SITE_HEADER
admin.site.site_title = settings.ADMIN_SITE_TITLE
admin.site.index_title = settings.ADMIN_INDEX_TITLE

urlpatterns = [
    path(
        "",
        TemplateView.as_view(template_name="home.html"),
        name="home",
    ),
    path("admin/", admin.site.urls),
    # Customer portal (HTML + portal APIs)
    path("portal/", include("core.portal_urls")),
    # Provisioner API
    path("api/", include("core.urls")),
]
