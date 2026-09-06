import datetime
from decimal import Decimal, InvalidOperation
from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Q
from django.contrib import messages
import json
import datetime
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_GET, require_POST
from .models import (
    Payrun,
    Payslip,
    PayslipLine,
    Employee,
    SalaryStructure,
    SalaryRule,
    SalaryStructureRule,
    LeaveType,
    LeaveRequest,
    LeaveAllocation,
    WorkingSchedule,
    ScheduleDay,
    Contract,
    Attendance,
)
from .services.payrun_service import PayrunService, PayrunWorkflowError
from .services.pdf_generator import PayslipPDFGenerator
from .services.leave_service import LeaveService, LeaveValidationError

def employee_list_view(request):
    view_type = request.GET.get('view', 'kanban')
    search_query = request.GET.get('q', '')

    employees = Employee.objects.all()

    if search_query:
        employees = employees.filter(
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query) |
            Q(email__icontains=search_query) |
            Q(department__icontains=search_query) |
            Q(job_title__icontains=search_query)
        )

    context = {
        'employees': employees,
        'view_type': view_type,
        'search_query': search_query,
    }

    if request.headers.get('HX-Request'):
        if view_type == 'list':
            return render(request, 'employees/partials/list_view.html', context)
        else:
            return render(request, 'employees/partials/kanban_view.html', context)

    return render(request, 'employees/employee_list.html', context)

def employee_detail_view(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    
    context = {
        'employee': employee,
        'contracts_count': employee.contracts.count(),
        'time_off_count': 0,
        'attendance_count': employee.attendances.count(),
    }
    return render(request, 'employees/employee_detail.html', context)


# ==============================================================================
# WORKING SCHEDULE VIEWS (LIST & FORM)
# ==============================================================================

COMMON_TIMEZONES = [
    "UTC",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "Europe/London",
    "Europe/Paris",
    "Europe/Brussels",
    "Europe/Berlin",
    "Asia/Kolkata",
    "Asia/Dubai",
    "Asia/Singapore",
    "Asia/Tokyo",
    "Australia/Sydney",
]


def _parse_time_value(time_str):
    if not time_str:
        return None
    time_str = time_str.strip()
    for fmt in ('%H:%M:%S', '%H:%M', '%I:%M %p', '%I:%M%p', '%I:%M'):
        try:
            return datetime.datetime.strptime(time_str, fmt).time()
        except ValueError:
            pass
    return None


def working_schedule_list_view(request):
    search_query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', 'all').strip()
    view_type = request.GET.get('view', 'list').strip()

    schedules = WorkingSchedule.objects.prefetch_related('days').all()

    if search_query:
        schedules = schedules.filter(
            Q(name__icontains=search_query) |
            Q(timezone__icontains=search_query)
        )

    if status_filter == 'active':
        schedules = schedules.filter(is_active=True)
    elif status_filter == 'inactive':
        schedules = schedules.filter(is_active=False)

    total_count = WorkingSchedule.objects.count()
    active_count = WorkingSchedule.objects.filter(is_active=True).count()
    inactive_count = total_count - active_count

    calendar_days = [
        {'idx': 0, 'name': 'Monday', 'short': 'Mon'},
        {'idx': 1, 'name': 'Tuesday', 'short': 'Tue'},
        {'idx': 2, 'name': 'Wednesday', 'short': 'Wed'},
        {'idx': 3, 'name': 'Thursday', 'short': 'Thu'},
        {'idx': 4, 'name': 'Friday', 'short': 'Fri'},
        {'idx': 5, 'name': 'Saturday', 'short': 'Sat'},
        {'idx': 6, 'name': 'Sunday', 'short': 'Sun'},
    ]

    context = {
        'schedules': schedules,
        'search_query': search_query,
        'status_filter': status_filter,
        'view_type': view_type,
        'total_count': total_count,
        'active_count': active_count,
        'inactive_count': inactive_count,
        'calendar_days': calendar_days,
    }

    if request.headers.get('HX-Request'):
        if view_type == 'calendar':
            return render(request, 'working_schedules/partials/schedule_calendar_partial.html', context)
        return render(request, 'working_schedules/partials/schedule_table_partial.html', context)

    return render(request, 'working_schedules/working_schedule_list.html', context)


def working_schedule_form_view(request, pk=None):
    schedule = None
    is_new = pk is None
    if pk:
        schedule = get_object_or_404(WorkingSchedule.objects.prefetch_related('days'), pk=pk)
        days_data = []
        for d in schedule.days.all().order_by('day_of_week', 'work_from'):
            days_data.append({
                'day_of_week': d.day_of_week,
                'work_from': d.work_from.strftime('%H:%M') if d.work_from else '09:00',
                'work_to': d.work_to.strftime('%H:%M') if d.work_to else '18:00',
                'break_hours': f"{d.break_hours.normalize():f}" if d.break_hours is not None else '1.00',
                'hours': d.formatted_hours,
            })
    else:
        # Default starter days: Monday through Friday (09:00 - 18:00, break 1h, 8h)
        days_data = [
            {'day_of_week': idx, 'work_from': '09:00', 'work_to': '18:00', 'break_hours': '1.00', 'hours': '8h'}
            for idx in range(5)
        ]

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        timezone = request.POST.get('timezone', 'UTC').strip()
        is_active = request.POST.get('is_active') in ('on', 'true', 'True', '1')

        if not name:
            messages.error(request, "Schedule Name is required.")
            return render(request, 'working_schedules/working_schedule_form.html', {
                'schedule': schedule,
                'days': days_data,
                'is_new': is_new,
                'days_of_week_choices': ScheduleDay.DAYS_OF_WEEK,
                'timezones': COMMON_TIMEZONES,
            })

        existing = WorkingSchedule.objects.filter(name__iexact=name)
        if schedule:
            existing = existing.exclude(pk=schedule.pk)
        if existing.exists():
            messages.error(request, f"A working schedule named '{name}' already exists.")
            return render(request, 'working_schedules/working_schedule_form.html', {
                'schedule': schedule,
                'days': days_data,
                'is_new': is_new,
                'days_of_week_choices': ScheduleDay.DAYS_OF_WEEK,
                'timezones': COMMON_TIMEZONES,
            })

        if is_new:
            schedule = WorkingSchedule.objects.create(
                name=name,
                timezone=timezone,
                is_active=is_active,
            )
        else:
            schedule.name = name
            schedule.timezone = timezone
            schedule.is_active = is_active
            schedule.save()

        # Parse submitted schedule days
        day_of_weeks = request.POST.getlist('day_of_week[]')
        work_froms = request.POST.getlist('work_from[]')
        work_tos = request.POST.getlist('work_to[]')
        break_hours_list = request.POST.getlist('break_hours[]')

        # Wipe old days and recreate
        schedule.days.all().delete()

        for i in range(len(day_of_weeks)):
            try:
                d_idx = int(day_of_weeks[i])
                w_from_str = work_froms[i].strip()
                w_to_str = work_tos[i].strip()
                if not w_from_str or not w_to_str:
                    continue

                w_from = _parse_time_value(w_from_str)
                w_to = _parse_time_value(w_to_str)
                if not w_from or not w_to:
                    continue

                try:
                    brk_val = Decimal(str(break_hours_list[i]).strip() or "0.00")
                except (InvalidOperation, ValueError):
                    brk_val = Decimal("0.00")

                t1 = datetime.datetime.combine(datetime.date.min, w_from)
                t2 = datetime.datetime.combine(datetime.date.min, w_to)
                diff = (t2 - t1).total_seconds() / 3600.0
                if diff < 0:
                    diff += 24.0
                net_calc = max(0.0, diff - float(brk_val))
                hrs_val = Decimal(f"{net_calc:.2f}")

                ScheduleDay.objects.create(
                    schedule=schedule,
                    day_of_week=d_idx,
                    work_from=w_from,
                    work_to=w_to,
                    break_hours=brk_val,
                    hours=hrs_val
                )
            except Exception:
                continue

        # Recalculate average_hours_per_day
        if schedule.days_per_week > 0:
            schedule.average_hours_per_day = Decimal(f"{(schedule.total_hours_per_week / schedule.days_per_week):.2f}")
        else:
            schedule.average_hours_per_day = Decimal("0.00")
        schedule.save()

        action_word = "created" if is_new else "updated"
        messages.success(request, f"Working Schedule '{schedule.name}' {action_word} successfully.")
        return redirect('working_schedule_detail', pk=schedule.pk)

    context = {
        'schedule': schedule,
        'days': days_data,
        'is_new': is_new,
        'days_of_week_choices': ScheduleDay.DAYS_OF_WEEK,
        'timezones': COMMON_TIMEZONES,
    }
    return render(request, 'working_schedules/working_schedule_form.html', context)


@require_POST
def working_schedule_delete_view(request, pk):
    schedule = get_object_or_404(WorkingSchedule, pk=pk)
    name = schedule.name
    schedule.delete()
    messages.success(request, f"Working Schedule '{name}' was deleted.")
    return redirect('working_schedule_list')


@require_POST
def working_schedule_toggle_status_view(request, pk):
    schedule = get_object_or_404(WorkingSchedule, pk=pk)
    schedule.is_active = not schedule.is_active
    schedule.save()
    status_label = "Active" if schedule.is_active else "Inactive"
    messages.success(request, f"Working Schedule '{schedule.name}' is now marked as {status_label}.")
    return redirect('working_schedule_detail', pk=schedule.pk)


# ==============================================================================
# CONTRACT VIEWS (CRUD, LIST & DETAIL)
# ==============================================================================

def _generate_contract_code(year=None):
    if not year:
        year = datetime.date.today().year
    prefix = f"CON/{year}/"
    existing_codes = Contract.objects.filter(name__startswith=prefix).values_list('name', flat=True)
    max_seq = 0
    for c in existing_codes:
        try:
            parts = c.split('/')
            seq = int(parts[-1])
            if seq > max_seq:
                max_seq = seq
        except (ValueError, IndexError):
            pass
    if max_seq == 0:
        max_seq = Contract.objects.count()
    return f"CON/{year}/{max_seq + 1:04d}"


def _validate_overlapping_active_contract(employee, start_date, end_date, exclude_pk=None):
    """
    Validates that the employee does not have another active contract for the given period.
    Returns overlapping Contract instance if violation found, else None.
    """
    qs = Contract.objects.filter(employee=employee, state='active')
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)

    if end_date:
        qs = qs.filter(start_date__lte=end_date)
    qs = qs.filter(Q(end_date__isnull=True) | Q(end_date__gte=start_date))
    return qs.first()


