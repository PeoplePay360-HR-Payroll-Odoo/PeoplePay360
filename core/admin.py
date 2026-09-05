from django.contrib import admin
from .models import (
    WorkingSchedule,
    ScheduleDay,
    Employee,
    SalaryStructure,
    SalaryRule,
    SalaryStructureRule,
    Contract,
    Payrun,
    Payslip,
    PayslipLine,
)


class ScheduleDayInline(admin.TabularInline):
    model = ScheduleDay
    extra = 0
    ordering = ['day_of_week', 'work_from']


@admin.register(WorkingSchedule)
class WorkingScheduleAdmin(admin.ModelAdmin):
    list_display = ('name', 'average_hours_per_day', 'created_at')
    search_fields = ('name',)
    inlines = [ScheduleDayInline]


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = (
        'code',
        'full_name',
        'email',
        'department',
        'job_title',
        'has_bank_details',
        'is_active',
        'date_of_joining'
    )
    list_filter = ('is_active', 'department')
    search_fields = ('code', 'first_name', 'last_name', 'email', 'department')
    fieldsets = (
        ('Basic Information', {
            'fields': ('code', 'first_name', 'last_name', 'email', 'date_of_joining', 'is_active')
        }),
        ('Job Details', {
            'fields': ('department', 'job_title')
        }),
        ('Banking & Disbursement', {
            'fields': ('bank_name', 'bank_account_number', 'bank_ifsc_or_swift'),
            'description': 'Required for disbursement and bank validation warnings during payruns.'
        }),
    )

    @admin.display(boolean=True, description="Bank Info Complete")
    def has_bank_details(self, obj):
        return obj.has_bank_details


class SalaryStructureRuleInline(admin.TabularInline):
    model = SalaryStructureRule
    extra = 1
    ordering = ['sequence']


@admin.register(SalaryStructure)
class SalaryStructureAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'is_active', 'created_at')
    search_fields = ('code', 'name')
    list_filter = ('is_active',)
    inlines = [SalaryStructureRuleInline]


@admin.register(SalaryRule)
class SalaryRuleAdmin(admin.ModelAdmin):
    list_display = (
        'code',
        'name',
        'category',
        'sequence',
        'amount_type',
        'fixed_amount',
        'percentage',
        'percentage_base_code',
        'is_active'
    )
    list_filter = ('category', 'amount_type', 'is_active')
    search_fields = ('code', 'name')
    ordering = ['sequence', 'code']


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'employee',
        'wage',
        'wage_type',
        'salary_structure',
        'start_date',
        'end_date',
        'state'
    )
    list_filter = ('state', 'wage_type', 'salary_structure')
    search_fields = ('name', 'employee__first_name', 'employee__last_name', 'employee__code')


class PayslipLineInline(admin.TabularInline):
    model = PayslipLine
    extra = 0
    readonly_fields = ('code', 'name', 'category', 'sequence', 'rate', 'amount', 'total')
    can_delete = False


@admin.register(Payslip)
class PayslipAdmin(admin.ModelAdmin):
    list_display = (
        'employee',
        'payrun',
        'period_start',
        'period_end',
        'basic_wage',
        'gross_wage',
        'total_deductions',
        'net_wage',
        'state'
    )
    list_filter = ('state', 'payrun')
    search_fields = ('employee__first_name', 'employee__last_name', 'employee__code', 'payrun__name')
    readonly_fields = ('basic_wage', 'gross_wage', 'total_deductions', 'net_wage')
    inlines = [PayslipLineInline]


class PayslipInline(admin.TabularInline):
    model = Payslip
    extra = 0
    fields = ('employee', 'gross_wage', 'total_deductions', 'net_wage', 'state')
    readonly_fields = ('employee', 'gross_wage', 'total_deductions', 'net_wage', 'state')
    show_change_link = True
    can_delete = False


@admin.register(Payrun)
class PayrunAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'salary_structure',
        'start_date',
        'end_date',
        'state',
        'payslip_count',
        'total_gross',
        'total_net'
    )
    list_filter = ('state', 'salary_structure')
    search_fields = ('name',)
    inlines = [PayslipInline]


@admin.register(PayslipLine)
class PayslipLineAdmin(admin.ModelAdmin):
    list_display = ('payslip', 'code', 'name', 'category', 'sequence', 'total')
    list_filter = ('category',)
    search_fields = ('code', 'name', 'payslip__employee__first_name', 'payslip__employee__last_name')
