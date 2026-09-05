from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from core.models import Employee


def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        # Form field in login.html is named 'email'
        login_identifier = (request.POST.get("email")).strip()
        password = request.POST.get("password", "")

        if not login_identifier or not password:
            messages.error(request, "Please enter both email/username and password.")
            return render(request, "accounts/login.html")

        # Find user by username, email, or Employee code
        target_username = login_identifier
        user_obj = User.objects.filter(email__iexact=login_identifier).first()
        if not user_obj:
            user_obj = User.objects.filter(username__iexact=login_identifier).first()
        if not user_obj:
            emp = Employee.objects.filter(code__iexact=login_identifier).first()
            if not emp:
                emp = Employee.objects.filter(email__iexact=login_identifier).first()
            if emp:
                user_obj = User.objects.filter(email__iexact=emp.email).first()
                if not user_obj:
                    user_obj = User.objects.create_user(
                        username=emp.email,
                        email=emp.email,
                        first_name=emp.first_name,
                        last_name=emp.last_name,
                        password=password
                    )

        if user_obj:
            target_username = user_obj.username

        user = authenticate(
            request,
            username=target_username,
            password=password
        )

        if user is not None:
            if user.is_active:
                login(request, user)
                return redirect("dashboard")
            else:
                messages.error(request, "This account is currently disabled.")
        else:
            messages.error(request, "Invalid email/username or password")

    return render(request, "accounts/login.html")


@login_required(login_url="login")
def dashboard_view(request):
    employees = Employee.objects.all().order_by("code")
    total_employees = employees.count()

    context = {
        "employees": employees,
        "total_employees": total_employees,
    }
    return render(request, "accounts/dashboard.html", context)


def logout_view(request):
    logout(request)
    return redirect("login")


def landing_view(request):
    return render(request, "accounts/landing.html")