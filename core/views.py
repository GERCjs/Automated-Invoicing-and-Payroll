from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from accounts.permissions import role_required
from accounts.roles import ADMIN, HR, SUPERADMIN

from .audit import get_client_ip, log_event


def home(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    return redirect("login")


@login_required
def dashboard(request):
    log_event(
        action="core.dashboard.viewed",
        user=request.user,
        metadata={"path": request.path},
        ip_address=get_client_ip(request),
    )
    return render(request, "core/dashboard.html")


@login_required
@role_required(SUPERADMIN, ADMIN, HR)
def finance_console(request):
    log_event(
        action="core.finance_console.viewed",
        user=request.user,
        metadata={"path": request.path},
        ip_address=get_client_ip(request),
    )
    return render(request, "core/finance_console.html")
