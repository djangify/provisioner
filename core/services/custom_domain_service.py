"""
Custom Domain Service - Production Safe

Handles custom domain provisioning with:
- Preflight checks (nginx conflicts, ownership)
- DNS verification
- SSL certificate issuance
- Idempotent operations (safe to call multiple times)
- Proper cleanup on removal
"""

import os
import glob
import socket
import subprocess
from django.conf import settings
from core.models import Instance, ProvisioningLog
from core.nginx_manager import NginxManager
from core.docker_manager import DockerManager


EXPECTED_IP = settings.SERVER_IP
NGINX_CONFIG_DIR = settings.NGINX_CONFIG_DIR


class CustomDomainError(Exception):
    """Raised when custom domain setup fails"""

    pass


# =========================
# PREFLIGHT CHECKS
# =========================


def check_domain_in_nginx(domain: str, exclude_instance=None) -> dict | None:
    """
    Check if domain already exists in any nginx config.

    Returns dict with details if found, None if clear.
    Excludes the current instance's config file.
    """
    search_patterns = [
        os.path.join(NGINX_CONFIG_DIR, "*.conf"),
        "/etc/nginx/sites-enabled/*",
        "/etc/nginx/conf.d/*.conf",
    ]

    exclude_file = None
    if exclude_instance:
        exclude_file = f"ebuilder-{exclude_instance.subdomain}.conf"

    for pattern in search_patterns:
        for config_file in glob.glob(pattern):
            # Skip the instance's own config
            if exclude_file and config_file.endswith(exclude_file):
                continue

            try:
                with open(config_file, "r") as f:
                    content = f.read()
                    # Check for domain in server_name directives
                    if "server_name" in content and domain in content:
                        return {
                            "file": config_file,
                            "domain": domain,
                        }
            except (IOError, PermissionError):
                continue

    return None


def check_domain_ownership(domain: str, exclude_instance=None) -> Instance | None:
    """
    Check if domain is claimed by another instance in the database.

    Returns the Instance if found, None if clear.
    """
    query = Instance.objects.filter(custom_domain=domain).exclude(status="deleted")

    if exclude_instance:
        query = query.exclude(id=exclude_instance.id)

    return query.first()


def preflight_domain_check(domain: str, instance) -> None:
    """
    Run all preflight checks before domain setup.
    Raises CustomDomainError if any check fails.
    """
    # Check 1: Domain not in nginx configs (excluding this instance)
    nginx_conflict = check_domain_in_nginx(domain, exclude_instance=instance)
    if nginx_conflict:
        ProvisioningLog.objects.create(
            instance=instance,
            action="error",
            message=f"Domain {domain} already exists in nginx config",
            details=nginx_conflict,
        )
        raise CustomDomainError(
            f"Domain {domain} is already configured in nginx ({nginx_conflict['file']}). "
            "Remove the existing config first."
        )

    # Check 2: Domain not claimed by another instance
    other_instance = check_domain_ownership(domain, exclude_instance=instance)
    if other_instance:
        ProvisioningLog.objects.create(
            instance=instance,
            action="error",
            message=f"Domain {domain} claimed by another instance",
            details={
                "domain": domain,
                "other_instance_id": other_instance.id,
                "other_subdomain": other_instance.subdomain,
            },
        )
        raise CustomDomainError(
            f"Domain {domain} is already assigned to {other_instance.subdomain}.{settings.BASE_DOMAIN}"
        )

    ProvisioningLog.objects.create(
        instance=instance,
        action="webhook",
        message=f"Preflight checks passed for {domain}",
    )


# =========================
# DNS VERIFICATION
# =========================


def verify_dns(domain: str) -> bool:
    """Verify both root and www resolve to expected IP."""
    try:
        root_ip = socket.gethostbyname(domain)
        www_ip = socket.gethostbyname(f"www.{domain}")
        return root_ip == EXPECTED_IP and www_ip == EXPECTED_IP
    except Exception:
        return False


# =========================
# SSL CERTIFICATE
# =========================


def check_ssl_certificate_exists(domain: str) -> bool:
    """Check if SSL certificate already exists for domain."""
    cert_path = f"/etc/letsencrypt/live/{domain}/fullchain.pem"
    return os.path.exists(cert_path)


