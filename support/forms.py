from django import forms
from django.contrib.auth import get_user_model

from accounts.roles import ADMIN, CUSTOMER, FINANCE, HR, STAFF, SUPERADMIN
from .models import SupportTicket

User = get_user_model()


class SupportTicketCreateForm(forms.ModelForm):
    class Meta:
        model = SupportTicket
        fields = ("category", "subject", "related_reference", "message")
        widgets = {
            "category": forms.Select(attrs={"class": "form-select"}),
            "subject": forms.TextInput(attrs={"class": "form-control"}),
            "related_reference": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Invoice, payment, or payroll reference"}
            ),
            "message": forms.Textarea(attrs={"class": "form-control", "rows": 5}),
        }

    def __init__(self, *args, actor_role=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].choices = self._category_choices_for(actor_role)

    def _category_choices_for(self, actor_role):
        if actor_role == CUSTOMER:
            allowed = {
                SupportTicket.CATEGORY_INVOICE,
                SupportTicket.CATEGORY_PAYMENT,
                SupportTicket.CATEGORY_ACCOUNT,
                SupportTicket.CATEGORY_OTHER,
            }
        elif actor_role == STAFF:
            allowed = {
                SupportTicket.CATEGORY_PAYROLL,
                SupportTicket.CATEGORY_ACCOUNT,
                SupportTicket.CATEGORY_OTHER,
            }
        else:
            allowed = {choice[0] for choice in SupportTicket.CATEGORY_CHOICES}
        return [choice for choice in SupportTicket.CATEGORY_CHOICES if choice[0] in allowed]


class SupportTicketUpdateForm(forms.ModelForm):
    class Meta:
        model = SupportTicket
        fields = ("status", "priority", "assigned_to", "resolution_note")
        widgets = {
            "status": forms.Select(attrs={"class": "form-select"}),
            "priority": forms.Select(attrs={"class": "form-select"}),
            "assigned_to": forms.Select(attrs={"class": "form-select"}),
            "resolution_note": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        }

    def __init__(self, *args, actor_role=None, **kwargs):
        super().__init__(*args, **kwargs)
        internal_roles = [SUPERADMIN, ADMIN, FINANCE, HR]
        self.fields["assigned_to"].queryset = (
            User.objects.select_related("role_profile")
            .filter(role_profile__role__in=internal_roles)
            .order_by("username")
        )
        if actor_role not in {SUPERADMIN, ADMIN}:
            self.fields["assigned_to"].disabled = True
