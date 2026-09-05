from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Q
from .models import Employee

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
import json
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_GET, require_POST
from django.shortcuts import get_object_or_404
from .models import (
    Payrun,
    Payslip,
    Employee,
    SalaryStructure,
)
from .services.payrun_service import PayrunService, PayrunWorkflowError
from .services.pdf_generator import PayslipPDFGenerator


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
