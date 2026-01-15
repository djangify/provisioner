"""
Stripe Webhook Handler (Order-Independent, Idempotent)

Handles Stripe events:
- checkout.session.completed → Create/Update customer + create/update pending instance (metadata lives here)
- customer.subscription.created → Create/Update subscription record
- customer.subscription.deleted → Cancel and stop instance
- invoice.payment_failed → Mark subscription as past_due
- invoice.paid → Confirm payment and provision (Docker + Nginx + Welcome email)

Key design:
- Stripe events can arrive out of order. Provisioning is STATE-DRIVEN, not EVENT-ORDER-DRIVEN.
- Instance creation happens ONLY when we have checkout metadata (subdomain/site_name) to avoid "UNKNOWN" instances.
- Provisioning is idempotent: safe on retries and safe across multiple webhook deliveries.
"""

import re
import stripe
from datetime import datetime

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.utils.crypto import get_random_string
from .nginx_manager import NginxManager
from .models import Customer, Subscription, Instance, ProvisioningLog
from .docker_manager import DockerManager
from .email_service import (
    send_welcome_email,
    send_instance_stopped_email,
    send_admin_notification,
)

stripe.api_key = settings.STRIPE_SECRET_KEY


# -------------------------
# Logging
# -------------------------
def log_webhook(action: str, message: str, details=None):
    """Log webhook events"""
    ProvisioningLog.objects.create(
        instance=None,
        action=action or "webhook",
        message=message,
        details=details or {},
    )


# -------------------------
# Helpers (Stripe → DB)
# -------------------------
def _get_or_create_customer(
    stripe_customer_id: str, email: str | None = None
) -> Customer:
    """
    Ensure a Customer row exists for a Stripe customer id.
    If email is available, store/update it.
    """
    customer, created = Customer.objects.get_or_create(
        stripe_customer_id=stripe_customer_id,
        defaults={"email": email or ""},
    )
    if email and customer.email != email:
        customer.email = email
        customer.save(update_fields=["email"])

    log_webhook(
        "webhook",
        f"Customer {'created' if created else 'found'}",
        {
            "customer_id": customer.id,
            "email": customer.email,
            "stripe_customer_id": stripe_customer_id,
        },
    )
    return customer


def _upsert_subscription_from_stripe(
    stripe_subscription_obj, customer: Customer
) -> Subscription:
    """
    Create/update a Subscription row from a Stripe subscription object.
    """
    stripe_subscription_id = stripe_subscription_obj.get("id", "")
    status = stripe_subscription_obj.get("status", "active")

    # Map Stripe status to our status
    status_map = {
        "active": "active",
        "past_due": "past_due",
        "canceled": "cancelled",
        "unpaid": "unpaid",
        "trialing": "trialing",
        "incomplete": "active",
        "incomplete_expired": "cancelled",
    }

    # Price ID
    price_id = ""
    items = stripe_subscription_obj.get("items", {})
    if items and items.get("data"):
        first_item = items["data"][0]
        price = first_item.get("price", {})
        price_id = price.get("id", "")

    # Period dates
    current_period_start = stripe_subscription_obj.get("current_period_start")
    current_period_end = stripe_subscription_obj.get("current_period_end")

    defaults = {
        "customer": customer,
        "stripe_price_id": price_id,
        "status": status_map.get(status, status),
        "current_period_start": datetime.fromtimestamp(
            current_period_start, tz=timezone.utc
        )
        if current_period_start
        else None,
        "current_period_end": datetime.fromtimestamp(
            current_period_end, tz=timezone.utc
        )
        if current_period_end
        else None,
    }

    sub, created = Subscription.objects.update_or_create(
        stripe_subscription_id=stripe_subscription_id,
        defaults=defaults,
    )

    log_webhook(
        "webhook",
        f"{'Created' if created else 'Updated'} subscription for customer {customer.email}",
        {
            "subscription_id": sub.id,
            "stripe_subscription_id": stripe_subscription_id,
            "status": sub.status,
        },
    )
    return sub