def contract_list_view(request):
    search_query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', 'all').strip()
    employee_id = request.GET.get('employee', '').strip()

    contracts = Contract.objects.select_related('employee', 'working_schedule', 'salary_structure').all()

    if employee_id:
        contracts = contracts.filter(employee_id=employee_id)

    if status_filter in ['active', 'draft', 'expired', 'cancelled']:
        contracts = contracts.filter(state=status_filter)

    if search_query:
        contracts = contracts.filter(
            Q(name__icontains=search_query) |
            Q(employee__first_name__icontains=search_query) |
            Q(employee__last_name__icontains=search_query) |
            Q(employee__code__icontains=search_query) |
            Q(employee__department__icontains=search_query) |
            Q(employee__job_title__icontains=search_query)
        )

    # Base counts for status tabs
    base_qs = Contract.objects.all()
    if employee_id:
        base_qs = base_qs.filter(employee_id=employee_id)

    total_count = base_qs.count()
    active_count = base_qs.filter(state='active').count()
    draft_count = base_qs.filter(state='draft').count()
    expired_count = base_qs.filter(state='expired').count()
    cancelled_count = base_qs.filter(state='cancelled').count()

    selected_employee = None
    if employee_id:
        selected_employee = Employee.objects.filter(id=employee_id).first()

    context = {
        'contracts': contracts,
        'search_query': search_query,
        'status_filter': status_filter,
        'employee_id': employee_id,
        'selected_employee': selected_employee,
        'total_count': total_count,
        'active_count': active_count,
        'draft_count': draft_count,
        'expired_count': expired_count,
        'cancelled_count': cancelled_count,
    }

    if request.headers.get('HX-Request'):
        return render(request, 'contracts/partials/contract_table_partial.html', context)

    return render(request, 'contracts/contract_list.html', context)


def contract_detail_view(request, pk):
    contract = get_object_or_404(
        Contract.objects.select_related('employee', 'working_schedule', 'salary_structure'),
        pk=pk
    )

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        employee_id = request.POST.get('employee_id')
        start_date_str = request.POST.get('start_date', '').strip()
        end_date_str = request.POST.get('end_date', '').strip()
        wage_str = request.POST.get('wage', '').strip()
        wage_type = request.POST.get('wage_type', 'monthly').strip()
        state = request.POST.get('state', 'draft').strip()
        working_schedule_id = request.POST.get('working_schedule_id', '').strip()
        salary_structure_id = request.POST.get('salary_structure_id', '').strip()

        # Validation
        employee = Employee.objects.filter(id=employee_id).first()
        if not employee:
            messages.error(request, "Please select a valid employee.")
            return redirect('contract_detail', pk=contract.pk)

        if not name:
            name = contract.name or _generate_contract_code()

        start_date = None
        if start_date_str:
            for fmt in ('%Y-%m-%d', '%d-%b-%Y', '%d/%m/%Y', '%m/%d/%Y'):
                try:
                    start_date = datetime.datetime.strptime(start_date_str, fmt).date()
                    break
                except ValueError:
                    pass
        if not start_date:
            messages.error(request, "A valid Start Date is required.")
            return redirect('contract_detail', pk=contract.pk)

        end_date = None
        if end_date_str:
            for fmt in ('%Y-%m-%d', '%d-%b-%Y', '%d/%m/%Y', '%m/%d/%Y'):
                try:
                    end_date = datetime.datetime.strptime(end_date_str, fmt).date()
                    break
                except ValueError:
                    pass
            if end_date and end_date < start_date:
                messages.error(request, "End Date cannot be before Start Date.")
                return redirect('contract_detail', pk=contract.pk)

        try:
            cleaned_wage = wage_str.replace('₹', '').replace('$', '').replace(',', '').strip()
            wage = Decimal(cleaned_wage)
            if wage < 0:
                raise ValueError()
        except (InvalidOperation, ValueError):
            messages.error(request, "Please enter a valid wage amount.")
            return redirect('contract_detail', pk=contract.pk)

        # Overlap check if activating contract
        if state == 'active':
            overlap = _validate_overlapping_active_contract(employee, start_date, end_date, exclude_pk=contract.pk)
            if overlap:
                messages.error(
                    request,
                    f"Validation Error: Employee '{employee.full_name}' already has an active contract ({overlap.name}) "
                    f"for this period ({overlap.formatted_start_date} to {overlap.formatted_end_date}). "
                    f"An employee cannot have multiple Active contracts for the same period."
                )
                return redirect('contract_detail', pk=contract.pk)

        # Working Schedule
        working_schedule = None
        if working_schedule_id:
            working_schedule = WorkingSchedule.objects.filter(id=working_schedule_id).first()

        # Salary Structure
        salary_structure = None
        if salary_structure_id:
            salary_structure = SalaryStructure.objects.filter(id=salary_structure_id).first()

        contract.name = name
        contract.employee = employee
        contract.start_date = start_date
        contract.end_date = end_date
        contract.wage = wage
        contract.wage_type = wage_type
        contract.state = state
        contract.working_schedule = working_schedule
        contract.salary_structure = salary_structure
        contract.save()

        messages.success(request, f"Contract '{contract.name}' updated successfully.")
        return redirect('contract_detail', pk=contract.pk)

    employees = Employee.objects.all().order_by('first_name', 'last_name')
    working_schedules = WorkingSchedule.objects.all().order_by('name')
    salary_structures = SalaryStructure.objects.all().order_by('name')

    context = {
        'contract': contract,
        'is_new': False,
        'employees': employees,
        'working_schedules': working_schedules,
        'salary_structures': salary_structures,
        'state_choices': Contract.STATE_CHOICES,
        'wage_type_choices': Contract.WAGE_TYPE_CHOICES,
    }
    return render(request, 'contracts/contract_detail.html', context)


def contract_create_view(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        employee_id = request.POST.get('employee_id')
        start_date_str = request.POST.get('start_date', '').strip()
        end_date_str = request.POST.get('end_date', '').strip()
        wage_str = request.POST.get('wage', '').strip()
        wage_type = request.POST.get('wage_type', 'monthly').strip()
        state = request.POST.get('state', 'draft').strip()
        working_schedule_id = request.POST.get('working_schedule_id', '').strip()
        salary_structure_id = request.POST.get('salary_structure_id', '').strip()

        # Validation
        employee = Employee.objects.filter(id=employee_id).first()
        if not employee:
            messages.error(request, "Please select an employee.")
            return redirect('contract_create')

        if not name:
            name = _generate_contract_code()

        start_date = None
        if start_date_str:
            for fmt in ('%Y-%m-%d', '%d-%b-%Y', '%d/%m/%Y', '%m/%d/%Y'):
                try:
                    start_date = datetime.datetime.strptime(start_date_str, fmt).date()
                    break
                except ValueError:
                    pass
        if not start_date:
            messages.error(request, "A valid Start Date is required.")
            return redirect('contract_create')

        end_date = None
        if end_date_str:
            for fmt in ('%Y-%m-%d', '%d-%b-%Y', '%d/%m/%Y', '%m/%d/%Y'):
                try:
                    end_date = datetime.datetime.strptime(end_date_str, fmt).date()
                    break
                except ValueError:
                    pass
            if end_date and end_date < start_date:
                messages.error(request, "End Date cannot be before Start Date.")
                return redirect('contract_create')

        try:
            cleaned_wage = wage_str.replace('₹', '').replace('$', '').replace(',', '').strip()
            wage = Decimal(cleaned_wage)
            if wage < 0:
                raise ValueError()
        except (InvalidOperation, ValueError):
            messages.error(request, "Please enter a valid wage amount.")
            return redirect('contract_create')

        # Overlap check if creating with active state
        if state == 'active':
            overlap = _validate_overlapping_active_contract(employee, start_date, end_date)
            if overlap:
                messages.error(
                    request,
                    f"Validation Error: Employee '{employee.full_name}' already has an active contract ({overlap.name}) "
                    f"for this period ({overlap.formatted_start_date} to {overlap.formatted_end_date}). "
                    f"An employee cannot have multiple Active contracts for the same period."
                )
                return redirect('contract_create')

        working_schedule = None
        if working_schedule_id:
            working_schedule = WorkingSchedule.objects.filter(id=working_schedule_id).first()

        salary_structure = None
        if salary_structure_id:
            salary_structure = SalaryStructure.objects.filter(id=salary_structure_id).first()
        elif SalaryStructure.objects.filter(is_active=True).exists():
            salary_structure = SalaryStructure.objects.filter(is_active=True).first()

        contract = Contract.objects.create(
            name=name,
            employee=employee,
            start_date=start_date,
            end_date=end_date,
            wage=wage,
            wage_type=wage_type,
            state=state,
            working_schedule=working_schedule,
            salary_structure=salary_structure,
        )

        messages.success(request, f"Contract '{contract.name}' created successfully.")
        return redirect('contract_detail', pk=contract.pk)

    preselected_employee_id = request.GET.get('employee', '')
    preselected_employee = None
    if preselected_employee_id:
        preselected_employee = Employee.objects.filter(id=preselected_employee_id).first()

    employees = Employee.objects.all().order_by('first_name', 'last_name')
    working_schedules = WorkingSchedule.objects.all().order_by('name')
    salary_structures = SalaryStructure.objects.all().order_by('name')
    suggested_name = _generate_contract_code()

    context = {
        'contract': None,
        'is_new': True,
        'suggested_name': suggested_name,
        'preselected_employee': preselected_employee,
        'employees': employees,
        'working_schedules': working_schedules,
        'salary_structures': salary_structures,
        'state_choices': Contract.STATE_CHOICES,
        'wage_type_choices': Contract.WAGE_TYPE_CHOICES,
        'today': datetime.date.today().strftime('%Y-%m-%d'),
    }
    return render(request, 'contracts/contract_detail.html', context)


@require_POST
def contract_delete_view(request, pk):
    contract = get_object_or_404(Contract, pk=pk)
    name = contract.name
    contract.delete()
    messages.success(request, f"Contract '{name}' was deleted successfully.")
    return redirect('contract_list')


# ==============================================================================
# 1. PDF PAYSLIP STREAMING & DOWNLOAD
# ==============================================================================

@require_GET
def payslip_pdf_view(request, payslip_id: int):
    """
    Renders and serves the official PDF payslip for a given Payslip ID.
    If the PDF file hasn't been generated yet, it generates and attaches it on-the-fly.
    """
    payslip = get_object_or_404(
        Payslip.objects.select_related('employee', 'contract', 'salary_structure', 'payrun'),
        id=payslip_id
    )

    # Generate if not already present or force regenerated if query param ?refresh=1
    force_refresh = request.GET.get('refresh', '0') == '1'
    if not payslip.pdf_file or force_refresh:
        PayslipPDFGenerator.generate_and_save(payslip)

    try:
        pdf_content = payslip.pdf_file.read()
    except Exception:
        # Fallback to in-memory generation if file storage has an issue
        pdf_content = PayslipPDFGenerator.generate_pdf_bytes(payslip)

    filename = f"Payslip_{payslip.employee.code}_{payslip.period_start.strftime('%Y_%m')}.pdf"
    as_download = request.GET.get('download', '0') == '1'
    disposition = 'attachment' if as_download else 'inline'

    response = HttpResponse(pdf_content, content_type='application/pdf')
    response['Content-Disposition'] = f'{disposition}; filename="{filename}"'
    return response


# ==============================================================================
# 2. JSON REST APIS FOR PERSON 3 (FRONTEND / DASHBOARD)
# ==============================================================================

@csrf_exempt
@require_http_methods(["GET", "POST"])
def api_payruns_list(request):
    """
    GET: List all payruns with their totals and state.
    POST: Create a new Payrun batch.
    """
    if request.method == "GET":
        payruns = Payrun.objects.select_related('salary_structure').all().order_by('-start_date')
        data = [
            {
                "id": p.id,
                "name": p.name,
                "salary_structure_code": p.salary_structure.code if p.salary_structure else None,
                "salary_structure_name": p.salary_structure.name if p.salary_structure else None,
                "start_date": str(p.start_date),
                "end_date": str(p.end_date),
                "payment_date": str(p.payment_date) if p.payment_date else None,
                "state": p.state,
                "payslip_count": p.payslip_count,
                "total_gross": float(p.total_gross),
                "total_net": float(p.total_net),
            }
            for p in payruns
        ]
        return JsonResponse({"payruns": data})

    elif request.method == "POST":
        try:
            body = json.loads(request.body.decode('utf-8'))
            structure_id = body.get('salary_structure_id')
            structure = get_object_or_404(SalaryStructure, id=structure_id) if structure_id else None

            payrun = Payrun.objects.create(
                name=body['name'],
                salary_structure=structure,
                start_date=body['start_date'],
                end_date=body['end_date'],
                state='draft'
            )
            return JsonResponse({
                "message": "Payrun created successfully.",
                "payrun": {
                    "id": payrun.id,
                    "name": payrun.name,
                    "state": payrun.state,
                    "start_date": str(payrun.start_date),
                    "end_date": str(payrun.end_date),
                }
            }, status=201)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)


