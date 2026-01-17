from django.urls import path

from .portal_views import (
    portal_login_view,
    portal_logout_view,
    portal_dashboard_view,
    portal_billing_view,
    portal_domain_view,
    portal_password_view,
)

from .portal_api import (
    portal_login_api,
    portal_logout_api,
    portal_dashboard_api,
    portal_billing_api,
    portal_cancel_subscription_api,
    portal_change_password_api,
    portal_set_custom_domain_api,
    portal_verify_custom_domain_api,
    portal_retry_ssl_api,
    portal_remove_custom_domain_api,
    portal_domain_status_api,
)

app_name = "portal"

urlpatterns = [
    # ---- HTML VIEWS ----
    path("login/", portal_login_view, name="login"),
    path("logout/", portal_logout_view, name="logout"),
    path("", portal_dashboard_view, name="dashboard"),
    path("billing/", portal_billing_view, name="billing"),
    path("domain/", portal_domain_view, name="domain"),
    path("password/", portal_password_view, name="password"),
    # ---- AUTH API ----
    path("api/login/", portal_login_api, name="api-login"),
    path("api/logout/", portal_logout_api, name="api-logout"),
    # ---- DASHBOARD API ----
    path("api/dashboard/", portal_dashboard_api, name="api-dashboard"),
    # ---- BILLING API ----
    path("api/billing/", portal_billing_api, name="api-billing"),
    path("api/cancel/", portal_cancel_subscription_api, name="api-cancel"),
    # ---- PASSWORD API ----
    path("api/password/", portal_change_password_api, name="api-password"),
    # ---- CUSTOM DOMAIN API ----
    path("api/domain/status/", portal_domain_status_api, name="api-domain-status"),
    path("api/domain/set/", portal_set_custom_domain_api, name="api-domain-set"),
    path(
        "api/domain/verify/", portal_verify_custom_domain_api, name="api-domain-verify"
    ),
    path("api/domain/retry-ssl/", portal_retry_ssl_api, name="api-domain-retry-ssl"),
    path(
        "api/domain/remove/", portal_remove_custom_domain_api, name="api-domain-remove"
    ),
]
