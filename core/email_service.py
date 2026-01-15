"""
Email Service

Sends transactional emails:
- Welcome email with login details
- Instance stopped notifications
- Payment failure warnings
"""

from django.conf import settings
from django.core.mail import send_mail
from .models import ProvisioningLog

PORTAL_LOGIN_URL = "https://my.djangify.com/portal/login/"


def send_welcome_email(instance, portal_password=None):
    """
    Send welcome email with admin + portal login details.
    Called after instance is successfully provisioned.
    """

    subject = "Your Djangify eCommerce store is ready!"

    portal_email = (
        instance.customer.email
        if hasattr(instance, "customer")
        else instance.admin_email
    )

    portal_block = ""
    if portal_password:
        portal_block = f"""
CUSTOMER PORTAL ACCESS
----------------------
Portal URL: {PORTAL_LOGIN_URL}
Email: {portal_email}
Temporary Password: {portal_password}

IMPORTANT: Please change your portal password after logging in.
"""

    message = f"""
Welcome to eBuilder Managed Hosting!

Your store "{instance.site_name}" is now live at:
{instance.full_url}

ADMIN LOGIN DETAILS
-------------------
Admin URL: {instance.admin_url}
Email: {instance.admin_email}
Temporary Password: {instance.admin_password}

IMPORTANT: Please change your admin password after logging in!
{portal_block}
GETTING STARTED
---------------
1. Log in to your admin panel at {instance.admin_url} and change your password
2. Go to Settings > Site Identity to update your store details
3. Add your first product in Shop > Products
4. Customise your homepage in Pages

Need help? Reply to this email or visit our documentation.

Welcome aboard!
Djangify eCommerce Builder
https://www.djangify.com
djangify@djangify.com
"""

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[instance.admin_email],
            fail_silently=False,
        )
        return True

    except Exception as e:
        ProvisioningLog.objects.create(
            instance=instance,
            action="error",
            message="Failed to send welcome email",
            details={
                "email": instance.admin_email,
                "error": str(e),
                "type": "welcome",
            },
        )
        return False


def send_portal_access_email(instance):
    """
    Resend portal access details INCLUDING existing portal password.
    Does NOT generate or reset passwords.
    """

    customer = instance.customer

    if not customer.portal_password:
        # Safety guard: we cannot send what doesn't exist
        ProvisioningLog.objects.create(
            instance=instance,
            action="error",
            message="Portal access email requested but no portal password exists",
        )
        return False

    subject = "Your Djangify customer portal access"

    portal_email = customer.email

    message = f"""
CUSTOMER PORTAL ACCESS
----------------------
Portal URL: {PORTAL_LOGIN_URL}
Email: {portal_email}
Temporary Password: {customer.portal_password}

IMPORTANT:
Please log in and change your portal password as soon as possible.

If you did not request this email, please contact support.

Djangify eCommerce Builder
https://www.djangify.com
djangify@djangify.com
"""

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[portal_email],
            fail_silently=False,
        )
        return True

    except Exception as e:
        ProvisioningLog.objects.create(
            instance=instance,
            action="error",
            message="Failed to send portal access email",
            details={"error": str(e)},
        )
        return False


def send_instance_stopped_email(instance, reason="subscription_cancelled"):
    """
    Notify customer their instance has been stopped.
    """
    reasons = {
        "subscription_cancelled": "Your subscription has been cancelled.",
        "payment_failed": "We were unable to process your payment.",
        "manual": "Your instance has been stopped by an administrator.",
    }

    reason_text = reasons.get(reason, reason)

    subject = "Your eBuilder store has been paused"

    message = f"""
Hi,

Your eBuilder store "{instance.site_name}" at {instance.full_url} has been paused.

Reason: {reason_text}

Your data is safe and will be kept for 30 days. To reactivate your store:
1. Update your payment method at [billing portal link]
2. Or contact us for assistance

If you have any questions, please reply to this email.

The eBuilder Team
"""

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[instance.admin_email],
            fail_silently=False,
        )
        return True
    except Exception as e:
        ProvisioningLog.objects.create(
            instance=instance,
            action="error",
            message="Failed to send instance stopped email",
            details={
                "email": instance.admin_email,
                "reason": reason,
                "error": str(e),
                "type": "instance_stopped",
            },
        )
        return False


def send_payment_warning_email(instance):
    """
    Warn customer about failed payment.
    """
    subject = "Action required: Payment failed for your eBuilder store"

    message = f"""
Hi,

We were unable to process payment for your eBuilder store "{instance.site_name}".

Your store is still running, but will be paused if payment is not received within 7 days.

Please update your payment method to avoid interruption:
[billing portal link]

If you have any questions, please reply to this email.

The eBuilder Team
"""

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[instance.admin_email],
            fail_silently=False,
        )
        return True
    except Exception as e:
        ProvisioningLog.objects.create(
            instance=instance,
            action="error",
            message="Failed to send instance stopped email",
            details={
                "email": instance.admin_email,
                "error": str(e),
                "type": "payment_warning",
            },
        )
        return False


def send_admin_notification(instance):
    """Notify admin when a new store is provisioned"""
    admin_email = settings.DEFAULT_FROM_EMAIL  # Or a specific admin email

    subject = f"New store provisioned: {instance.subdomain}"
    message = f"""

    A new eBuilder store has been provisioned:

    Store: {instance.site_name}
    Subdomain: {instance.subdomain}
    Customer Email: {instance.admin_email}
    URL: https://{instance.subdomain}.djangify.com

    Provisioned at: {instance.created_at}
    """

    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [admin_email],  # Add your admin email here
        fail_silently=True,
    )
