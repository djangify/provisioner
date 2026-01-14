from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.shortcuts import redirect

from .models import Customer
from .portal_auth import portal_login, portal_logout


# Login Endpoint
@csrf_exempt
@require_POST
def portal_login_api(request):
    email = request.POST.get("email", "").strip().lower()
    password = request.POST.get("password", "")

    if not email or not password:
        return JsonResponse({"error": "Email and password are required"}, status=400)

    try:
        customer = Customer.objects.get(email=email)
    except Customer.DoesNotExist:
        return JsonResponse({"error": "Invalid credentials"}, status=401)

    if not customer.check_portal_password(password):
        return JsonResponse({"error": "Invalid credentials"}, status=401)

    portal_login(request, customer)

    return JsonResponse({"success": True})


# Logout end point
@require_POST
def portal_logout_api(request):
    portal_logout(request)
    return JsonResponse({"success": True})
