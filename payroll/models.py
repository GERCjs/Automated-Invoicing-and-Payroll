from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q


class Employee(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_INACTIVE = "inactive"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_INACTIVE, "Inactive"),
    ]
    LEAVE_STATUS_NONE = "none"
    LEAVE_STATUS_ON_LEAVE = "on_leave"
    LEAVE_STATUS_CHOICES = [
        (LEAVE_STATUS_NONE, "Available"),
        (LEAVE_STATUS_ON_LEAVE, "On Leave"),
    ]
    LEGAL_STATUS_CITIZEN = "citizen"
    LEGAL_STATUS_PR = "pr"
    LEGAL_STATUS_WP = "work_permit"
    LEGAL_STATUS_EP = "employment_pass"
    LEGAL_STATUS_SP = "s_pass"
    LEGAL_STATUS_CHOICES = [
        (LEGAL_STATUS_CITIZEN, "Singapore Citizen"),
        (LEGAL_STATUS_PR, "Permanent Resident"),
        (LEGAL_STATUS_WP, "Work Permit"),
        (LEGAL_STATUS_EP, "Employment Pass"),
        (LEGAL_STATUS_SP, "S Pass"),
    ]
    GENDER_MALE = "male"
    GENDER_FEMALE = "female"
    GENDER_CHOICES = [
        (GENDER_MALE, "Male"),
        (GENDER_FEMALE, "Female"),
    ]
    PAYMENT_METHOD_CASH = "cash"
    PAYMENT_METHOD_CHEQUE = "cheque"
    PAYMENT_METHOD_GIRO = "giro"
    PAYMENT_METHOD_CHOICES = [
        (PAYMENT_METHOD_CASH, "Cash"),
        (PAYMENT_METHOD_CHEQUE, "Cheque"),
        (PAYMENT_METHOD_GIRO, "GIRO"),
    ]
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employee_profile",
    )
    employee_code = models.CharField(max_length=50, unique=True)
    nric = models.CharField(max_length=20, blank=True)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    date_of_birth = models.DateField(null=True, blank=True)
    date_of_appointment = models.DateField(null=True, blank=True)
    legal_status = models.CharField(max_length=30, choices=LEGAL_STATUS_CHOICES, blank=True)
    gender = models.CharField(max_length=20, choices=GENDER_CHOICES, blank=True)
    race = models.CharField(max_length=60, blank=True)
    religion = models.CharField(max_length=60, blank=True)
    sdl_exempt = models.BooleanField(default=False)
    cpf_exempt = models.BooleanField(default=False)
    job_title = models.CharField(max_length=150, blank=True)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, blank=True)
    bank_name = models.CharField(max_length=120, blank=True)
    bank_account_number = models.CharField(max_length=50, blank=True)
    bank_branch_code = models.CharField(max_length=30, blank=True)
    email = models.EmailField(unique=True)
    department = models.CharField(max_length=100, blank=True)
    position = models.CharField(max_length=100, blank=True)
    hire_date = models.DateField()
    base_salary = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    leave_status = models.CharField(max_length=20, choices=LEAVE_STATUS_CHOICES, default=LEAVE_STATUS_NONE)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employees_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "employee"
        ordering = ["employee_code"]
        indexes = [
            models.Index(fields=["employee_code"]),
            models.Index(fields=["status"]),
            models.Index(fields=["email"]),
        ]

    def __str__(self) -> str:
        return f"{self.employee_code} - {self.first_name} {self.last_name}"


