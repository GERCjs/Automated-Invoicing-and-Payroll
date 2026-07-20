from django import forms
from django.db.models import Sum
from django.utils import timezone

from .models import PaymentBankDetails, PaymentRecord, PaymentRefund


class BankTransferNoticeForm(forms.Form):
    manual_customer_amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=0,
        label="Transferred amount",
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    manual_customer_transfer_date = forms.DateField(
        label="Transfer date",
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    manual_customer_bank_reference = forms.CharField(
        max_length=100,
        label="Bank reference number",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    manual_customer_notes = forms.CharField(
        required=False,
        label="Notes",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )
    manual_customer_proof = forms.FileField(
        required=False,
        label="Proof of payment",
        widget=forms.ClearableFileInput(attrs={"class": "form-control"}),
    )

    def __init__(self, *args, payment_record: PaymentRecord | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.payment_record = payment_record
        if payment_record is not None and not self.is_bound:
            self.fields["manual_customer_amount"].initial = (
                payment_record.manual_customer_amount or payment_record.amount
            )
            self.fields["manual_customer_transfer_date"].initial = (
                payment_record.manual_customer_transfer_date or timezone.localdate()
            )
            self.fields["manual_customer_bank_reference"].initial = (
                payment_record.manual_customer_bank_reference
            )
            self.fields["manual_customer_notes"].initial = payment_record.manual_customer_notes

    def clean_manual_customer_transfer_date(self):
        transfer_date = self.cleaned_data["manual_customer_transfer_date"]
        if transfer_date > timezone.localdate():
            raise forms.ValidationError("Transfer date cannot be in the future.")
        return transfer_date

    def clean_manual_customer_bank_reference(self):
        bank_reference = self.cleaned_data["manual_customer_bank_reference"].strip()
        if not bank_reference:
            raise forms.ValidationError("Bank reference number is required.")
        return bank_reference

    def clean(self):
        cleaned_data = super().clean()
        payment_record = self.payment_record
        transferred_amount = cleaned_data.get("manual_customer_amount")
        if payment_record is not None and transferred_amount is not None:
            if transferred_amount != payment_record.amount:
                raise forms.ValidationError(
                    f"Transferred amount must match the invoice payment amount ({payment_record.currency} {payment_record.amount})."
                )
        return cleaned_data


class BankTransferConfirmationForm(forms.Form):
    manual_received_amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=0,
        label="Amount received",
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    manual_received_date = forms.DateField(
        label="Received date",
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    manual_bank_reference = forms.CharField(
        max_length=100,
        label="Bank reference",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    manual_confirmation_notes = forms.CharField(
        required=False,
        label="Confirmation notes",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )

    def __init__(self, *args, payment_record: PaymentRecord | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.payment_record = payment_record
        if payment_record is not None and not self.is_bound:
            self.fields["manual_received_amount"].initial = (
                payment_record.manual_customer_amount or payment_record.amount
            )
            self.fields["manual_received_date"].initial = (
                payment_record.manual_customer_transfer_date or timezone.localdate()
            )
            self.fields["manual_bank_reference"].initial = payment_record.manual_customer_bank_reference

    def clean_manual_received_date(self):
        received_date = self.cleaned_data["manual_received_date"]
        if received_date > timezone.localdate():
            raise forms.ValidationError("Received date cannot be in the future.")
        return received_date

    def clean_manual_bank_reference(self):
        bank_reference = self.cleaned_data["manual_bank_reference"].strip()
        if not bank_reference:
            raise forms.ValidationError("Bank reference is required.")
        return bank_reference

    def clean(self):
        cleaned_data = super().clean()
        payment_record = self.payment_record
        received_amount = cleaned_data.get("manual_received_amount")
        if payment_record is not None and received_amount is not None:
            if received_amount != payment_record.amount:
                raise forms.ValidationError(
                    f"Amount received must match the invoice payment amount ({payment_record.currency} {payment_record.amount})."
                )
        return cleaned_data


class PaymentRefundForm(forms.Form):
    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=0,
        label="Refund amount",
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    customer_message = forms.CharField(
        label="Message to customer",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 4}),
    )
    bank_reference = forms.CharField(
        required=False,
        max_length=100,
        label="Bank refund reference",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )

    def __init__(self, *args, payment_record: PaymentRecord | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.payment_record = payment_record
        if payment_record is None:
            self.remaining_refundable_amount = 0
            return

        refunded_total = (
            PaymentRefund.objects.filter(
                payment_record=payment_record,
                status=PaymentRefund.STATUS_SUCCEEDED,
            ).aggregate(total=Sum("amount"))["total"]
            or 0
        )
        self.refunded_total = refunded_total
        self.remaining_refundable_amount = payment_record.amount - refunded_total
        if not self.is_bound:
            self.fields["amount"].initial = self.remaining_refundable_amount
        if payment_record.provider != PaymentRecord.PROVIDER_MANUAL:
            self.fields["bank_reference"].widget = forms.HiddenInput()

    def clean_customer_message(self):
        message = (self.cleaned_data["customer_message"] or "").strip()
        if not message:
            raise forms.ValidationError("Message to customer is required.")
        return message

    def clean_bank_reference(self):
        bank_reference = (self.cleaned_data.get("bank_reference") or "").strip()
        if (
            self.payment_record is not None
            and self.payment_record.provider == PaymentRecord.PROVIDER_MANUAL
            and not bank_reference
        ):
            raise forms.ValidationError("Bank refund reference is required for bank-transfer refunds.")
        return bank_reference

    def clean(self):
        cleaned_data = super().clean()
        amount = cleaned_data.get("amount")
        if self.payment_record is None or amount is None:
            return cleaned_data
        if amount <= 0:
            self.add_error("amount", "Refund amount must be greater than zero.")
        if amount > self.remaining_refundable_amount:
            self.add_error(
                "amount",
                (
                    "Refund amount cannot exceed the remaining refundable amount "
                    f"({self.payment_record.currency} {self.remaining_refundable_amount})."
                ),
            )
        return cleaned_data


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
