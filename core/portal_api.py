import json
import stripe

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from .models import Customer
from .portal_auth import portal_login, portal_logout, portal_login_required
from django.views.decorators.csrf import csrf_exempt

from core.services.custom_domain_service import (
    setup_custom_domain,
    remove_custom_domain,
    retry_ssl,
    check_domain_in_nginx,
    check_domain_ownership,
    CustomDomainError,
)


stripe.api_key = settings.STRIPE_SECRET_KEY


# =========================
# AUTH ENDPOINTS
# =========================


@csrf_exempt
@require_POST
def portal_login_api(request):
    """Login endpoint for customer portal."""
    email = request.POST.get("email", "").strip().lower()
    password = request.POST.get("password", "")

    if not email or not password:
        return JsonResponse({"error": "Email and password are required"}, status=400)

    try:
        customer = Customer.objects.get(email=email)
    except Customer.DoesNotExist:
        return JsonResponse({"error": "Invalid credentials"}, status=401)

    if not customer.check_portal_password(password):
        return JsonResponse({"error": "Invalid credentials"}, status=401)

    portal_login(request, customer)

    return JsonResponse({"success": True})


@require_POST
def portal_logout_api(request):
    """Logout endpoint for customer portal."""
    portal_logout(request)
    return JsonResponse({"success": True})


# =========================
# DASHBOARD ENDPOINT
# =========================


@portal_login_required
@require_GET
def portal_dashboard_api(request):
    """Get dashboard data for logged-in customer."""
    customer = request.portal_customer
    instance = customer.instance
    subscription = customer.active_subscription

    data = {
        "customer": {
            "email": customer.email,
            "name": customer.name,
        },
        "instance": None,
        "subscription": None,
    }

    if instance:
        data["instance"] = {
            "subdomain": instance.subdomain,
            "custom_domain": instance.custom_domain,
            "custom_domain_verified": instance.custom_domain_verified,
            "custom_domain_ssl": instance.custom_domain_ssl,
            "status": instance.status,
            "site_name": instance.site_name,
            "url": instance.full_url,
        }

    if subscription:
        data["subscription"] = {
            "status": subscription.status,
            "current_period_end": subscription.current_period_end,
            "cancelled_at": subscription.cancelled_at,
        }

    return JsonResponse(data)


# =========================
# BILLING ENDPOINTS
# =========================


@portal_login_required
@require_GET
def portal_billing_api(request):
    """Redirect to Stripe billing portal."""
    customer = request.portal_customer

    if not customer.stripe_customer_id:
        return JsonResponse({"error": "No Stripe customer found"}, status=400)

    try:
        session = stripe.billing_portal.Session.create(
            customer=customer.stripe_customer_id,
            return_url=request.build_absolute_uri("/portal/"),
        )
    except stripe.error.StripeError as e:
        return JsonResponse({"error": str(e)}, status=400)

    return JsonResponse({"url": session.url})


@portal_login_required
@require_POST
def portal_cancel_subscription_api(request):
    """Cancel subscription at period end."""
    customer = request.portal_customer
    subscription = customer.active_subscription

    if not subscription:
        return JsonResponse({"error": "No active subscription"}, status=400)

    try:
        stripe.Subscription.modify(
            subscription.stripe_subscription_id,
            cancel_at_period_end=True,
        )
    except stripe.error.StripeError as e:
        return JsonResponse({"error": str(e)}, status=400)

    return JsonResponse(
        {
            "success": True,
            "message": "Subscription will cancel at the end of the billing period.",
        }
    )


# =========================
# PASSWORD ENDPOINT
# =========================


@portal_login_required
@require_POST
def portal_change_password_api(request):
    """Change portal password."""
    customer = request.portal_customer

    new_password = request.POST.get("new_password", "")
    confirm_password = request.POST.get("confirm_password", "")

    if not new_password or not confirm_password:
        return JsonResponse({"error": "Both password fields are required"}, status=400)

    if new_password != confirm_password:
        return JsonResponse({"error": "Passwords do not match"}, status=400)

    if len(new_password) < 8:
        return JsonResponse(
            {"error": "Password must be at least 8 characters"}, status=400
        )

    customer.set_portal_password(new_password)

    return JsonResponse({"success": True})


# =========================
# CUSTOM DOMAIN ENDPOINTS
# =========================