class PayrollBatch(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_VALIDATED = "validated"
    STATUS_PROCESSED = "processed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_VALIDATED, "Validated"),
        (STATUS_PROCESSED, "Processed"),
        (STATUS_FAILED, "Failed"),
    ]

    batch_reference = models.CharField(max_length=50, unique=True)
    period_start = models.DateField()
    period_end = models.DateField()
    payout_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payroll_batches_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payroll_details"
        ordering = ["-period_start", "-created_at"]
        indexes = [
            models.Index(fields=["batch_reference"]),
            models.Index(fields=["status"]),
            models.Index(fields=["payout_date"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(period_end__gte=F("period_start")),
                name="payroll_batch_period_end_after_start",
            ),
        ]

    def __str__(self) -> str:
        return self.batch_reference


class PayrollEntry(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
    ]

    batch = models.ForeignKey(PayrollBatch, on_delete=models.CASCADE, related_name="entries")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="payroll_entries")
    gross_pay = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    allowances = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    deductions = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    tax_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    net_pay = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payroll_entries_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payroll"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["batch", "employee"], name="unique_batch_employee"),
        ]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["batch", "employee"]),
        ]

    def __str__(self) -> str:
        return f"{self.batch.batch_reference} - {self.employee.employee_code}"


class PayslipRecord(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_ISSUED = "issued"
    STATUS_SENT = "sent"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_ISSUED, "Issued"),
        (STATUS_SENT, "Sent"),
    ]

    payroll_entry = models.OneToOneField(
        PayrollEntry,
        on_delete=models.CASCADE,
        related_name="payslip",
    )
    payslip_number = models.CharField(max_length=50, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    issued_at = models.DateTimeField(null=True, blank=True)
    file_path = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payslips_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "legacy_payslip_record"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["payslip_number"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return self.payslip_number


class PayrollRecord(models.Model):
    employee_name = models.CharField(max_length=200)
    employee_id = models.CharField(max_length=50, db_index=True)
    basic_salary = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    allowances = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    physical_products_commission = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    credit_commission = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    services_commission = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    deductions = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    loan_deduction = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    other_deductions = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    cpf_contribution = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    net_salary = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    payment_date = models.DateField()
    nric = models.CharField(max_length=9, blank=True, db_column="NRIC")
    cpf_exempted = models.BooleanField(null=True, blank=True, db_column="cpf_exempted")
    sdl_exempted = models.BooleanField(null=True, blank=True, db_column="sdl_exempted")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payroll_records_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payslip_record"
        ordering = ["-payment_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee_id", "payment_date"],
                name="unique_payroll_employee_payment_date",
            ),
        ]
        indexes = [
            models.Index(fields=["payment_date"]),
            models.Index(fields=["employee_id", "payment_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.employee_id} - {self.employee_name}"


class PayrollSetup(models.Model):
    PAYMENT_DATE_LAST_DAY = "last_day"
    PAYMENT_DATE_SPECIFIC_DAY = "specific_day"
    PAYMENT_DATE_CHOICES = [
        (PAYMENT_DATE_LAST_DAY, "Last day of month"),
        (PAYMENT_DATE_SPECIFIC_DAY, "Specific day of month"),
    ]

    employee = models.OneToOneField(Employee, on_delete=models.CASCADE, related_name="payroll_setup")
    basic_salary = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    physical_products_commission = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    credit_commission = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    services_commission = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    loan_deduction = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    other_deductions = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    payment_date_type = models.CharField(
        max_length=20,
        choices=PAYMENT_DATE_CHOICES,
        default=PAYMENT_DATE_LAST_DAY,
    )
    payment_day_of_month = models.PositiveSmallIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payroll_setups_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payroll_setups_updated",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payroll_setup"
        ordering = ["employee__employee_code"]
        indexes = [
            models.Index(fields=["payment_date_type"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self) -> str:
        return f"Payroll setup - {self.employee.employee_code}"


class PayrollTemplateSettings(models.Model):
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

    company_display_name = models.CharField(max_length=255, blank=True, default="")
    company_address = models.TextField(blank=True, default="")
    company_email = models.EmailField(blank=True, default="")
    company_phone = models.CharField(max_length=50, blank=True, default="")
    company_registration_number = models.CharField(max_length=100, blank=True, default="")
    header_text = models.TextField(blank=True, default="")
    footer_text = models.TextField(blank=True, default="")
    logo = models.ImageField(
        upload_to="payroll_branding/logos/",
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
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payroll_template_settings_updates",
        db_constraint=False,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payroll_template_settings"
        verbose_name = "Payroll template settings"
        verbose_name_plural = "Payroll template settings"

    def __str__(self) -> str:
        return "Payroll template settings"

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
