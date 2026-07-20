from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.forms import UserCreationForm
from django.db.models import Q

from invoicing.models import Customer as InvoiceCustomer
from notifications.models import (
    PAYMENT_REMINDER_DEFAULT_BODY_TEMPLATE,
    PAYMENT_REMINDER_DEFAULT_SUBJECT_TEMPLATE,
    get_unknown_payment_reminder_template_fields,
    PaymentReminderSettings,
)

from .models import EmailVerificationToken, LoginSecurityPolicy
from .roles import ADMIN, CUSTOMER, FINANCE, HR, ROLE_CHOICES, STAFF, SUPERADMIN

User = get_user_model()

# Internal roles can be created/managed by admins.
MANAGED_INTERNAL_ROLES = {ADMIN, FINANCE, HR, STAFF}
# Default email domain treated as a company staff email.
DEFAULT_COMPANY_EMAIL_DOMAINS = {"vaniday.com"}


def _email_domain(email: str) -> str:
    # Return the part after @, lowercased.
    return (email or "").strip().lower().rsplit("@", 1)[-1]


def get_company_email_domains() -> set[str]:
    # Build the list of company domains from settings plus the default.
    configured = (getattr(settings, "COMPANY_EMAIL_DOMAINS", "") or "").strip()
    domains = {domain.strip().lower() for domain in configured.split(",") if domain.strip()}
    company_email = (getattr(settings, "COMPANY_EMAIL", "") or "").strip().lower()
    if "@" in company_email:
        domains.add(company_email.rsplit("@", 1)[-1])
    domains |= DEFAULT_COMPANY_EMAIL_DOMAINS
    return domains


# Login form used by the login page.
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
        # Keep the entered username/email so we can show clearer errors.
        identifier = str(self.data.get("username", "")).strip()
        try:
            return super().clean()
        except forms.ValidationError:
            if identifier:
                # Try to find the user by username or email.
                user = (
                    User.objects.select_related("role_profile")
                    .filter(Q(username__iexact=identifier) | Q(email__iexact=identifier))
                    .order_by("id")
                    .first()
                )
                # Show a clear message for suspended accounts.
                if user and getattr(user.role_profile, "is_suspended", False):
                    raise forms.ValidationError(
                        "Your account is suspended. Please contact an administrator.",
                        code="account_suspended",
                    )
                # Show a clear message for accounts waiting on email verification.
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


# Public registration form.
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
        # Default role is staff unless email rules decide customer.
        self._resolved_role = STAFF
        self.fields["password1"].widget.attrs.update({"class": "form-control", "placeholder": "Password"})
        self.fields["password2"].widget.attrs.update(
            {"class": "form-control", "placeholder": "Confirm Password"}
        )

    def clean_email(self):
        # Normalize email and reject duplicates.
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("This email is already in use.")

        # Company email addresses register as staff.
        company_domains = get_company_email_domains()
        is_company_email = _email_domain(email) in company_domains
        if is_company_email:
            self._resolved_role = STAFF
            return email

        # Non-company emails must match an existing invoice customer.
        has_customer_invoice_profile = InvoiceCustomer.objects.filter(email__iexact=email).exists()
        if not has_customer_invoice_profile:
            raise forms.ValidationError(
                "Customer registration requires an email linked to an existing invoice customer record."
            )
        self._resolved_role = CUSTOMER
        return email

    def get_registration_role(self):
        # Views call this after validation to know which role to assign.
        return getattr(self, "_resolved_role", STAFF)

    def save(self, commit=True):
        # New self-registered accounts must verify email before logging in.
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.is_active = False
        if commit:
            user.save()
        return user


class AdminAccountCreationForm(RegistrationForm):
    """Admin-only form used to create project Admin role accounts."""

    def save(self, commit=True):
        # Create a normal registered user, then upgrade it to an Admin account.
        user = super().save(commit=commit)
        if commit:
            user.is_staff = True
            user.save(update_fields=["is_staff"])
            user.role_profile.role = ADMIN
            user.role_profile.save(update_fields=["role", "updated_at"])
        return user


