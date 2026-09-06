import datetime
from decimal import Decimal
from typing import Dict, Any, List, Optional
from django.db import transaction
from django.utils import timezone
from core.models import (
    Employee,
    LeaveType,
    LeaveRequest,
    LeaveAllocation,
)


class LeaveValidationError(Exception):
    """Raised when leave application fails validation (insufficient balance, overlap, etc.)."""
    pass


class LeaveService:
    """
    Business logic for Time Off & Leave Management:
    Leave balance tracking, leave applications, approval workflows,
    and payroll unpaid-leave integration.
    """

    @classmethod
    def calculate_leave_days(cls, start_date: datetime.date, end_date: datetime.date) -> Decimal:
        """Calculates total calendar days between start and end date inclusive."""
        if start_date > end_date:
            raise LeaveValidationError(f"Start date ({start_date}) cannot be after end date ({end_date}).")
        days = (end_date - start_date).days + 1
        return Decimal(str(days))

    @classmethod
    def get_leave_balance(
        cls,
        employee: Employee,
        leave_type: LeaveType,
        year: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Calculates used, pending, and remaining days for an employee and leave type in a calendar year.
        """
        if not year:
            year = timezone.now().year

        # Check if employee has approved allocations for this leave type in this year
        allocations = LeaveAllocation.objects.filter(
            employee=employee,
            leave_type=leave_type,
            status='approved',
            year=year
        )
        if allocations.exists():
            quota = sum((a.allocated_days for a in allocations), Decimal('0.00'))
        else:
            quota = leave_type.max_days_per_year

        # Approved leaves in this year
        approved_requests = LeaveRequest.objects.filter(
            employee=employee,
            leave_type=leave_type,
            status='approved',
            start_date__year=year
        )
        used_days = sum(req.number_of_days for req in approved_requests)

        # Pending approval requests
        pending_requests = LeaveRequest.objects.filter(
            employee=employee,
            leave_type=leave_type,
            status='submitted',
            start_date__year=year
        )
        pending_days = sum(req.number_of_days for req in pending_requests)

        is_unlimited = (quota == Decimal('0.00'))
        remaining_days = max(Decimal('0.00'), quota - used_days) if not is_unlimited else Decimal('999.00')

        return {
            'leave_type_id': leave_type.id,
            'leave_type_code': leave_type.code,
            'leave_type_name': leave_type.name,
            'is_paid': leave_type.is_paid,
            'color': leave_type.color,
            'year': year,
            'quota_days': float(quota),
            'used_days': float(used_days),
            'pending_days': float(pending_days),
            'remaining_days': float(remaining_days) if not is_unlimited else "Unlimited",
            'is_unlimited': is_unlimited,
        }

    @classmethod
    def get_employee_balances(cls, employee: Employee, year: Optional[int] = None) -> List[Dict[str, Any]]:
        """Returns leave balances across all active leave types for an employee."""
        leave_types = LeaveType.objects.filter(is_active=True).order_by('name')
        return [cls.get_leave_balance(employee, lt, year) for lt in leave_types]

    @classmethod
    def apply_leave(
        cls,
        employee: Employee,
        leave_type: LeaveType,
        start_date: datetime.date,
        end_date: datetime.date,
        reason: str = ""
    ) -> LeaveRequest:
        """
        Applies for leave on behalf of an employee.
        Validates date ordering, prevents duplicate/overlapping leaves,
        and enforces allocation quota limits.
        """
        number_of_days = cls.calculate_leave_days(start_date, end_date)

        # Check for overlapping existing active/pending requests
        overlapping = LeaveRequest.objects.filter(
            employee=employee,
            status__in=['submitted', 'approved'],
            start_date__lte=end_date,
            end_date__gte=start_date
        )
        if overlapping.exists():
            existing = overlapping.first()
            raise LeaveValidationError(
                f"Dates overlap with an existing {existing.leave_type.code} leave request "
                f"from {existing.start_date} to {existing.end_date} [{existing.get_status_display()}]."
            )

        # Enforce quota check if not unlimited
        if leave_type.max_days_per_year > Decimal('0.00'):
            bal = cls.get_leave_balance(employee, leave_type, year=start_date.year)
            available = Decimal(str(bal['remaining_days']))
            if number_of_days > available:
                raise LeaveValidationError(
                    f"Insufficient leave balance for {leave_type.name}. "
                    f"Requested: {number_of_days} days, Available: {available} days."
                )

        leave_request = LeaveRequest.objects.create(
            employee=employee,
            leave_type=leave_type,
            start_date=start_date,
            end_date=end_date,
            number_of_days=number_of_days,
            reason=reason,
            status='submitted'
        )
        return leave_request

    @classmethod
    def approve_leave(cls, leave_request: LeaveRequest, approver_user=None) -> LeaveRequest:
        """Approves a submitted or previously rejected leave request."""
        if leave_request.status not in ['submitted', 'rejected']:
            raise LeaveValidationError(f"Cannot approve leave request in '{leave_request.status}' status.")

        leave_request.status = 'approved'
        leave_request.approved_by = approver_user
        leave_request.approved_at = timezone.now()
        leave_request.save()
        return leave_request

    @classmethod
    def reject_leave(cls, leave_request: LeaveRequest, approver_user=None, rejection_reason: str = "") -> LeaveRequest:
        """Rejects a submitted or approved leave request."""
        if leave_request.status not in ['submitted', 'approved']:
            raise LeaveValidationError(f"Cannot reject leave request in '{leave_request.status}' status.")

        leave_request.status = 'rejected'
        leave_request.approved_by = approver_user
        leave_request.rejection_reason = rejection_reason
        leave_request.save()
        return leave_request

    @classmethod
    def get_unpaid_leave_days_in_period(
        cls,
        employee: Employee,
        period_start: datetime.date,
        period_end: datetime.date
    ) -> Decimal:
        """
        Finds all approved unpaid leaves for an employee that overlap with [period_start, period_end].
        Clips overlapping dates to the payrun period boundaries and returns total unpaid days.
        Used by the Payroll Engine to deduct Loss of Pay (LOP) from worked days.
        """
        unpaid_requests = LeaveRequest.objects.filter(
            employee=employee,
            leave_type__is_paid=False,
            status='approved',
            start_date__lte=period_end,
            end_date__gte=period_start
        )

        total_unpaid_days = Decimal('0.00')
        for req in unpaid_requests:
            # Overlap window
            overlap_start = max(req.start_date, period_start)
            overlap_end = min(req.end_date, period_end)
            if overlap_start <= overlap_end:
                days = (overlap_end - overlap_start).days + 1
                total_unpaid_days += Decimal(str(days))

        return total_unpaid_days
