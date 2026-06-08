# Visible Fixes Summary

## Simple Overview

I focused on the website changes that users can actually see and test.
AS OF 9/6/2026 

The main fixes I added are:

- Block `0` dollar or negative invoice item prices.
- Stop overdue reminder emails from being sent multiple times on the same day.
- Add bank transfer payment details and payment reference numbers.
- Add role-based checks for approving bank transfer payments.
- Add mass email enable/disable protection.
- Add a resend verification email button.
- Add a filter for unverified accounts.

## What I Added Or Fixed


| Fix                              | Before                                                                                       | After                                                                                       |
| ---------------------------------- | ---------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `0` dollar invoice fix           | Users could create invoice items with unit price`0` or below.                                | The website now blocks unit price`0` or negative values.                                    |
| Reminder email duplicate fix     | Overdue invoice reminders could be sent multiple times for the same invoice on the same day. | The same overdue invoice reminder can only be sent once per day.                            |
| Bank transfer payment            | Customers did not have clear bank transfer payment details on invoices.                      | Customers can now see DBS bank transfer details, amount, and a generated payment reference. |
| Bank transfer approval checks    | Bank transfer payment approval needed stronger role protection.                              | Only approved internal users can confirm a bank transfer as paid.                           |
| Mass email enable/disable        | Mass email sending needed a clear protection when disabled.                                  | When mass email is disabled, no mass email should be sent.                                  |
| Resend verification email button | Resending verification emails was not clearly available as a website button.                 | A visible resend verification button was added for eligible unverified accounts.            |
| Unverified account filter        | Users had no easy way to find only unverified accounts.                                      | A new unverified account filter was added.                                                  |