def obtain_ssl_certificate(domain: str) -> bool:
    """
    Run certbot to obtain SSL certificate.
    Non-fatal on failure - site continues to work over HTTP.
    """
    cmd = [
        "/usr/bin/sudo",
        "/usr/bin/certbot",
        "--nginx",
        "-d",
        domain,
        "-d",
        f"www.{domain}",
        "--non-interactive",
        "--agree-tos",
        "--email",
        "noreply@ebuilder.host",
    ]

    try:
        subprocess.check_call(cmd)
        return True
    except subprocess.CalledProcessError:
        return False


def delete_ssl_certificate(domain: str) -> bool:
    """
    Delete SSL certificate for domain.
    Non-fatal on failure.
    """
    cmd = [
        "/usr/bin/sudo",
        "/usr/bin/certbot",
        "delete",
        "--cert-name",
        domain,
        "--non-interactive",
    ]

    try:
        subprocess.check_call(cmd)
        return True
    except subprocess.CalledProcessError:
        return False


# =========================
# NGINX RELOAD (SAFE)
# =========================


def reload_nginx():
    """Test and reload nginx configuration."""
    subprocess.check_call(["/usr/bin/sudo", "/usr/sbin/nginx", "-t"])
    subprocess.check_call(["/usr/bin/sudo", "/usr/bin/systemctl", "reload", "nginx"])


# =========================
# CONTAINER
# =========================


def update_container_allowed_hosts(instance):
    """Restart container to pick up new ALLOWED_HOSTS."""
    manager = DockerManager()
    manager.restart_instance(instance)


# =========================
# ORCHESTRATION
# =========================


def setup_custom_domain(instance):
    """
    Full custom domain provisioning (IDEMPOTENT).

    Safe to call multiple times - checks current state before each step.

    Steps:
    1. Preflight checks (nginx conflicts, ownership)
    2. Verify DNS
    3. Write nginx config (HTTP)
    4. Run certbot
    5. Update nginx config (HTTPS)
    6. Restart container
    """
    domain = instance.custom_domain

    if not domain or not domain.strip():
        raise CustomDomainError("No custom domain set")

    domain = domain.strip().lower()

    # ---- IDEMPOTENCY: Already fully set up? ----
    if (
        instance.custom_domain_verified
        and instance.custom_domain_ssl
        and check_ssl_certificate_exists(domain)
    ):
        ProvisioningLog.objects.create(
            instance=instance,
            action="webhook",
            message=f"Domain {domain} already fully configured (idempotent skip)",
        )
        return  # Nothing to do

    # ---- PREFLIGHT CHECKS ----
    preflight_domain_check(domain, instance)

    # ---- DNS CHECK ----
    if not instance.custom_domain_verified:
        if not verify_dns(domain):
            ProvisioningLog.objects.create(
                instance=instance,
                action="error",
                message=f"DNS not yet pointing to server for {domain}",
                details={"expected_ip": EXPECTED_IP},
            )
            raise CustomDomainError(
                f"DNS does not resolve to server IP. "
                f"Add A records for {domain} and www.{domain} pointing to {EXPECTED_IP}"
            )

        instance.custom_domain_verified = True
        instance.save(update_fields=["custom_domain_verified"])

        ProvisioningLog.objects.create(
            instance=instance,
            action="webhook",
            message=f"DNS verified for {domain}",
        )
    else:
        ProvisioningLog.objects.create(
            instance=instance,
            action="webhook",
            message=f"DNS already verified for {domain} (idempotent skip)",
        )

    nginx = NginxManager()

    # ---- NGINX (HTTP FIRST) ----
    nginx.provision_nginx(instance)

    ProvisioningLog.objects.create(
        instance=instance,
        action="create",
        message=f"Nginx config written for {domain}",
    )

    # ---- SSL ATTEMPT (NON-FATAL) ----
    if not instance.custom_domain_ssl:
        # Check if cert already exists (e.g., from previous partial run)
        if check_ssl_certificate_exists(domain):
            ProvisioningLog.objects.create(
                instance=instance,
                action="webhook",
                message=f"SSL certificate already exists for {domain}",
            )
            ssl_ok = True
        else:
            ssl_ok = obtain_ssl_certificate(domain)

        if not ssl_ok:
            ProvisioningLog.objects.create(
                instance=instance,
                action="error",
                message=f"SSL issuance failed for {domain} (site works over HTTP, retry SSL later)",
            )
            # Update container with HTTP-only config
            update_container_allowed_hosts(instance)
            return  # DO NOT ROLLBACK - subdomain stays live

        instance.custom_domain_ssl = True
        instance.save(update_fields=["custom_domain_ssl"])

        # ---- NGINX (HTTPS ENABLED) ----
        nginx.provision_nginx(instance)

        ProvisioningLog.objects.create(
            instance=instance,
            action="create",
            message=f"Nginx config updated for {domain} (HTTPS)",
        )
    else:
        ProvisioningLog.objects.create(
            instance=instance,
            action="webhook",
            message=f"SSL already configured for {domain} (idempotent skip)",
        )

    # ---- CONTAINER RESTART ----
    update_container_allowed_hosts(instance)

    ProvisioningLog.objects.create(
        instance=instance,
        action="create",
        message=f"Custom domain {domain} is live with SSL",
    )


