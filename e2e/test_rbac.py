import re

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts.models import EmailVerificationToken
from accounts.roles import ADMIN, CUSTOMER, FINANCE, HR, STAFF
from invoicing.models import Customer


PASSWORD = "TempPass123!"


ZERO_ARG_RBAC_ROUTES = {
    "create-admin-account": {"superadmin"},
    "admin-dashboard": {"superadmin", ADMIN},
    "managed-account-create": {"superadmin", ADMIN},
    "payment-reminder-settings-update": {"superadmin", ADMIN},
    "mass-email-send": {"superadmin", ADMIN},
    "email-delivery-log-list": {"superadmin", ADMIN},
    "suspicious-activity-list": {"superadmin", ADMIN},
    "dashboard-audit-logs": {"superadmin", ADMIN},
    "dashboard-validation-errors": {"superadmin", ADMIN},
    "payment-bank-transfer-settings": {"superadmin", ADMIN},
    "admin-security-report": {"superadmin", ADMIN},
    "invoice-list": {"superadmin", ADMIN, FINANCE},
    "invoice-dashboard": {"superadmin", ADMIN, FINANCE},
    "invoice-template-settings": {"superadmin", ADMIN, FINANCE},
    "invoice-create": {"superadmin", ADMIN, FINANCE},
    "invoice-customer-create": {"superadmin", ADMIN, FINANCE},
    "invoice-csv-upload": {"superadmin", ADMIN, FINANCE},
    "invoice-customer-report": {"superadmin", ADMIN, FINANCE},
    "payment-stripe-report": {"superadmin", ADMIN, FINANCE},
    "payroll-dashboard": {"superadmin", ADMIN, HR},
    "payroll-list": {"superadmin", ADMIN, HR},
    "payroll-template-settings": {"superadmin", ADMIN, HR},
    "employee-list": {"superadmin", ADMIN, HR},
    "employee-create": {"superadmin", ADMIN, HR},
    "employee-upload-preview": {"superadmin", ADMIN, HR},
    "payroll-create": {"superadmin", ADMIN, HR},
    "payroll-upload-preview": {"superadmin", ADMIN, HR},
    "payroll-report": {"superadmin", ADMIN, HR},
    "support-ticket-list": {"superadmin", ADMIN, FINANCE, HR},
    "finance-support-ticket-list": {"superadmin", ADMIN, FINANCE},
    "support-ticket-create": {"superadmin", ADMIN, FINANCE, HR},
    "support-ticket-settings-update": {"superadmin", ADMIN},
    "my-payslips": {STAFF},
    "customer-support-ticket-list": {CUSTOMER, STAFF},
    "customer-invoice-dashboard": {CUSTOMER},
}


def login(page, live_server, username, password=PASSWORD):
    page.goto(f"{live_server.url}{reverse('login')}")
    page.get_by_label("Email / Username").fill(username)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Log In").click()


def logout(page):
    page.get_by_role("button", name="Logout").click()
    page.wait_for_url(re.compile(r".*/accounts/login/?$"))


def create_managed_account(page, live_server, *, username, email, role):
    page.goto(f"{live_server.url}{reverse('managed-account-create')}")
    page.locator("#id_username").fill(username)
    page.locator("#id_email").fill(email)
    page.locator("#id_role").select_option(role)
    page.locator("#id_password1").fill(PASSWORD)
    page.locator("#id_password2").fill(PASSWORD)
    page.get_by_role("button", name="Create Account").click()
    page.wait_for_url(re.compile(r".*/accounts/admin-dashboard/?$"))
    assert page.get_by_text(username, exact=True).is_visible()


def assert_forbidden(page, live_server, route_name):
    response = page.goto(f"{live_server.url}{reverse(route_name)}")
    assert response.status == 403
    assert page.get_by_role("heading", name="Permission Denied").is_visible()


def create_role_user(username, role, email):
    User = get_user_model()
    user = User.objects.create_user(username=username, email=email, password=PASSWORD, is_active=True)
    user.role_profile.role = role
    user.role_profile.save(update_fields=["role", "updated_at"])
    return user


