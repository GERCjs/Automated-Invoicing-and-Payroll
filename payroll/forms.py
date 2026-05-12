from django import forms


class PayrollUploadForm(forms.Form):
    payroll_file = forms.FileField(
        label="Payroll Excel file",
        help_text="Upload .xlsx file using the provided template.",
    )