@require_GET
def api_payrun_detail(request, pk: int):
    """
    Detailed view of a single Payrun including all employee payslips and validation report.
    """
    payrun = get_object_or_404(
        Payrun.objects.select_related('salary_structure').prefetch_related('payslips__employee'),
        pk=pk
    )

    report = PayrunService.validate_payrun_preflight(payrun)
    payslips = payrun.payslips.all().order_by('employee__code')

    return JsonResponse({
        "id": payrun.id,
        "name": payrun.name,
        "salary_structure": {
            "id": payrun.salary_structure.id if payrun.salary_structure else None,
            "code": payrun.salary_structure.code if payrun.salary_structure else None,
            "name": payrun.salary_structure.name if payrun.salary_structure else None,
        },
        "start_date": str(payrun.start_date),
        "end_date": str(payrun.end_date),
        "payment_date": str(payrun.payment_date) if payrun.payment_date else None,
        "state": payrun.state,
        "total_gross": float(payrun.total_gross),
        "total_net": float(payrun.total_net),
        "payslip_count": payrun.payslip_count,
        "validation_report": {
            "is_valid": report.is_valid,
            "errors": [e.to_dict() for e in report.errors],
            "warnings": [w.to_dict() for w in report.warnings],
        },
        "payslips": [
            {
                "id": ps.id,
                "employee_code": ps.employee.code,
                "employee_name": ps.employee.full_name,
                "department": ps.employee.department,
                "gross_wage": float(ps.gross_wage),
                "total_deductions": float(ps.total_deductions),
                "net_wage": float(ps.net_wage),
                "state": ps.state,
                "has_bank_details": ps.employee.has_bank_details,
                "pdf_url": f"/api/payslips/{ps.id}/pdf/",
            }
            for ps in payslips
        ]
    })


@csrf_exempt
@require_POST
def api_payrun_compute(request, pk: int):
    """
    Executes payroll batch calculation for all eligible employees.
    """
    payrun = get_object_or_404(Payrun, pk=pk)
    try:
        body = json.loads(request.body.decode('utf-8')) if request.body else {}
        employee_ids = body.get('employee_ids', None)

        res = PayrunService.compute_payrun(payrun, employee_ids=employee_ids)
        report = res['validation_report']

        return JsonResponse({
            "message": f"Successfully computed {res['computed_count']} payslip(s).",
            "state": payrun.state,
            "computed_count": res['computed_count'],
            "total_gross": float(res['total_gross']),
            "total_net": float(res['total_net']),
            "warnings": [w.to_dict() for w in report.warnings],
            "errors": [e.to_dict() for e in report.errors],
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
@require_POST
def api_payrun_validate(request, pk: int):
    """
    Transitions payrun from 'computed' to 'validated'.
    """
    payrun = get_object_or_404(Payrun, pk=pk)
    try:
        PayrunService.validate_payrun(payrun)
        return JsonResponse({
            "message": f"Payrun '{payrun.name}' validated successfully.",
            "state": payrun.state,
        })
    except PayrunWorkflowError as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
@require_POST
def api_payrun_mark_paid(request, pk: int):
    """
    Transitions payrun from 'validated' to 'paid'.
    """
    payrun = get_object_or_404(Payrun, pk=pk)
    try:
        PayrunService.mark_payrun_paid(payrun)
        return JsonResponse({
            "message": f"Payrun '{payrun.name}' marked as PAID.",
            "state": payrun.state,
            "payment_date": str(payrun.payment_date),
        })
    except PayrunWorkflowError as e:
        return JsonResponse({"error": str(e)}, status=400)


@require_GET
def api_payslip_detail(request, pk: int):
    """
    Returns full individual payslip detail including itemized rule calculation lines.
    """
    payslip = get_object_or_404(
        Payslip.objects.select_related('employee', 'contract', 'salary_structure', 'payrun')
        .prefetch_related('lines'),
        pk=pk
    )

    lines = [
        {
            "id": l.id,
            "code": l.code,
            "name": l.name,
            "category": l.category,
            "sequence": l.sequence,
            "rate": float(l.rate),
            "amount": float(l.amount),
            "total": float(l.total),
        }
        for l in payslip.lines.all().order_by('sequence', 'id')
    ]

    return JsonResponse({
        "id": payslip.id,
        "payrun_id": payslip.payrun_id,
        "payrun_name": payslip.payrun.name,
        "employee": {
            "code": payslip.employee.code,
            "name": payslip.employee.full_name,
            "department": payslip.employee.department,
            "job_title": payslip.employee.job_title,
            "bank_name": payslip.employee.bank_name,
            "has_bank_details": payslip.employee.has_bank_details,
        },
        "period_start": str(payslip.period_start),
        "period_end": str(payslip.period_end),
        "worked_days": float(payslip.worked_days),
        "basic_wage": float(payslip.basic_wage),
        "gross_wage": float(payslip.gross_wage),
        "total_deductions": float(payslip.total_deductions),
        "net_wage": float(payslip.net_wage),
        "state": payslip.state,
        "pdf_url": f"/api/payslips/{payslip.id}/pdf/",
        "lines": lines,
    })


@require_GET
def api_employees_list(request):
    """
    Lists all employees with their contract status and bank information completeness.
    """
    employees = Employee.objects.all().order_by('code')
    data = [
        {
            "id": e.id,
            "code": e.code,
            "name": e.full_name,
            "department": e.department,
            "job_title": e.job_title,
            "is_active": e.is_active,
            "has_bank_details": e.has_bank_details,
            "has_active_contract": e.contracts.filter(state='active').exists(),
        }
        for e in employees
    ]
    return JsonResponse({"employees": data})


@require_GET
def api_salary_structures_list(request):
    """
    Lists available salary structures for payrun selection.
    """
    structures = SalaryStructure.objects.filter(is_active=True).prefetch_related('structure_rules__rule')
    data = [
        {
            "id": s.id,
            "code": s.code,
            "name": s.name,
            "rule_count": s.structure_rules.filter(rule__is_active=True).count(),
        }
        for s in structures
    ]
    return JsonResponse({"salary_structures": data})


# ==============================================================================
# 3. LEAVE / TIME OFF REST APIS (PERSON 3 FRONTEND)
# ==============================================================================

@require_GET
def api_leave_types_list(request):
    """Lists all active leave types (PTO, Sick, Casual, Unpaid)."""
    types = LeaveType.objects.filter(is_active=True).order_by('name')
    data = [
        {
            "id": lt.id,
            "name": lt.name,
            "code": lt.code,
            "is_paid": lt.is_paid,
            "max_days_per_year": float(lt.max_days_per_year),
            "color": lt.color,
        }
        for lt in types
    ]
    return JsonResponse({"leave_types": data})


@require_GET
def api_leave_balances(request):
    """
    Returns annual leave balances (quota, used, remaining) for an employee.
    Query params: ?employee_id=X (optional &year=YYYY)
    """
    emp_id = request.GET.get('employee_id')
    if not emp_id:
        return JsonResponse({"error": "Query parameter 'employee_id' is required."}, status=400)

    employee = get_object_or_404(Employee, pk=emp_id)
    year = int(request.GET.get('year', 0)) or None
    balances = LeaveService.get_employee_balances(employee, year=year)

    return JsonResponse({
        "employee_id": employee.id,
        "employee_code": employee.code,
        "employee_name": employee.full_name,
        "balances": balances
    })


@csrf_exempt
@require_http_methods(["GET", "POST"])
def api_leave_requests(request):
    """
    GET: List leave requests with optional filters (?employee_id=X, ?status=Y).
    POST: Submit a new leave application.
    """
    if request.method == "GET":
        qs = LeaveRequest.objects.select_related('employee', 'leave_type', 'approved_by').all().order_by('-start_date')

        emp_id = request.GET.get('employee_id')
        if emp_id:
            qs = qs.filter(employee_id=emp_id)

        status_filter = request.GET.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)

        data = [
            {
                "id": req.id,
                "employee_id": req.employee_id,
                "employee_code": req.employee.code,
                "employee_name": req.employee.full_name,
                "leave_type_code": req.leave_type.code,
                "leave_type_name": req.leave_type.name,
                "is_paid": req.leave_type.is_paid,
                "color": req.leave_type.color,
                "start_date": str(req.start_date),
                "end_date": str(req.end_date),
                "number_of_days": float(req.number_of_days),
                "reason": req.reason,
                "status": req.status,
                "approved_by": req.approved_by.username if req.approved_by else None,
                "created_at": req.created_at.strftime("%Y-%m-%d %H:%M"),
            }
            for req in qs
        ]
        return JsonResponse({"leave_requests": data})

    elif request.method == "POST":
        try:
            body = json.loads(request.body.decode('utf-8'))
            employee = get_object_or_404(Employee, pk=body['employee_id'])
            leave_type = get_object_or_404(LeaveType, pk=body['leave_type_id'])
            start_date = datetime.date.fromisoformat(body['start_date'])
            end_date = datetime.date.fromisoformat(body['end_date'])
            reason = body.get('reason', '')

            req = LeaveService.apply_leave(
                employee=employee,
                leave_type=leave_type,
                start_date=start_date,
                end_date=end_date,
                reason=reason
            )
            return JsonResponse({
                "message": f"Leave request for {req.number_of_days} day(s) submitted successfully.",
                "leave_request": {
                    "id": req.id,
                    "employee_code": employee.code,
                    "leave_type": leave_type.code,
                    "start_date": str(req.start_date),
                    "end_date": str(req.end_date),
                    "number_of_days": float(req.number_of_days),
                    "status": req.status,
                }
            }, status=201)
        except LeaveValidationError as e:
            return JsonResponse({"error": str(e)}, status=400)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
@require_POST
def api_leave_request_approve(request, pk: int):
    """Approves a submitted leave request."""
    leave_req = get_object_or_404(LeaveRequest, pk=pk)
    try:
        user = request.user if request.user.is_authenticated else None
        LeaveService.approve_leave(leave_req, approver_user=user)
        return JsonResponse({
            "message": f"Leave request for {leave_req.employee.full_name} approved.",
            "status": leave_req.status,
        })
    except LeaveValidationError as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
@require_POST
def api_leave_request_reject(request, pk: int):
    """Rejects a submitted leave request with optional reason."""
    leave_req = get_object_or_404(LeaveRequest, pk=pk)
    try:
        body = json.loads(request.body.decode('utf-8')) if request.body else {}
        rejection_reason = body.get('rejection_reason', 'Rejected via API.')
        user = request.user if request.user.is_authenticated else None

        LeaveService.reject_leave(leave_req, approver_user=user, rejection_reason=rejection_reason)
        return JsonResponse({
            "message": f"Leave request for {leave_req.employee.full_name} rejected.",
            "status": leave_req.status,
            "rejection_reason": leave_req.rejection_reason,
        })
    except LeaveValidationError as e:
        return JsonResponse({"error": str(e)}, status=400)


# ==============================================================================
# ATTENDANCE VIEWS & HELPERS
# ==============================================================================

def _parse_datetime_input(dt_str):
    """Parses various datetime input strings and returns a timezone-aware datetime."""
    if not dt_str:
        return None
    dt_str = dt_str.strip()
    formats = [
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M',
        '%d-%b-%Y %H:%M:%S',
        '%d-%b-%Y %H:%M',
        '%d-%B-%Y %H:%M:%S',
        '%d-%B-%Y %H:%M',
        '%d/%m/%Y %H:%M:%S',
        '%d/%m/%Y %H:%M',
        '%Y-%m-%d',
    ]
    for fmt in formats:
        try:
            dt = datetime.datetime.strptime(dt_str, fmt)
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_current_timezone())
            return dt
        except ValueError:
            pass
    try:
        dt = datetime.datetime.fromisoformat(dt_str)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        return dt
    except Exception:
        return None