@pytest.mark.django_db
def test_superadmin_creates_rbac_accounts_and_roles_land_on_expected_pages(live_server, page):
    User = get_user_model()
    User.objects.create_superuser(
        username="rbac_superadmin",
        email="rbac_superadmin@vaniday.com",
        password=PASSWORD,
    )

    login(page, live_server, "rbac_superadmin")
    page.wait_for_url(re.compile(r".*/dashboard/?$"))
    page.goto(f"{live_server.url}{reverse('admin-dashboard')}")

    managed_accounts = [
        (ADMIN, "rbac_admin", "rbac_admin@vaniday.com", r".*/dashboard/?$"),
        (FINANCE, "rbac_finance", "rbac_finance@vaniday.com", r".*/invoices/dashboard/?$"),
        (HR, "rbac_hr", "rbac_hr@vaniday.com", r".*/payroll/dashboard/?$"),
        (STAFF, "rbac_staff", "rbac_staff@vaniday.com", r".*/payroll/my-payslips/?$"),
    ]
    for role, username, email, _landing_pattern in managed_accounts:
        create_managed_account(page, live_server, username=username, email=email, role=role)
    logout(page)

    Customer.objects.create(name="RBAC Customer", email="rbac_customer@customer.test")
    page.goto(f"{live_server.url}{reverse('register')}")
    page.get_by_label("Username").fill("rbac_customer")
    page.get_by_label("Email").fill("rbac_customer@customer.test")
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_label("Confirm Password").fill(PASSWORD)
    page.get_by_role("button", name="Create Account").click()
    page.wait_for_url(re.compile(r".*/accounts/login/?$"))

    customer_user = User.objects.get(username="rbac_customer")
    assert customer_user.role_profile.role == CUSTOMER
    verification = EmailVerificationToken.objects.filter(user=customer_user, used_at__isnull=True).first()
    page.goto(f"{live_server.url}{reverse('verify-email', args=[verification.token])}")
    page.wait_for_url(re.compile(r".*/accounts/login/?$"))

    expected_landings = managed_accounts + [
        (CUSTOMER, "rbac_customer", "rbac_customer@customer.test", r".*/invoices/my/dashboard/?$"),
    ]
    for _role, username, _email, landing_pattern in expected_landings:
        login(page, live_server, username)
        page.wait_for_url(re.compile(landing_pattern))
        logout(page)


@pytest.mark.django_db
def test_rbac_blocks_cross_role_page_access_after_login(live_server, page):
    User = get_user_model()
    role_users = [
        (ADMIN, "matrix_admin", "matrix_admin@vaniday.com"),
        (FINANCE, "matrix_finance", "matrix_finance@vaniday.com"),
        (HR, "matrix_hr", "matrix_hr@vaniday.com"),
        (STAFF, "matrix_staff", "matrix_staff@vaniday.com"),
        (CUSTOMER, "matrix_customer", "matrix_customer@customer.test"),
    ]
    Customer.objects.create(name="Matrix Customer", email="matrix_customer@customer.test")
    for role, username, email in role_users:
        user = User.objects.create_user(username=username, email=email, password=PASSWORD, is_active=True)
        user.role_profile.role = role
        user.role_profile.save(update_fields=["role", "updated_at"])

    checks = [
        ("matrix_finance", ["admin-dashboard", "payroll-dashboard"]),
        ("matrix_hr", ["admin-dashboard", "invoice-dashboard"]),
        ("matrix_staff", ["admin-dashboard", "invoice-dashboard", "payroll-dashboard"]),
        ("matrix_customer", ["admin-dashboard", "invoice-dashboard", "payroll-dashboard"]),
    ]
    for username, forbidden_routes in checks:
        login(page, live_server, username)
        page.wait_for_load_state("networkidle")
        for route_name in forbidden_routes:
            assert_forbidden(page, live_server, route_name)
        logout(page)


