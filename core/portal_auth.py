from functools import wraps
from django.shortcuts import redirect
from django.http import JsonResponse
from django.urls import reverse

from .models import Customer


SESSION_KEY = "portal_customer_id"


def get_logged_in_customer(request):
    """
    Return the logged-in Customer or None.
    """
    customer_id = request.session.get(SESSION_KEY)
    if not customer_id:
        return None
    try:
        return Customer.objects.get(id=customer_id)
    except Customer.DoesNotExist:
        return None


def portal_login_required(view_func):
    """
    Decorator for portal views (HTML or API).
    Redirects HTML requests, returns 401 for API.
    """

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        customer = get_logged_in_customer(request)
        if not customer:
            if request.path.startswith("/api/"):
                return JsonResponse({"error": "Authentication required"}, status=401)
            return redirect(reverse("portal:login"))
        request.portal_customer = customer
        return view_func(request, *args, **kwargs)

    return _wrapped


def portal_login(request, customer: Customer):
    """
    Log a customer in by setting session.
    """
    request.session[SESSION_KEY] = customer.id
    request.session.modified = True


def portal_logout(request):
    """
    Log a customer out.
    """
    request.session.pop(SESSION_KEY, None)
    request.session.modified = True