def _get_or_create_subscription(
    customer: Customer,
    stripe_customer_id: str,
    stripe_subscription_id: str | None = None,
) -> Subscription | None:
    """
    Ensure a Subscription exists.
    Preference:
      1) DB by stripe_subscription_id (if provided)
      2) DB by customer
      3) Fetch from Stripe for that customer and upsert
    """
    subscription = None

    if stripe_subscription_id:
        subscription = Subscription.objects.filter(
            stripe_subscription_id=stripe_subscription_id
        ).first()

    if not subscription:
        subscription = (
            Subscription.objects.filter(customer=customer).order_by("-id").first()
        )

    if subscription:
        return subscription

    # Last resort: fetch from Stripe
    try:
        stripe_subs = stripe.Subscription.list(customer=stripe_customer_id, limit=1)
        if not stripe_subs.data:
            log_webhook(
                "error",
                "No subscription found for customer in Stripe",
                {"stripe_customer_id": stripe_customer_id},
            )
            return None

        stripe_sub = stripe_subs.data[0]
        log_webhook(
            "webhook",
            "Recovered subscription from Stripe",
            {"stripe_subscription_id": stripe_sub.id},
        )
        return _upsert_subscription_from_stripe(stripe_sub, customer)

    except stripe.error.StripeError as e:
        log_webhook(
            "error",
            f"Failed to fetch subscription from Stripe: {e}",
            {"stripe_customer_id": stripe_customer_id},
        )
        return None


def _stripe_latest_invoice_is_paid(stripe_subscription_id: str) -> bool:
    """
    Conservative paid check.
    For a subscription, inspect latest_invoice.paid where possible.
    """
    try:
        sub = stripe.Subscription.retrieve(
            stripe_subscription_id, expand=["latest_invoice"]
        )
        latest_invoice = sub.get("latest_invoice")
        if isinstance(latest_invoice, dict):
            return bool(latest_invoice.get("paid"))
        # If latest_invoice is just an ID, retrieve it
        if latest_invoice:
            inv = stripe.Invoice.retrieve(latest_invoice)
            return bool(inv.get("paid"))
        return False
    except stripe.error.StripeError as e:
        log_webhook(
            "error",
            f"Stripe paid check failed: {e}",
            {"stripe_subscription_id": stripe_subscription_id},
        )
        return False


