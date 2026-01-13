"""
Stripe Webhook Handler

Handles Stripe events:
- checkout.session.completed → Create customer and Start / resume instance
- customer.subscription.created → Create subscription record
- customer.subscription.deleted → Cancel and stop instance
- invoice.payment_failed → Mark subscription as past_due
- invoice.paid → Reactivate if was past_due
"""

import stripe
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone
from datetime import datetime

from .models import Customer, Subscription, Instance, ProvisioningLog
from .docker_manager import DockerManager
from .email_service import (
    send_welcome_email,
    send_instance_stopped_email,
    send_admin_notification,
)

stripe.api_key = settings.STRIPE_SECRET_KEY


def log_webhook(action, message, details=None):
    """Log webhook events"""
    ProvisioningLog.objects.create(
        instance=None, action="webhook", message=message, details=details or {}
    )


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
        log_webhook("webhook", "Invalid payload")
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError:
        log_webhook("webhook", "Invalid signature")
        return HttpResponse(status=400)

    event_type = event["type"]

    log_webhook("webhook", f"Received event: {event_type}", {"event_id": event["id"]})

    # Route to appropriate handler
    handlers = {
        "checkout.session.completed": handle_checkout_completed,
        "customer.subscription.created": handle_subscription_created,
        "customer.subscription.deleted": handle_subscription_deleted,
        "invoice.payment_failed": handle_payment_failed,
        "invoice.paid": handle_invoice_paid,
    }

    handler = handlers.get(event_type)
    if not handler:
        # Unhandled event - log it and return 200 to stop Stripe retrying
        log_webhook("webhook", f"Unhandled event type: {event_type}")
        return HttpResponse(status=200)

    try:
        handler(event["data"]["object"])
    except Exception as e:
        log_webhook("error", f"Error handling {event_type}: {e}")
        # Return 200 anyway to prevent Stripe retries for our bugs

    return HttpResponse(status=200)


def handle_checkout_completed(session):
    """
    Checkout completed successfully.

    This confirms user intent, NOT payment.
    We create:
    - Customer (if needed)
    - Instance in 'pending' state

    Actual provisioning happens on invoice.paid.
    """
    import re

    email = session.get("customer_email")
    stripe_customer_id = session.get("customer")
    metadata = session.get("metadata", {}) or {}

    subdomain = metadata.get("subdomain", "").lower().strip()
    site_name = metadata.get("site_name", "My Shop")

    log_webhook(
        "webhook",
        "Processing checkout.session.completed",
        {
            "session_id": session.get("id"),
            "stripe_customer_id": stripe_customer_id,
            "email": email,
            "subdomain": subdomain,
        },
    )

    # Safety checks
    if not stripe_customer_id:
        log_webhook("error", "Checkout completed without customer ID", session)
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

    # Ensure subdomain is not already in use
    if Instance.objects.filter(subdomain=subdomain).exclude(status="deleted").exists():
        log_webhook("error", f"Subdomain already taken: {subdomain}")
        return

    # Create or update customer
    customer, created = Customer.objects.get_or_create(
        stripe_customer_id=stripe_customer_id,
        defaults={"email": email},
    )

    if email and customer.email != email:
        customer.email = email
        customer.save(update_fields=["email"])

    log_webhook(
        "webhook",
        f"Customer {'created' if created else 'found'}",
        {"customer_id": customer.id, "email": customer.email},
    )

    # Prevent duplicate instances for same customer + subdomain
    existing_instance = (
        Instance.objects.filter(customer=customer, subdomain=subdomain)
        .exclude(status="deleted")
        .first()
    )

    if existing_instance:
        log_webhook(
            "webhook",
            "Instance already exists for customer and subdomain",
            {"instance_id": existing_instance.id},
        )
        return

    # Create instance in pending state
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
        {
            "instance_id": instance.id,
            "subdomain": subdomain,
        },
    )


def handle_subscription_created(subscription_data):
    """
    A new subscription was created.

    This event fires once when a subscription is created, with clear intent.
    We use this to create/update the subscription record with period dates.
    """
    stripe_subscription_id = subscription_data["id"]
    stripe_customer_id = subscription_data.get("customer")
    status = subscription_data.get("status", "active")

    log_webhook(
        "webhook",
        f"Processing subscription created: {stripe_subscription_id}",
        {"customer_id": stripe_customer_id, "status": status},
    )

    # Find the customer
    try:
        customer = Customer.objects.get(stripe_customer_id=stripe_customer_id)
    except Customer.DoesNotExist:
        log_webhook(
            "webhook",
            f"Customer not found for subscription {stripe_subscription_id}",
            {"stripe_customer_id": stripe_customer_id},
        )
        return

    # Check if subscription already exists
    existing_sub = Subscription.objects.filter(
        stripe_subscription_id=stripe_subscription_id
    ).first()

    if existing_sub:
        log_webhook(
            "webhook",
            f"Subscription already exists: {stripe_subscription_id}",
        )
        return

    # Get price ID from subscription items
    price_id = ""
    items = subscription_data.get("items", {})
    if items and items.get("data"):
        first_item = items["data"][0]
        price = first_item.get("price", {})
        price_id = price.get("id", "")

    # Get period dates safely
    current_period_start = subscription_data.get("current_period_start")
    current_period_end = subscription_data.get("current_period_end")

    # Map Stripe status to our status
    status_map = {
        "active": "active",
        "past_due": "past_due",
        "canceled": "cancelled",
        "unpaid": "unpaid",
        "trialing": "trialing",
        "incomplete": "active",  # Treat incomplete as active for now
        "incomplete_expired": "cancelled",
    }

    # Create subscription record
    subscription = Subscription.objects.create(
        customer=customer,
        stripe_subscription_id=stripe_subscription_id,
        stripe_price_id=price_id,
        status=status_map.get(status, status),
        current_period_start=datetime.fromtimestamp(
            current_period_start, tz=timezone.utc
        )
        if current_period_start
        else None,
        current_period_end=datetime.fromtimestamp(current_period_end, tz=timezone.utc)
        if current_period_end
        else None,
    )

    log_webhook(
        "webhook",
        f"Created subscription for customer {customer.email}",
        {"subscription_id": subscription.id, "status": status},
    )


