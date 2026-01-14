import socket
import subprocess
from django.conf import settings
from core.models import ProvisioningLog
from core.nginx_manager import NginxManager
from core.docker_manager import DockerManager


EXPECTED_IP = settings.SERVER_IP


class CustomDomainError(Exception):
    pass


def verify_dns(domain: str) -> bool:
    try:
        resolved = socket.gethostbyname(domain)
        return resolved == EXPECTED_IP
    except Exception:
        return False


def obtain_ssl_certificate(domain: str):
    cmd = [
        "/usr/bin/sudo",
        "certbot",
        "certonly",
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
    subprocess.check_call(cmd)


def update_container_allowed_hosts(instance):
    manager = DockerManager()
    manager.restart_instance(instance)


def setup_custom_domain(instance):
    domain = instance.custom_domain

    if not verify_dns(domain):
        raise CustomDomainError("DNS does not resolve to server IP")

    instance.custom_domain_verified = True
    instance.save(update_fields=["custom_domain_verified"])

    obtain_ssl_certificate(domain)

    instance.custom_domain_ssl = True
    instance.save(update_fields=["custom_domain_ssl"])

    nginx = NginxManager()
    nginx.provision_nginx(instance)

    update_container_allowed_hosts(instance)

    ProvisioningLog.objects.create(
        instance=instance,
        action="create",
        message=f"Custom domain {domain} provisioned successfully",
    )


def remove_custom_domain(instance):
    domain = instance.custom_domain

    nginx = NginxManager()
    nginx.provision_nginx(instance)

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

    update_container_allowed_hosts(instance)

    ProvisioningLog.objects.create(
        instance=instance,
        action="delete",
        message=f"Custom domain {domain} removed",
    )