# -------------------------
# Core: ensure provisioned
# -------------------------
def ensure_instance_provisioned(
    *,
    customer: Customer,
    stripe_customer_id: str,
    stripe_subscription_id: str | None = None,
    payment_confirmed: bool = False,
) -> bool:
    """
    State-driven provisioning:
    - Requires: subscription is active AND (payment_confirmed OR Stripe indicates latest invoice paid)
    - Requires: instance exists (created when we have checkout metadata)
    - Idempotent: safe to call repeatedly
    """
    # Instance must exist (we only create it on checkout.session.completed when we have subdomain metadata)
    instance = getattr(customer, "instance", None)
    if not instance:
        log_webhook(
            "webhook",
            "Provisioning deferred: customer has no instance yet (waiting for checkout metadata)",
            {"customer_id": customer.id, "stripe_customer_id": stripe_customer_id},
        )
        return False

    # Subscription must exist / be recoverable
    subscription = _get_or_create_subscription(
        customer, stripe_customer_id, stripe_subscription_id=stripe_subscription_id
    )
    if not subscription:
        log_webhook(
            "webhook",
            "Provisioning deferred: no subscription record available yet",
            {"customer_id": customer.id, "instance_id": instance.id},
        )
        return False

    # Normalize subscription to active when appropriate (invoice.paid can arrive before subscription.created)
    if subscription.status != "active":
        # Only flip to active if we are certain
        if payment_confirmed or (
            stripe_subscription_id
            and _stripe_latest_invoice_is_paid(stripe_subscription_id)
        ):
            subscription.status = "active"
            subscription.save(update_fields=["status"])
        else:
            log_webhook(
                "webhook",
                "Provisioning deferred: subscription not active and payment not confirmed",
                {
                    "subscription_id": subscription.id,
                    "status": subscription.status,
                    "instance_id": instance.id,
                },
            )
            return False

    # Paid check (conservative)
    if not payment_confirmed:
        if stripe_subscription_id:
            if not _stripe_latest_invoice_is_paid(stripe_subscription_id):
                log_webhook(
                    "webhook",
                    "Provisioning deferred: latest invoice not confirmed paid yet",
                    {
                        "instance_id": instance.id,
                        "stripe_subscription_id": stripe_subscription_id,
                    },
                )
                return False
        # If no stripe_subscription_id, we already recovered/created subscription; treat active as sufficient here.

    # If already running and email sent, nothing to do
    if instance.status == "running" and instance.welcome_email_sent:
        log_webhook(
            "webhook",
            "Instance already running and welcome email already sent",
            {"instance_id": instance.id},
        )
        return True

    try:
        manager = DockerManager()

        # Provision container + nginx if not running
        if instance.status != "running":
            manager.provision_instance(instance)

            nginx_manager = NginxManager()
            nginx_manager.provision_nginx(instance)

            instance.status = "running"

        # Send welcome email ONCE (only mark sent if the send succeeds)
        if not instance.welcome_email_sent:
            customer = instance.customer

            portal_password = None
            if not customer.portal_password:
                portal_password = get_random_string(12)
                customer.set_portal_password(portal_password)
                customer.save(update_fields=["portal_password"])

            sent = send_welcome_email(instance, portal_password=portal_password)

            if sent:
                instance.welcome_email_sent = True
                send_admin_notification(instance)

            else:
                log_webhook(
                    "error",
                    "Welcome email failed to send (will retry on next paid/checkout event)",
                    {"instance_id": instance.id},
                )

        instance.save(update_fields=["status", "welcome_email_sent"])

        log_webhook(
            "webhook",
            "Instance active (provisioning ensured)",
            {"instance_id": instance.id, "subdomain": instance.subdomain},
        )
        return True

    except Exception as e:
        log_webhook(
            "error",
            "Failed during provisioning ensure()",
            {"instance_id": instance.id, "error": str(e)},
        )
        return False


# -------------------------
# Main webhook endpoint
# -------------------------
@csrf_exempt
@require_POST
def stripe_webhook(request):
    """
    Main Stripe webhook endpoint.
    Stripe sends events here when payments happen.
    """
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        log_webhook("error", "Invalid payload")
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError:
        log_webhook("error", "Invalid signature")
        return HttpResponse(status=400)

    event_type = event["type"]
    log_webhook("webhook", f"Received event: {event_type}", {"event_id": event["id"]})

    handlers = {
        "checkout.session.completed": handle_checkout_completed,
        "customer.subscription.created": handle_subscription_created,
        "customer.subscription.deleted": handle_subscription_deleted,
        "invoice.payment_failed": handle_payment_failed,
        "invoice.paid": handle_invoice_paid,
    }

    handler = handlers.get(event_type)
    if not handler:
        log_webhook("webhook", f"Unhandled event type: {event_type}")
        return HttpResponse(status=200)

    try:
        handler(event["data"]["object"])
    except Exception as e:
        # IMPORTANT: return 200 anyway so Stripe doesn't retry forever due to our bug
        log_webhook("error", f"Error handling {event_type}: {e}")

    return HttpResponse(status=200)


