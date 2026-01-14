from django.urls import path

from .portal_views import (
    portal_login_view,
    portal_logout_view,
)

from .portal_api import (
    portal_login_api,
    portal_logout_api,
)

app_name = "portal"

urlpatterns = [
    # HTML
    path("login/", portal_login_view, name="login"),
    path("logout/", portal_logout_view, name="logout"),
    # API
    path("api/login/", portal_login_api, name="api-login"),
    path("api/logout/", portal_logout_api, name="api-logout"),
]
