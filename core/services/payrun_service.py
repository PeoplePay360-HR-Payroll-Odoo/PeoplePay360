import datetime
from decimal import Decimal
from typing import Dict, Any, List, Optional
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from core.models import (
    Employee,
    Contract,
    Payrun,
    Payslip,
)
from .payroll_engine import PayrollEngine, PayrollCalculationError


class PayrunWorkflowError(Exception):
    """Raised when an invalid state transition or blocking validation occurs during payrun workflow."""
    pass


class ValidationIssue:
    """Represents a pre-flight warning or error on a payrun or specific employee."""
    def __init__(self, level: str, message: str, employee: Optional[Employee] = None):
        self.level = level  # 'error' or 'warning'
        self.message = message
        self.employee = employee

    def to_dict(self) -> Dict[str, Any]:
        return {
            'level': self.level,
            'message': self.message,
            'employee_code': self.employee.code if self.employee else None,
            'employee_name': self.employee.full_name if self.employee else None,
        }

    def __repr__(self):
        emp_info = f" ({self.employee.code})" if self.employee else ""
        return f"[{self.level.upper()}]{emp_info} {self.message}"


class ValidationReport:
    """Encapsulates all pre-flight warnings and blocking errors for a payrun."""
    def __init__(self):
        self.issues: List[ValidationIssue] = []

    def add_error(self, message: str, employee: Optional[Employee] = None):
        self.issues.append(ValidationIssue('error', message, employee))

    def add_warning(self, message: str, employee: Optional[Employee] = None):
        self.issues.append(ValidationIssue('warning', message, employee))

    @property
    def is_valid(self) -> bool:
        """True if there are zero blocking errors (warnings are non-blocking)."""
        return not any(i.level == 'error' for i in self.issues)

    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.level == 'error']

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.level == 'warning']

    def summary(self) -> str:
        err_count = len(self.errors)
        warn_count = len(self.warnings)
        return f"{err_count} Error(s), {warn_count} Warning(s)"


