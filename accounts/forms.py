from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.forms import UserCreationForm
from django.db.models import Q

from invoicing.models import Customer as InvoiceCustomer
from notifications.models import PaymentReminderSettings

from .models import EmailVerificationToken, LoginSecurityPolicy
from .roles import ADMIN, CUSTOMER, FINANCE, HR, ROLE_CHOICES, STAFF, SUPERADMIN

User = get_user_model()

MANAGED_INTERNAL_ROLES = {ADMIN, FINANCE, HR, STAFF}
DEFAULT_COMPANY_EMAIL_DOMAINS = {"vaniday.com"}


def _email_domain(email: str) -> str:
    return (email or "").strip().lower().rsplit("@", 1)[-1]


def get_company_email_domains() -> set[str]:
    configured = (getattr(settings, "COMPANY_EMAIL_DOMAINS", "") or "").strip()
    domains = {domain.strip().lower() for domain in configured.split(",") if domain.strip()}
    company_email = (getattr(settings, "COMPANY_EMAIL", "") or "").strip().lower()
    if "@" in company_email:
        domains.add(company_email.rsplit("@", 1)[-1])
    domains |= DEFAULT_COMPANY_EMAIL_DOMAINS
    return domains


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Username or Email",
                "autofocus": True,
            }
        )
    )
    password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "Password",
            }
        )
    )

    def clean(self):
        identifier = str(self.data.get("username", "")).strip()
        try:
            return super().clean()
        except forms.ValidationError:
            if identifier:
                user = (
                    User.objects.select_related("role_profile")
                    .filter(Q(username__iexact=identifier) | Q(email__iexact=identifier))
                    .order_by("id")
                    .first()
                )
                if user and getattr(user.role_profile, "is_suspended", False):
                    raise forms.ValidationError(
                        "Your account is suspended. Please contact an administrator.",
                        code="account_suspended",
                    )
                if user and not user.is_active:
                    pending_verification = EmailVerificationToken.objects.filter(
                        user=user,
                        used_at__isnull=True,
                    ).exists()
                    if pending_verification:
                        raise forms.ValidationError(
                            "Your account is not verified. Please check your email for the verification link.",
                            code="account_unverified",
                        )
            raise