def remove_custom_domain(instance, delete_certificate=False):
    """
    Remove custom domain from instance.

    Subdomain continues to work.

    Args:
        instance: The Instance to remove domain from
        delete_certificate: If True, also delete the SSL certificate from certbot
    """
    domain = instance.custom_domain

    if not domain:
        ProvisioningLog.objects.create(
            instance=instance,
            action="webhook",
            message="No custom domain to remove",
        )
        return

    had_ssl = instance.custom_domain_ssl

    # ---- CLEAR FLAGS FIRST ----
    instance.custom_domain = ""
    instance.custom_domain_verified = False
    instance.custom_domain_ssl = False
    instance.save(
        update_fields=[
            "custom_domain",
            "custom_domain_verified",
            "custom_domain_ssl",
        ]
    )

    # ---- REGENERATE NGINX (subdomain only) ----
    nginx = NginxManager()
    try:
        nginx.provision_nginx(instance)
        ProvisioningLog.objects.create(
            instance=instance,
            action="create",
            message=f"Nginx config regenerated (subdomain only)",
        )
    except Exception as e:
        ProvisioningLog.objects.create(
            instance=instance,
            action="error",
            message=f"Nginx regeneration failed during removal of {domain}: {e}",
        )
        # Continue anyway - domain flags are already cleared

    # ---- OPTIONAL: DELETE SSL CERTIFICATE ----
    if delete_certificate and had_ssl:
        cert_deleted = delete_ssl_certificate(domain)
        if cert_deleted:
            ProvisioningLog.objects.create(
                instance=instance,
                action="delete",
                message=f"SSL certificate deleted for {domain}",
            )
        else:
            ProvisioningLog.objects.create(
                instance=instance,
                action="error",
                message=f"Failed to delete SSL certificate for {domain} (may need manual cleanup)",
            )

    # ---- CONTAINER RESTART ----
    try:
        update_container_allowed_hosts(instance)
    except Exception as e:
        ProvisioningLog.objects.create(
            instance=instance,
            action="error",
            message=f"Container restart failed during removal: {e}",
        )

    ProvisioningLog.objects.create(
        instance=instance,
        action="delete",
        message=f"Custom domain {domain} removed; subdomain remains active",
    )


def retry_ssl(instance):
    """
    Retry SSL certificate issuance for a domain that failed previously.

    Use this when:
    - Domain is verified but SSL failed
    - Rate limits have cleared
    """
    domain = instance.custom_domain

    if not domain:
        raise CustomDomainError("No custom domain set")

    if not instance.custom_domain_verified:
        raise CustomDomainError("Domain not verified yet - run full setup first")

    if instance.custom_domain_ssl and check_ssl_certificate_exists(domain):
        ProvisioningLog.objects.create(
            instance=instance,
            action="webhook",
            message=f"SSL already configured for {domain}",
        )
        return

    ssl_ok = obtain_ssl_certificate(domain)

    if not ssl_ok:
        ProvisioningLog.objects.create(
            instance=instance,
            action="error",
            message=f"SSL retry failed for {domain}",
        )
        raise CustomDomainError("SSL certificate issuance failed")

    instance.custom_domain_ssl = True
    instance.save(update_fields=["custom_domain_ssl"])

    # Regenerate nginx with HTTPS
    nginx = NginxManager()
    nginx.provision_nginx(instance)

    # Restart container
    update_container_allowed_hosts(instance)

    ProvisioningLog.objects.create(
        instance=instance,
        action="create",
        message=f"SSL retry successful for {domain}",
    )
