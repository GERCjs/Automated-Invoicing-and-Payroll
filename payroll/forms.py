from django import forms
from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db.models.fields.files import FieldFile
from PIL import Image as PilImage
from PIL import UnidentifiedImageError

from .models import Employee, PayrollRecord, PayrollSetup, PayrollTemplateSettings

EMPLOYEE_DEPARTMENT_CHOICES = [
    ("", "Select Department"),
    ("IT", "IT"),
    ("Finance", "Finance"),
    ("Logistics", "Logistics"),
    ("HR", "HR"),
    ("Sales", "Sales"),
    ("Operations", "Operations"),
]


def _format_file_size_limit(max_bytes: int) -> str:
    if max_bytes >= 1024 * 1024:
        return f"{max_bytes // (1024 * 1024)} MB"
    if max_bytes >= 1024:
        return f"{max_bytes // 1024} KB"
    return f"{max_bytes} bytes"


class PayrollUploadForm(forms.Form):
    payroll_file = forms.FileField(
        label="Payroll Excel file",
        help_text="Upload .xlsx file using the provided template.",
    )
    payment_date = forms.DateField(
        label="Payment Date",
        input_formats=["%d-%m-%Y", "%Y-%m-%d"],
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "DD-MM-YYYY"}),
        help_text="Format: DD-MM-YYYY. This date will be used for all saved payroll records in this upload.",
    )


class EmployeeUploadForm(forms.Form):
    employee_file = forms.FileField(
        label="Employee Excel file",
        help_text="Upload .xlsx file using the provided template.",
    )


class PayrollRecordForm(forms.ModelForm):
    DUPLICATE_ERROR = "A payroll record already exists for this employee and payment date."
    EMPLOYEE_PLACEHOLDER = [("", "Select an employee")]

    employee_id = forms.ChoiceField(
        choices=EMPLOYEE_PLACEHOLDER,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    payment_date = forms.DateField(
        input_formats=["%d-%m-%Y", "%Y-%m-%d"],
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "DD-MM-YYYY"}),
    )
    physical_products_commission = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=2,
        max_digits=12,
        initial=0,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    credit_commission = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=2,
        max_digits=12,
        initial=0,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    services_commission = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=2,
        max_digits=12,
        initial=0,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    loan_deduction = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=2,
        max_digits=12,
        initial=0,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    other_deductions = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=2,
        max_digits=12,
        initial=0,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    employer_cpf_contribution = forms.DecimalField(
        required=False,
        decimal_places=2,
        max_digits=12,
        initial=0,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "step": "0.01",
                "readonly": "readonly",
                "tabindex": "-1",
            }
        ),
    )

    class Meta:
        model = PayrollRecord
        fields = [
            "employee_name",
            "employee_id",
            "basic_salary",
            "cpf_contribution",
            "payment_date",
        ]
        widgets = {
            "employee_name": forms.TextInput(attrs={"class": "form-control", "readonly": "readonly"}),
            "basic_salary": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "cpf_contribution": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "step": "0.01",
                    "readonly": "readonly",
                    "tabindex": "-1",
                }
            ),
            "payment_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
        }

    def clean_employee_id(self):
        employee_id = (self.cleaned_data.get("employee_id") or "").strip()
        if not employee_id:
            return employee_id
        if not Employee.objects.filter(employee_code=employee_id).exists():
            raise forms.ValidationError("Employee ID not found in employee records.")
        return employee_id

    def clean(self):
        cleaned_data = super().clean()
        employee_id = (cleaned_data.get("employee_id") or "").strip()
        payment_date = cleaned_data.get("payment_date")

        if not employee_id or payment_date is None:
            return cleaned_data

        duplicate_qs = PayrollRecord.objects.filter(
            employee_id=employee_id,
            payment_date=payment_date,
        )
        if self.instance and self.instance.pk:
            duplicate_qs = duplicate_qs.exclude(pk=self.instance.pk)

        if duplicate_qs.exists():
            raise forms.ValidationError(self.DUPLICATE_ERROR)

        return cleaned_data

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        employee_choices = self.EMPLOYEE_PLACEHOLDER + [
            (
                employee.employee_code,
                f"{employee.employee_code} - {employee.first_name} {employee.last_name}".strip(),
            )
            for employee in Employee.objects.order_by("employee_code")
        ]
        self.fields["employee_id"].choices = employee_choices
        if self.instance and self.instance.pk:
            self.fields["physical_products_commission"].initial = (
                self.instance.physical_products_commission or 0
            )
            self.fields["credit_commission"].initial = self.instance.credit_commission or 0
            self.fields["services_commission"].initial = self.instance.services_commission or 0
            self.fields["loan_deduction"].initial = self.instance.loan_deduction or 0
            self.fields["other_deductions"].initial = self.instance.other_deductions or 0


