from django.contrib import admin

from .models import ImportJob, ImportRowError


class ImportRowErrorInline(admin.TabularInline):
    model = ImportRowError
    extra = 0
    readonly_fields = ("row_number", "field_name", "error_message", "raw_data", "created_at")


@admin.register(ImportJob)
class ImportJobAdmin(admin.ModelAdmin):
    list_display = (
        "module",
        "source_file_name",
        "status",
        "total_rows",
        "valid_rows",
        "invalid_rows",
        "saved_rows",
        "created_at",
    )
    list_filter = ("module", "status", "created_at")
    search_fields = ("source_file_name",)
    inlines = [ImportRowErrorInline]


@admin.register(ImportRowError)
class ImportRowErrorAdmin(admin.ModelAdmin):
    list_display = ("import_job", "row_number", "field_name", "created_at")
    list_filter = ("import_job__module", "created_at")
    search_fields = ("field_name", "error_message")
