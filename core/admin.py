"""
Admin interface for eBuilder Provisioner

Provides a dashboard to:
- View all customers and their subscription status
- See all running instances
- Perform actions: start, stop, restart, delete containers
- View provisioning logs for debugging
"""

from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from .models import Customer, Subscription, Instance, ProvisioningLog
from django.contrib.admin import SimpleListFilter
from .email_service import send_welcome_email, send_portal_access_email
from core.services.custom_domain_service import setup_custom_domain, verify_dns


class SubscriptionInline(admin.TabularInline):
    model = Subscription
    extra = 0
    readonly_fields = [
        "stripe_subscription_id",
        "status",
        "current_period_start",
        "current_period_end",
    ]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class InstanceInline(admin.TabularInline):
    model = Instance
    extra = 0
    readonly_fields = ["subdomain", "status", "port", "container_id"]
    fields = ["subdomain", "status", "port", "site_name"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = [
        "email",
        "name",
        "subscription_status_badge",
        "instance_status_badge",
        "created_at",
    ]
    list_filter = ["subscriptions__status", "created_at"]
    search_fields = ["email", "name", "stripe_customer_id"]
    readonly_fields = ["stripe_customer_id", "created_at", "updated_at"]
    inlines = [SubscriptionInline, InstanceInline]

    fieldsets = (
        (None, {"fields": ("email", "name")}),
        ("Stripe", {"fields": ("stripe_customer_id",), "classes": ("collapse",)}),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    def subscription_status_badge(self, obj):
        sub = obj.active_subscription
        if sub:
            colors = {
                "active": "green",
                "trialing": "blue",
                "past_due": "orange",
                "cancelled": "red",
                "unpaid": "red",
            }
            color = colors.get(sub.status, "gray")
            return format_html(
                '<span style="background-color: {}; color: white; padding: 3px 8px; '
                'border-radius: 3px; font-size: 11px;">{}</span>',
                color,
                sub.status.upper(),
            )
        return format_html(
            '<span style="background-color: gray; color: white; padding: 3px 8px; '
            'border-radius: 3px; font-size: 11px;">NO SUB</span>'
        )

    subscription_status_badge.short_description = "Subscription"

    def instance_status_badge(self, obj):
        instance = obj.instance
        if instance:
            colors = {
                "running": "green",
                "pending": "blue",
                "creating": "blue",
                "stopped": "orange",
                "error": "red",
                "deleted": "gray",
            }
            color = colors.get(instance.status, "gray")
            return format_html(
                '<span style="background-color: {}; color: white; padding: 3px 8px; '
                'border-radius: 3px; font-size: 11px;">{}</span>',
                color,
                instance.status.upper(),
            )
        return format_html(
            '<span style="background-color: gray; color: white; padding: 3px 8px; '
            'border-radius: 3px; font-size: 11px;">NONE</span>'
        )

    instance_status_badge.short_description = "Instance"


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ["customer", "status_badge", "current_period_end", "created_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["customer__email", "stripe_subscription_id"]
    readonly_fields = [
        "stripe_subscription_id",
        "stripe_price_id",
        "current_period_start",
        "current_period_end",
        "cancelled_at",
        "created_at",
        "updated_at",
    ]

    def status_badge(self, obj):
        colors = {
            "active": "green",
            "trialing": "blue",
            "past_due": "orange",
            "cancelled": "red",
            "unpaid": "red",
        }
        color = colors.get(obj.status, "gray")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; '
            'border-radius: 3px; font-size: 11px;">{}</span>',
            color,
            obj.status.upper(),
        )

    status_badge.short_description = "Status"


class EmailStatusFilter(SimpleListFilter):
    title = "Email status"
    parameter_name = "email_status"

    def lookups(self, request, model_admin):
        return (
            ("welcome_sent", "Welcome email sent"),
            ("welcome_not_sent", "Welcome email NOT sent"),
            ("email_errors", "Has email errors"),
        )

    def queryset(self, request, queryset):
        if self.value() == "welcome_sent":
            return queryset.filter(welcome_email_sent=True)

        if self.value() == "welcome_not_sent":
            return queryset.filter(welcome_email_sent=False)

        if self.value() == "email_errors":
            error_instance_ids = ProvisioningLog.objects.filter(
                action="error",
                message__icontains="email",
                instance__isnull=False,
            ).values_list("instance_id", flat=True)

            return queryset.filter(id__in=error_instance_ids)

        return queryset


@admin.register(Instance)
class InstanceAdmin(admin.ModelAdmin):
    list_display = [
        "subdomain_link",
        "external_link",
        "customer",
        "status_badge",
        "port",
        "last_health_check",
        "created_at",
    ]
    list_filter = ["status", "created_at", EmailStatusFilter]
    search_fields = ["subdomain", "customer__email", "container_id"]
    readonly_fields = [
        "container_id",
        "container_name",
        "port",
        "secret_key",
        "created_at",
        "updated_at",
        "last_health_check",
        "full_url_link",
    ]
    actions = [
        "start_instances",
        "stop_instances",
        "restart_instances",
        "check_health",
        "resend_welcome_email",
        "resend_portal_access_email",
    ]

    fieldsets = (
        ("Customer", {"fields": ("customer",)}),
        ("Domain", {"fields": ("subdomain", "custom_domain", "full_url_link")}),
        ("Status", {"fields": ("status", "status_message", "last_health_check")}),
        (
            "Container",
            {
                "fields": ("container_id", "container_name", "port"),
                "classes": ("collapse",),
            },
        ),
        (
            "Configuration",
            {
                "fields": ("site_name", "admin_email", "admin_password", "secret_key"),
                "classes": ("collapse",),
            },
        ),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    def subdomain_link(self, obj):
        # Link to edit page, not the external site
        edit_url = reverse("admin:core_instance_change", args=[obj.pk])
        return format_html('<a href="{}">{}</a>', edit_url, obj.subdomain)

    subdomain_link.short_description = "Subdomain"

    def external_link(self, obj):
        return format_html('<a href="{}" target="_blank">🔗 Visit</a>', obj.full_url)

    external_link.short_description = "Site"

    def full_url_link(self, obj):
        return format_html(
            '<a href="{}" target="_blank">{}</a>', obj.full_url, obj.full_url
        )

    full_url_link.short_description = "URL"

    def status_badge(self, obj):
        colors = {
            "running": "green",
            "pending": "blue",
            "creating": "blue",
            "stopped": "orange",
            "error": "red",
            "deleted": "gray",
        }
        color = colors.get(obj.status, "gray")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; '
            'border-radius: 3px; font-size: 11px;">{}</span>',
            color,
            obj.status.upper(),
        )

    status_badge.short_description = "Status"

    @admin.action(description="📧 Resend welcome email")
    def resend_welcome_email(self, request, queryset):
        sent = 0
        failed = 0

        for instance in queryset:
            success = send_welcome_email(instance)
            if success:
                instance.welcome_email_sent = True
                instance.save(update_fields=["welcome_email_sent"])
                sent += 1
            else:
                failed += 1

        self.message_user(
            request,
            f"Welcome email resent: {sent} success, {failed} failed",
        )

        @admin.action(description="🔐 Resend portal access email (includes password)")
        def resend_portal_access_email(self, request, queryset):
            sent = 0
            failed = 0

            for instance in queryset:
                success = send_portal_access_email(instance)
                if success:
                    sent += 1
                else:
                    failed += 1

            self.message_user(
                request,
                f"Portal access email sent: {sent} success, {failed} failed",
            )

    # Admin Actions
    @admin.action(description="▶️ Start selected instances")
    def start_instances(self, request, queryset):
        from .docker_manager import DockerManager

        manager = DockerManager()
        started = 0
        for instance in queryset.filter(status__in=["stopped", "error"]):
            try:
                manager.start_instance(instance)
                started += 1
            except Exception as e:
                self.message_user(
                    request, f"Error starting {instance.subdomain}: {e}", level="error"
                )
        self.message_user(request, f"Started {started} instance(s)")

    @admin.action(description="⏹️ Stop selected instances")
    def stop_instances(self, request, queryset):
        from .docker_manager import DockerManager

        manager = DockerManager()
        stopped = 0
        for instance in queryset.filter(status="running"):
            try:
                manager.stop_instance(instance)
                stopped += 1
            except Exception as e:
                self.message_user(
                    request, f"Error stopping {instance.subdomain}: {e}", level="error"
                )
        self.message_user(request, f"Stopped {stopped} instance(s)")

    @admin.action(description="🔄 Restart selected instances")
    def restart_instances(self, request, queryset):
        from .docker_manager import DockerManager

        manager = DockerManager()
        restarted = 0
        for instance in queryset.filter(status="running"):
            try:
                manager.restart_instance(instance)
                restarted += 1
            except Exception as e:
                self.message_user(
                    request,
                    f"Error restarting {instance.subdomain}: {e}",
                    level="error",
                )
        self.message_user(request, f"Restarted {restarted} instance(s)")

    @admin.action(description="🏥 Check health of selected instances")
    def check_health(self, request, queryset):
        from .docker_manager import DockerManager

        manager = DockerManager()
        for instance in queryset.filter(status="running"):
            is_healthy = manager.health_check(instance)
            status = "healthy" if is_healthy else "unhealthy"
            self.message_user(request, f"{instance.subdomain}: {status}")


@admin.register(ProvisioningLog)
class ProvisioningLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "action_badge", "instance", "message_truncated"]
    list_filter = ["action", "created_at"]
    search_fields = ["message", "instance__subdomain"]
    readonly_fields = ["instance", "action", "message", "details", "created_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def action_badge(self, obj):
        colors = {
            "create": "green",
            "start": "green",
            "stop": "orange",
            "restart": "blue",
            "delete": "red",
            "health_check": "gray",
            "webhook": "purple",
            "error": "red",
        }
        color = colors.get(obj.action, "gray")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; '
            'border-radius: 3px; font-size: 11px;">{}</span>',
            color,
            obj.action.upper(),
        )

    action_badge.short_description = "Action"

    def message_truncated(self, obj):
        return obj.message[:100] + "..." if len(obj.message) > 100 else obj.message

    message_truncated.short_description = "Message"


@admin.action(description="Check DNS propagation")
def check_dns(self, request, queryset):
    for instance in queryset:
        ok = verify_dns(instance.custom_domain)
        self.message_user(
            request,
            f"{instance.custom_domain}: {'OK' if ok else 'NOT RESOLVED'}",
        )


@admin.action(description="Setup custom domain")
def setup_domain(self, request, queryset):
    for instance in queryset:
        try:
            setup_custom_domain(instance)
            self.message_user(request, f"{instance.custom_domain} provisioned")
        except Exception as e:
            self.message_user(request, str(e), level="error")
