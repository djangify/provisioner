"""
Stripe Webhook Handler

Handles Stripe events:
- checkout.session.completed → Create customer, subscription, and provision instance
- customer.subscription.updated → Update subscription status
- customer.subscription.deleted → Cancel and stop instance
- invoice.payment_failed → Mark subscription as past_due
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
from .email_service import send_welcome_email, send_instance_stopped_email

stripe.api_key = settings.STRIPE_SECRET_KEY


def log_webhook(action, message, details=None):
    """Log webhook events"""
    ProvisioningLog.objects.create(
        instance=None,
        action='webhook',
        message=message,
        details=details or {}
    )


@csrf_exempt
@require_POST
def stripe_webhook(request):
    """
    Main Stripe webhook endpoint.
    Stripe sends events here when payments happen.
    """
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        log_webhook('webhook', 'Invalid payload')
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError:
        log_webhook('webhook', 'Invalid signature')
        return HttpResponse(status=400)
    
    event_type = event['type']
    
    log_webhook('webhook', f'Received event: {event_type}', {'event_id': event['id']})
    
    # Route to appropriate handler
    handlers = {
        'checkout.session.completed': handle_checkout_completed,
        'customer.subscription.updated': handle_subscription_updated,
        'customer.subscription.deleted': handle_subscription_deleted,
        'invoice.payment_failed': handle_payment_failed,
        'invoice.paid': handle_invoice_paid,
    }
    
    handler = handlers.get(event_type)
    if handler:
        try:
            handler(event['data']['object'])
        except Exception as e:
            log_webhook('error', f'Error handling {event_type}: {e}')
            # Return 200 anyway to prevent Stripe retries for our bugs
    
    return HttpResponse(status=200)


def handle_checkout_completed(session):
    """
    New customer completed checkout.
    
    Expected metadata in checkout session:
    - subdomain: The subdomain they chose (e.g., 'janes-shop')
    - site_name: Their store name (e.g., "Jane's Digital Shop")
    """
    email = session.get('customer_email')
    stripe_customer_id = session.get('customer')
    stripe_subscription_id = session.get('subscription')
    metadata = session.get('metadata', {})
    
    subdomain = metadata.get('subdomain', '').lower().strip()
    site_name = metadata.get('site_name', 'My Shop')
    
    if not subdomain:
        log_webhook('error', 'Checkout completed but no subdomain in metadata', {
            'session_id': session['id']
        })
        return
    
    # Validate subdomain
    import re
    if not re.match(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$', subdomain):
        log_webhook('error', f'Invalid subdomain format: {subdomain}')
        return
    
    # Check subdomain availability
    if Instance.objects.filter(subdomain=subdomain).exclude(status='deleted').exists():
        log_webhook('error', f'Subdomain already taken: {subdomain}')
        # TODO: Handle this edge case - email customer?
        return
    
    # Create or get customer
    customer, created = Customer.objects.get_or_create(
        stripe_customer_id=stripe_customer_id,
        defaults={'email': email}
    )
    if not created and customer.email != email:
        customer.email = email
        customer.save(update_fields=['email'])
    
    # Fetch subscription details from Stripe
    stripe_sub = stripe.Subscription.retrieve(stripe_subscription_id)
    
    # Create subscription record
    subscription = Subscription.objects.create(
        customer=customer,
        stripe_subscription_id=stripe_subscription_id,
        stripe_price_id=stripe_sub['items']['data'][0]['price']['id'],
        status='active',
        current_period_start=datetime.fromtimestamp(
            stripe_sub['current_period_start'], tz=timezone.utc
        ),
        current_period_end=datetime.fromtimestamp(
            stripe_sub['current_period_end'], tz=timezone.utc
        ),
    )
    
    # Create instance record
    instance = Instance.objects.create(
        customer=customer,
        subdomain=subdomain,
        site_name=site_name,
        admin_email=email,
        status='pending'
    )
    
    log_webhook('webhook', f'Created customer and instance for {subdomain}', {
        'customer_id': customer.id,
        'instance_id': instance.id
    })
    
    # Provision the Docker container
    try:
        manager = DockerManager()
        manager.provision_instance(instance)
        
        # Send welcome email
        send_welcome_email(instance)
        
        log_webhook('webhook', f'Successfully provisioned {subdomain}')
        
    except Exception as e:
        log_webhook('error', f'Failed to provision {subdomain}: {e}')
        # Instance status will be 'error' from docker_manager


def handle_subscription_updated(subscription_data):
    """
    Subscription status changed (e.g., payment method updated, plan changed).
    """
    stripe_subscription_id = subscription_data['id']
    new_status = subscription_data['status']
    
    try:
        subscription = Subscription.objects.get(
            stripe_subscription_id=stripe_subscription_id
        )
    except Subscription.DoesNotExist:
        log_webhook('webhook', f'Subscription not found: {stripe_subscription_id}')
        return
    
    # Map Stripe status to our status
    status_map = {
        'active': 'active',
        'past_due': 'past_due',
        'canceled': 'cancelled',
        'unpaid': 'unpaid',
        'trialing': 'trialing',
    }
    
    subscription.status = status_map.get(new_status, new_status)
    subscription.current_period_start = datetime.fromtimestamp(
        subscription_data['current_period_start'], tz=timezone.utc
    )
    subscription.current_period_end = datetime.fromtimestamp(
        subscription_data['current_period_end'], tz=timezone.utc
    )
    subscription.save()
    
    log_webhook('webhook', f'Updated subscription {stripe_subscription_id} to {new_status}')


def handle_subscription_deleted(subscription_data):
    """
    Subscription cancelled.
    Stop the instance (but keep data for potential reactivation).
    """
    stripe_subscription_id = subscription_data['id']
    
    try:
        subscription = Subscription.objects.get(
            stripe_subscription_id=stripe_subscription_id
        )
    except Subscription.DoesNotExist:
        return
    
    subscription.status = 'cancelled'
    subscription.cancelled_at = timezone.now()
    subscription.save()
    
    # Stop the instance
    instance = subscription.customer.instance
    if instance and instance.status == 'running':
        try:
            manager = DockerManager()
            manager.stop_instance(instance)
            send_instance_stopped_email(instance, reason='subscription_cancelled')
        except Exception as e:
            log_webhook('error', f'Failed to stop instance: {e}')
    
    log_webhook('webhook', f'Cancelled subscription {stripe_subscription_id}')


def handle_payment_failed(invoice):
    """
    Payment failed.
    Mark subscription as past_due but don't immediately stop instance.
    """
    stripe_subscription_id = invoice.get('subscription')
    if not stripe_subscription_id:
        return
    
    try:
        subscription = Subscription.objects.get(
            stripe_subscription_id=stripe_subscription_id
        )
        subscription.status = 'past_due'
        subscription.save(update_fields=['status'])
        log_webhook('webhook', f'Payment failed for {stripe_subscription_id}')
    except Subscription.DoesNotExist:
        pass


def handle_invoice_paid(invoice):
    """
    Invoice paid successfully.
    If subscription was past_due, reactivate instance.
    """
    stripe_subscription_id = invoice.get('subscription')
    if not stripe_subscription_id:
        return
    
    try:
        subscription = Subscription.objects.get(
            stripe_subscription_id=stripe_subscription_id
        )
        
        if subscription.status == 'past_due':
            subscription.status = 'active'
            subscription.save(update_fields=['status'])
            
            # Restart instance if it was stopped
            instance = subscription.customer.instance
            if instance and instance.status == 'stopped':
                manager = DockerManager()
                manager.start_instance(instance)
            
            log_webhook('webhook', f'Reactivated subscription {stripe_subscription_id}')
    
    except Subscription.DoesNotExist:
        pass