class PayrunService:
    """
    Manages the lifecycle, pre-flight validations, batch computations,
    and state transitions of Payrun records:
    Draft -> Computed -> Validated -> Paid.
    """

    @classmethod
    def get_eligible_employees(cls, payrun: Payrun) -> List[Employee]:
        """
        Finds all active employees who have an active contract covering the payrun period
        assigned to the payrun's salary structure (or assigned to any active structure if structure is set).
        """
        contracts = Contract.objects.filter(
            state='active',
            employee__is_active=True,
            start_date__lte=payrun.end_date,
        ).filter(
            Q(end_date__isnull=True) | Q(end_date__gte=payrun.start_date)
        ).select_related('employee', 'salary_structure')

        if payrun.salary_structure:
            contracts = contracts.filter(salary_structure=payrun.salary_structure)

        # Ensure uniqueness of employees
        seen_ids = set()
        eligible = []
        for contract in contracts:
            if contract.employee_id not in seen_ids:
                seen_ids.add(contract.employee_id)
                eligible.append(contract.employee)

        return sorted(eligible, key=lambda e: e.code)

    @classmethod
    def validate_payrun_preflight(
        cls,
        payrun: Payrun,
        employees: Optional[List[Employee]] = None
    ) -> ValidationReport:
        """
        Runs comprehensive pre-flight checks on a payrun and its candidate employees.
        Checks:
        1. Payrun has a valid SalaryStructure with active rules.
        2. Date integrity (start_date <= end_date).
        3. Each employee has an active contract in the period.
        4. Banking Details Check: flags warnings for employees missing bank details (disbursement warning).
        5. Zero or negative salary check on already computed payslips.
        """
        report = ValidationReport()

        # 1. Structure check
        if not payrun.salary_structure:
            report.add_error("Payrun has no Salary Structure assigned.")
        elif not payrun.salary_structure.structure_rules.filter(rule__is_active=True).exists():
            report.add_error(f"Salary Structure '{payrun.salary_structure.code}' has no active rules.")

        # 2. Date integrity
        if payrun.start_date > payrun.end_date:
            report.add_error(f"Invalid period: start date ({payrun.start_date}) is after end date ({payrun.end_date}).")

        # 3. Employee checks
        if employees is None:
            employees = cls.get_eligible_employees(payrun)

        if not employees:
            report.add_warning("No eligible employees with active contracts found for this payrun period.")

        for emp in employees:
            # Check contract
            try:
                PayrollEngine.find_applicable_contract(emp, payrun.start_date, payrun.end_date)
            except PayrollCalculationError as e:
                report.add_error(str(e), employee=emp)

            # Check bank details (disbursement warning)
            if not emp.has_bank_details:
                report.add_warning(
                    f"Employee is missing banking details (Account/Bank/IFSC). Disbursement may fail.",
                    employee=emp
                )

        # 4. Computed payslips check (if payrun is already computed)
        for payslip in payrun.payslips.all():
            if payslip.net_wage <= Decimal('0.00'):
                report.add_warning(
                    f"Calculated Net Wage is ₹{payslip.net_wage} (Zero or Negative).",
                    employee=payslip.employee
                )

        return report

    @classmethod
    def compute_payrun(
        cls,
        payrun: Payrun,
        employee_ids: Optional[List[int]] = None
    ) -> Dict[str, Any]:
        """
        Executes batch payroll calculation for all eligible employees (or a selected subset).
        Creates/updates Payslips and PayslipLines, and sets payrun state to 'computed'.
        """
        if payrun.state in ('validated', 'paid'):
            raise PayrunWorkflowError(
                f"Cannot compute Payrun '{payrun.name}' in '{payrun.state}' state. Reset to draft first."
            )

        # Determine target employees
        if employee_ids:
            employees = list(Employee.objects.filter(id__in=employee_ids, is_active=True))
        elif payrun.payslips.exists():
            employees = [p.employee for p in payrun.payslips.select_related('employee').all() if p.employee.is_active]
        else:
            employees = cls.get_eligible_employees(payrun)

        if not employees:
            raise PayrunWorkflowError(
                f"No eligible employees found to compute for Payrun '{payrun.name}'."
            )

        computed_payslips = []
        errors = []

        with transaction.atomic():
            for emp in employees:
                try:
                    payslip = PayrollEngine.compute_payslip(payrun=payrun, employee=emp)
                    computed_payslips.append(payslip)
                except Exception as e:
                    errors.append({
                        'employee_code': emp.code,
                        'employee_name': emp.full_name,
                        'error': str(e)
                    })

            # Update payrun state
            payrun.state = 'computed'
            payrun.save()

        # Run post-computation validation
        validation_report = cls.validate_payrun_preflight(payrun, employees)

        return {
            'payrun': payrun,
            'computed_count': len(computed_payslips),
            'error_count': len(errors),
            'errors': errors,
            'total_gross': payrun.total_gross,
            'total_net': payrun.total_net,
            'validation_report': validation_report,
        }

    @classmethod
    def validate_payrun(cls, payrun: Payrun) -> ValidationReport:
        """
        Transitions payrun from 'computed' to 'validated'.
        Requires that all pre-flight blocking errors are resolved.
        """
        if payrun.state != 'computed':
            raise PayrunWorkflowError(
                f"Payrun must be in 'computed' state to validate. Current state: '{payrun.state}'."
            )

        report = cls.validate_payrun_preflight(payrun)
        if not report.is_valid:
            error_msgs = "; ".join([e.message for e in report.errors])
            raise PayrunWorkflowError(f"Cannot validate Payrun due to blocking errors: {error_msgs}")

        with transaction.atomic():
            payrun.state = 'validated'
            payrun.save()
            payrun.payslips.update(state='validated')

        return report

    @classmethod
    def mark_payrun_paid(
        cls,
        payrun: Payrun,
        payment_date: Optional[datetime.date] = None
    ) -> Payrun:
        """
        Transitions payrun from 'validated' to 'paid'.
        Locks the payrun, sets payment_date, and marks all payslips as 'paid'.
        """
        if payrun.state != 'validated':
            raise PayrunWorkflowError(
                f"Payrun must be in 'validated' state before marking as paid. Current state: '{payrun.state}'."
            )

        if not payment_date:
            payment_date = timezone.now().date()

        with transaction.atomic():
            payrun.state = 'paid'
            payrun.payment_date = payment_date
            payrun.save()
            payrun.payslips.update(state='paid')

        return payrun

    @classmethod
    def reset_to_draft(cls, payrun: Payrun) -> Payrun:
        """
        Resets a payrun back to 'draft' state, allowing adjustments and recalculation.
        Cannot reset a paid payrun unless explicitly intended.
        """
        if payrun.state == 'paid':
            raise PayrunWorkflowError("Cannot reset a 'paid' payrun back to draft for auditing integrity.")

        with transaction.atomic():
            payrun.state = 'draft'
            payrun.save()
            payrun.payslips.update(state='draft')

        return payrun

    @classmethod
    def create_payrun_with_employees(
        cls,
        name: str,
        salary_structure,
        start_date: datetime.date,
        end_date: datetime.date,
        employee_ids: List[int],
    ) -> Payrun:
        """
        Atomically creates a Payrun in draft state and initializes draft Payslips
        for only the explicitly selected employees.
        """
        with transaction.atomic():
            payrun = Payrun.objects.create(
                name=name,
                salary_structure=salary_structure,
                start_date=start_date,
                end_date=end_date,
                state='draft'
            )
            employees = Employee.objects.filter(id__in=employee_ids, is_active=True)
            period_days = (end_date - start_date).days + 1
            for emp in employees:
                # Find applicable contract
                contract = Contract.objects.filter(
                    employee=emp,
                    state='active',
                    start_date__lte=end_date,
                ).filter(
                    Q(end_date__isnull=True) | Q(end_date__gte=start_date)
                ).first()
                if not contract:
                    contract = emp.contracts.filter(state='active').first() or emp.contracts.first()

                structure = salary_structure or (contract.salary_structure if contract else None)

                if contract and structure:
                    Payslip.objects.create(
                        payrun=payrun,
                        employee=emp,
                        contract=contract,
                        salary_structure=structure,
                        period_start=start_date,
                        period_end=end_date,
                        worked_days=Decimal(str(period_days)),
                        basic_wage=contract.wage,
                        gross_wage=contract.wage,
                        net_wage=contract.wage,
                        state='draft'
                    )

            return payrun

