from django import forms
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db.models.fields.files import FieldFile
from django.forms import inlineformset_factory
from PIL import Image as PilImage
from PIL import UnidentifiedImageError

from .models import Customer, Invoice, InvoiceItem, InvoiceTemplateSettings


def _format_file_size_limit(max_bytes: int) -> str:
    if max_bytes >= 1024 * 1024:
        return f"{max_bytes // (1024 * 1024)} MB"
    if max_bytes >= 1024:
        return f"{max_bytes // 1024} KB"
    return f"{max_bytes} bytes"


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
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk is None:
            self.initial.pop("quantity", None)
            self.initial.pop("tax_rate", None)
            self.fields["quantity"].initial = None
            self.fields["tax_rate"].initial = None

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
        label="Upload Invoice File",
        help_text="Choose a Vaniday CSV or Excel (.xlsx) invoice source file.",
        widget=forms.ClearableFileInput(attrs={"class": "form-control", "accept": ".csv,.xlsx"}),
    )

    def clean_csv_file(self):
        uploaded_file = self.cleaned_data["csv_file"]
        file_name = (uploaded_file.name or "").strip().lower()
        if not (file_name.endswith(".csv") or file_name.endswith(".xlsx")):
            raise forms.ValidationError("Upload a CSV or Excel (.xlsx) file.")
        max_bytes = int(getattr(settings, "INVOICE_IMPORT_MAX_UPLOAD_BYTES", 2097152))
        if uploaded_file.size > max_bytes:
            raise forms.ValidationError(
                f"Upload exceeds the maximum file size of {max_bytes // (1024 * 1024)} MB."
            )
        return uploaded_file


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


class InvoiceTemplateSettingsForm(forms.ModelForm):
    logo = forms.FileField(
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "form-control", "accept": ".png,.jpg,.jpeg"}),
    )

    def __init__(self, *args, **kwargs):
        self.stale_logo_missing = False
        super().__init__(*args, **kwargs)

    class Meta:
        model = InvoiceTemplateSettings
        fields = [
            "company_display_name",
            "company_address",
            "company_email",
            "company_phone",
            "company_registration_number",
            "registered_office_text",
            "default_payment_term_days",
            "invoice_payment_notes",
            "header_text",
            "footer_text",
            "logo",
            "logo_size",
            "logo_position",
            "address_position",
        ]
        widgets = {
            "company_display_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Company name shown on invoice PDFs"}
            ),
            "company_address": forms.Textarea(
                attrs={"class": "form-control", "rows": 4, "placeholder": "Company address shown on invoice PDFs"}
            ),
            "company_email": forms.EmailInput(
                attrs={"class": "form-control", "placeholder": "finance@example.com"}
            ),
            "company_phone": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "+65 6000 0000"}
            ),
            "company_registration_number": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Company registration number"}
            ),
            "registered_office_text": forms.Textarea(
                attrs={"class": "form-control", "rows": 3, "placeholder": "Registered office text shown on invoice PDFs"}
            ),
            "default_payment_term_days": forms.NumberInput(
                attrs={"class": "form-control", "min": "1", "max": "365", "step": "1"}
            ),
            "invoice_payment_notes": forms.Textarea(
                attrs={"class": "form-control", "rows": 4, "placeholder": "Payment notes shown on invoice PDFs"}
            ),
            "header_text": forms.Textarea(
                attrs={"class": "form-control", "rows": 2, "placeholder": "Optional header text"}
            ),
            "footer_text": forms.Textarea(
                attrs={"class": "form-control", "rows": 2, "placeholder": "Optional footer text"}
            ),
            "logo_size": forms.Select(attrs={"class": "form-select"}),
            "logo_position": forms.Select(attrs={"class": "form-select"}),
            "address_position": forms.Select(attrs={"class": "form-select"}),
        }

    def clean_logo(self):
        logo = self.cleaned_data.get("logo")
        if logo is False or not logo:
            return logo

        if isinstance(logo, FieldFile):
            if not logo.name:
                return logo
            try:
                logo_exists = logo.storage.exists(logo.name)
            except (NotImplementedError, OSError, ValueError):
                logo_exists = False
            if not logo_exists:
                self.stale_logo_missing = True
                return False
            return logo

        if not isinstance(logo, UploadedFile):
            return logo

        max_bytes = int(getattr(settings, "INVOICE_TEMPLATE_LOGO_MAX_UPLOAD_BYTES", 2097152))
        if logo.size > max_bytes:
            raise forms.ValidationError(f"Logo exceeds the maximum file size of {_format_file_size_limit(max_bytes)}.")

        file_name = (getattr(logo, "name", "") or "").strip().lower()
        if not file_name.endswith((".png", ".jpg", ".jpeg")):
            raise forms.ValidationError("Upload a PNG or JPEG image.")

        try:
            image = PilImage.open(logo)
            image_format = (image.format or "").upper()
            image.verify()
        except (UnidentifiedImageError, OSError, ValueError):
            raise forms.ValidationError("Upload a PNG or JPEG image.")
        finally:
            logo.seek(0)

        if image_format not in {"PNG", "JPEG"}:
            raise forms.ValidationError("Upload a PNG or JPEG image.")

        return logo
