from django import forms
from django.forms import inlineformset_factory
from django.core.validators import FileExtensionValidator

from .models import Customer, Invoice, InvoiceItem


class InvoiceForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ["customer", "issue_date", "due_date", "currency", "notes"]
        widgets = {
            "issue_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "due_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "currency": forms.TextInput(attrs={"class": "form-control", "maxlength": 3}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "customer": forms.Select(attrs={"class": "form-select"}),
        }


class InvoiceItemForm(forms.ModelForm):
    class Meta:
        model = InvoiceItem
        fields = ["description", "quantity", "unit_price", "tax_rate"]
        labels = {
            "tax_rate": "GST %",
        }
        widgets = {
            "description": forms.TextInput(attrs={"class": "form-control"}),
            "quantity": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0.01"}),
            "unit_price": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0.01"}),
            "tax_rate": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0", "max": "100"}),
        }

    def clean_unit_price(self):
        unit_price = self.cleaned_data.get("unit_price")
        if unit_price is not None and unit_price <= 0:
            raise forms.ValidationError("Unit price must be greater than 0.")
        return unit_price


InvoiceItemFormSet = inlineformset_factory(
    parent_model=Invoice,
    model=InvoiceItem,
    form=InvoiceItemForm,
    extra=1,
    min_num=1,
    validate_min=True,
    can_delete=True,
)


class InvoiceCsvUploadForm(forms.Form):
    csv_file = forms.FileField(
        label="Vaniday Invoice CSV File",
        help_text="Upload a CSV file exported from Vaniday invoice source data.",
        validators=[FileExtensionValidator(allowed_extensions=["csv"])],
        widget=forms.ClearableFileInput(attrs={"class": "form-control"}),
    )


class CustomerCreateForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ["name", "email", "phone", "billing_address", "tax_number", "status"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Customer name"}),
            "email": forms.EmailInput(attrs={"class": "form-control", "placeholder": "billing@example.com"}),
            "phone": forms.TextInput(attrs={"class": "form-control", "placeholder": "+65 ..."}),
            "billing_address": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "tax_number": forms.TextInput(attrs={"class": "form-control", "placeholder": "Optional"}),
            "status": forms.Select(attrs={"class": "form-select"}),
        }
