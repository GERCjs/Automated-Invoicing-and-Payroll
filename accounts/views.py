from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect, render
from django.urls import reverse_lazy

from core.audit import get_client_ip, log_event

from .forms import AdminAccountCreationForm, LoginForm, RegistrationForm
from .permissions import role_required
from .roles import ADMIN, CUSTOMER, SUPERADMIN


class UserLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


class UserLogoutView(LogoutView):
    next_page = reverse_lazy("login")


def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            user.role_profile.role = CUSTOMER
            user.role_profile.save(update_fields=["role", "updated_at"])
            log_event(
                action="auth.registered",
                user=user,
                target_type="user",
                target_id=str(user.id),
                metadata={"username": user.username, "role": user.role_profile.role},
                ip_address=get_client_ip(request),
            )
            login(request, user)
            messages.success(request, "Registration successful.")
            return redirect("dashboard")
    else:
        form = RegistrationForm()

    return render(request, "accounts/register.html", {"form": form})


@login_required
@role_required(SUPERADMIN, ADMIN)
def create_admin_account(request):
    if request.method == "POST":
        form = AdminAccountCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            log_event(
                action="auth.admin_account.created",
                user=request.user,
                target_type="user",
                target_id=str(user.id),
                metadata={"username": user.username, "role": user.role_profile.role},
                ip_address=get_client_ip(request),
            )
            messages.success(request, f"Admin account {user.username} created.")
            return redirect("create-admin-account")
    else:
        form = AdminAccountCreationForm()

    return render(request, "accounts/create_admin_account.html", {"form": form})
