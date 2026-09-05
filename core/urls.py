from django.urls import path
from . import views

urlpatterns = [
    path('', views.employee_list_view, name='employee_list'),
    path('employee/<int:pk>/', views.employee_detail_view, name='employee_detail'),
]