def _parse_attendance_query(query_str, reference_date=None):
    """
    Parses comma-separated attendance query strings such as:
    - 'today'
    - 'yesterday'
    - '2026-09-05'
    - 'employee: Aarav' or 'Employee: Aarav Mehta'
    - '2026-09-05, Employee: Aarav'
    - 'today, employee: Sara'
    Returns: (combined_Q_filter, active_chips, normalized_query_str, active_mode)
    """
    if reference_date is None:
        reference_date = timezone.localdate()

    is_default = False
    if query_str is None:
        query_str = 'today'
        is_default = True

    query_str_clean = query_str.strip()
    if not query_str_clean:
        query_str_clean = 'today'
        is_default = True

    tokens = [t.strip() for t in query_str_clean.split(',') if t.strip()]

    combined_q = Q()
    chips = []
    has_date_filter = False
    active_mode = 'custom'

    if len(tokens) == 1 and tokens[0].lower() == 'today':
        active_mode = 'today'
    elif len(tokens) == 1 and tokens[0].lower() == 'yesterday':
        active_mode = 'yesterday'
    elif len(tokens) == 1 and tokens[0].lower() == 'all':
        active_mode = 'all'

    for token in tokens:
        token_lower = token.lower()
        if token_lower == 'today':
            combined_q &= Q(date=reference_date)
            chips.append({'label': 'Today', 'value': 'today', 'type': 'date'})
            has_date_filter = True
        elif token_lower == 'yesterday':
            y_date = reference_date - datetime.timedelta(days=1)
            combined_q &= Q(date=y_date)
            chips.append({'label': f'Yesterday ({y_date.strftime("%d-%b")})', 'value': 'yesterday', 'type': 'date'})
            has_date_filter = True
        elif token_lower == 'all':
            # Explicitly all records
            chips.append({'label': 'All Dates', 'value': 'all', 'type': 'all'})
            has_date_filter = True
        elif token_lower.startswith('employee:') or token_lower.startswith('emp:'):
            emp_val = token.split(':', 1)[1].strip()
            emp_q = (
                Q(employee__first_name__icontains=emp_val) |
                Q(employee__last_name__icontains=emp_val) |
                Q(employee__code__icontains=emp_val)
            )
            words = emp_val.split()
            if len(words) >= 2:
                emp_q |= (Q(employee__first_name__icontains=words[0]) & Q(employee__last_name__icontains=words[-1]))
            combined_q &= emp_q
            chips.append({'label': f'Employee: {emp_val}', 'value': token, 'type': 'employee'})
        else:
            # Check if token is a date
            parsed_date = None
            for fmt in ('%Y-%m-%d', '%d-%b-%Y', '%d-%B-%Y', '%d-%m-%Y', '%d/%m/%Y', '%m/%d/%Y', '%Y/%m/%d'):
                try:
                    parsed_date = datetime.datetime.strptime(token, fmt).date()
                    break
                except ValueError:
                    pass
                

            if parsed_date:
                combined_q &= Q(date=parsed_date)
                chips.append({'label': parsed_date.strftime('%Y-%m-%d'), 'value': token, 'type': 'date'})
                has_date_filter = True
            else:
                # Treat as employee name or code or department
                words = token.split()
                emp_q = (
                    Q(employee__first_name__icontains=token) |
                    Q(employee__last_name__icontains=token) |
                    Q(employee__code__icontains=token) |
                    Q(employee__department__icontains=token)
                )
                if len(words) >= 2:
                    emp_q |= (Q(employee__first_name__icontains=words[0]) & Q(employee__last_name__icontains=words[-1]))
                combined_q &= emp_q
                chips.append({'label': f'Employee: {token}', 'value': token, 'type': 'employee'})

    # If no date filter was specified at all, default to today
    if not has_date_filter and is_default:
        combined_q &= Q(date=reference_date)

    return combined_q, chips, query_str_clean, active_mode


def attendance_list_view(request):
    """
    List view for employee attendance records.
    Supports comma-separated query search (today, yesterday, YYYY-MM-DD, employee: emp_name).
    By default shows today's attendance records.
    """
    raw_query = request.GET.get('q')
    today_date = timezone.localdate()

    filter_q, chips, search_query, active_mode = _parse_attendance_query(raw_query, reference_date=today_date)

    attendances = Attendance.objects.select_related(
        'employee', 'employee__manager'
    ).filter(filter_q).order_by('-date', '-check_in', 'employee__first_name')

    total_count = attendances.count()
    present_count = attendances.filter(status='present').count()
    absent_count = attendances.filter(status='absent').count()

    context = {
        'attendances': attendances,
        'search_query': search_query,
        'chips': chips,
        'active_mode': active_mode,
        'today_date': today_date,
        'total_count': total_count,
        'present_count': present_count,
        'absent_count': absent_count,
    }

    if request.headers.get('HX-Request'):
        return render(request, 'attendance/partials/attendance_table_partial.html', context)

    return render(request, 'attendance/attendance_list.html', context)


def attendance_detail_view(request, pk):
    """
    View and edit an attendance record.
    Overtime is calculated dynamically using the Working Schedule defined
    in the current contract of the employee.
    """
    attendance = get_object_or_404(
        Attendance.objects.select_related('employee', 'employee__manager'),
        pk=pk
    )

    if request.method == 'POST':
        employee_id = request.POST.get('employee_id')
        check_in_str = request.POST.get('check_in', '').strip()
        check_out_str = request.POST.get('check_out', '').strip()
        status = request.POST.get('status', 'present').strip()
        date_str = request.POST.get('date', '').strip()

        if employee_id:
            emp = Employee.objects.filter(pk=employee_id).first()
            if emp:
                attendance.employee = emp

        if date_str:
            try:
                attendance.date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                pass

        attendance.check_in = _parse_datetime_input(check_in_str)
        attendance.check_out = _parse_datetime_input(check_out_str)
        attendance.status = status

        if attendance.check_in and not date_str:
            attendance.date = timezone.localtime(attendance.check_in).date()

        # Dynamic recalculation
        attendance.worked_hours = attendance.calculate_worked_hours()
        attendance.overtime_hours = attendance.calculate_overtime(attendance.worked_hours)
        attendance.save()

        messages.success(request, f"Attendance record for {attendance.employee.full_name} updated successfully.")
        return redirect('attendance_detail', pk=attendance.pk)

    contract = attendance.get_applicable_contract()
    working_schedule = contract.working_schedule if contract else None
    employees = Employee.objects.filter(is_active=True).order_by('first_name', 'last_name')

    # Format check_in and check_out for datetime-local inputs
    check_in_local = timezone.localtime(attendance.check_in).strftime('%Y-%m-%dT%H:%M') if attendance.check_in else ''
    check_out_local = timezone.localtime(attendance.check_out).strftime('%Y-%m-%dT%H:%M') if attendance.check_out else ''

    context = {
        'attendance': attendance,
        'employee': attendance.employee,
        'contract': contract,
        'working_schedule': working_schedule,
        'employees': employees,
        'check_in_local': check_in_local,
        'check_out_local': check_out_local,
        'is_new': False,
    }
    return render(request, 'attendance/attendance_detail.html', context)


def attendance_create_view(request):
    """
    Form view to create a new attendance record.
    Overtime is calculated dynamically using the Working Schedule defined
    in the current contract of the employee.
    """
    employees = Employee.objects.filter(is_active=True).order_by('first_name', 'last_name')
    today_date = timezone.localdate()

    if request.method == 'POST':
        employee_id = request.POST.get('employee_id')
        date_str = request.POST.get('date', '').strip()
        check_in_str = request.POST.get('check_in', '').strip()
        check_out_str = request.POST.get('check_out', '').strip()
        status = request.POST.get('status', 'present').strip()

        employee = Employee.objects.filter(pk=employee_id).first()
        if not employee:
            messages.error(request, "Please select an employee.")
            return redirect('attendance_create')

        att_date = today_date
        if date_str:
            try:
                att_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                pass

        check_in_dt = _parse_datetime_input(check_in_str)
        check_out_dt = _parse_datetime_input(check_out_str)

        if check_in_dt and not date_str:
            att_date = timezone.localtime(check_in_dt).date()

        attendance = Attendance(
            employee=employee,
            date=att_date,
            check_in=check_in_dt,
            check_out=check_out_dt,
            status=status,
        )
        attendance.worked_hours = attendance.calculate_worked_hours()
        attendance.overtime_hours = attendance.calculate_overtime(attendance.worked_hours)
        attendance.save()

        messages.success(request, f"Attendance record for {employee.full_name} created successfully.")
        return redirect('attendance_detail', pk=attendance.pk)

    # Initial default times
    now_local = timezone.localtime()
    default_check_in = now_local.replace(hour=9, minute=0, second=0).strftime('%Y-%m-%dT%H:%M')
    default_check_out = now_local.replace(hour=18, minute=0, second=0).strftime('%Y-%m-%dT%H:%M')

    context = {
        'employees': employees,
        'today_date': today_date,
        'default_check_in': default_check_in,
        'default_check_out': default_check_out,
        'is_new': True,
    }
    return render(request, 'attendance/attendance_detail.html', context)


@require_POST
def attendance_delete_view(request, pk):
    """Deletes an attendance record."""
    attendance = get_object_or_404(Attendance, pk=pk)
    emp_name = attendance.employee.full_name
    att_date = attendance.date
    attendance.delete()
    messages.success(request, f"Attendance record for {emp_name} ({att_date}) deleted.")
    return redirect('attendance_list')


