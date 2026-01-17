import socket
import subprocess
from django.conf import settings
from core.models import ProvisioningLog
from core.nginx_manager import NginxManager
from core.docker_manager import DockerManager


EXPECTED_IP = settings.SERVER_IP


class CustomDomainError(Exception):
    pass


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


def obtain_ssl_certificate(domain: str) -> bool:
    """Run certbot to obtain SSL certificate. Non-fatal on failure."""
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
    Full custom domain provisioning:
    1. Verify DNS
    2. Write nginx config (HTTP)
    3. Run certbot
    4. Update nginx config (HTTPS)
    5. Restart container
    """
    domain = instance.custom_domain

    # ---- DNS CHECK ----
    if not verify_dns(domain):
        ProvisioningLog.objects.create(
            instance=instance,
            action="error",
            message=f"DNS not yet pointing to server for {domain}",
            details={"expected_ip": EXPECTED_IP},
        )
        raise CustomDomainError("DNS does not resolve to server IP")

    instance.custom_domain_verified = True
    instance.save(update_fields=["custom_domain_verified"])

    ProvisioningLog.objects.create(
        instance=instance,
        action="webhook",
        message=f"DNS verified for {domain}",
    )

    nginx = NginxManager()

    # ---- NGINX (HTTP FIRST) ----
    nginx.provision_nginx(instance)

    ProvisioningLog.objects.create(
        instance=instance,
        action="create",
        message=f"Nginx config written for {domain} (HTTP)",
    )

    # ---- SSL ATTEMPT (NON-FATAL) ----
    ssl_ok = obtain_ssl_certificate(domain)

    if not ssl_ok:
        ProvisioningLog.objects.create(
            instance=instance,
            action="error",
            message=f"SSL issuance failed for {domain} (site works over HTTP, retry SSL later)",
        )
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

    # ---- CONTAINER RESTART ----
    update_container_allowed_hosts(instance)

    ProvisioningLog.objects.create(
        instance=instance,
        action="create",
        message=f"Custom domain {domain} is live with SSL",
    )


def remove_custom_domain(instance):
    """
    Remove custom domain from instance.
    Subdomain continues to work.
    """
    domain = instance.custom_domain

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

    nginx = NginxManager()
    nginx.provision_nginx(instance)

    ProvisioningLog.objects.create(
        instance=instance,
        action="delete",
        message=f"Custom domain {domain} removed; subdomain remains active",
    )

    update_container_allowed_hosts(instance)
