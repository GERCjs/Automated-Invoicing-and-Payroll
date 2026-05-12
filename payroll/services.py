from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from openpyxl import load_workbook


MONEY_PLACES = Decimal("0.01")

TEMPLATE_HEADERS = [
    "employee_code",
    "employee_name",
    "employee_age",
    "primary_work_location",
    "working_days",
    "no_pay_leave_days",
    "basic_salary",
    "physical_products_commission",
    "credit_commission",
    "services_commission",
    "loan_deduction",
    "other_deductions",
    "notes",
]


@dataclass
class CPFResult:
    employee_rate: Decimal
    employer_rate: Decimal
    employee_amount: Decimal
    employer_amount: Decimal


def as_decimal(value: Any, default: str = "0") -> Decimal:
    if value in (None, ""):
        return Decimal(default)
    return Decimal(str(value))


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)


def cpf_rates_for_age(age: int) -> tuple[Decimal, Decimal]:
    if age <= 55:
        return Decimal("20"), Decimal("17")
    if age <= 60:
        return Decimal("18"), Decimal("16")
    if age <= 65:
        return Decimal("12.5"), Decimal("12.5")
    if age <= 70:
        return Decimal("7.5"), Decimal("9")
    return Decimal("5"), Decimal("7.5")


def cpf_for_2026(monthly_wage: Decimal, age: int) -> CPFResult:
    employee_rate, employer_rate = cpf_rates_for_age(age)

    if monthly_wage <= Decimal("50"):
        employee_amount = Decimal("0")
        employer_amount = Decimal("0")
    elif monthly_wage <= Decimal("500"):
        employee_amount = Decimal("0")
        employer_amount = monthly_wage * (employer_rate / Decimal("100"))
    elif monthly_wage <= Decimal("750"):
        graduated_multiplier = employee_rate / Decimal("100")
        employee_amount = graduated_multiplier * (monthly_wage - Decimal("500"))
        total_amount = (monthly_wage * (employer_rate / Decimal("100"))) + employee_amount
        employer_amount = total_amount - employee_amount
    else:
        employee_amount = monthly_wage * (employee_rate / Decimal("100"))
        employer_amount = monthly_wage * (employer_rate / Decimal("100"))

    return CPFResult(
        employee_rate=employee_rate,
        employer_rate=employer_rate,
        employee_amount=money(employee_amount),
        employer_amount=money(employer_amount),
    )


def parse_payroll_excel(uploaded_file) -> list[dict[str, Any]]:
    workbook = load_workbook(uploaded_file, data_only=True)
    sheet = workbook.active
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        raise ValueError("Uploaded file is empty.")

    headers = [str(h).strip().lower() if h is not None else "" for h in rows[0]]
    required = set(TEMPLATE_HEADERS)
    missing = required - set(headers)
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")

    index_map = {name: headers.index(name) for name in required}
    parsed: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows[1:], start=2):
        if row is None or all(v in (None, "") for v in row):
            continue
        age = int(as_decimal(row[index_map["employee_age"]], "0"))
        basic_salary = as_decimal(row[index_map["basic_salary"]])
        physical_products_commission = as_decimal(row[index_map["physical_products_commission"]])
        credit_commission = as_decimal(row[index_map["credit_commission"]])
        services_commission = as_decimal(row[index_map["services_commission"]])
        loan_deduction = as_decimal(row[index_map["loan_deduction"]])
        other_deductions = as_decimal(row[index_map["other_deductions"]])

        total_earnings = basic_salary + physical_products_commission + credit_commission + services_commission
        cpf = cpf_for_2026(total_earnings, age)
        total_deductions = loan_deduction + other_deductions + cpf.employee_amount
        net_pay = total_earnings - total_deductions

        parsed.append(
            {
                "row_number": row_number,
                "employee_code": row[index_map["employee_code"]] or "",
                "employee_name": row[index_map["employee_name"]] or "",
                "employee_age": age,
                "primary_work_location": row[index_map["primary_work_location"]] or "",
                "working_days": as_decimal(row[index_map["working_days"]]),
                "no_pay_leave_days": as_decimal(row[index_map["no_pay_leave_days"]]),
                "basic_salary": money(basic_salary),
                "physical_products_commission": money(physical_products_commission),
                "credit_commission": money(credit_commission),
                "services_commission": money(services_commission),
                "loan_deduction": money(loan_deduction),
                "other_deductions": money(other_deductions),
                "notes": row[index_map["notes"]] or "",
                "total_earnings": money(total_earnings),
                "total_deductions": money(total_deductions),
                "net_pay": money(net_pay),
                "cpf_employee_rate": cpf.employee_rate,
                "cpf_employer_rate": cpf.employer_rate,
                "cpf_employee_amount": cpf.employee_amount,
                "cpf_employer_amount": cpf.employer_amount,
            }
        )
    return parsed


def default_template_row() -> list[Any]:
    return [
        "EMP001",
        "Alex Tan",
        34,
        "Raffles",
        27,
        0,
        3000,
        15.9,
        325,
        700,
        139.45,
        0,
        "Sample row",
    ]
