from django.contrib import admin

from .models import Customer, Invoice, InvoiceItem, InvoiceSourceRow


class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("name", "email", "tax_number")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "invoice_number",
        "customer",
        "status",
        "issue_date",
        "due_date",
        "total_amount",
    )
    list_filter = ("status", "issue_date", "due_date")
    search_fields = ("invoice_number", "customer__name")
    inlines = [InvoiceItemInline]


@admin.register(InvoiceItem)
class InvoiceItemAdmin(admin.ModelAdmin):
    list_display = ("invoice", "description", "quantity", "unit_price", "line_total")
    list_filter = ("created_at",)
    search_fields = ("invoice__invoice_number", "description")


@admin.register(InvoiceSourceRow)
class InvoiceSourceRowAdmin(admin.ModelAdmin):
    list_display = (
        "order_id",
        "shop_title",
        "customer_name",
        "email",
        "service_name",
        "booked_date",
        "total_revenue",
    )
    list_filter = ("status", "order_status", "booked_date", "created_at")
    search_fields = ("order_id", "shop_title", "customer_name", "email", "service_name")
