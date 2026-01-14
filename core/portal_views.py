from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods

from .models import Customer
from .portal_auth import portal_login, portal_logout, portal_login_required


# Login Page


@require_http_methods(["GET", "POST"])
def portal_login_view(request):
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        password = request.POST.get("password", "")

        error = None

        try:
            customer = Customer.objects.get(email=email)
            if not customer.check_portal_password(password):
                error = "Invalid email or password"
        except Customer.DoesNotExist:
            error = "Invalid email or password"

        if not error:
            portal_login(request, customer)
            return redirect("portal:dashboard")

        return render(
            request,
            "portal/login.html",
            {"error": error},
        )

    return render(request, "portal/login.html")


# Logout view
@portal_login_required
def portal_logout_view(request):
    portal_logout(request)
    return redirect("portal:login")