@csrf_exempt
@portal_login_required
@require_POST
def portal_set_custom_domain_api(request):
    """
    Save custom domain (validation only, no provisioning yet).

    Performs preflight checks:
    - Domain format validation
    - Check nginx configs for conflicts
    - Check database for ownership conflicts
    """
    instance = request.portal_customer.instance

    if not instance:
        return JsonResponse({"error": "No instance found"}, status=400)

    data = json.loads(request.body or "{}")

    domain = (
        data.get("domain", "")
        .strip()
        .lower()
        .replace("https://", "")
        .replace("http://", "")
        .rstrip("/")
    )

    # ---- VALIDATION ----
    if not domain:
        return JsonResponse({"error": "Domain is required"}, status=400)

    if " " in domain or "." not in domain:
        return JsonResponse({"error": "Invalid domain format"}, status=400)

    # Don't allow subdomains of our own domain
    if domain.endswith(f".{settings.BASE_DOMAIN}"):
        return JsonResponse(
            {"error": f"Cannot use subdomains of {settings.BASE_DOMAIN}"}, status=400
        )

    # ---- PREFLIGHT: CHECK NGINX CONFIGS ----
    nginx_conflict = check_domain_in_nginx(domain, exclude_instance=instance)
    if nginx_conflict:
        return JsonResponse(
            {
                "error": f"Domain {domain} is already configured on this server. "
                f"Contact support if you believe this is an error.",
            },
            status=400,
        )

    # ---- PREFLIGHT: CHECK DATABASE OWNERSHIP ----
    other_instance = check_domain_ownership(domain, exclude_instance=instance)
    if other_instance:
        return JsonResponse(
            {
                "error": f"Domain {domain} is already assigned to another store.",
            },
            status=400,
        )

    # ---- SAVE (not yet verified) ----
    instance.custom_domain = domain
    instance.custom_domain_verified = False
    instance.custom_domain_ssl = False
    instance.save(
        update_fields=[
            "custom_domain",
            "custom_domain_verified",
            "custom_domain_ssl",
        ]
    )

    return JsonResponse(
        {
            "success": True,
            "domain": domain,
            "instructions": (
                f"Add A records for {domain} and www.{domain} "
                f"pointing to {settings.SERVER_IP}. "
                f"Then click 'Verify & activate'."
            ),
            "server_ip": settings.SERVER_IP,
        }
    )


@csrf_exempt
@portal_login_required
@require_POST
def portal_verify_custom_domain_api(request):
    """
    Verify DNS and provision nginx + SSL for custom domain.

    This is idempotent - safe to call multiple times.
    """
    instance = request.portal_customer.instance

    if not instance:
        return JsonResponse({"error": "No instance found"}, status=400)

    if not instance.custom_domain:
        return JsonResponse(
            {"error": "No custom domain set. Save a domain first."}, status=400
        )

    try:
        setup_custom_domain(instance)

        # Refresh instance from DB
        instance.refresh_from_db()

        return JsonResponse(
            {
                "success": True,
                "domain": instance.custom_domain,
                "verified": instance.custom_domain_verified,
                "ssl": instance.custom_domain_ssl,
                "message": (
                    "Domain is live with HTTPS!"
                    if instance.custom_domain_ssl
                    else "Domain is live (SSL pending - you can retry later)"
                ),
            }
        )
    except CustomDomainError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except Exception as e:
        return JsonResponse({"error": f"Unexpected error: {str(e)}"}, status=500)


@csrf_exempt
@portal_login_required
@require_POST
def portal_retry_ssl_api(request):
    """
    Retry SSL certificate issuance for a verified domain.
    """
    from core.services.custom_domain_service import obtain_ssl_certificate
    from core.nginx_manager import NginxManager

    instance = request.portal_customer.instance

    if not instance:
        return JsonResponse({"error": "No instance found"}, status=400)

    if not instance.custom_domain:
        return JsonResponse({"error": "No custom domain configured"}, status=400)

    if not instance.custom_domain_verified:
        return JsonResponse(
            {"error": "Domain must be verified before SSL can be issued"}, status=400
        )

    if instance.custom_domain_ssl:
        return JsonResponse({"error": "SSL is already active"}, status=400)

    # Attempt SSL issuance
    ssl_ok = obtain_ssl_certificate(instance.custom_domain)

    if not ssl_ok:
        return JsonResponse(
            {"error": "SSL certificate issuance failed. Please try again later."},
            status=400,
        )

    # Update instance and nginx
    instance.custom_domain_ssl = True
    instance.save(update_fields=["custom_domain_ssl"])

    nginx = NginxManager()
    nginx.provision_nginx(instance)

    return JsonResponse(
        {"success": True, "message": "SSL certificate issued successfully!"}
    )


@csrf_exempt
@portal_login_required
@require_POST
def portal_remove_custom_domain_api(request):
    """
    Remove custom domain from instance.

    Optional: Pass {"delete_certificate": true} to also remove SSL cert.
    """
    instance = request.portal_customer.instance

    if not instance:
        return JsonResponse({"error": "No instance found"}, status=400)

    if not instance.custom_domain:
        return JsonResponse(
            {
                "success": True,
                "message": "No custom domain to remove",
            }
        )

    # Check if user wants to delete the SSL certificate too
    delete_certificate = False
    try:
        data = json.loads(request.body or "{}")
        delete_certificate = data.get("delete_certificate", False)
    except json.JSONDecodeError:
        pass

    domain = instance.custom_domain  # Save before removal

    remove_custom_domain(instance, delete_certificate=delete_certificate)

    return JsonResponse(
        {
            "success": True,
            "message": f"Custom domain {domain} removed. Your subdomain continues to work.",
            "certificate_deleted": delete_certificate,
        }
    )


@csrf_exempt
@portal_login_required
@require_GET
def portal_domain_status_api(request):
    """
    Get current domain status for the instance.
    """
    instance = request.portal_customer.instance

    if not instance:
        return JsonResponse({"error": "No instance found"}, status=400)

    return JsonResponse(
        {
            "subdomain": instance.subdomain,
            "subdomain_url": instance.full_url,
            "custom_domain": instance.custom_domain or None,
            "custom_domain_verified": instance.custom_domain_verified,
            "custom_domain_ssl": instance.custom_domain_ssl,
            "custom_domain_url": f"https://{instance.custom_domain}"
            if instance.custom_domain and instance.custom_domain_ssl
            else None,
            "server_ip": settings.SERVER_IP,
        }
    )
