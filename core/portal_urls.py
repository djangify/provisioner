from django.urls import path

from .portal_views import (
    portal_login_view,
    portal_logout_view,
)

from .portal_api import (
    portal_login_api,
    portal_logout_api,
)

from .portal_api import (
    portal_dashboard_api,
    portal_billing_api,
    portal_cancel_subscription_api,
    portal_change_password_api,
)

from .portal_views import (
    portal_dashboard_view,
    portal_billing_view,
    portal_domain_view,
    portal_password_view,
)

from .portal_api import (
    portal_set_custom_domain_api,
    portal_verify_custom_domain_api,
    portal_remove_custom_domain_api,
)

app_name = "portal"

urlpatterns = [
    # HTML
    path("login/", portal_login_view, name="login"),
    path("logout/", portal_logout_view, name="logout"),
    # API
    path("api/login/", portal_login_api, name="api-login"),
    path("api/logout/", portal_logout_api, name="api-logout"),
    # billing API
    path("api/dashboard/", portal_dashboard_api, name="api-dashboard"),
    path("api/billing/", portal_billing_api, name="api-billing"),
    path("api/cancel/", portal_cancel_subscription_api, name="api-cancel"),
    path("api/password/", portal_change_password_api, name="api-password"),
    # portal
    path("", portal_dashboard_view, name="dashboard"),
    path("billing/", portal_billing_view, name="billing"),
    path("domain/", portal_domain_view, name="domain"),
    path("password/", portal_password_view, name="password"),
    # custom domain
    path("api/domain/set/", portal_set_custom_domain_api),
    path("api/domain/verify/", portal_verify_custom_domain_api),
    path("api/domain/remove/", portal_remove_custom_domain_api),
]
