"""
Core models for eBuilder Provisioner

Customer - The person paying for hosting
Subscription - Their Stripe subscription details
Instance - Their running eBuilder container
"""

import secrets
import string
from django.db import models
from django.conf import settings
from django.utils import timezone
from django.contrib.auth.hashers import make_password, check_password


def generate_temp_password(length=12):
    """Generate a secure temporary password"""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_secret_key():
    """Generate a Django secret key"""
    return secrets.token_urlsafe(50)


class Customer(models.Model):
    """
    A customer who has signed up for managed hosting.
    Created when they complete Stripe checkout.
    """

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=255, blank=True)

    # Stripe identifiers
    stripe_customer_id = models.CharField(max_length=255, unique=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # password for portal my.djangify.com
    portal_password = models.CharField(
        max_length=128,
        blank=True,
        help_text="Hashed password for customer portal login",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.email}"

    @property
    def active_subscription(self):
        return self.subscriptions.filter(status="active").first()

    @property
    def instance(self):
        return self.instances.first()

    def set_portal_password(self, raw_password: str):
        """
        Hash and store the portal password.
        """
        self.portal_password = make_password(raw_password)
        self.save(update_fields=["portal_password"])

    def check_portal_password(self, raw_password: str) -> bool:
        """
        Verify a portal password.
        """
        if not self.portal_password:
            return False
        return check_password(raw_password, self.portal_password)


class Subscription(models.Model):
    """
    Tracks Stripe subscription status.
    One customer can have subscription history (cancelled, resubscribed, etc.)
    """

    STATUS_CHOICES = [
        ("active", "Active"),
        ("past_due", "Past Due"),
        ("cancelled", "Cancelled"),
        ("unpaid", "Unpaid"),
        ("trialing", "Trialing"),
    ]

    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="subscriptions"
    )

    # Stripe identifiers
    stripe_subscription_id = models.CharField(max_length=255, unique=True)
    stripe_price_id = models.CharField(max_length=255)

    # Status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")

    # Dates
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.customer.email} - {self.status}"

    @property
    def is_active(self):
        return self.status in ["active", "trialing"]


class Instance(models.Model):
    """
    A running eBuilder Docker container for a customer.
    """

    STATUS_CHOICES = [
        ("pending", "Pending"),  # Payment received, not yet created
        ("creating", "Creating"),  # Docker container being created
        ("running", "Running"),  # Container running
        ("stopped", "Stopped"),  # Container stopped (non-payment, manual)
        ("error", "Error"),  # Something went wrong
        ("deleted", "Deleted"),  # Container removed
    ]

    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="instances"
    )

    # Instance identification
    subdomain = models.CharField(
        max_length=63,
        unique=True,
        help_text="Subdomain for this instance (e.g., 'janes-shop' for janes-shop.ebuilder.host)",
    )
    custom_domain = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional custom domain (e.g., 'shop.janesdomain.com')",
    )

    # Container details
    container_id = models.CharField(max_length=64, blank=True)
    container_name = models.CharField(max_length=255, blank=True)
    port = models.IntegerField(unique=True, null=True, blank=True)

    # Status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    status_message = models.TextField(
        blank=True, help_text="Error details or status info"
    )

    # Instance configuration (stored for recreation if needed)
    site_name = models.CharField(max_length=255, default="My Shop")
    admin_email = models.EmailField()
    admin_password = models.CharField(
        max_length=255,
        blank=True,
        help_text="Temporary password (should be changed by user)",
    )
    secret_key = models.CharField(max_length=255, blank=True)
    welcome_email_sent = models.BooleanField(default=False)

    # Custom Domains
    custom_domain = models.CharField(max_length=255, blank=True)
    custom_domain_verified = models.BooleanField(default=False)
    custom_domain_ssl = models.BooleanField(default=False)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_health_check = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Instance"
        verbose_name_plural = "Instances"

    def __str__(self):
        return f"{self.subdomain}.{settings.BASE_DOMAIN} ({self.status})"

    @property
    def full_url(self):
        return f"https://{self.subdomain}.{settings.BASE_DOMAIN}"

    @property
    def admin_url(self):
        return f"{self.full_url}/admin/"

    @property
    def data_directory(self):
        """Where this instance's data is stored on host"""
        return f"{settings.CUSTOMER_DATA_ROOT}/{self.id}"

    def save(self, *args, **kwargs):
        # Generate secrets on first save
        if not self.secret_key:
            self.secret_key = generate_secret_key()
        if not self.admin_password:
            self.admin_password = generate_temp_password()
        if not self.admin_email:
            self.admin_email = self.customer.email
        super().save(*args, **kwargs)

    def allocate_port(self):
        """Find the next available port"""
        if self.port:
            return self.port

        used_ports = set(
            Instance.objects.exclude(id=self.id)
            .exclude(status="deleted")
            .values_list("port", flat=True)
        )

        for port in range(settings.PORT_RANGE_START, settings.PORT_RANGE_END + 1):
            if port not in used_ports:
                self.port = port
                self.save(update_fields=["port"])
                return port

        raise Exception("No available ports in range")


class ProvisioningLog(models.Model):
    """
    Audit log for provisioning actions.
    Useful for debugging and support.
    """

    ACTION_CHOICES = [
        ("create", "Create Instance"),
        ("start", "Start Instance"),
        ("stop", "Stop Instance"),
        ("restart", "Restart Instance"),
        ("delete", "Delete Instance"),
        ("health_check", "Health Check"),
        ("webhook", "Stripe Webhook"),
        ("error", "Error"),
    ]

    instance = models.ForeignKey(
        Instance, on_delete=models.CASCADE, related_name="logs", null=True, blank=True
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    message = models.TextField()
    details = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Provisioning Log"
        verbose_name_plural = "Provisioning Logs"

    def __str__(self):
        instance_str = self.instance.subdomain if self.instance else "System"
        return f"[{self.action}] {instance_str}: {self.message[:50]}"