def api_calculate_overtime(request):
    """
    Live API endpoint for dynamic calculation of Worked Hours and Overtime
    when HR modifies Employee, Check In, or Check Out in the form.
    """
    employee_id = request.GET.get('employee_id') or request.POST.get('employee_id')
    check_in_str = request.GET.get('check_in') or request.POST.get('check_in')
    check_out_str = request.GET.get('check_out') or request.POST.get('check_out')
    status = request.GET.get('status') or request.POST.get('status', 'present')

    if not employee_id:
        return JsonResponse({'error': 'employee_id required'}, status=400)

    employee = Employee.objects.filter(pk=employee_id).first()
    if not employee:
        return JsonResponse({'error': 'Employee not found'}, status=404)

    check_in_dt = _parse_datetime_input(check_in_str)
    check_out_dt = _parse_datetime_input(check_out_str)
    ref_date = timezone.localtime(check_in_dt).date() if check_in_dt else timezone.localdate()

    # Calculate worked hours
    if status == 'absent':
        worked_hours = Decimal("0.00")
    elif check_in_dt and check_out_dt:
        dur = (check_out_dt - check_in_dt).total_seconds() / 3600.0
        worked_hours = Decimal(f"{max(0.0, dur):.2f}")
    elif check_in_dt and not check_out_dt:
        now = timezone.now()
        if now > check_in_dt:
            dur = (now - check_in_dt).total_seconds() / 3600.0
            worked_hours = Decimal(f"{max(0.0, dur):.2f}")
        else:
            worked_hours = Decimal("0.00")
    else:
        worked_hours = Decimal("0.00")

    # Determine applicable contract and working schedule
    contract = employee.contracts.filter(state='active', start_date__lte=ref_date).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=ref_date)
    ).first()
    if not contract:
        contract = employee.contracts.filter(state='active').first()
    if not contract:
        contract = employee.contracts.order_by('-start_date').first()

    expected_hours = Decimal("8.00")
    schedule_name = "None"
    if contract and contract.working_schedule:
        ws = contract.working_schedule
        schedule_name = ws.name
        weekday = ref_date.weekday()
        s_day = ws.days.filter(day_of_week=weekday).first()
        if s_day:
            expected_hours = s_day.hours
        else:
            if ws.days.exists():
                expected_hours = Decimal("0.00")
            elif ws.average_hours_per_day:
                expected_hours = ws.average_hours_per_day

    if worked_hours > expected_hours:
        overtime = (worked_hours - expected_hours).quantize(Decimal("0.01"))
    else:
        overtime = Decimal("0.00")

    manager_name = employee.manager.full_name if employee.manager else "—"

    return JsonResponse({
        'worked_hours': f"{worked_hours:.2f}",
        'overtime_hours': f"{overtime:.2f}",
        'overtime_display': f"{overtime:.2f} hrs",
        'department': employee.department or "—",
        'manager': manager_name,
        'schedule_name': schedule_name,
        'expected_hours': f"{expected_hours:.2f}",
    })


# ==============================================================================
# TIME OFF MANAGEMENT VIEWS (HR PORTAL)
# ==============================================================================

# ------------------------------------------------------------------------------
# 1. TIME OFF REQUESTS
# ------------------------------------------------------------------------------

def time_off_request_list_view(request):
    """
    Time Off Requests List for HR:
    - Search requests by employee name or time off type.
    - Filter by status (submitted, approved, rejected, all).
    - Note: No NEW button as HR does not create requests.
    """
    search_query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', 'all').strip().lower()

    requests_qs = LeaveRequest.objects.select_related(
        'employee', 'leave_type', 'approved_by'
    ).all().order_by('-start_date', '-id')

    if search_query:
        requests_qs = requests_qs.filter(
            Q(employee__first_name__icontains=search_query) |
            Q(employee__last_name__icontains=search_query) |
            Q(employee__code__icontains=search_query) |
            Q(leave_type__name__icontains=search_query) |
            Q(leave_type__code__icontains=search_query)
        )

    if status_filter and status_filter != 'all':
        requests_qs = requests_qs.filter(status=status_filter)

    # Status counts for filter pills
    all_count = LeaveRequest.objects.count()
    pending_count = LeaveRequest.objects.filter(status='submitted').count()
    approved_count = LeaveRequest.objects.filter(status='approved').count()
    rejected_count = LeaveRequest.objects.filter(status='rejected').count()

    context = {
        'requests': requests_qs,
        'search_query': search_query,
        'current_status': status_filter,
        'counts': {
            'all': all_count,
            'submitted': pending_count,
            'approved': approved_count,
            'rejected': rejected_count,
        }
    }

    if request.headers.get('HX-Request'):
        return render(request, 'time_off/partials/request_table_partial.html', context)

    return render(request, 'time_off/request_list.html', context)


def time_off_request_detail_view(request, pk):
    """
    Time Off Request Detail View for HR:
    - Displays request details, employee balance overview, approver info.
    - Provides back button to return to request list.
    """
    leave_req = get_object_or_404(
        LeaveRequest.objects.select_related('employee', 'leave_type', 'approved_by'),
        pk=pk
    )

    # Calculate employee balance for this leave type in the year of request
    year = leave_req.start_date.year if leave_req.start_date else timezone.now().year
    balance = LeaveService.get_leave_balance(leave_req.employee, leave_req.leave_type, year=year)

    context = {
        'req': leave_req,
        'balance': balance,
    }
    return render(request, 'time_off/request_detail.html', context)


@require_POST
def time_off_request_approve_view(request, pk):
    """
    Approve a pending leave request.
    """
    leave_req = get_object_or_404(LeaveRequest, pk=pk)
    approver = request.user if request.user.is_authenticated else None
    try:
        LeaveService.approve_leave(leave_req, approver_user=approver)
        messages.success(request, f"Leave request for {leave_req.employee.full_name} has been approved.")
    except LeaveValidationError as e:
        messages.error(request, f"Approval error: {str(e)}")
    except Exception as e:
        messages.error(request, f"Unexpected error during approval: {str(e)}")

    return redirect('time_off_request_detail', pk=pk)


@require_POST
def time_off_request_reject_view(request, pk):
    """
    Reject a pending leave request with an optional reason.
    """
    leave_req = get_object_or_404(LeaveRequest, pk=pk)
    rejection_reason = request.POST.get('rejection_reason', '').strip()
    approver = request.user if request.user.is_authenticated else None
    try:
        LeaveService.reject_leave(leave_req, approver_user=approver, rejection_reason=rejection_reason)
        messages.warning(request, f"Leave request for {leave_req.employee.full_name} was refused.")
    except LeaveValidationError as e:
        messages.error(request, f"Refusal error: {str(e)}")
    except Exception as e:
        messages.error(request, f"Unexpected error: {str(e)}")

    return redirect('time_off_request_detail', pk=pk)


# ------------------------------------------------------------------------------
# 2. ALLOCATIONS
# ------------------------------------------------------------------------------

def time_off_allocation_list_view(request):
    """
    Allocations List View:
    - Search allocations by employee name and time off type.
    - Columns: Employee, Type, Allocated, Taken, Remaining, Status.
    """
    search_query = request.GET.get('q', '').strip()
    allocations_qs = LeaveAllocation.objects.select_related(
        'employee', 'leave_type', 'approved_by'
    ).all().order_by('-created_at', '-id')

    if search_query:
        allocations_qs = allocations_qs.filter(
            Q(employee__first_name__icontains=search_query) |
            Q(employee__last_name__icontains=search_query) |
            Q(employee__code__icontains=search_query) |
            Q(leave_type__name__icontains=search_query) |
            Q(leave_type__code__icontains=search_query)
        )

    context = {
        'allocations': allocations_qs,
        'search_query': search_query,
    }

    if request.headers.get('HX-Request'):
        return render(request, 'time_off/partials/allocation_table_partial.html', context)

    return render(request, 'time_off/allocation_list.html', context)


def time_off_allocation_create_view(request):
    """
    Create Allocation:
    - Allocations created by HR are automatically approved.
    - Back button provided.
    """
    if request.method == 'POST':
        employee_id = request.POST.get('employee_id')
        leave_type_id = request.POST.get('leave_type_id')
        name = request.POST.get('name', '').strip()
        allocated_days_raw = request.POST.get('allocated_days', '0').strip()
        year_raw = request.POST.get('year', str(timezone.now().year)).strip()
        notes = request.POST.get('notes', '').strip()

        try:
            allocated_days = Decimal(allocated_days_raw)
            if allocated_days <= 0:
                raise ValueError("Allocated days must be greater than 0.")
            year = int(year_raw) if year_raw else timezone.now().year
        except (InvalidOperation, ValueError) as e:
            messages.error(request, f"Invalid number: {str(e)}")
            employees = Employee.objects.filter(is_active=True).order_by('first_name', 'last_name')
            leave_types = LeaveType.objects.filter(is_active=True).order_by('name')
            return render(request, 'time_off/allocation_detail.html', {
                'is_new': True,
                'employees': employees,
                'leave_types': leave_types,
                'form_data': request.POST,
            })

        employee = get_object_or_404(Employee, pk=employee_id)
        leave_type = get_object_or_404(LeaveType, pk=leave_type_id)

        # Allocations created by HR are automatically approved
        approver = request.user if request.user.is_authenticated else None
        allocation = LeaveAllocation.objects.create(
            employee=employee,
            leave_type=leave_type,
            name=name or f"{year} {leave_type.name} Allocation",
            allocated_days=allocated_days,
            status='approved',
            approved_by=approver,
            approved_at=timezone.now(),
            year=year,
            notes=notes,
        )
        messages.success(request, f"Allocation of {allocation.allocated_days} days for {employee.full_name} created and automatically approved.")
        return redirect('time_off_allocation_detail', pk=allocation.pk)

    employees = Employee.objects.filter(is_active=True).order_by('first_name', 'last_name')
    leave_types = LeaveType.objects.filter(is_active=True).order_by('name')
    context = {
        'is_new': True,
        'employees': employees,
        'leave_types': leave_types,
        'current_year': timezone.now().year,
    }
    return render(request, 'time_off/allocation_detail.html', context)


def time_off_allocation_detail_view(request, pk):
    """
    Allocation Detail / Edit View:
    - Displays employee, type, allocated, taken, remaining, status, approver, notes.
    - Allows HR to edit allocated days, notes, validity year.
    - Back button provided.
    """
    allocation = get_object_or_404(
        LeaveAllocation.objects.select_related('employee', 'leave_type', 'approved_by'),
        pk=pk
    )

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        allocated_days_raw = request.POST.get('allocated_days', '').strip()
        year_raw = request.POST.get('year', '').strip()
        notes = request.POST.get('notes', '').strip()

        try:
            if allocated_days_raw:
                allocated_days = Decimal(allocated_days_raw)
                if allocated_days < 0:
                    raise ValueError("Allocated days cannot be negative.")
                allocation.allocated_days = allocated_days
            if year_raw:
                allocation.year = int(year_raw)
            allocation.name = name or allocation.name
            allocation.notes = notes
            allocation.save()
            messages.success(request, f"Allocation #{allocation.id} updated successfully.")
            return redirect('time_off_allocation_detail', pk=pk)
        except (InvalidOperation, ValueError) as e:
            messages.error(request, f"Error updating allocation: {str(e)}")

    employees = Employee.objects.filter(is_active=True).order_by('first_name', 'last_name')
    leave_types = LeaveType.objects.filter(is_active=True).order_by('name')
    context = {
        'is_new': False,
        'allocation': allocation,
        'employees': employees,
        'leave_types': leave_types,
    }
    return render(request, 'time_off/allocation_detail.html', context)


