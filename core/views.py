import datetime
from decimal import Decimal, InvalidOperation
from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Q
from django.contrib import messages
import json
import datetime
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
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
        'attendance_count': 0,
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

