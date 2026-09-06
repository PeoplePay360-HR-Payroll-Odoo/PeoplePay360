from django.urls import path
from . import views

urlpatterns = [
    path('', views.employee_list_view, name='employee_list'),
    path('employee/<int:pk>/', views.employee_detail_view, name='employee_detail'),

    # Working Schedules
    path('schedules/', views.working_schedule_list_view, name='working_schedule_list'),
    path('schedules/new/', views.working_schedule_form_view, name='working_schedule_create'),
    path('schedules/<int:pk>/', views.working_schedule_form_view, name='working_schedule_detail'),
    path('schedules/<int:pk>/delete/', views.working_schedule_delete_view, name='working_schedule_delete'),
    path('schedules/<int:pk>/toggle-status/', views.working_schedule_toggle_status_view, name='working_schedule_toggle_status'),

    # Contracts (HR CRUD)
    path('contracts/', views.contract_list_view, name='contract_list'),
    path('contracts/new/', views.contract_create_view, name='contract_create'),
    path('contracts/<int:pk>/', views.contract_detail_view, name='contract_detail'),
    path('contracts/<int:pk>/delete/', views.contract_delete_view, name='contract_delete'),

    # Attendance (HR CRUD & Overtime)
    path('attendance/', views.attendance_list_view, name='attendance_list'),
    path('attendance/new/', views.attendance_create_view, name='attendance_create'),
    path('attendance/<int:pk>/', views.attendance_detail_view, name='attendance_detail'),
    path('attendance/<int:pk>/delete/', views.attendance_delete_view, name='attendance_delete'),
    path('api/attendance/calculate-overtime/', views.api_calculate_overtime, name='api_calculate_overtime'),
    # PDF Payslip View & Download
    path('api/payslips/<int:payslip_id>/pdf/', views.payslip_pdf_view, name='payslip_pdf'),
    path('payslips/<int:payslip_id>/pdf/', views.payslip_pdf_view, name='payslip_pdf_short'),

    # REST APIs for Person 3 (Frontend team)
    path('api/payruns/', views.api_payruns_list, name='api_payruns_list'),
    path('api/payruns/<int:pk>/', views.api_payrun_detail, name='api_payrun_detail'),
    path('api/payruns/<int:pk>/compute/', views.api_payrun_compute, name='api_payrun_compute'),
    path('api/payruns/<int:pk>/validate/', views.api_payrun_validate, name='api_payrun_validate'),
    path('api/payruns/<int:pk>/mark-paid/', views.api_payrun_mark_paid, name='api_payrun_mark_paid'),

    path('api/payslips/<int:pk>/', views.api_payslip_detail, name='api_payslip_detail'),
    path('api/employees/', views.api_employees_list, name='api_employees_list'),
    path('api/salary-structures/', views.api_salary_structures_list, name='api_salary_structures_list'),

    # Time Off & Leave Management APIs (Person 3)
    path('api/leaves/types/', views.api_leave_types_list, name='api_leave_types_list'),
    path('api/leaves/balances/', views.api_leave_balances, name='api_leave_balances'),
    path('api/leaves/requests/', views.api_leave_requests, name='api_leave_requests'),
    path('api/leaves/requests/<int:pk>/approve/', views.api_leave_request_approve, name='api_leave_request_approve'),
    path('api/leaves/requests/<int:pk>/reject/', views.api_leave_request_reject, name='api_leave_request_reject'),

    # Time Off Management (HR Web Views)
    # 1. Requests
    path('time-off/requests/', views.time_off_request_list_view, name='time_off_request_list'),
    path('time-off/requests/<int:pk>/', views.time_off_request_detail_view, name='time_off_request_detail'),
    path('time-off/requests/<int:pk>/approve/', views.time_off_request_approve_view, name='time_off_request_approve'),
    path('time-off/requests/<int:pk>/reject/', views.time_off_request_reject_view, name='time_off_request_reject'),

    # 2. Allocations (HR CRUD)
    path('time-off/allocations/', views.time_off_allocation_list_view, name='time_off_allocation_list'),
    path('time-off/allocations/new/', views.time_off_allocation_create_view, name='time_off_allocation_create'),
    path('time-off/allocations/<int:pk>/', views.time_off_allocation_detail_view, name='time_off_allocation_detail'),
    path('time-off/allocations/<int:pk>/delete/', views.time_off_allocation_delete_view, name='time_off_allocation_delete'),

    # 3. Time Off Types (HR CRUD)
    path('time-off/types/', views.time_off_type_list_view, name='time_off_type_list'),
    path('time-off/types/new/', views.time_off_type_create_view, name='time_off_type_create'),
    path('time-off/types/<int:pk>/', views.time_off_type_detail_view, name='time_off_type_detail'),
    path('time-off/types/<int:pk>/delete/', views.time_off_type_delete_view, name='time_off_type_delete'),
]
