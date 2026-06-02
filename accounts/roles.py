# These constants are the role values stored in the database.
SUPERADMIN = "superadmin"
ADMIN = "admin"
FINANCE = "finance"
HR = "hr"
STAFF = "staff"
CUSTOMER = "customer"

# Django forms/models use this list to show role choices to users.
ROLE_CHOICES = [
    (SUPERADMIN, "SuperAdmin"),
    (ADMIN, "Admin"),
    (FINANCE, "Finance Officer"),
    (HR, "Payroll Officer"),
    (STAFF, "Staff"),
    (CUSTOMER, "Customer"),
]