# Form used by admins to create internal accounts.
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
        # actor is the admin/superadmin performing the action.
        self.actor = actor
        self.fields["role"].choices = self._allowed_role_choices()

    def _allowed_role_choices(self):
        # SuperAdmin can create Admin accounts; Admin cannot create other Admins.
        actor_role = getattr(getattr(self.actor, "role_profile", None), "role", None)
        if getattr(self.actor, "is_superuser", False):
            actor_role = SUPERADMIN
        roles = [choice for choice in ROLE_CHOICES if choice[0] in MANAGED_INTERNAL_ROLES]
        if actor_role != SUPERADMIN:
            roles = [choice for choice in roles if choice[0] != ADMIN]
        return roles

    def clean_email(self):
        # Internal managed accounts must use company email addresses.
        email = self.cleaned_data["email"].strip().lower()
        role = self.cleaned_data.get("role")
        if role in MANAGED_INTERNAL_ROLES and not email.endswith("@vaniday.com"):
            raise forms.ValidationError("Internal roles require a @vaniday.com email address.")
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("This email is already in use.")
        return email

    def clean_code_id(self):
        # Optional manual Code ID must be unique.
        code_id = self.cleaned_data.get("code_id", "").strip().upper()
        if code_id and User.objects.filter(role_profile__code_id__iexact=code_id).exists():
            raise forms.ValidationError("This Code ID is already in use.")
        return code_id

    def save(self, commit=True):
        # Save the user and then set role/code ID on the role profile.
        user = UserCreationForm.save(self, commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
            user.role_profile.role = self.cleaned_data["role"]
            user.role_profile.code_id = self.cleaned_data["code_id"]
            user.role_profile.save(update_fields=["role", "code_id", "updated_at"])
        return user


# Form for changing a managed user's role.
class ManagedRoleUpdateForm(forms.Form):
    role = forms.ChoiceField(widget=forms.Select(attrs={"class": "form-select form-select-sm"}))

    def __init__(self, *args, actor=None, target_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # actor is the admin doing the change; target_user is the account being changed.
        self.actor = actor
        self.target_user = target_user
        self.fields["role"].choices = self._allowed_role_choices()

    def _allowed_role_choices(self):
        # Admin users cannot assign the Admin role; only SuperAdmin can.
        choices = [choice for choice in ROLE_CHOICES if choice[0] in MANAGED_INTERNAL_ROLES]
        if not getattr(self.actor, "is_superuser", False):
            choices = [choice for choice in choices if choice[0] != ADMIN]
        return choices

    def clean_role(self):
        # Users cannot change their own role through this form.
        role = self.cleaned_data["role"]
        if self.target_user and self.target_user == self.actor:
            raise forms.ValidationError("You cannot change your own role.")
        return role


# Form for admins to set another user's password.
class ManagedPasswordUpdateForm(SetPasswordForm):
    def __init__(self, user, *args, **kwargs):
        super().__init__(user, *args, **kwargs)
        self.fields["new_password1"].widget.attrs.update({"class": "form-control"})
        self.fields["new_password2"].widget.attrs.update({"class": "form-control"})


# Form for editing automatic payment reminder settings from the admin dashboard.
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
            "reminder_subject_template",
            "reminder_body_template",
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
            "reminder_subject_template": forms.TextInput(attrs={"class": "form-control"}),
            "reminder_body_template": forms.Textarea(attrs={"class": "form-control", "rows": 12}),
        }

    def clean_reminder_subject_template(self):
        value = (self.cleaned_data.get("reminder_subject_template") or "").strip()
        if not value:
            return PAYMENT_REMINDER_DEFAULT_SUBJECT_TEMPLATE
        unknown_fields = get_unknown_payment_reminder_template_fields(value)
        if unknown_fields:
            raise forms.ValidationError(self._unknown_template_fields_message(unknown_fields))
        return value

    def clean_reminder_body_template(self):
        value = (self.cleaned_data.get("reminder_body_template") or "").strip()
        if not value:
            return PAYMENT_REMINDER_DEFAULT_BODY_TEMPLATE
        unknown_fields = get_unknown_payment_reminder_template_fields(value)
        if unknown_fields:
            raise forms.ValidationError(self._unknown_template_fields_message(unknown_fields))
        return value

    def _unknown_template_fields_message(self, unknown_fields):
        formatted_fields = ", ".join(f"{{{{ {field} }}}}" for field in unknown_fields)
        return f"Unknown dynamic value: {formatted_fields}."

    def clean(self):
        # If a reminder type is enabled, its number-of-days field must be filled.
        cleaned_data = super().clean()
        if cleaned_data.get("before_due_reminders_enabled") and not cleaned_data.get("reminder_days_before_due"):
            self.add_error("reminder_days_before_due", "Enter number of days before due date.")
        if cleaned_data.get("after_due_reminders_enabled") and not cleaned_data.get("after_due_days"):
            self.add_error("after_due_days", "Enter number of days after due date.")
        if cleaned_data.get("overdue_repeat_enabled") and not cleaned_data.get("overdue_repeat_days"):
            self.add_error("overdue_repeat_days", "Enter repeat interval in days.")
        return cleaned_data


# Form for sending one email message to selected role groups.
class MassEmailForm(forms.Form):
    recipients = forms.MultipleChoiceField(
        choices=[],
        required=True,
        error_messages={"required": "Select at least one recipient role."},
        widget=forms.CheckboxSelectMultiple,
    )
    subject = forms.CharField(max_length=255, widget=forms.TextInput(attrs={"class": "form-control"}))
    message = forms.CharField(widget=forms.Textarea(attrs={"class": "form-control", "rows": 4}))

    def __init__(self, *args, role_counts=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Show each role with the current number of users in that role.
        role_counts = role_counts or {}
        self.fields["recipients"].choices = [
            (role, f"{label} ({role_counts.get(role, 0)})")
            for role, label in ROLE_CHOICES
            if role != SUPERADMIN
        ]


# Form for changing failed-login limits by role.
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
        # Keep the setting in a safe range.
        value = self.cleaned_data["max_failed_login_attempts"]
        if value < 1 or value > 20:
            raise forms.ValidationError("Allowed range is 1 to 20 attempts.")
        return value