@pytest.mark.django_db
def test_zero_argument_rbac_routes_match_role_matrix(live_server, page):
    User = get_user_model()
    User.objects.create_superuser(
        username="matrix_superadmin",
        email="matrix_superadmin@vaniday.com",
        password=PASSWORD,
    )
    create_role_user("matrix_route_admin", ADMIN, "matrix_route_admin@vaniday.com")
    create_role_user("matrix_route_finance", FINANCE, "matrix_route_finance@vaniday.com")
    create_role_user("matrix_route_hr", HR, "matrix_route_hr@vaniday.com")
    create_role_user("matrix_route_staff", STAFF, "matrix_route_staff@vaniday.com")
    Customer.objects.create(name="Route Matrix Customer", email="matrix_route_customer@customer.test")
    create_role_user("matrix_route_customer", CUSTOMER, "matrix_route_customer@customer.test")

    role_users = {
        "superadmin": "matrix_superadmin",
        ADMIN: "matrix_route_admin",
        FINANCE: "matrix_route_finance",
        HR: "matrix_route_hr",
        STAFF: "matrix_route_staff",
        CUSTOMER: "matrix_route_customer",
    }

    for role, username in role_users.items():
        login(page, live_server, username)
        page.wait_for_load_state("networkidle")
        for route_name, allowed_roles in ZERO_ARG_RBAC_ROUTES.items():
            response = page.goto(f"{live_server.url}{reverse(route_name)}")
            if role in allowed_roles:
                assert response.status == 200, f"{role} should access {route_name}"
            else:
                assert response.status == 403, f"{role} should be forbidden from {route_name}"
        logout(page)


@pytest.mark.django_db
def test_admin_cannot_assign_admin_role_when_creating_managed_account(live_server, page):
    User = get_user_model()
    admin = User.objects.create_user(
        username="limited_admin",
        email="limited_admin@vaniday.com",
        password=PASSWORD,
        is_active=True,
    )
    admin.role_profile.role = ADMIN
    admin.role_profile.save(update_fields=["role", "updated_at"])

    login(page, live_server, "limited_admin")
    page.wait_for_url(re.compile(r".*/dashboard/?$"))
    page.goto(f"{live_server.url}{reverse('managed-account-create')}")

    admin_options = page.locator("#id_role option").evaluate_all(
        "(options) => options.map((option) => option.value)"
    )
    assert ADMIN not in admin_options


@pytest.mark.django_db
def test_admin_cannot_create_admin_through_legacy_admin_create_page(live_server, page):
    User = get_user_model()
    admin = User.objects.create_user(
        username="legacy_admin_actor",
        email="legacy_admin_actor@vaniday.com",
        password=PASSWORD,
        is_active=True,
    )
    admin.role_profile.role = ADMIN
    admin.role_profile.save(update_fields=["role", "updated_at"])

    login(page, live_server, "legacy_admin_actor")
    page.wait_for_url(re.compile(r".*/dashboard/?$"))
    response = page.goto(f"{live_server.url}{reverse('create-admin-account')}")

    assert response.status == 403
    assert page.get_by_role("heading", name="Permission Denied").is_visible()
    assert not User.objects.filter(username="legacy_created_admin", role_profile__role=ADMIN).exists()


@pytest.mark.django_db
def test_unsuspending_unverified_user_does_not_bypass_email_verification(live_server, page):
    admin = create_role_user("unsuspend_admin", ADMIN, "unsuspend_admin@vaniday.com")
    target = create_role_user("unverified_suspended_user", STAFF, "unverified_suspended_user@vaniday.com")
    target.is_active = False
    target.save(update_fields=["is_active"])
    EmailVerificationToken.issue_for_user(target)
    target.role_profile.suspend(by=admin, reason="E2E suspension before verification")

    login(page, live_server, "unsuspend_admin")
    page.wait_for_url(re.compile(r".*/dashboard/?$"))
    page.goto(f"{live_server.url}{reverse('admin-dashboard')}?q=unverified_suspended_user")
    page.get_by_role("button", name="Reactivate").click()
    page.wait_for_url(re.compile(r".*/accounts/admin-dashboard/?$"))

    target.refresh_from_db()
    assert target.is_active is False
    assert target.role_profile.is_suspended is False

    logout(page)
    login(page, live_server, "unverified_suspended_user")
    assert page.get_by_text("Your account is not verified. Please check your email for the verification link.").is_visible()
