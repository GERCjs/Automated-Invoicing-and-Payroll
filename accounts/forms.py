from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.forms import UserCreationForm

from notifications.models import PaymentReminderSettings

from .roles import ADMIN, FINANCE, HR, ROLE_CHOICES, STAFF, SUPERADMIN

User = get_user_model()

MANAGED_INTERNAL_ROLES = {ADMIN, FINANCE, HR, STAFF}


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Username",
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
        self.fields["password1"].widget.attrs.update({"class": "form-control", "placeholder": "Password"})
        self.fields["password2"].widget.attrs.update(
            {"class": "form-control", "placeholder": "Confirm Password"}
        )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if not email.endswith("@vaniday.com"):
            raise forms.ValidationError("Staff registration requires a @vaniday.com email address.")
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("This email is already in use.")
        return email


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
    role = forms.ChoiceField(
        choices=ROLE_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    class Meta(RegistrationForm.Meta):
        fields = ("username", "email", "role", "password1", "password2")

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

    def save(self, commit=True):
        user = UserCreationForm.save(self, commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
            user.role_profile.role = self.cleaned_data["role"]
            user.role_profile.save(update_fields=["role", "updated_at"])
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
            "reminder_days_before_due",
            "overdue_reminders_enabled",
            "overdue_repeat_days",
            "mass_email_enabled",
        )
        widgets = {
            "reminder_days_before_due": forms.NumberInput(attrs={"class": "form-control", "min": "0"}),
            "overdue_repeat_days": forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
            "overdue_reminders_enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "mass_email_enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


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