def handle_subscription_deleted(subscription_data):
    """
    Subscription cancelled/deleted.
    Stop the instance (but keep data for potential reactivation).
    """
    stripe_subscription_id = subscription_data["id"]

    try:
        subscription = Subscription.objects.get(
            stripe_subscription_id=stripe_subscription_id
        )
    except Subscription.DoesNotExist:
        log_webhook(
            "webhook",
            f"Subscription not found for deletion: {stripe_subscription_id}",
        )
        return

    subscription.status = "cancelled"
    subscription.cancelled_at = timezone.now()
    subscription.save()

    # Stop the instance
    instance = subscription.customer.instance
    if instance and instance.status == "running":
        try:
            manager = DockerManager()
            manager.stop_instance(instance)
            send_instance_stopped_email(instance, reason="subscription_cancelled")
            log_webhook("webhook", "Stopped instance for cancelled subscription")
        except Exception as e:
            log_webhook("error", f"Failed to stop instance: {e}")

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

    # Find the customer
    try:
        customer = Customer.objects.get(stripe_customer_id=stripe_customer_id)
    except Customer.DoesNotExist:
        log_webhook(
            "error",
            "Invoice paid but customer not found",
            {"stripe_customer_id": stripe_customer_id},
        )
        return

    # Find subscription - either by ID from invoice, or by looking up via customer
    subscription = None

    if stripe_subscription_id:
        subscription = Subscription.objects.filter(
            stripe_subscription_id=stripe_subscription_id
        ).first()

    if not subscription:
        # Try to find subscription via customer
        subscription = Subscription.objects.filter(customer=customer).first()

    if not subscription:
        # Last resort: fetch from Stripe
        try:
            stripe_subs = stripe.Subscription.list(customer=stripe_customer_id, limit=1)
            if stripe_subs.data:
                stripe_sub = stripe_subs.data[0]
                log_webhook(
                    "webhook",
                    f"Found subscription in Stripe: {stripe_sub.id}",
                )
                # Create subscription record using existing logic from handle_subscription_created
                price_id = ""
                items = stripe_sub.get("items", {})
                if items and items.get("data"):
                    first_item = items["data"][0]
                    price = first_item.get("price", {})
                    price_id = price.get("id", "")

                subscription = Subscription.objects.create(
                    customer=customer,
                    stripe_subscription_id=stripe_sub.id,
                    stripe_price_id=price_id,
                    status="active",
                )
            else:
                log_webhook(
                    "error",
                    "No subscription found for customer",
                    {"stripe_customer_id": stripe_customer_id},
                )
                return
        except stripe.error.StripeError as e:
            log_webhook(
                "error",
                f"Failed to fetch subscription from Stripe: {e}",
            )
            return

    # Mark subscription active
    if subscription.status != "active":
        subscription.status = "active"
        subscription.save(update_fields=["status"])

    instance = subscription.customer.instance
    if not instance:
        log_webhook(
            "error",
            "Invoice paid but no instance found",
            {"subscription_id": subscription.id},
        )
        return

    # If already running AND email already sent, this is a retry or renewal
    if instance.status == "running" and instance.welcome_email_sent:
        log_webhook(
            "webhook",
            "Invoice paid retry received; instance already active",
            {"instance_id": instance.id},
        )
        return

    try:
        manager = DockerManager()

        # Provision if not running
        if instance.status != "running":
            manager.provision_instance(instance)
            instance.status = "running"

        # Send welcome email ONCE
        if not instance.welcome_email_sent:
            send_welcome_email(instance)
            instance.welcome_email_sent = True
            send_admin_notification(instance)

        instance.save(update_fields=["status", "welcome_email_sent"])

        log_webhook(
            "webhook",
            "Instance active and welcome email confirmed",
            {"instance_id": instance.id},
        )

    except Exception as e:
        log_webhook(
            "error",
            "Failed during invoice.paid handling",
            {"instance_id": instance.id, "error": str(e)},
        )