@require_POST
def time_off_allocation_delete_view(request, pk):
    """
    Delete an allocation.
    """
    allocation = get_object_or_404(LeaveAllocation, pk=pk)
    emp_name = allocation.employee.full_name
    type_name = allocation.leave_type.name
    allocation.delete()
    messages.success(request, f"Allocation for {emp_name} ({type_name}) deleted successfully.")
    return redirect('time_off_allocation_list')


# ------------------------------------------------------------------------------
# 3. TIME OFF TYPES
# ------------------------------------------------------------------------------

def time_off_type_list_view(request):
    """
    Time Off Types List:
    - Can be searched by name or code.
    - Columns: Type Name, Unit, Requires Allocation, Is Paid, Status.
    - Has NEW button for HR CRUD.
    """
    search_query = request.GET.get('q', '').strip()
    types_qs = LeaveType.objects.all().order_by('name')

    if search_query:
        types_qs = types_qs.filter(
            Q(name__icontains=search_query) |
            Q(code__icontains=search_query)
        )

    context = {
        'leave_types': types_qs,
        'search_query': search_query,
    }

    if request.headers.get('HX-Request'):
        return render(request, 'time_off/partials/type_table_partial.html', context)

    return render(request, 'time_off/type_list.html', context)


def time_off_type_create_view(request):
    """
    Create a new Time Off Type:
    - Back button provided.
    """
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        code = request.POST.get('code', '').strip().upper()
        unit = request.POST.get('unit', 'days').strip().lower()
        requires_allocation = request.POST.get('requires_allocation') == 'on' or request.POST.get('requires_allocation') == 'true'
        is_paid = request.POST.get('is_paid') == 'on' or request.POST.get('is_paid') == 'true'
        color = request.POST.get('color', '#2563EB').strip()
        max_days_raw = request.POST.get('max_days_per_year', '15.00').strip()
        is_active = request.POST.get('is_active') == 'on' or request.POST.get('is_active') == 'true'
        notes = request.POST.get('notes', '').strip()

        if not name or not code:
            messages.error(request, "Name and Code are required.")
            return render(request, 'time_off/type_detail.html', {'is_new': True, 'form_data': request.POST})

        if LeaveType.objects.filter(code=code).exists():
            messages.error(request, f"Leave type with code '{code}' already exists.")
            return render(request, 'time_off/type_detail.html', {'is_new': True, 'form_data': request.POST})

        try:
            max_days = Decimal(max_days_raw) if max_days_raw else Decimal('0.00')
        except InvalidOperation:
            max_days = Decimal('0.00')

        leave_type = LeaveType.objects.create(
            name=name,
            code=code,
            unit=unit if unit in ['days', 'hours'] else 'days',
            requires_allocation=requires_allocation,
            is_paid=is_paid,
            color=color,
            max_days_per_year=max_days,
            is_active=is_active,
            notes=notes,
        )
        messages.success(request, f"Time Off Type '{leave_type.name}' ({leave_type.code}) created successfully.")
        return redirect('time_off_type_detail', pk=leave_type.pk)

    context = {
        'is_new': True,
    }
    return render(request, 'time_off/type_detail.html', context)


def time_off_type_detail_view(request, pk):
    """
    Time Off Type Detail / Edit:
    - Back button provided.
    - Displays configuration, allows editing.
    """
    leave_type = get_object_or_404(LeaveType, pk=pk)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        code = request.POST.get('code', '').strip().upper()
        unit = request.POST.get('unit', 'days').strip().lower()
        requires_allocation = request.POST.get('requires_allocation') == 'on' or request.POST.get('requires_allocation') == 'true'
        is_paid = request.POST.get('is_paid') == 'on' or request.POST.get('is_paid') == 'true'
        color = request.POST.get('color', '#2563EB').strip()
        max_days_raw = request.POST.get('max_days_per_year', '15.00').strip()
        is_active = request.POST.get('is_active') == 'on' or request.POST.get('is_active') == 'true'
        notes = request.POST.get('notes', '').strip()

        if not name or not code:
            messages.error(request, "Name and Code are required.")
            return render(request, 'time_off/type_detail.html', {'is_new': False, 'leave_type': leave_type})

        # Check unique code if changed
        if code != leave_type.code and LeaveType.objects.filter(code=code).exists():
            messages.error(request, f"Leave type with code '{code}' already exists.")
            return render(request, 'time_off/type_detail.html', {'is_new': False, 'leave_type': leave_type})

        try:
            max_days = Decimal(max_days_raw) if max_days_raw else Decimal('0.00')
        except InvalidOperation:
            max_days = leave_type.max_days_per_year

        leave_type.name = name
        leave_type.code = code
        leave_type.unit = unit if unit in ['days', 'hours'] else 'days'
        leave_type.requires_allocation = requires_allocation
        leave_type.is_paid = is_paid
        leave_type.color = color
        leave_type.max_days_per_year = max_days
        leave_type.is_active = is_active
        leave_type.notes = notes
        leave_type.save()

        messages.success(request, f"Time Off Type '{leave_type.name}' updated successfully.")
        return redirect('time_off_type_detail', pk=pk)

    context = {
        'is_new': False,
        'leave_type': leave_type,
    }
    return render(request, 'time_off/type_detail.html', context)


@require_POST
def time_off_type_delete_view(request, pk):
    """
    Delete a Time Off Type:
    - Protects against deletion if referenced by existing requests or allocations.
    """
    leave_type = get_object_or_404(LeaveType, pk=pk)
    if leave_type.leave_requests.exists() or leave_type.leave_allocations.exists():
        messages.error(
            request,
            f"Cannot delete '{leave_type.name}' because there are existing requests or allocations referencing it. You can deactivate it instead."
        )
        return redirect('time_off_type_detail', pk=pk)

    type_name = leave_type.name
    leave_type.delete()
    messages.success(request, f"Time Off Type '{type_name}' was deleted.")
    return redirect('time_off_type_list')


# ==============================================================================
# PAYROLL MODULE: DASHBOARD, PAYRUNS, PAYSLIPS, STRUCTURES & RULES
# ==============================================================================

def payroll_dashboard_view(request):
    """
    Rich interactive Payroll Dashboard displaying live analytics:
    - Total Payroll Cost, Active Employees Paid, Avg Net Salary, Overtime Hours, Attendance Rate
    - Department Salary Cost distribution (Chart.js)
    - Monthly Net Salary Trend (Chart.js)
    - Payslip Status Breakdown (Chart.js)
    - Overtime and Time Off impact summaries
    - Filterable by Period (Month) and Department
    """
    period_filter = request.GET.get('period', '').strip()
    dept_filter = request.GET.get('department', '').strip()

    # Discover available periods from existing Payruns
    all_payruns = Payrun.objects.all().order_by('-start_date')
    available_periods = []
    seen_periods = set()
    for pr in all_payruns:
        p_str = pr.start_date.strftime("%Y-%m")
        if p_str not in seen_periods:
            seen_periods.add(p_str)
            available_periods.append({
                'value': p_str,
                'label': pr.start_date.strftime("%B %Y")
            })

    # Available departments from active employees
    departments = list(
        Employee.objects.filter(is_active=True)
        .exclude(department="")
        .values_list('department', flat=True)
        .distinct()
        .order_by('department')
    )

    # Base QuerySets
    payslip_qs = Payslip.objects.select_related('employee', 'payrun', 'contract').all()
    attendance_qs = Attendance.objects.select_related('employee').all()
    leave_qs = LeaveRequest.objects.select_related('employee', 'leave_type').filter(status='approved')

    # Apply Period Filter
    if period_filter and period_filter != 'all':
        payslip_qs = payslip_qs.filter(period_start__startswith=period_filter)
        attendance_qs = attendance_qs.filter(date__startswith=period_filter)
        leave_qs = leave_qs.filter(start_date__startswith=period_filter)

    # Apply Department Filter
    if dept_filter and dept_filter != 'all':
        payslip_qs = payslip_qs.filter(employee__department=dept_filter)
        attendance_qs = attendance_qs.filter(employee__department=dept_filter)
        leave_qs = leave_qs.filter(employee__department=dept_filter)

    # 1. KPI Cards
    total_payslips = payslip_qs.count()
    total_gross = sum((p.gross_wage for p in payslip_qs), Decimal('0.00'))
    total_net = sum((p.net_wage for p in payslip_qs), Decimal('0.00'))
    total_deductions = sum((p.total_deductions for p in payslip_qs), Decimal('0.00'))

    paid_payslips = payslip_qs.filter(state='paid')
    active_employees_paid = paid_payslips.values('employee_id').distinct().count()
    if active_employees_paid == 0 and total_payslips > 0:
        active_employees_paid = payslip_qs.values('employee_id').distinct().count()

    avg_net = (total_net / total_payslips).quantize(Decimal('0.01')) if total_payslips > 0 else Decimal('0.00')

    # Overtime metrics
    total_overtime_hours = sum((att.overtime_hours for att in attendance_qs if att.overtime_hours), Decimal('0.00'))

    # Attendance compliance rate
    total_attendances = attendance_qs.count()
    on_time_attendances = attendance_qs.filter(overtime_hours__gte=0).count()
    attendance_rate = round((on_time_attendances / total_attendances) * 100) if total_attendances > 0 else 98

    # 2. Salary Cost by Department (for Bar Chart)
    dept_totals = {}
    for p in payslip_qs:
        dept = p.employee.department or "General"
        dept_totals[dept] = dept_totals.get(dept, Decimal('0.00')) + p.net_wage

    dept_chart_labels = list(dept_totals.keys())
    dept_chart_data = [float(dept_totals[d]) for d in dept_chart_labels]

    # 3. Monthly Net Salary Trend (for Line Chart) - Last 6 months
    monthly_trend = {}
    for p in Payslip.objects.select_related('payrun').all():
        m_key = p.period_start.strftime("%b %Y")
        m_sort = p.period_start.strftime("%Y-%m")
        if m_sort not in monthly_trend:
            monthly_trend[m_sort] = {'label': m_key, 'total': Decimal('0.00')}
        monthly_trend[m_sort]['total'] += p.net_wage

    sorted_months = sorted(monthly_trend.keys())[-6:]
    trend_chart_labels = [monthly_trend[k]['label'] for k in sorted_months]
    trend_chart_data = [float(monthly_trend[k]['total']) for k in sorted_months]

    # 4. Payslip Status Breakdown
    status_counts = {
        'draft': payslip_qs.filter(state='draft').count(),
        'computed': payslip_qs.filter(state='computed').count(),
        'validated': payslip_qs.filter(state='validated').count(),
        'paid': payslip_qs.filter(state='paid').count(),
    }

    # 5. Overtime Breakdown by Department
    dept_overtime = {}
    for att in attendance_qs:
        if att.overtime_hours and att.overtime_hours > 0:
            dept = att.employee.department or "General"
            dept_overtime[dept] = dept_overtime.get(dept, Decimal('0.00')) + att.overtime_hours

    dept_overtime_list = [
        {'department': d, 'hours': float(h)}
        for d, h in sorted(dept_overtime.items(), key=lambda x: x[1], reverse=True)[:5]
    ]

    # 6. Time Off Impact Overview
    approved_leave_count = leave_qs.count()
    total_leave_days = sum((lr.number_of_days for lr in leave_qs), Decimal('0.00'))
    unpaid_leave_days = sum(
        (lr.number_of_days for lr in leave_qs if not lr.leave_type.is_paid),
        Decimal('0.00')
    )

    # 7. Recent Payruns
    recent_payruns = all_payruns[:5]

    context = {
        'period_filter': period_filter,
        'dept_filter': dept_filter,
        'available_periods': available_periods,
        'departments': departments,
        'total_payslips': total_payslips,
        'total_gross': total_gross,
        'total_net': total_net,
        'total_deductions': total_deductions,
        'active_employees_paid': active_employees_paid,
        'avg_net': avg_net,
        'total_overtime_hours': total_overtime_hours,
        'attendance_rate': attendance_rate,
        'dept_chart_labels': json.dumps(dept_chart_labels),
        'dept_chart_data': json.dumps(dept_chart_data),
        'trend_chart_labels': json.dumps(trend_chart_labels),
        'trend_chart_data': json.dumps(trend_chart_data),
        'status_counts': status_counts,
        'status_chart_data': json.dumps([
            status_counts['draft'],
            status_counts['computed'],
            status_counts['validated'],
            status_counts['paid'],
        ]),
        'dept_overtime_list': dept_overtime_list,
        'approved_leave_count': approved_leave_count,
        'total_leave_days': total_leave_days,
        'unpaid_leave_days': unpaid_leave_days,
        'recent_payruns': recent_payruns,
    }
    return render(request, 'payroll/dashboard.html', context)


