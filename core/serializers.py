"""
API Serializers for eBuilder Provisioner
"""

from rest_framework import serializers
from .models import Customer, Subscription, Instance, ProvisioningLog


class SubscriptionSerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(read_only=True)
    
    class Meta:
        model = Subscription
        fields = [
            'id', 'stripe_subscription_id', 'status', 'is_active',
            'current_period_start', 'current_period_end', 
            'cancelled_at', 'created_at'
        ]
        read_only_fields = fields


class InstanceSerializer(serializers.ModelSerializer):
    full_url = serializers.CharField(read_only=True)
    admin_url = serializers.CharField(read_only=True)
    
    class Meta:
        model = Instance
        fields = [
            'id', 'subdomain', 'custom_domain', 'status', 'status_message',
            'site_name', 'full_url', 'admin_url', 'port',
            'last_health_check', 'created_at'
        ]
        read_only_fields = [
            'id', 'status', 'status_message', 'full_url', 'admin_url',
            'port', 'last_health_check', 'created_at'
        ]


class CustomerSerializer(serializers.ModelSerializer):
    active_subscription = SubscriptionSerializer(read_only=True)
    instance = InstanceSerializer(read_only=True)
    
    class Meta:
        model = Customer
        fields = [
            'id', 'email', 'name', 'active_subscription', 
            'instance', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class ProvisioningLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProvisioningLog
        fields = ['id', 'action', 'message', 'details', 'created_at']
        read_only_fields = fields


class SubdomainCheckSerializer(serializers.Serializer):
    """For checking if a subdomain is available"""
    subdomain = serializers.CharField(max_length=63)
    
    def validate_subdomain(self, value):
        import re
        value = value.lower().strip()
        
        # Must be valid subdomain format
        if not re.match(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$', value):
            raise serializers.ValidationError(
                "Subdomain must start and end with a letter or number, "
                "and can only contain letters, numbers, and hyphens."
            )
        
        # Reserved subdomains
        reserved = ['www', 'api', 'admin', 'mail', 'ftp', 'test', 'staging', 'app']
        if value in reserved:
            raise serializers.ValidationError(
                f"'{value}' is a reserved subdomain."
            )
        
        return value


class CreateCheckoutSerializer(serializers.Serializer):
    """For creating a Stripe checkout session"""
    subdomain = serializers.CharField(max_length=63)
    site_name = serializers.CharField(max_length=255)
    email = serializers.EmailField()
    
    def validate_subdomain(self, value):
        # Reuse validation from SubdomainCheckSerializer
        serializer = SubdomainCheckSerializer(data={'subdomain': value})
        serializer.is_valid(raise_exception=True)
        
        # Check if already taken
        if Instance.objects.filter(subdomain=value).exclude(status='deleted').exists():
            raise serializers.ValidationError("This subdomain is already taken.")
        
        return value.lower().strip()
