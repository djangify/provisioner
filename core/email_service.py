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


def send_welcome_email(instance):
    """
    Send welcome email with login details.
    Called after instance is successfully provisioned.
    """
    subject = "Your eBuilder store is ready! 🎉"

    context = {
        "site_name": instance.site_name,
        "subdomain": instance.subdomain,
        "full_url": instance.full_url,
        "admin_url": instance.admin_url,
        "admin_email": instance.admin_email,
        "admin_password": instance.admin_password,
        "base_domain": settings.BASE_DOMAIN,
    }

    # Plain text version
    message = f"""
Welcome to eBuilder Managed Hosting!

Your store "{instance.site_name}" is now live at:
{instance.full_url}

ADMIN LOGIN DETAILS
-------------------
Admin URL: {instance.admin_url}
Email: {instance.admin_email}
Temporary Password: {instance.admin_password}

IMPORTANT: Please change your password after logging in!

GETTING STARTED
---------------
1. Log in to your admin panel at {instance.admin_url}
2. Go to Settings > Site Identity to update your store details
3. Add your first product in Shop > Products
4. Customise your homepage in Pages

Need help? Reply to this email or visit our documentation.

Welcome aboard!
The eBuilder Team
"""

    # HTML version (optional, could use a template)
    html_message = None

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[instance.admin_email],
            html_message=html_message,
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