# -------------------------
# Event handlers
# -------------------------
def handle_checkout_completed(session):
    """
    Checkout completed successfully (intent signal, metadata source).

    We create/update:
    - Customer
    - Instance in 'pending' state (ONLY here, because metadata lives here)

    Then we call ensure_instance_provisioned() which will provision immediately
    if subscription/payment is already active/confirmed (covers out-of-order events).
    """
    email = session.get("customer_email")
    stripe_customer_id = session.get("customer")
    stripe_subscription_id = session.get(
        "subscription"
    )  # may be present for subscription mode
    metadata = session.get("metadata", {}) or {}

    subdomain = (metadata.get("subdomain", "") or "").lower().strip()
    site_name = metadata.get("site_name", "My Shop")

    log_webhook(
        "webhook",
        "Processing checkout.session.completed",
        {
            "session_id": session.get("id"),
            "stripe_customer_id": stripe_customer_id,
            "email": email,
            "subdomain": subdomain,
            "stripe_subscription_id": stripe_subscription_id,
        },
    )

    # Safety checks
    if not stripe_customer_id:
        log_webhook(
            "error",
            "Checkout completed without customer ID",
            {"session_id": session.get("id")},
        )
        return

    if not subdomain:
        log_webhook(
            "error",
            "Checkout completed but no subdomain provided",
            {"session_id": session.get("id")},
        )
        return

    # Validate subdomain format
    if not re.match(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", subdomain):
        log_webhook("error", f"Invalid subdomain format: {subdomain}")
        return

    # Ensure subdomain is not already in use (excluding deleted)
    if Instance.objects.filter(subdomain=subdomain).exclude(status="deleted").exists():
        log_webhook("error", f"Subdomain already taken: {subdomain}")
        return

    # Customer
    customer = _get_or_create_customer(stripe_customer_id, email=email)

    # Instance (ONLY here)
    instance = getattr(customer, "instance", None)

    if instance:
        # If an instance exists, ensure it matches this checkout's subdomain (guard)
        if instance.subdomain != subdomain and instance.status != "deleted":
            log_webhook(
                "error",
                "Customer already has an instance with a different subdomain",
                {
                    "customer_id": customer.id,
                    "existing_subdomain": instance.subdomain,
                    "new_subdomain": subdomain,
                },
            )
            return

        # Update fields if missing
        changed = False
        if site_name and instance.site_name != site_name:
            instance.site_name = site_name
            changed = True
        if email and instance.admin_email != email:
            instance.admin_email = email
            changed = True
        if instance.status == "deleted":
            instance.status = "pending"
            changed = True

        if changed:
            instance.save(update_fields=["site_name", "admin_email", "status"])

        log_webhook(
            "webhook",
            "Instance already exists for customer",
            {"instance_id": instance.id, "subdomain": instance.subdomain},
        )

    else:
        instance = Instance.objects.create(
            customer=customer,
            subdomain=subdomain,
            site_name=site_name,
            admin_email=email,
            status="pending",
        )
        log_webhook(
            "webhook",
            "Instance created in pending state",
            {"instance_id": instance.id, "subdomain": subdomain},
        )

    # Ensure provisioning now (covers out-of-order: invoice.paid may have arrived earlier)
    ensure_instance_provisioned(
        customer=customer,
        stripe_customer_id=stripe_customer_id,
        stripe_subscription_id=stripe_subscription_id,
        payment_confirmed=False,
    )


def handle_subscription_created(subscription_data):
    """
    A new subscription was created.
    We upsert the subscription record.
    Then attempt ensure_instance_provisioned() (may still defer until instance exists).
    """
    stripe_subscription_id = subscription_data.get("id")
    stripe_customer_id = subscription_data.get("customer")
    status = subscription_data.get("status", "active")

    log_webhook(
        "webhook",
        f"Processing subscription created: {stripe_subscription_id}",
        {"customer_id": stripe_customer_id, "status": status},
    )

    if not stripe_customer_id:
        log_webhook(
            "error",
            "Subscription created without customer id",
            {"stripe_subscription_id": stripe_subscription_id},
        )
        return

    # Ensure customer exists (recover email from Stripe if needed)
    try:
        customer = Customer.objects.get(stripe_customer_id=stripe_customer_id)
    except Customer.DoesNotExist:
        stripe_customer = stripe.Customer.retrieve(stripe_customer_id)
        customer = _get_or_create_customer(
            stripe_customer_id, email=stripe_customer.get("email")
        )

    # Upsert subscription
    _upsert_subscription_from_stripe(subscription_data, customer)

    # Try provision (may defer)
    ensure_instance_provisioned(
        customer=customer,
        stripe_customer_id=stripe_customer_id,
        stripe_subscription_id=stripe_subscription_id,
        payment_confirmed=False,
    )


def handle_subscription_deleted(subscription_data):
    """
    Subscription cancelled/deleted.
    Stop the instance (but keep data for potential reactivation).
    """
    stripe_subscription_id = subscription_data.get("id")

    try:
        subscription = Subscription.objects.get(
            stripe_subscription_id=stripe_subscription_id
        )
    except Subscription.DoesNotExist:
        log_webhook(
            "webhook", f"Subscription not found for deletion: {stripe_subscription_id}"
        )
        return

    subscription.status = "cancelled"
    subscription.cancelled_at = timezone.now()
    subscription.save(update_fields=["status", "cancelled_at"])

    # Stop the instance if running
    instance = getattr(subscription.customer, "instance", None)
    if instance and instance.status == "running":
        try:
            manager = DockerManager()
            manager.stop_instance(instance)
            send_instance_stopped_email(instance, reason="subscription_cancelled")
            log_webhook(
                "webhook",
                "Stopped instance for cancelled subscription",
                {"instance_id": instance.id},
            )
        except Exception as e:
            log_webhook(
                "error", f"Failed to stop instance: {e}", {"instance_id": instance.id}
            )

    log_webhook("webhook", f"Cancelled subscription {stripe_subscription_id}")


def handle_payment_failed(invoice):
    """
    Payment failed.
    Mark subscription as past_due but don't immediately stop instance.
    """
    stripe_subscription_id = invoice.get("subscription")
    if not stripe_subscription_id:
        return

    try:
        subscription = Subscription.objects.get(
            stripe_subscription_id=stripe_subscription_id
        )
        subscription.status = "past_due"
        subscription.save(update_fields=["status"])
        log_webhook("webhook", f"Payment failed for {stripe_subscription_id}")
    except Subscription.DoesNotExist:
        log_webhook(
            "webhook",
            f"Subscription not found for failed payment: {stripe_subscription_id}",
        )


def handle_invoice_paid(invoice):
    """
    Payment confirmed.
    This is the strongest signal we have, so we:
    - Ensure customer exists (recover from Stripe)
    - Ensure subscription exists (recover from Stripe)
    - Attempt provisioning (will defer if instance doesn't exist yet)
    """
    stripe_subscription_id = invoice.get("subscription")
    stripe_customer_id = invoice.get("customer")

    log_webhook(
        "webhook",
        "Processing invoice.paid",
        {
            "invoice_id": invoice.get("id"),
            "subscription_id": stripe_subscription_id,
            "customer_id": stripe_customer_id,
        },
    )

    if not stripe_customer_id:
        log_webhook("webhook", "Invoice paid but no customer ID - skipping")
        return

    # Ensure customer exists (recover from Stripe if missing)
    try:
        customer = Customer.objects.get(stripe_customer_id=stripe_customer_id)
    except Customer.DoesNotExist:
        stripe_customer = stripe.Customer.retrieve(stripe_customer_id)
        customer = _get_or_create_customer(
            stripe_customer_id, email=stripe_customer.get("email")
        )
        log_webhook(
            "webhook",
            "Recovered missing customer during invoice.paid",
            {"stripe_customer_id": stripe_customer_id},
        )

    # Ensure subscription exists (recover if missing)
    subscription = _get_or_create_subscription(
        customer, stripe_customer_id, stripe_subscription_id=stripe_subscription_id
    )
    if subscription and subscription.status != "active":
        subscription.status = "active"
        subscription.save(update_fields=["status"])

    # Attempt provisioning (may defer until checkout metadata creates instance)
    ensured = ensure_instance_provisioned(
        customer=customer,
        stripe_customer_id=stripe_customer_id,
        stripe_subscription_id=stripe_subscription_id,
        payment_confirmed=True,
    )

    if not ensured:
        log_webhook(
            "webhook",
            "invoice.paid received but provisioning deferred (waiting for checkout/session metadata)",
            {
                "stripe_customer_id": stripe_customer_id,
                "stripe_subscription_id": stripe_subscription_id,
            },
        )
