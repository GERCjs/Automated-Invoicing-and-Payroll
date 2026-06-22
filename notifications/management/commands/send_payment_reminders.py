from django.core.management.base import BaseCommand

from core.audit import log_event
from notifications.services import run_payment_reminder_check


class Command(BaseCommand):
    help = "Send payment reminder emails for invoices matching the current reminder settings."

    def add_arguments(self, parser):
        parser.add_argument(
            "--base-url",
            default="",
            help="Base URL used in public invoice links, for example http://127.0.0.1:8000.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Create simulated reminder logs without sending emails.",
        )

    def handle(self, *args, **options):
        simulate = bool(options["dry_run"])
        summary = run_payment_reminder_check(
            base_url=options["base_url"].rstrip("/"),
            simulate=simulate,
        )
        log_event(
            action="admin.payment_reminders.run_check",
            target_type="payment_reminder_check",
            metadata={**summary, "source": "scheduled_command"},
        )
        mode = "simulation" if simulate else "send"
        skipped_count = summary["skipped_already_logged_today"] + summary.get("skipped_not_due", 0)
        self.stdout.write(
            self.style.SUCCESS(
                (
                    f"Reminder {mode} complete. Matched: {summary['checked_invoices']}, "
                    f"processed: {summary['processed']}, sent: {summary['sent']}, "
                    f"simulated: {summary['simulated']}, failed: {summary['failed']}, "
                    f"skipped: {skipped_count}."
                )
            )
        )
