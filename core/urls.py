from django.urls import path
from . import views

urlpatterns = [
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
]
