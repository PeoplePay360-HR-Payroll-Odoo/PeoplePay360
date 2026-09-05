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
    Employee,
    SalaryStructure,
    LeaveType,
    LeaveRequest,
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