# ==============================================================================
# PAYRUN VIEWS & WORKFLOW
# ==============================================================================

def payrun_list_view(request):
    """
    Payrun list view showing all batches, search, status filter tabs,
    and triggering the 2-step New Pay Run wizard modal.
    """
    search_query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', 'all').strip()

    payruns = Payrun.objects.select_related('salary_structure').prefetch_related('payslips').all()

    if search_query:
        payruns = payruns.filter(
            Q(name__icontains=search_query) |
            Q(salary_structure__name__icontains=search_query) |
            Q(salary_structure__code__icontains=search_query)
        )

    if status_filter in ['draft', 'computed', 'validated', 'paid', 'cancelled']:
        payruns = payruns.filter(state=status_filter)

    total_count = Payrun.objects.count()
    draft_count = Payrun.objects.filter(state='draft').count()
    computed_count = Payrun.objects.filter(state='computed').count()
    validated_count = Payrun.objects.filter(state='validated').count()
    paid_count = Payrun.objects.filter(state='paid').count()

    structures = SalaryStructure.objects.filter(is_active=True).order_by('name')

    # Default period for modal: 1st of current month to end of current month
    today = timezone.now().date()
    first_day = today.replace(day=1)
    if today.month == 12:
        last_day = today.replace(day=31)
    else:
        next_month = today.replace(month=today.month + 1, day=1)
        last_day = next_month - datetime.timedelta(days=1)

    default_name = f"{first_day.strftime('%B %Y')} Regular Payrun"

    context = {
        'payruns': payruns,
        'search_query': search_query,
        'status_filter': status_filter,
        'total_count': total_count,
        'draft_count': draft_count,
        'computed_count': computed_count,
        'validated_count': validated_count,
        'paid_count': paid_count,
        'structures': structures,
        'default_start_date': first_day.isoformat(),
        'default_end_date': last_day.isoformat(),
        'default_name': default_name,
    }
    return render(request, 'payroll/payrun_list.html', context)


@require_GET
def api_eligible_employees_for_payrun(request):
    """
    AJAX helper for Stage 2 of the New Pay Run wizard.
    Given structure_id, start_date, and end_date, returns eligible employees with active contracts.
    """
    structure_id = request.GET.get('structure_id')
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')

    if not (start_date_str and end_date_str):
        return JsonResponse({'employees': [], 'error': 'Dates are required'}, status=400)

    try:
        start_date = datetime.date.fromisoformat(start_date_str)
        end_date = datetime.date.fromisoformat(end_date_str)
    except ValueError:
        return JsonResponse({'employees': [], 'error': 'Invalid date format'}, status=400)

    contracts_qs = Contract.objects.filter(
        state='active',
        employee__is_active=True,
        start_date__lte=end_date,
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=start_date)
    ).select_related('employee', 'salary_structure', 'working_schedule')

    if structure_id:
        contracts_qs = contracts_qs.filter(salary_structure_id=structure_id)

    seen = set()
    result = []
    for c in contracts_qs:
        if c.employee_id not in seen:
            seen.add(c.employee_id)
            emp = c.employee
            result.append({
                'id': emp.id,
                'code': emp.code,
                'name': emp.full_name,
                'department': emp.department or '—',
                'job_title': emp.job_title or '—',
                'wage': float(c.wage),
                'schedule': c.working_schedule.name if c.working_schedule else 'Standard 40h',
                'has_bank_details': emp.has_bank_details,
            })

    result.sort(key=lambda x: x['code'])
    return JsonResponse({'employees': result})


@require_POST
def payrun_create_view(request):
    """
    Creates a Payrun in 2-step flow. Only selected employees from Stage 2 are included!
    """
    name = request.POST.get('name', '').strip()
    structure_id = request.POST.get('salary_structure')
    start_date_str = request.POST.get('start_date')
    end_date_str = request.POST.get('end_date')
    selected_employees = request.POST.getlist('selected_employees')

    if not name:
        messages.error(request, "Payrun name is required.")
        return redirect('payrun_list')

    try:
        start_date = datetime.date.fromisoformat(start_date_str)
        end_date = datetime.date.fromisoformat(end_date_str)
    except (ValueError, TypeError):
        messages.error(request, "Invalid period dates.")
        return redirect('payrun_list')

    if start_date > end_date:
        messages.error(request, "Start date cannot be after end date.")
        return redirect('payrun_list')

    structure = get_object_or_404(SalaryStructure, pk=structure_id) if structure_id else None
    if not structure:
        messages.error(request, "Salary Structure is required.")
        return redirect('payrun_list')

    employee_ids = [int(e_id) for e_id in selected_employees if e_id.isdigit()]
    if not employee_ids:
        messages.error(request, "Please select at least one employee for the Payrun.")
        return redirect('payrun_list')

    try:
        payrun = PayrunService.create_payrun_with_employees(
            name=name,
            salary_structure=structure,
            start_date=start_date,
            end_date=end_date,
            employee_ids=employee_ids
        )
        messages.success(
            request,
            f"Payrun '{payrun.name}' created with {len(employee_ids)} selected employee(s). Ready for computation."
        )
        return redirect('payrun_detail', pk=payrun.pk)
    except Exception as e:
        messages.error(request, f"Failed to create payrun: {str(e)}")
        return redirect('payrun_list')


def payrun_detail_view(request, pk):
    """
    Payrun Detail View:
    - Status badge & workflow action buttons
    - Pre-flight validation report (missing bank details, contract errors)
    - Summary wage buckets
    - Itemized payslips table with links to individual payslips and PDF downloads
    """
    payrun = get_object_or_404(
        Payrun.objects.select_related('salary_structure').prefetch_related('payslips__employee', 'payslips__contract'),
        pk=pk
    )

    # Preflight validation report
    validation_report = PayrunService.validate_payrun_preflight(payrun)

    payslips = payrun.payslips.select_related('employee', 'contract').all().order_by('employee__code')

    total_basic = sum((p.basic_wage for p in payslips), Decimal('0.00'))
    total_gross = sum((p.gross_wage for p in payslips), Decimal('0.00'))
    total_deductions = sum((p.total_deductions for p in payslips), Decimal('0.00'))
    total_net = sum((p.net_wage for p in payslips), Decimal('0.00'))
    total_allowances = max(Decimal('0.00'), total_gross - total_basic)

    today = timezone.now().date().isoformat()

    context = {
        'payrun': payrun,
        'validation_report': validation_report,
        'payslips': payslips,
        'total_basic': total_basic,
        'total_gross': total_gross,
        'total_deductions': total_deductions,
        'total_net': total_net,
        'total_allowances': total_allowances,
        'today': today,
    }
    return render(request, 'payroll/payrun_detail.html', context)


@require_POST
def payrun_compute_view(request, pk):
    """Computes/recomputes payroll for the Payrun."""
    payrun = get_object_or_404(Payrun, pk=pk)
    try:
        res = PayrunService.compute_payrun(payrun)
        messages.success(
            request,
            f"Successfully computed payroll for {res['computed_count']} employee(s). Total Net: ₹{res['total_net']:,.2f}"
        )
    except PayrunWorkflowError as e:
        messages.error(request, str(e))
    except Exception as e:
        messages.error(request, f"Computation error: {str(e)}")

    return redirect('payrun_detail', pk=pk)


@require_POST
def payrun_validate_view(request, pk):
    """Transitions Payrun from computed to validated."""
    payrun = get_object_or_404(Payrun, pk=pk)
    try:
        PayrunService.validate_payrun(payrun)
        messages.success(request, f"Payrun '{payrun.name}' has been validated successfully.")
    except PayrunWorkflowError as e:
        messages.error(request, str(e))
    except Exception as e:
        messages.error(request, f"Validation error: {str(e)}")

    return redirect('payrun_detail', pk=pk)


@require_POST
def payrun_mark_paid_view(request, pk):
    """Transitions Payrun from validated to paid with payment date."""
    payrun = get_object_or_404(Payrun, pk=pk)
    payment_date_str = request.POST.get('payment_date')
    payment_date = None
    if payment_date_str:
        try:
            payment_date = datetime.date.fromisoformat(payment_date_str)
        except ValueError:
            pass

    try:
        PayrunService.mark_payrun_paid(payrun, payment_date=payment_date)
        messages.success(request, f"Payrun '{payrun.name}' marked as PAID. Disbursements finalized.")
    except PayrunWorkflowError as e:
        messages.error(request, str(e))
    except Exception as e:
        messages.error(request, f"Disbursement error: {str(e)}")

    return redirect('payrun_detail', pk=pk)


@require_POST
def payrun_reset_draft_view(request, pk):
    """Resets Payrun back to draft state."""
    payrun = get_object_or_404(Payrun, pk=pk)
    try:
        PayrunService.reset_to_draft(payrun)
        messages.info(request, f"Payrun '{payrun.name}' has been reset to Draft.")
    except PayrunWorkflowError as e:
        messages.error(request, str(e))
    except Exception as e:
        messages.error(request, f"Reset error: {str(e)}")

    return redirect('payrun_detail', pk=pk)


