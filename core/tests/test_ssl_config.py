# from unittest.mock import patch
# from django.test import TestCase
# from core.models import Customer, Instance
# from core.nginx_manager import NginxManager


# class TestSSLConfig(TestCase):
#     @patch.object(NginxManager, "write_config")
#     def test_ssl_config_written_on_instance_create(self, mock_write):
#         customer = Customer.objects.create(
#             email="test@example.com",
#             stripe_customer_id="cus_test123",
#         )

#         instance = Instance.objects.create(
#             customer=customer,
#             subdomain="testssl",
#             site_name="SSL Test",
#             admin_email="test@example.com",
#         )

#         manager = NginxManager()
#         manager.create_site(instance)

#         mock_write.assert_called_once()
