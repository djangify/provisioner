import stripe

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from .models import Customer
from .portal_auth import portal_login, portal_logout, portal_login_required
from django.views.decorators.csrf import csrf_exempt

from core.services.custom_domain_service import (
    verify_dns,
    setup_custom_domain,
    remove_custom_domain,
)


stripe.api_key = settings.STRIPE_SECRET_KEY


# Login Endpoint
@csrf_exempt
@require_POST
def portal_login_api(request):
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


# Logout end point
@require_POST
def portal_logout_api(request):
    portal_logout(request)
    return JsonResponse({"success": True})


# Dashboard endpoint


@portal_login_required
@require_GET
def portal_dashboard_api(request):
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


# Stripe billing portal endpoint - GET /portal/api/billing/
@portal_login_required
@require_GET
def portal_billing_api(request):
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


# Cancel subscription endpoint - POST /portal/api/cancel/


@portal_login_required
@require_POST
def portal_cancel_subscription_api(request):
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


# Change portal password - POST /portal/api/password/
@portal_login_required
@require_POST
def portal_change_password_api(request):
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


@portal_login_required
@require_POST
def portal_set_custom_domain_api(request):
    instance = request.portal_customer.instance
    domain = request.POST.get("domain", "").strip().lower()

    instance.custom_domain = domain
    instance.custom_domain_verified = False
    instance.custom_domain_ssl = False
    instance.save(
        update_fields=["custom_domain", "custom_domain_verified", "custom_domain_ssl"]
    )

    return JsonResponse(
        {
            "success": True,
            "instructions": f"Add an A record for {domain} → {settings.SERVER_IP}",
        }
    )


@portal_login_required
@require_POST
def portal_verify_custom_domain_api(request):
    instance = request.portal_customer.instance

    try:
        setup_custom_domain(instance)
        return JsonResponse({"success": True})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@portal_login_required
@require_POST
def portal_remove_custom_domain_api(request):
    instance = request.portal_customer.instance
    remove_custom_domain(instance)
    return JsonResponse({"success": True})
