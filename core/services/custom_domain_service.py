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


# verify both root + www and compare against expected IP
def verify_dns(domain: str) -> bool:
    try:
        root_ip = socket.gethostbyname(domain)
        www_ip = socket.gethostbyname(f"www.{domain}")
        return root_ip == EXPECTED_IP and www_ip == EXPECTED_IP
    except Exception:
        return False


# =========================
# SSL CERTIFICATE
# =========================


#  certbot failure is NON-FATAL
def obtain_ssl_certificate(domain: str) -> bool:
    cmd = [
        "certbot",
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
# NGINX RELOAD (SAFE)
# =========================


# NEW: nginx config test + reload
def reload_nginx():
    subprocess.check_call(["sudo", "/usr/sbin/nginx", "-t"])
    subprocess.check_call(["sudo", "/usr/bin/systemctl", "reload", "nginx"])


# =========================
# CONTAINER
# =========================


def update_container_allowed_hosts(instance):
    manager = DockerManager()
    manager.restart_instance(instance)


# =========================
# ORCHESTRATION
# =========================


def setup_custom_domain(instance):
    domain = instance.custom_domain

    # ---- DNS CHECK ----
    if not verify_dns(domain):
        ProvisioningLog.objects.create(
            instance=instance,
            action="dns_check",
            message=f"DNS not yet pointing to server for {domain}",
        )
        raise CustomDomainError("DNS does not resolve to server IP")

    instance.custom_domain_verified = True
    instance.save(update_fields=["custom_domain_verified"])

    ProvisioningLog.objects.create(
        instance=instance,
        action="dns_check",
        message=f"DNS verified for {domain}",
    )

    nginx = NginxManager()

    # ---- NGINX (HTTP FIRST) ----
    nginx.provision_nginx(instance)

    try:
        reload_nginx()
        ProvisioningLog.objects.create(
            instance=instance,
            action="nginx_reload",
            message=f"Nginx reloaded for {domain} (HTTP)",
        )
    except Exception as e:
        ProvisioningLog.objects.create(
            instance=instance,
            action="custom_domain_error",
            message=f"Nginx reload failed for {domain}: {e}",
        )
        raise

    # ---- SSL ATTEMPT (NON-FATAL) ----
    ssl_ok = obtain_ssl_certificate(domain)

    if not ssl_ok:
        ProvisioningLog.objects.create(
            instance=instance,
            action="ssl_issue",
            message=f"SSL issuance failed for {domain} (retry later)",
        )
        return  # IMPORTANT: DO NOT ROLLBACK ANYTHING

    instance.custom_domain_ssl = True
    instance.save(update_fields=["custom_domain_ssl"])

    # ---- NGINX (HTTPS ENABLED) ----
    nginx.provision_nginx(instance)

    try:
        reload_nginx()
        ProvisioningLog.objects.create(
            instance=instance,
            action="nginx_reload",
            message=f"Nginx reloaded for {domain} (HTTPS)",
        )
    except Exception as e:
        ProvisioningLog.objects.create(
            instance=instance,
            action="custom_domain_error",
            message=f"Nginx reload failed after SSL for {domain}: {e}",
        )
        return  # Still do not rollback

    # ---- CONTAINER RESTART ----
    update_container_allowed_hosts(instance)

    ProvisioningLog.objects.create(
        instance=instance,
        action="custom_domain_ready",
        message=f"Custom domain {domain} is live with SSL",
    )


def remove_custom_domain(instance):
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

    try:
        reload_nginx()
    except Exception as e:
        ProvisioningLog.objects.create(
            instance=instance,
            action="custom_domain_error",
            message=f"Nginx reload failed during removal of {domain}: {e}",
        )
        return

    update_container_allowed_hosts(instance)

    ProvisioningLog.objects.create(
        instance=instance,
        action="custom_domain_ready",
        message=f"Custom domain {domain} removed; subdomain remains active",
    )
