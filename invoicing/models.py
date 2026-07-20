import uuid

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q


def generate_public_view_token():
    return uuid.uuid4().hex


class Customer(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_INACTIVE = "inactive"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_INACTIVE, "Inactive"),
    ]

    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=30, blank=True)
    billing_address = models.TextField(blank=True)
    tax_number = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customers_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "customer"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["email"]),
        ]

    def __str__(self) -> str:
        return self.name


class Invoice(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_SENT = "sent"
    STATUS_VIEWED = "viewed"
    STATUS_PAID = "paid"
    STATUS_PARTIALLY_REFUNDED = "partially_refunded"
    STATUS_OVERDUE = "overdue"
    STATUS_REFUNDED = "refunded"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SENT, "Pending Payment"),
        (STATUS_VIEWED, "Viewed"),
        (STATUS_PAID, "Paid"),
        (STATUS_PARTIALLY_REFUNDED, "Partially Refunded"),
        (STATUS_OVERDUE, "Overdue"),
        (STATUS_REFUNDED, "Refunded"),
    ]

    invoice_number = models.CharField(max_length=50, unique=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="invoices")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    issue_date = models.DateField()
    due_date = models.DateField()
    currency = models.CharField(max_length=3, default="SGD")
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    public_view_token = models.CharField(max_length=64, db_index=True, default=generate_public_view_token)
    viewed_at = models.DateTimeField(null=True, blank=True)
    view_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "invoice"
        ordering = ["-issue_date", "-created_at"]
        indexes = [
            models.Index(fields=["invoice_number"]),
            models.Index(fields=["status"]),
            models.Index(fields=["issue_date"]),
            models.Index(fields=["due_date"]),
            models.Index(fields=["public_view_token"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(due_date__gte=F("issue_date")),
                name="invoice_due_on_or_after_issue",
            ),
            models.CheckConstraint(
                condition=Q(subtotal__gte=0),
                name="invoice_subtotal_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(tax_amount__gte=0),
                name="invoice_tax_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(total_amount__gte=0),
                name="invoice_total_non_negative",
            ),
        ]

    def __str__(self) -> str:
        return self.invoice_number


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="items")
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=1,
        validators=[MinValueValidator(0.01)],
    )
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    tax_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    line_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "invoice_item"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["invoice"]),
        ]

    def __str__(self) -> str:
        return f"{self.invoice.invoice_number} - {self.description}"


class InvoiceSourceRow(models.Model):
    seller_id = models.CharField(max_length=50, blank=True)
    shop_title = models.CharField(max_length=255, blank=True)
    order_id = models.CharField(max_length=50, blank=True, db_index=True)
    partner_type_name = models.CharField(max_length=100, blank=True)
    payment_method = models.CharField(max_length=100, blank=True)
    product_type = models.CharField(max_length=100, blank=True)
    customer_id = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=50, blank=True)
    order_status = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    customer_name = models.CharField(max_length=255, blank=True)
    contact_no = models.CharField(max_length=50, blank=True)
    qty = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    service_name = models.CharField(max_length=255, blank=True)
    booked_date = models.DateTimeField(null=True, blank=True)
    service_duration = models.PositiveIntegerField(null=True, blank=True)
    staff_id = models.CharField(max_length=50, blank=True)
    staff_name = models.CharField(max_length=255, blank=True)
    total_revenue = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    credit_card = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    shipping_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    reward_point = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    vaniday_commission = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    vaniday_share = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    cashback_fee = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    cashback_discount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    cashback_date = models.DateField(null=True, blank=True)
    salon_share = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    source_file_name = models.CharField(max_length=255, default="Vaniday Invoice Sample data_RP.csv")
    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "invoice_source_row"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["order_id"]),
            models.Index(fields=["email"]),
            models.Index(fields=["booked_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.order_id} - {self.service_name}"


class InvoiceTemplateSettings(models.Model):
    LOGO_SIZE_SMALL = "small"
    LOGO_SIZE_MEDIUM = "medium"
    LOGO_SIZE_LARGE = "large"
    LOGO_SIZE_CHOICES = [
        (LOGO_SIZE_SMALL, "Small"),
        (LOGO_SIZE_MEDIUM, "Medium"),
        (LOGO_SIZE_LARGE, "Large"),
    ]

    LOGO_POSITION_LEFT = "left"
    LOGO_POSITION_CENTRE = "centre"
    LOGO_POSITION_RIGHT = "right"
    LOGO_POSITION_CHOICES = [
        (LOGO_POSITION_LEFT, "Left"),
        (LOGO_POSITION_CENTRE, "Centre"),
        (LOGO_POSITION_RIGHT, "Right"),
    ]

    ADDRESS_POSITION_LEFT = "left"
    ADDRESS_POSITION_RIGHT = "right"
    ADDRESS_POSITION_CHOICES = [
        (ADDRESS_POSITION_LEFT, "Left"),
        (ADDRESS_POSITION_RIGHT, "Right"),
    ]

    company_display_name = models.CharField(max_length=255, blank=True, default="")
    company_address = models.TextField(blank=True, default="")
    company_email = models.EmailField(blank=True, default="")
    company_phone = models.CharField(max_length=50, blank=True, default="")
    company_registration_number = models.CharField(max_length=100, blank=True, default="")
    registered_office_text = models.TextField(blank=True, default="")
    default_payment_term_days = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(365)],
    )
    invoice_payment_notes = models.TextField(blank=True, default="")
    header_text = models.TextField(blank=True, default="")
    footer_text = models.TextField(blank=True, default="")
    logo = models.ImageField(
        upload_to="invoice_branding/logos/",
        blank=True,
        default="",
        validators=[FileExtensionValidator(allowed_extensions=["png", "jpg", "jpeg"])],
    )
    logo_size = models.CharField(
        max_length=10,
        choices=LOGO_SIZE_CHOICES,
        default=LOGO_SIZE_MEDIUM,
    )
    logo_position = models.CharField(
        max_length=10,
        choices=LOGO_POSITION_CHOICES,
        default=LOGO_POSITION_LEFT,
    )
    address_position = models.CharField(
        max_length=10,
        choices=ADDRESS_POSITION_CHOICES,
        default=ADDRESS_POSITION_LEFT,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoice_template_settings_updates",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "invoice_template_settings"
        verbose_name = "Invoice template settings"
        verbose_name_plural = "Invoice template settings"

    def __str__(self) -> str:
        return "Invoice template settings"

    def has_logo_file(self) -> bool:
        if not self.logo or not self.logo.name:
            return False
        try:
            return self.logo.storage.exists(self.logo.name)
        except (NotImplementedError, OSError, ValueError):
            return False

    @classmethod
    def load(cls):
        settings_obj, _ = cls.objects.get_or_create(pk=1)
        return settings_obj

    @classmethod
    def current(cls):
        return cls.objects.order_by("-updated_at", "-pk").first()
