from django import forms

from .models import PaymentBankDetails


class PaymentBankDetailsForm(forms.ModelForm):
    class Meta:
        model = PaymentBankDetails
        fields = (
            "account_name",
            "bank_name",
            "account_number",
            "paynow_id",
            "bic",
            "instructions",
        )
        widgets = {
            "account_name": forms.TextInput(attrs={"class": "form-control"}),
            "bank_name": forms.TextInput(attrs={"class": "form-control"}),
            "account_number": forms.TextInput(attrs={"class": "form-control"}),
            "paynow_id": forms.TextInput(attrs={"class": "form-control"}),
            "bic": forms.TextInput(attrs={"class": "form-control"}),
            "instructions": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }
