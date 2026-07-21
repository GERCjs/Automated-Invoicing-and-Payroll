import re
from urllib.parse import urlparse

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts.models import EmailVerificationToken


@pytest.mark.django_db
def test_superadmin_can_log_in(live_server, page):
    User = get_user_model()
    User.objects.create_superuser(
        username="e2e_superadmin",
        email="e2e_superadmin@vaniday.com",
        password="TempPass123!",
    )

    page.goto(f"{live_server.url}{reverse('login')}")
    page.get_by_label("Email / Username").fill("e2e_superadmin")
    page.get_by_label("Password").fill("TempPass123!")
    page.get_by_role("button", name="Log In").click()

    page.wait_for_url(re.compile(r".*/dashboard/?$"))
    assert page.get_by_role("heading", name=re.compile("dashboard", re.IGNORECASE)).first.is_visible()


@pytest.mark.django_db
def test_company_user_can_register_and_is_prompted_to_verify_email(live_server, page):
    page.goto(f"{live_server.url}{reverse('register')}")
    page.get_by_label("Username").fill("e2e_register_user")
    page.get_by_label("Email").fill("e2e_register_user@vaniday.com")
    page.get_by_label("Password", exact=True).fill("TempPass123!")
    page.get_by_label("Confirm Password").fill("TempPass123!")
    page.get_by_role("button", name="Create Account").click()

    page.wait_for_url(re.compile(r".*/accounts/login/?$"))
    assert page.get_by_text("Registration successful. Please verify your email before logging in.").is_visible()

    User = get_user_model()
    user = User.objects.get(username="e2e_register_user")
    assert user.is_active is False
    assert EmailVerificationToken.objects.filter(user=user, used_at__isnull=True).exists()


@pytest.mark.django_db
def test_forgot_password_reset_flow_updates_password(live_server, page, mailoutbox):
    User = get_user_model()
    User.objects.create_user(
        username="e2e_reset_user",
        email="e2e_reset_user@vaniday.com",
        password="OldPass123!",
        is_active=True,
    )

    page.goto(f"{live_server.url}{reverse('login')}")
    page.get_by_role("link", name="Forgot Password?").click()
    page.wait_for_url(re.compile(r".*/accounts/password-reset/?$"))
    page.get_by_label("Email").fill("e2e_reset_user@vaniday.com")
    page.get_by_role("button", name="Send Reset Link").click()

    page.wait_for_url(re.compile(r".*/accounts/password-reset/done/?$"))
    assert page.get_by_text("If the email matches an active account").is_visible()
    assert len(mailoutbox) == 1

    reset_url = re.search(r"https?://\S+", mailoutbox[0].body).group(0)
    reset_path = urlparse(reset_url).path
    page.goto(f"{live_server.url}{reset_path}")
    page.get_by_label("New Password", exact=True).fill("NewPass123!")
    page.get_by_label("Confirm New Password").fill("NewPass123!")
    page.get_by_role("button", name="Update Password").click()

    page.wait_for_url(re.compile(r".*/accounts/password-reset/complete/?$"))
    assert page.get_by_text("You can now log in with your new password.").is_visible()

    page.get_by_role("link", name="Back to Login").click()
    page.get_by_label("Email / Username").fill("e2e_reset_user")
    page.get_by_label("Password").fill("NewPass123!")
    page.get_by_role("button", name="Log In").click()
    page.wait_for_url(re.compile(r".*/payroll/my-payslips/?$"))