class PayrollSetupForm(forms.ModelForm):
    DAY_OF_MONTH_CHOICES = [("", "Select day")] + [(day, f"{day:02d}") for day in range(1, 32)]

    employee = forms.ModelChoiceField(
        queryset=Employee.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    estimated_cpf_contribution = forms.DecimalField(
        required=False,
        decimal_places=2,
        max_digits=12,
        initial=0,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "step": "0.01",
                "readonly": "readonly",
                "tabindex": "-1",
            }
        ),
    )
    payment_day_of_month = forms.TypedChoiceField(
        required=False,
        choices=DAY_OF_MONTH_CHOICES,
        coerce=int,
        empty_value=None,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    class Meta:
        model = PayrollSetup
        fields = [
            "employee",
            "basic_salary",
            "physical_products_commission",
            "credit_commission",
            "services_commission",
            "loan_deduction",
            "other_deductions",
            "payment_date_type",
            "payment_day_of_month",
            "is_active",
        ]
        widgets = {
            "basic_salary": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "physical_products_commission": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "credit_commission": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "services_commission": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "loan_deduction": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "other_deductions": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "payment_date_type": forms.Select(attrs={"class": "form-select"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        available_employees = Employee.objects.order_by("employee_code")
        if self.instance and self.instance.pk:
            self.fields["employee"].queryset = available_employees
            self.initial["employee"] = self.instance.employee
        else:
            setup_employee_ids = PayrollSetup.objects.values_list("employee_id", flat=True)
            self.fields["employee"].queryset = available_employees.exclude(pk__in=setup_employee_ids)
        if not (self.instance and self.instance.pk):
            self.fields["basic_salary"].initial = "0.00"

    def clean(self):
        cleaned_data = super().clean()
        payment_date_type = cleaned_data.get("payment_date_type")
        payment_day_of_month = cleaned_data.get("payment_day_of_month")
        if payment_date_type == PayrollSetup.PAYMENT_DATE_SPECIFIC_DAY and payment_day_of_month is None:
            self.add_error("payment_day_of_month", "Enter the payment day of month for this setup.")
        elif payment_date_type != PayrollSetup.PAYMENT_DATE_SPECIFIC_DAY:
            cleaned_data["payment_day_of_month"] = None
        return cleaned_data


SINGAPORE_BANK_CHOICES = [
    ("", "Select Bank"),
    ("DBS Bank", "DBS Bank"),
    ("POSB", "POSB"),
    ("OCBC Bank", "OCBC Bank"),
    ("UOB", "UOB"),
    ("Standard Chartered Singapore", "Standard Chartered Singapore"),
    ("Citibank Singapore", "Citibank Singapore"),
    ("HSBC Singapore", "HSBC Singapore"),
    ("Maybank Singapore", "Maybank Singapore"),
    ("CIMB Singapore", "CIMB Singapore"),
    ("Bank of China Singapore", "Bank of China Singapore"),
    ("ICBC Singapore", "ICBC Singapore"),
    ("State Bank of India Singapore", "State Bank of India Singapore"),
    ("RHB Bank Singapore", "RHB Bank Singapore"),
    ("Bank of India Singapore", "Bank of India Singapore"),
    ("Bangkok Bank Singapore", "Bangkok Bank Singapore"),
    ("Mizuho Bank Singapore", "Mizuho Bank Singapore"),
    ("MUFG Bank Singapore", "MUFG Bank Singapore"),
    ("ANZ Singapore", "ANZ Singapore"),
    ("Deutsche Bank Singapore", "Deutsche Bank Singapore"),
]


class EmployeeForm(forms.ModelForm):
    bank_name = forms.ChoiceField(choices=SINGAPORE_BANK_CHOICES, required=False)
    department = forms.ChoiceField(choices=EMPLOYEE_DEPARTMENT_CHOICES, required=False)
    date_of_birth = forms.DateField(
        required=False,
        input_formats=["%d-%m-%Y"],
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "DD-MM-YYYY"}),
    )
    date_of_appointment = forms.DateField(
        required=True,
        input_formats=["%d-%m-%Y"],
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "DD-MM-YYYY"}),
    )

    class Meta:
        model = Employee
        fields = [
            "employee_code",
            "nric",
            "first_name",
            "last_name",
            "date_of_birth",
            "date_of_appointment",
            "legal_status",
            "gender",
            "race",
            "religion",
            "department",
            "leave_status",
            "sdl_exempt",
            "cpf_exempt",
            "job_title",
            "email",
            "payment_method",
            "status",
            "bank_name",
            "bank_account_number",
            "bank_branch_code",
        ]
        widgets = {
            "employee_code": forms.TextInput(attrs={"class": "form-control"}),
            "nric": forms.TextInput(attrs={"class": "form-control", "maxlength": "9"}),
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
            "legal_status": forms.Select(attrs={"class": "form-select"}),
            "gender": forms.Select(attrs={"class": "form-select"}),
            "race": forms.TextInput(attrs={"class": "form-control"}),
            "religion": forms.TextInput(attrs={"class": "form-control"}),
            "department": forms.Select(attrs={"class": "form-select"}),
            "leave_status": forms.Select(attrs={"class": "form-select"}),
            "sdl_exempt": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "cpf_exempt": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "job_title": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "payment_method": forms.Select(attrs={"class": "form-select"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "bank_account_number": forms.TextInput(attrs={"class": "form-control"}),
            "bank_branch_code": forms.TextInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._linked_user = getattr(self.instance, "user", None)
        required_on_create = [
            "employee_code",
            "email",
            "nric",
            "first_name",
            "last_name",
            "date_of_birth",
            "date_of_appointment",
            "legal_status",
            "gender",
            "race",
            "religion",
            "department",
            "job_title",
            "payment_method",
            "bank_name",
            "bank_account_number",
            "bank_branch_code",
        ]
        for field_name in required_on_create:
            self.fields[field_name].required = True
        self.fields["bank_name"].widget.attrs["class"] = "form-select"
        self.fields["department"].widget.attrs["class"] = "form-select"
        self.fields["leave_status"].required = False
        self.fields["status"].required = False
        if not (self.instance and self.instance.pk):
            self.fields["status"].initial = Employee.STATUS_ACTIVE
            self.fields["leave_status"].initial = Employee.LEAVE_STATUS_NONE
        current_department = (getattr(self.instance, "department", "") or "").strip()
        if current_department and current_department not in {
            value for value, _label in EMPLOYEE_DEPARTMENT_CHOICES if value
        }:
            self.fields["department"].choices = [
                *EMPLOYEE_DEPARTMENT_CHOICES,
                (current_department, current_department),
            ]
        if self.instance and self.instance.pk:
            if self.instance.date_of_birth:
                self.initial["date_of_birth"] = self.instance.date_of_birth.strftime("%d-%m-%Y")
            if self.instance.date_of_appointment:
                self.initial["date_of_appointment"] = self.instance.date_of_appointment.strftime("%d-%m-%Y")

    def clean(self):
        cleaned_data = super().clean()
        email = (cleaned_data.get("email") or "").strip()
        self._linked_user = None

        if not email:
            return cleaned_data

        User = get_user_model()
        matched_user = User.objects.filter(email__iexact=email).order_by("id").first()
        if matched_user is None:
            return cleaned_data

        existing_employee = Employee.objects.filter(user=matched_user)
        if self.instance and self.instance.pk:
            existing_employee = existing_employee.exclude(pk=self.instance.pk)
        existing_employee = existing_employee.first()
        if existing_employee is not None:
            self.add_error("email", f"This email is already linked to employee {existing_employee.employee_code}.")
            return cleaned_data

        self._linked_user = matched_user
        return cleaned_data

    def save(self, commit=True):
        employee = super().save(commit=False)
        employee.user = self._linked_user
        if commit:
            employee.save()
        return employee


class PayrollTemplateSettingsForm(forms.ModelForm):
    logo = forms.FileField(
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "form-control", "accept": ".png,.jpg,.jpeg"}),
    )

    def __init__(self, *args, **kwargs):
        self.stale_logo_missing = False
        super().__init__(*args, **kwargs)

    class Meta:
        model = PayrollTemplateSettings
        fields = [
            "company_display_name",
            "company_address",
            "company_email",
            "company_phone",
            "company_registration_number",
            "header_text",
            "footer_text",
            "logo",
            "logo_size",
            "logo_position",
        ]
        widgets = {
            "company_display_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Company name shown on payslip PDFs"}
            ),
            "company_address": forms.Textarea(
                attrs={"class": "form-control", "rows": 4, "placeholder": "Company address shown on payslip PDFs"}
            ),
            "company_email": forms.EmailInput(
                attrs={"class": "form-control", "placeholder": "hr@example.com"}
            ),
            "company_phone": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "+65 6000 0000"}
            ),
            "company_registration_number": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Company registration number"}
            ),
            "header_text": forms.Textarea(
                attrs={"class": "form-control", "rows": 2, "placeholder": "Optional subtitle text below the payslip title"}
            ),
            "footer_text": forms.Textarea(
                attrs={"class": "form-control", "rows": 3, "placeholder": "Optional footer note shown at the bottom of payslip PDFs"}
            ),
            "logo_size": forms.Select(attrs={"class": "form-select"}),
            "logo_position": forms.Select(attrs={"class": "form-select"}),
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

        max_bytes = int(getattr(settings, "PAYROLL_TEMPLATE_LOGO_MAX_UPLOAD_BYTES", 2097152))
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