class RegistrationForm(UserCreationForm):
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(
            attrs={
                "class": "form-control",
                "placeholder": "Email",
            }
        ),
    )

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")
        widgets = {
            "username": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Username",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._resolved_role = STAFF
        self.fields["password1"].widget.attrs.update({"class": "form-control", "placeholder": "Password"})
        self.fields["password2"].widget.attrs.update(
            {"class": "form-control", "placeholder": "Confirm Password"}
        )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("This email is already in use.")

        company_domains = get_company_email_domains()
        is_company_email = _email_domain(email) in company_domains
        if is_company_email:
            self._resolved_role = STAFF
            return email

        has_customer_invoice_profile = InvoiceCustomer.objects.filter(email__iexact=email).exists()
        if not has_customer_invoice_profile:
            raise forms.ValidationError(
                "Customer registration requires an email linked to an existing invoice customer record."
            )
        self._resolved_role = CUSTOMER
        return email

    def get_registration_role(self):
        return getattr(self, "_resolved_role", STAFF)

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.is_active = False
        if commit:
            user.save()
        return user


class AdminAccountCreationForm(RegistrationForm):
    """Admin-only form used to create project Admin role accounts."""

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            user.is_staff = True
            user.save(update_fields=["is_staff"])
            user.role_profile.role = ADMIN
            user.role_profile.save(update_fields=["role", "updated_at"])
        return user


class ManagedAccountCreationForm(RegistrationForm):
    code_id = forms.CharField(
        required=False,
        label="Code ID",
        help_text="Leave blank to generate automatically.",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Auto-generated if blank"}),
    )
    role = forms.ChoiceField(
        choices=ROLE_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    class Meta(RegistrationForm.Meta):
        fields = ("username", "email", "code_id", "role", "password1", "password2")

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.fields["role"].choices = self._allowed_role_choices()

    def _allowed_role_choices(self):
        actor_role = getattr(getattr(self.actor, "role_profile", None), "role", None)
        if getattr(self.actor, "is_superuser", False):
            actor_role = SUPERADMIN
        roles = [choice for choice in ROLE_CHOICES if choice[0] in MANAGED_INTERNAL_ROLES]
        if actor_role != SUPERADMIN:
            roles = [choice for choice in roles if choice[0] != ADMIN]
        return roles

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        role = self.cleaned_data.get("role")
        if role in MANAGED_INTERNAL_ROLES and not email.endswith("@vaniday.com"):
            raise forms.ValidationError("Internal roles require a @vaniday.com email address.")
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("This email is already in use.")
        return email

    def clean_code_id(self):
        code_id = self.cleaned_data.get("code_id", "").strip().upper()
        if code_id and User.objects.filter(role_profile__code_id__iexact=code_id).exists():
            raise forms.ValidationError("This Code ID is already in use.")
        return code_id

    def save(self, commit=True):
        user = UserCreationForm.save(self, commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
            user.role_profile.role = self.cleaned_data["role"]
            user.role_profile.code_id = self.cleaned_data["code_id"]
            user.role_profile.save(update_fields=["role", "code_id", "updated_at"])
        return user


class ManagedRoleUpdateForm(forms.Form):
    role = forms.ChoiceField(widget=forms.Select(attrs={"class": "form-select form-select-sm"}))

    def __init__(self, *args, actor=None, target_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.target_user = target_user
        self.fields["role"].choices = self._allowed_role_choices()

    def _allowed_role_choices(self):
        choices = [choice for choice in ROLE_CHOICES if choice[0] in MANAGED_INTERNAL_ROLES]
        if not getattr(self.actor, "is_superuser", False):
            choices = [choice for choice in choices if choice[0] != ADMIN]
        return choices

    def clean_role(self):
        role = self.cleaned_data["role"]
        if self.target_user and self.target_user == self.actor:
            raise forms.ValidationError("You cannot change your own role.")
        return role


class ManagedPasswordUpdateForm(SetPasswordForm):
    def __init__(self, user, *args, **kwargs):
        super().__init__(user, *args, **kwargs)
        self.fields["new_password1"].widget.attrs.update({"class": "form-control"})
        self.fields["new_password2"].widget.attrs.update({"class": "form-control"})


class PaymentReminderSettingsForm(forms.ModelForm):
    class Meta:
        model = PaymentReminderSettings
        fields = (
            "before_due_reminders_enabled",
            "reminder_days_before_due",
            "due_date_reminders_enabled",
            "after_due_reminders_enabled",
            "after_due_days",
            "overdue_repeat_enabled",
            "overdue_repeat_days",
            "mass_email_enabled",
        )
        widgets = {
            "reminder_days_before_due": forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
            "after_due_days": forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
            "overdue_repeat_days": forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
            "before_due_reminders_enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "due_date_reminders_enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "after_due_reminders_enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "overdue_repeat_enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "mass_email_enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("before_due_reminders_enabled") and not cleaned_data.get("reminder_days_before_due"):
            self.add_error("reminder_days_before_due", "Enter number of days before due date.")
        if cleaned_data.get("after_due_reminders_enabled") and not cleaned_data.get("after_due_days"):
            self.add_error("after_due_days", "Enter number of days after due date.")
        if cleaned_data.get("overdue_repeat_enabled") and not cleaned_data.get("overdue_repeat_days"):
            self.add_error("overdue_repeat_days", "Enter repeat interval in days.")
        return cleaned_data


class MassEmailForm(forms.Form):
    recipients = forms.MultipleChoiceField(
        choices=[],
        required=True,
        widget=forms.CheckboxSelectMultiple,
    )
    subject = forms.CharField(max_length=255, widget=forms.TextInput(attrs={"class": "form-control"}))
    message = forms.CharField(widget=forms.Textarea(attrs={"class": "form-control", "rows": 4}))

    def __init__(self, *args, role_counts=None, **kwargs):
        super().__init__(*args, **kwargs)
        role_counts = role_counts or {}
        self.fields["recipients"].choices = [
            (role, f"{label} ({role_counts.get(role, 0)})")
            for role, label in ROLE_CHOICES
            if role != SUPERADMIN
        ]


class LoginSecurityPolicyForm(forms.ModelForm):
    class Meta:
        model = LoginSecurityPolicy
        fields = ("role", "max_failed_login_attempts")
        widgets = {
            "role": forms.HiddenInput(),
            "max_failed_login_attempts": forms.NumberInput(
                attrs={"class": "form-control form-control-sm", "min": "1", "max": "20"}
            ),
        }

    def clean_max_failed_login_attempts(self):
        value = self.cleaned_data["max_failed_login_attempts"]
        if value < 1 or value > 20:
            raise forms.ValidationError("Allowed range is 1 to 20 attempts.")
        return value
