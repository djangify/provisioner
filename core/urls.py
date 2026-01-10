"""
URL routing for the core API
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    InstanceViewSet, CustomerViewSet,
    check_subdomain, create_checkout, dashboard_stats
)
from .stripe_webhooks import stripe_webhook

# DRF Router for ViewSets
router = DefaultRouter()
router.register(r'instances', InstanceViewSet, basename='instance')
router.register(r'customers', CustomerViewSet, basename='customer')

urlpatterns = [
    # Stripe webhook (public)
    path('webhook/stripe/', stripe_webhook, name='stripe-webhook'),
    
    # Public endpoints
    path('check-subdomain/', check_subdomain, name='check-subdomain'),
    path('create-checkout/', create_checkout, name='create-checkout'),
    
    # Admin dashboard endpoint
    path('stats/', dashboard_stats, name='dashboard-stats'),
    
    # ViewSet routes
    path('', include(router.urls)),
]
