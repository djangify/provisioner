"""
API Views for eBuilder Provisioner

Public endpoints (no auth):
- POST /api/webhook/stripe/ - Stripe webhook
- POST /api/check-subdomain/ - Check if subdomain is available
- POST /api/create-checkout/ - Create Stripe checkout session

Authenticated endpoints (for admin dashboard):
- GET /api/instances/ - List all instances
- GET /api/instances/<id>/ - Instance details
- POST /api/instances/<id>/start/ - Start instance
- POST /api/instances/<id>/stop/ - Stop instance
- POST /api/instances/<id>/restart/ - Restart instance
"""

import stripe
from django.conf import settings
from rest_framework import status, viewsets
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response

from .models import Customer, Subscription, Instance, ProvisioningLog
from .serializers import (
    CustomerSerializer, InstanceSerializer, ProvisioningLogSerializer,
    SubdomainCheckSerializer, CreateCheckoutSerializer
)
from .docker_manager import DockerManager

stripe.api_key = settings.STRIPE_SECRET_KEY


# =============================================================================
# PUBLIC ENDPOINTS
# =============================================================================

@api_view(['POST'])
@permission_classes([AllowAny])
def check_subdomain(request):
    """
    Check if a subdomain is available.
    
    POST /api/check-subdomain/
    {
        "subdomain": "janes-shop"
    }
    
    Returns:
    {
        "available": true,
        "subdomain": "janes-shop"
    }
    """
    serializer = SubdomainCheckSerializer(data=request.data)
    
    if not serializer.is_valid():
        return Response({
            'available': False,
            'error': serializer.errors.get('subdomain', ['Invalid subdomain'])[0]
        }, status=status.HTTP_400_BAD_REQUEST)
    
    subdomain = serializer.validated_data['subdomain']
    
    # Check if taken
    is_taken = Instance.objects.filter(
        subdomain=subdomain
    ).exclude(status='deleted').exists()
    
    return Response({
        'available': not is_taken,
        'subdomain': subdomain
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def create_checkout(request):
    """
    Create a Stripe Checkout session for new signup.
    
    POST /api/create-checkout/
    {
        "subdomain": "janes-shop",
        "site_name": "Jane's Digital Shop",
        "email": "jane@example.com"
    }
    
    Returns:
    {
        "checkout_url": "https://checkout.stripe.com/..."
    }
    """
    serializer = CreateCheckoutSerializer(data=request.data)
    
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    subdomain = serializer.validated_data['subdomain']
    site_name = serializer.validated_data['site_name']
    email = serializer.validated_data['email']
    
    try:
        # Create Stripe checkout session
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': settings.STRIPE_PRICE_ID,
                'quantity': 1,
            }],
            mode='subscription',
            customer_email=email,
            success_url=f"https://{settings.BASE_DOMAIN}/signup/success/?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"https://{settings.BASE_DOMAIN}/signup/cancelled/",
            metadata={
                'subdomain': subdomain,
                'site_name': site_name,
            },
            subscription_data={
                'metadata': {
                    'subdomain': subdomain,
                    'site_name': site_name,
                }
            }
        )
        
        return Response({
            'checkout_url': checkout_session.url,
            'session_id': checkout_session.id
        })
        
    except stripe.error.StripeError as e:
        return Response({
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


# =============================================================================
# ADMIN ENDPOINTS (for dashboard)
# =============================================================================

class InstanceViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for managing instances.
    Admin only.
    """
    queryset = Instance.objects.all()
    serializer_class = InstanceSerializer
    permission_classes = [IsAdminUser]
    
    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        """Start a stopped instance"""
        instance = self.get_object()
        
        if instance.status not in ['stopped', 'error']:
            return Response({
                'error': f'Cannot start instance with status: {instance.status}'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            manager = DockerManager()
            manager.start_instance(instance)
            return Response({'status': 'started'})
        except Exception as e:
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=True, methods=['post'])
    def stop(self, request, pk=None):
        """Stop a running instance"""
        instance = self.get_object()
        
        if instance.status != 'running':
            return Response({
                'error': f'Cannot stop instance with status: {instance.status}'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            manager = DockerManager()
            manager.stop_instance(instance)
            return Response({'status': 'stopped'})
        except Exception as e:
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=True, methods=['post'])
    def restart(self, request, pk=None):
        """Restart an instance"""
        instance = self.get_object()
        
        if instance.status != 'running':
            return Response({
                'error': f'Cannot restart instance with status: {instance.status}'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            manager = DockerManager()
            manager.restart_instance(instance)
            return Response({'status': 'restarted'})
        except Exception as e:
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=True, methods=['get'])
    def health(self, request, pk=None):
        """Check instance health"""
        instance = self.get_object()
        
        try:
            manager = DockerManager()
            is_healthy = manager.health_check(instance)
            return Response({
                'healthy': is_healthy,
                'last_check': instance.last_health_check
            })
        except Exception as e:
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=True, methods=['get'])
    def stats(self, request, pk=None):
        """Get container resource stats"""
        instance = self.get_object()
        
        try:
            manager = DockerManager()
            stats = manager.get_container_stats(instance)
            if stats:
                return Response(stats)
            return Response({
                'error': 'Could not retrieve stats'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=True, methods=['get'])
    def logs(self, request, pk=None):
        """Get provisioning logs for an instance"""
        instance = self.get_object()
        logs = instance.logs.all()[:50]
        serializer = ProvisioningLogSerializer(logs, many=True)
        return Response(serializer.data)


class CustomerViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing customers.
    Admin only.
    """
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
    permission_classes = [IsAdminUser]


@api_view(['GET'])
@permission_classes([IsAdminUser])
def dashboard_stats(request):
    """
    Get overview stats for admin dashboard.
    
    GET /api/stats/
    """
    return Response({
        'total_customers': Customer.objects.count(),
        'active_subscriptions': Subscription.objects.filter(status='active').count(),
        'running_instances': Instance.objects.filter(status='running').count(),
        'stopped_instances': Instance.objects.filter(status='stopped').count(),
        'error_instances': Instance.objects.filter(status='error').count(),
    })
