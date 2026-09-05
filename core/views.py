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
