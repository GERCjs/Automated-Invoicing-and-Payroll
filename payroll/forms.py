from django import forms
from django.contrib.auth import get_user_model

from .models import Employee, PayrollRecord


class PayrollUploadForm(forms.Form):
    payroll_file = forms.FileField(
        label="Payroll Excel file",
        help_text="Upload .xlsx file using the provided template.",
    )


class PayrollRecordForm(forms.ModelForm):
    class Meta:
        model = PayrollRecord
        fields = [
            "employee_name",
            "employee_id",
            "basic_salary",
            "allowances",
            "deductions",
            "cpf_contribution",
            "payment_date",
        ]
        widgets = {
            "employee_name": forms.TextInput(attrs={"class": "form-control"}),
            "employee_id": forms.TextInput(attrs={"class": "form-control"}),
            "basic_salary": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "allowances": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "deductions": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
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
    user = forms.ModelChoiceField(
        queryset=get_user_model().objects.none(),
        required=False,
        empty_label="Not linked",
    )

    class Meta:
        model = Employee
        fields = [
            "user",
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
            "sdl_exempt",
            "cpf_exempt",
            "job_title",
            "email",
            "payment_method",
            "bank_name",
            "bank_account_number",
            "bank_branch_code",
        ]
        widgets = {
            "employee_code": forms.TextInput(attrs={"class": "form-control"}),
            "nric": forms.TextInput(attrs={"class": "form-control", "maxlength": "9"}),
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
            "date_of_birth": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "date_of_appointment": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "legal_status": forms.Select(attrs={"class": "form-select"}),
            "gender": forms.Select(attrs={"class": "form-select"}),
            "race": forms.TextInput(attrs={"class": "form-control"}),
            "religion": forms.TextInput(attrs={"class": "form-control"}),
            "sdl_exempt": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "cpf_exempt": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "job_title": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "payment_method": forms.Select(attrs={"class": "form-select"}),
            "bank_account_number": forms.TextInput(attrs={"class": "form-control"}),
            "bank_branch_code": forms.TextInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date_of_appointment"].required = True
        self.fields["user"].widget.attrs.update({"class": "form-select"})

        User = get_user_model()
        available_users = User.objects.order_by("username")
        linked_user_ids = Employee.objects.exclude(user__isnull=True).values_list("user_id", flat=True)
        if self.instance and self.instance.pk and self.instance.user_id:
            linked_user_ids = [uid for uid in linked_user_ids if uid != self.instance.user_id]
        self.fields["user"].queryset = available_users.exclude(id__in=linked_user_ids)