@require_POST
def payrun_delete_view(request, pk):
    """Deletes a draft payrun."""
    payrun = get_object_or_404(Payrun, pk=pk)
    if payrun.state in ('validated', 'paid'):
        messages.error(request, f"Cannot delete a '{payrun.state}' payrun. Only Draft or Cancelled payruns can be removed.")
        return redirect('payrun_detail', pk=pk)

    name = payrun.name
    payrun.delete()
    messages.success(request, f"Payrun '{name}' was deleted.")
    return redirect('payrun_list')


# ==============================================================================
# PAYSLIP VIEWS
# ==============================================================================

def payslip_list_view(request):
    """
    Overview list of all payslips across all payruns, filterable by payrun, status, or employee.
    """
    search_query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', 'all').strip()
    payrun_id = request.GET.get('payrun', '').strip()

    payslips = Payslip.objects.select_related('employee', 'contract', 'salary_structure', 'payrun').all()

    if search_query:
        payslips = payslips.filter(
            Q(employee__first_name__icontains=search_query) |
            Q(employee__last_name__icontains=search_query) |
            Q(employee__code__icontains=search_query) |
            Q(payrun__name__icontains=search_query)
        )

    if status_filter in ['draft', 'computed', 'validated', 'paid', 'cancelled']:
        payslips = payslips.filter(state=status_filter)

    if payrun_id and payrun_id.isdigit():
        payslips = payslips.filter(payrun_id=payrun_id)

    total_count = Payslip.objects.count()
    all_payruns = Payrun.objects.all().order_by('-start_date')

    context = {
        'payslips': payslips,
        'search_query': search_query,
        'status_filter': status_filter,
        'payrun_id': payrun_id,
        'total_count': total_count,
        'all_payruns': all_payruns,
    }
    return render(request, 'payroll/payslip_list.html', context)


def payslip_detail_view(request, pk):
    """
    Detailed individual payslip view:
    - Employee & Contract snapshot
    - Bank details (with disbursement warnings if incomplete)
    - Itemized line items from PayslipLine
    - PDF download link & simulated email delivery
    """
    payslip = get_object_or_404(
        Payslip.objects.select_related('employee', 'contract', 'salary_structure', 'payrun')
        .prefetch_related('lines'),
        pk=pk
    )

    lines = payslip.lines.all().order_by('sequence', 'id')
    basic_lines = [l for l in lines if l.category == 'BASIC']
    allowance_lines = [l for l in lines if l.category == 'ALLOWANCE']
    deduction_lines = [l for l in lines if l.category == 'DEDUCTION']
    gross_lines = [l for l in lines if l.category == 'GROSS']
    net_lines = [l for l in lines if l.category == 'NET']

    context = {
        'payslip': payslip,
        'allowances': payslip.allowances,
        'lines': lines,
        'basic_lines': basic_lines,
        'allowance_lines': allowance_lines,
        'deduction_lines': deduction_lines,
        'gross_lines': gross_lines,
        'net_lines': net_lines,
    }
    return render(request, 'payroll/payslip_detail.html', context)


@require_POST
def payslip_send_email_view(request, pk):
    """Simulates sending payslip PDF via email to employee."""
    payslip = get_object_or_404(Payslip.objects.select_related('employee', 'payrun'), pk=pk)
    messages.success(
        request,
        f"Payslip for {payslip.period_start.strftime('%B %Y')} has been sent to {payslip.employee.full_name} ({payslip.employee.email})."
    )
    return redirect('payslip_detail', pk=pk)


# ==============================================================================
# SALARY STRUCTURE VIEWS
# ==============================================================================

def salary_structure_list_view(request):
    """Lists all salary structures with rule counts."""
    structures = SalaryStructure.objects.prefetch_related('structure_rules__rule').all().order_by('name')
    context = {
        'structures': structures,
        'total_count': structures.count(),
    }
    return render(request, 'payroll/structure_list.html', context)


def salary_structure_form_view(request, pk=None):
    """Create or edit a Salary Structure, including rule sequence assignment."""
    is_new = pk is None
    structure = get_object_or_404(SalaryStructure, pk=pk) if not is_new else None

    all_rules = SalaryRule.objects.all().order_by('sequence', 'code')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        code = request.POST.get('code', '').strip().upper()
        description = request.POST.get('description', '').strip()
        is_active = request.POST.get('is_active') == 'on'

        if not name or not code:
            messages.error(request, "Name and Code are required.")
            return render(request, 'payroll/structure_form.html', {
                'is_new': is_new, 'structure': structure, 'all_rules': all_rules
            })

        # Check code uniqueness
        dup_qs = SalaryStructure.objects.filter(code=code)
        if not is_new:
            dup_qs = dup_qs.exclude(pk=structure.pk)
        if dup_qs.exists():
            messages.error(request, f"Salary Structure with code '{code}' already exists.")
            return render(request, 'payroll/structure_form.html', {
                'is_new': is_new, 'structure': structure, 'all_rules': all_rules
            })

        if is_new:
            structure = SalaryStructure.objects.create(
                name=name, code=code, description=description, is_active=is_active
            )
            messages.success(request, f"Salary Structure '{structure.name}' created.")
        else:
            structure.name = name
            structure.code = code
            structure.description = description
            structure.is_active = is_active
            structure.save()
            messages.success(request, f"Salary Structure '{structure.name}' updated.")

        # Update assigned rules
        selected_rule_ids = request.POST.getlist('assigned_rules')
        rule_ids = [int(rid) for rid in selected_rule_ids if rid.isdigit()]

        SalaryStructureRule.objects.filter(structure=structure).delete()
        new_assignments = []
        for rid in rule_ids:
            rule_obj = SalaryRule.objects.filter(id=rid).first()
            if rule_obj:
                seq_val = request.POST.get(f'sequence_{rid}', str(rule_obj.sequence))
                seq = int(seq_val) if seq_val.isdigit() else rule_obj.sequence
                new_assignments.append(SalaryStructureRule(
                    structure=structure,
                    rule=rule_obj,
                    sequence=seq
                ))
        SalaryStructureRule.objects.bulk_create(new_assignments)

        return redirect('salary_structure_list')

    assigned_rule_ids = set()
    rule_sequences = {}
    if structure:
        for sr in structure.structure_rules.all():
            assigned_rule_ids.add(sr.rule_id)
            rule_sequences[sr.rule_id] = sr.sequence

    rule_items = []
    for r in all_rules:
        rule_items.append({
            'rule': r,
            'is_assigned': r.id in assigned_rule_ids,
            'sequence': rule_sequences.get(r.id, r.sequence),
        })

    context = {
        'is_new': is_new,
        'structure': structure,
        'rule_items': rule_items,
    }
    return render(request, 'payroll/structure_form.html', context)


@require_POST
def salary_structure_delete_view(request, pk):
    """Deletes a salary structure if not used in existing payruns."""
    structure = get_object_or_404(SalaryStructure, pk=pk)
    if structure.payruns.exists():
        messages.error(request, f"Cannot delete '{structure.name}' because it is linked to existing Payruns.")
        return redirect('salary_structure_list')

    name = structure.name
    structure.delete()
    messages.success(request, f"Salary Structure '{name}' deleted.")
    return redirect('salary_structure_list')


# ==============================================================================
# SALARY RULE VIEWS
# ==============================================================================

def salary_rule_list_view(request):
    """List of all salary rules with category filter pills."""
    category_filter = request.GET.get('category', 'all').strip().upper()
    rules = SalaryRule.objects.all().order_by('sequence', 'code')

    if category_filter in ['BASIC', 'ALLOWANCE', 'DEDUCTION', 'GROSS', 'NET']:
        rules = rules.filter(category=category_filter)

    categories = [
        ('all', 'All Categories'),
        ('BASIC', 'Basic Salary'),
        ('ALLOWANCE', 'Allowance'),
        ('GROSS', 'Gross'),
        ('DEDUCTION', 'Deduction'),
        ('NET', 'Net Salary'),
    ]

    context = {
        'rules': rules,
        'category_filter': category_filter,
        'categories': categories,
        'total_count': SalaryRule.objects.count(),
    }
    return render(request, 'payroll/rule_list.html', context)


def salary_rule_form_view(request, pk=None):
    """Create or edit a Salary Rule (Fixed, Percentage, Formula)."""
    is_new = pk is None
    rule = get_object_or_404(SalaryRule, pk=pk) if not is_new else None

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        code = request.POST.get('code', '').strip().upper()
        category = request.POST.get('category', 'ALLOWANCE')
        sequence_str = request.POST.get('sequence', '10')
        amount_type = request.POST.get('amount_type', 'fixed')
        fixed_amount_str = request.POST.get('fixed_amount', '0.00')
        percentage_str = request.POST.get('percentage', '0.00')
        percentage_base_code = request.POST.get('percentage_base_code', '').strip().upper()
        formula = request.POST.get('formula', '').strip()
        is_active = request.POST.get('is_active') == 'on'

        if not name or not code:
            messages.error(request, "Name and Code are required.")
            return render(request, 'payroll/rule_form.html', {'is_new': is_new, 'rule': rule})

        # Validate unique code
        dup = SalaryRule.objects.filter(code=code)
        if not is_new:
            dup = dup.exclude(pk=rule.pk)
        if dup.exists():
            messages.error(request, f"Salary Rule with code '{code}' already exists.")
            return render(request, 'payroll/rule_form.html', {'is_new': is_new, 'rule': rule})

        sequence = int(sequence_str) if sequence_str.isdigit() else 10
        try:
            fixed_amount = Decimal(fixed_amount_str) if fixed_amount_str else Decimal('0.00')
        except InvalidOperation:
            fixed_amount = Decimal('0.00')

        try:
            percentage = Decimal(percentage_str) if percentage_str else Decimal('0.00')
        except InvalidOperation:
            percentage = Decimal('0.00')

        if is_new:
            rule = SalaryRule.objects.create(
                name=name,
                code=code,
                category=category,
                sequence=sequence,
                amount_type=amount_type,
                fixed_amount=fixed_amount,
                percentage=percentage,
                percentage_base_code=percentage_base_code,
                formula=formula,
                is_active=is_active
            )
            messages.success(request, f"Salary Rule '{rule.name}' ({rule.code}) created.")
        else:
            rule.name = name
            rule.code = code
            rule.category = category
            rule.sequence = sequence
            rule.amount_type = amount_type
            rule.fixed_amount = fixed_amount
            rule.percentage = percentage
            rule.percentage_base_code = percentage_base_code
            rule.formula = formula
            rule.is_active = is_active
            rule.save()
            messages.success(request, f"Salary Rule '{rule.name}' ({rule.code}) updated.")

        return redirect('salary_rule_list')

    context = {
        'is_new': is_new,
        'rule': rule,
        'categories': SalaryRule.CATEGORY_CHOICES,
        'amount_types': SalaryRule.AMOUNT_TYPE_CHOICES,
    }
    return render(request, 'payroll/rule_form.html', context)


@require_POST
def salary_rule_delete_view(request, pk):
    """Deletes a salary rule."""
    rule = get_object_or_404(SalaryRule, pk=pk)
    name = rule.name
    rule.delete()
    messages.success(request, f"Salary Rule '{name}' deleted.")
    return redirect('salary_rule_list')



