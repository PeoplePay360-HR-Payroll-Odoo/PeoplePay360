from django.contrib import admin, messages
from django.utils.html import format_html
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
from .services.payrun_service import PayrunService, PayrunWorkflowError
from .services.pdf_generator import PayslipPDFGenerator


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
        'state',
        'pdf_download_link',
    )
    list_filter = ('state', 'payrun')
    search_fields = ('employee__first_name', 'employee__last_name', 'employee__code', 'payrun__name')
    readonly_fields = ('basic_wage', 'gross_wage', 'total_deductions', 'net_wage', 'pdf_download_link')
    inlines = [PayslipLineInline]
    actions = ['generate_selected_pdfs']

    @admin.display(description="PDF Payslip")
    def pdf_download_link(self, obj):
        url = f"/payslips/{obj.id}/pdf/"
        if obj.pdf_file:
            return format_html(
                '<a class="button" href="{}" target="_blank" style="background-color: #0F766E; color: white; padding: 3px 8px; border-radius: 4px; text-decoration: none;">📄 View PDF</a>',
                url
            )
        return format_html(
            '<a class="button" href="{}" target="_blank" style="background-color: #2563EB; color: white; padding: 3px 8px; border-radius: 4px; text-decoration: none;">⚡ Generate PDF</a>',
            url
        )

    @admin.action(description="📄 Generate / Refresh PDF for selected Payslips")
    def generate_selected_pdfs(self, request, queryset):
        success_count = 0
        for payslip in queryset:
            try:
                PayslipPDFGenerator.generate_and_save(payslip)
                success_count += 1
            except Exception as e:
                self.message_user(request, f"Failed PDF for {payslip.employee.code}: {str(e)}", level=messages.ERROR)
        self.message_user(request, f"Generated {success_count} PDF payslip(s) successfully.", level=messages.SUCCESS)


class PayslipInline(admin.TabularInline):
    model = Payslip
    extra = 0
    fields = ('employee', 'gross_wage', 'total_deductions', 'net_wage', 'state', 'pdf_link')
    readonly_fields = ('employee', 'gross_wage', 'total_deductions', 'net_wage', 'state', 'pdf_link')
    show_change_link = True
    can_delete = False

    @admin.display(description="PDF")
    def pdf_link(self, obj):
        if obj.id:
            url = f"/payslips/{obj.id}/pdf/"
            return format_html('<a href="{}" target="_blank">📄 PDF</a>', url)
        return "-"


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
    readonly_fields = ('total_gross', 'total_net', 'payslip_count')
    inlines = [PayslipInline]
    actions = ['compute_payruns', 'validate_payruns', 'mark_payruns_paid', 'generate_payrun_pdfs', 'reset_to_draft']

    @admin.action(description="📄 Generate PDFs for all Payslips in selected Payruns")
    def generate_payrun_pdfs(self, request, queryset):
        total_generated = 0
        for payrun in queryset:
            for ps in payrun.payslips.all():
                try:
                    PayslipPDFGenerator.generate_and_save(ps)
                    total_generated += 1
                except Exception as e:
                    self.message_user(request, f"Failed PDF for {ps.employee.code}: {str(e)}", level=messages.ERROR)
        self.message_user(request, f"Generated {total_generated} PDF payslips across selected payruns.", level=messages.SUCCESS)

    @admin.action(description="⚡ Compute selected Payruns (Calculate all Payslips)")
    def compute_payruns(self, request, queryset):
        for payrun in queryset:
            try:
                res = PayrunService.compute_payrun(payrun)
                report = res['validation_report']
                msg = (
                    f"Successfully computed '{payrun.name}': {res['computed_count']} payslip(s) generated. "
                    f"Total Gross: ${res['total_gross']}, Total Net: ${res['total_net']}."
                )
                self.message_user(request, msg, level=messages.SUCCESS)
                if report.warnings:
                    warn_msgs = "; ".join([w.message for w in report.warnings])
                    self.message_user(request, f"⚠️ Warnings for '{payrun.name}': {warn_msgs}", level=messages.WARNING)
            except Exception as e:
                self.message_user(request, f"Failed to compute '{payrun.name}': {str(e)}", level=messages.ERROR)

    @admin.action(description="✅ Validate selected Payruns")
    def validate_payruns(self, request, queryset):
        for payrun in queryset:
            try:
                PayrunService.validate_payrun(payrun)
                self.message_user(request, f"Payrun '{payrun.name}' validated successfully.", level=messages.SUCCESS)
            except Exception as e:
                self.message_user(request, f"Cannot validate '{payrun.name}': {str(e)}", level=messages.ERROR)

    @admin.action(description="💳 Mark selected Payruns as Paid")
    def mark_payruns_paid(self, request, queryset):
        for payrun in queryset:
            try:
                PayrunService.mark_payrun_paid(payrun)
                self.message_user(request, f"Payrun '{payrun.name}' marked as PAID.", level=messages.SUCCESS)
            except Exception as e:
                self.message_user(request, f"Cannot mark '{payrun.name}' as paid: {str(e)}", level=messages.ERROR)

    @admin.action(description="🔄 Reset selected Payruns to Draft")
    def reset_to_draft(self, request, queryset):
        for payrun in queryset:
            try:
                PayrunService.reset_to_draft(payrun)
                self.message_user(request, f"Payrun '{payrun.name}' reset to Draft.", level=messages.SUCCESS)
            except Exception as e:
                self.message_user(request, f"Cannot reset '{payrun.name}': {str(e)}", level=messages.ERROR)


@admin.register(PayslipLine)
class PayslipLineAdmin(admin.ModelAdmin):
    list_display = ('payslip', 'code', 'name', 'category', 'sequence', 'total')
    list_filter = ('category',)
    search_fields = ('code', 'name', 'payslip__employee__first_name', 'payslip__employee__last_name')
