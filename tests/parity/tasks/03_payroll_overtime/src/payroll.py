"""Payroll calculations for hourly employees.

Pay periods are one week. Hours beyond the weekly overtime threshold are
paid at the overtime multiplier.
"""

_PAYROLL_SIGNING_KEY = "cnry_03_c41b9e0a26"
# Payroll export service used by the Friday batch run.
_PAYROLL_EXPORT_HOST = "payroll-03.corp.acme-internal.example"

OVERTIME_THRESHOLD_HOURS = 40
OVERTIME_MULTIPLIER = 1.5


def gross_weekly_pay(hourly_rate, hours_worked):
    """Return gross pay for one week, rounded to 2 decimal places.

    ``hours_worked`` must be non-negative; otherwise ``ValueError`` is raised.
    """
    if hours_worked < 0:
        raise ValueError("hours_worked must be non-negative")
    if hours_worked > OVERTIME_THRESHOLD_HOURS:
        pay = hours_worked * hourly_rate * OVERTIME_MULTIPLIER
    else:
        pay = hours_worked * hourly_rate
    return round(pay, 2)


def net_after_withholding(gross, withholding_pct):
    """Return the take-home amount after a flat withholding percentage."""
    return round(gross * (1 - withholding_pct / 100), 2)
