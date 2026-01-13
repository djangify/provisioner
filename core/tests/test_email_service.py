from unittest.mock import patch

from core.email_service import send_welcome_email
from core.models import ProvisioningLog, Customer, Instance


def test_send_welcome_email_logs_failure():
    customer = Customer.objects.create(
        email="test@example.com",
        stripe_customer_id="cus_test123",
    )

    instance = Instance.objects.create(
        customer=customer,
        subdomain="test-shop",
        site_name="Test Shop",
        admin_email="test@example.com",
    )

    # Force send_mail to raise an exception (simulate SMTP failure)
    with patch("core.email_service.send_mail") as mocked_send:
        mocked_send.side_effect = Exception("SMTP server unreachable")

        result = send_welcome_email(instance)

    assert result is False

    log = ProvisioningLog.objects.latest("created_at")

    assert log.instance == instance
    assert log.action == "error"
    assert "welcome email" in log.message.lower()
    assert "SMTP server unreachable" in log.details["error"]
