from django.db import models
from django.utils.translation import gettext_lazy as _


# ==============================================================================
# 1. HR / FOUNDATION MODELS
# ==============================================================================

class WorkingSchedule(models.Model):
    """
    Defines working calendar patterns (e.g. Standard 40h/week, Shift A).
    Used by Contracts and for calculating worked hours/days.
    """
    name = models.CharField(max_length=100, unique=True, help_text="e.g. Standard 40 Hours/Week")
    timezone = models.CharField(max_length=100, default="UTC", blank=True, help_text="Timezone for this working schedule")
    is_active = models.BooleanField(default=True, help_text="Whether this schedule is currently active")
    average_hours_per_day = models.DecimalField(
        max_digits=4, decimal_places=2, default=8.00,
        help_text="Expected working hours per day"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Working Schedule")
        verbose_name_plural = _("Working Schedules")
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def days_per_week(self):
        return self.days.values('day_of_week').distinct().count()

    @property
    def total_hours_per_week(self):
        from decimal import Decimal
        return sum((d.hours for d in self.days.all()), Decimal("0.00"))

    @property
    def formatted_hours_per_week(self):
        val = self.total_hours_per_week
        if val == int(val):
            return f"{int(val)}h"
        return f"{val.normalize():f}h"


class ScheduleDay(models.Model):
    """
    Defines individual active working days and shifts within a WorkingSchedule.
    0 = Monday, 6 = Sunday.
    """
    DAYS_OF_WEEK = [
        (0, _("Monday")),
        (1, _("Tuesday")),
        (2, _("Wednesday")),
        (3, _("Thursday")),
        (4, _("Friday")),
        (5, _("Saturday")),
        (6, _("Sunday")),
    ]

    schedule = models.ForeignKey(
        WorkingSchedule,
        on_delete=models.CASCADE,
        related_name="days"
    )
    day_of_week = models.IntegerField(choices=DAYS_OF_WEEK)
    work_from = models.TimeField(default="09:00:00")
    work_to = models.TimeField(default="18:00:00")
    break_hours = models.DecimalField(
        max_digits=4, decimal_places=2, default=1.00,
        help_text="Break duration in hours"
    )
    hours = models.DecimalField(max_digits=4, decimal_places=2, default=8.00)

    class Meta:
        verbose_name = _("Schedule Day")
        verbose_name_plural = _("Schedule Days")
        ordering = ['schedule', 'day_of_week', 'work_from']
        unique_together = ('schedule', 'day_of_week', 'work_from')

    def __str__(self):
        return f"{self.schedule.name} - {self.get_day_of_week_display()} ({self.work_from} - {self.work_to})"

    def calculate_hours(self):
        from decimal import Decimal
        import datetime
        if not self.work_from or not self.work_to:
            return Decimal("0.00")
        t1 = datetime.datetime.combine(datetime.date.min, self.work_from)
        t2 = datetime.datetime.combine(datetime.date.min, self.work_to)
        diff_hours = (t2 - t1).total_seconds() / 3600.0
        if diff_hours < 0:
            diff_hours += 24.0
        net = max(0.0, diff_hours - float(self.break_hours or 0))
        return Decimal(f"{net:.2f}")

    @property
    def formatted_work_from(self):
        return self.work_from.strftime("%I:%M %p").lstrip("0") if self.work_from else ""

    @property
    def formatted_work_to(self):
        return self.work_to.strftime("%I:%M %p").lstrip("0") if self.work_to else ""

    @property
    def formatted_break(self):
        val = self.break_hours
        if val == int(val):
            return f"{int(val)}h"
        return f"{val.normalize():f}h"

    @property
    def formatted_hours(self):
        val = self.hours
        if val == int(val):
            return f"{int(val)}h"
        return f"{val.normalize():f}h"


class Employee(models.Model):
    """
    Primary employee profile. Person 2 (Attendance & Leave) will connect their
    models to this via ForeignKey('core.Employee').
    """
    code = models.CharField(max_length=30, unique=True, help_text="Unique employee code, e.g. EMP001")
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    department = models.CharField(max_length=100, blank=True, default="")
    job_title = models.CharField(max_length=100, blank=True, default="")
    manager = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='subordinates')
    work_location = models.CharField(max_length=100, blank=True, default="")
    
    # Banking details (Critical for Step 5/8 Payroll Validation Warnings)
    bank_name = models.CharField(max_length=100, blank=True, default="", help_text="Bank institution name")
    bank_account_number = models.CharField(max_length=50, blank=True, default="", help_text="Account / IBAN number")
    bank_ifsc_or_swift = models.CharField(max_length=50, blank=True, default="", help_text="Routing / IFSC / SWIFT code")

    date_of_joining = models.DateField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Employee")
        verbose_name_plural = _("Employees")
        ordering = ['code']

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def has_bank_details(self):
        return bool(self.bank_account_number and self.bank_name)

    def __str__(self):
        return f"[{self.code}] {self.full_name}"


# ==============================================================================
# 2. PAYROLL CONFIGURATION MODELS
# ==============================================================================

class SalaryStructure(models.Model):
    """
    A collection of SalaryRules applicable to an employee contract or payrun.
    (e.g., 'Regular Full-Time Structure', 'Intern Structure').
    """
    name = models.CharField(max_length=150, unique=True)
    code = models.CharField(max_length=50, unique=True, help_text="Unique structure identifier, e.g. REG_FT")
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Salary Structure")
        verbose_name_plural = _("Salary Structures")
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"


class SalaryRule(models.Model):
    """
    Individual calculation rules that compose a SalaryStructure.
    Rules drive the dynamic calculation (Fixed, Percentage, Formula).
    """
    CATEGORY_CHOICES = [
        ('BASIC', _('Basic Salary')),
        ('ALLOWANCE', _('Allowance')),
        ('GROSS', _('Gross')),
        ('DEDUCTION', _('Deduction')),
        ('NET', _('Net Salary')),
    ]

    AMOUNT_TYPE_CHOICES = [
        ('fixed', _('Fixed Amount')),
        ('percentage', _('Percentage of Another Rule')),
        ('formula', _('Python Formula Expression')),
    ]

    name = models.CharField(max_length=150, help_text="e.g. Basic Salary, HRA, Provident Fund")
    code = models.CharField(
        max_length=50,
        unique=True,
        help_text="Variable name used in formulas, e.g. BASIC, HRA, PF, GROSS, NET"
    )
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    sequence = models.IntegerField(
        default=10,
        help_text="Execution sequence (smaller numbers computed first, e.g. BASIC=10, HRA=20, GROSS=50, PF=60, NET=100)"
    )

    amount_type = models.CharField(max_length=20, choices=AMOUNT_TYPE_CHOICES, default='fixed')
    fixed_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    
    # Used when amount_type == 'percentage'
    percentage_base_code = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="Code of rule to calculate percentage against, e.g. 'BASIC' or 'GROSS'"
    )
    percentage = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0.00,
        help_text="Percentage value, e.g. 40.00 for 40%"
    )

    # Used when amount_type == 'formula'
    formula = models.TextField(
        blank=True,
        null=True,
        help_text="Python expression. Available variables: contract, employee, worked_days, rules (dict of computed rules). e.g.: contract.wage * 0.5"
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Salary Rule")
        verbose_name_plural = _("Salary Rules")
        ordering = ['sequence', 'code']

    def __str__(self):
        return f"[{self.category}] {self.name} ({self.code}) - Seq: {self.sequence}"


class SalaryStructureRule(models.Model):
    """
    Associates SalaryRules with a SalaryStructure, preserving execution sequence.
    """
    structure = models.ForeignKey(
        SalaryStructure,
        on_delete=models.CASCADE,
        related_name="structure_rules"
    )
    rule = models.ForeignKey(
        SalaryRule,
        on_delete=models.CASCADE,
        related_name="structure_rules"
    )
    sequence = models.IntegerField(
        default=10,
        help_text="Override or structure-specific evaluation sequence"
    )

    class Meta:
        verbose_name = _("Structure Rule Assignment")
        verbose_name_plural = _("Structure Rule Assignments")
        ordering = ['structure', 'sequence', 'rule__sequence']
        unique_together = ('structure', 'rule')

    def __str__(self):
        return f"{self.structure.code} -> {self.rule.code} (Seq: {self.sequence})"


# ==============================================================================
# 3. CONTRACT MODEL
# ==============================================================================

class Contract(models.Model):
    """
    Employment contract specifying wage, schedule, and applicable salary structure.
    A valid contract active in the payrun period is mandatory to calculate payroll.
    """
    STATE_CHOICES = [
        ('draft', _('Draft')),
        ('active', _('Active')),
        ('expired', _('Expired')),
        ('cancelled', _('Cancelled')),
    ]

    WAGE_TYPE_CHOICES = [
        ('monthly', _('Monthly')),
        ('hourly', _('Hourly')),
    ]

    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="contracts"
    )
    name = models.CharField(max_length=150, help_text="e.g. Alice Smith - Full Time Contract 2026")
    wage = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Monthly wage or base rate used in salary calculations"
    )
    wage_type = models.CharField(max_length=20, choices=WAGE_TYPE_CHOICES, default='monthly')

    working_schedule = models.ForeignKey(
        WorkingSchedule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contracts"
    )
    salary_structure = models.ForeignKey(
        SalaryStructure,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contracts",
        help_text="Default salary structure assigned to this contract"
    )

    start_date = models.DateField(help_text="Contract start date")
    end_date = models.DateField(null=True, blank=True, help_text="Leave blank if permanent / open-ended")
    state = models.CharField(max_length=20, choices=STATE_CHOICES, default='draft')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Contract")
        verbose_name_plural = _("Contracts")
        ordering = ['-start_date', 'employee']

    def is_applicable_for(self, period_start, period_end):
        """
        Check if this contract covers the given period and is in active state.
        Contract must start on/before period_end, and either have no end_date or end on/after period_start.
        """
        if self.state != 'active':
            return False
        if self.start_date > period_end:
            return False
        if self.end_date and self.end_date < period_start:
            return False
        return True

    @property
    def status_badge_class(self):
        mapping = {
            'active': 'status-active',
            'draft': 'status-draft',
            'expired': 'status-expired',
            'cancelled': 'status-cancelled',
        }
        return mapping.get(self.state, 'status-draft')

    @property
    def formatted_wage(self):
        if self.wage is not None:
            if self.wage == int(self.wage):
                return f"₹{int(self.wage):,}"
            return f"₹{self.wage:,.2f}"
        return "₹0"

    @property
    def formatted_start_date(self):
        return self.start_date.strftime("%d-%b-%Y") if self.start_date else ""

    @property
    def formatted_end_date(self):
        return self.end_date.strftime("%d-%b-%Y") if self.end_date else "—"

    def __str__(self):
        return f"{self.name} - {self.employee.full_name} [{self.get_state_display()}] (${self.wage})"


# ==============================================================================
# 4. PAYROLL PROCESSING: PAYRUN, PAYSLIP, PAYSLIPLINE
# ==============================================================================

class Payrun(models.Model):
    """
    A Payrun batch process for a specific salary structure and date period.
    Controls the workflow: Draft -> Computed -> Validated -> Paid.
    """
    STATE_CHOICES = [
        ('draft', _('Draft')),
        ('computed', _('Computed')),
        ('validated', _('Validated')),
        ('paid', _('Paid')),
        ('cancelled', _('Cancelled')),
    ]

    name = models.CharField(max_length=150, help_text="e.g. September 2026 Regular Payrun")
    salary_structure = models.ForeignKey(
        SalaryStructure,
        on_delete=models.PROTECT,
        related_name="payruns",
        help_text="Salary structure applied to this payrun batch"
    )
    start_date = models.DateField(help_text="Period start date (e.g. 2026-09-01)")
    end_date = models.DateField(help_text="Period end date (e.g. 2026-09-30)")
    payment_date = models.DateField(null=True, blank=True, help_text="Date disbursements are made")
    state = models.CharField(max_length=20, choices=STATE_CHOICES, default='draft')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Payrun")
        verbose_name_plural = _("Payruns")
        ordering = ['-start_date', '-id']

    @property
    def total_net(self):
        return sum(p.net_wage for p in self.payslips.all())

    @property
    def total_gross(self):
        return sum(p.gross_wage for p in self.payslips.all())

    @property
    def payslip_count(self):
        return self.payslips.count()

    def __str__(self):
        return f"{self.name} [{self.get_state_display()}] ({self.start_date} to {self.end_date})"


class Payslip(models.Model):
    """
    Individual payslip calculated for an employee under a specific Payrun.
    Preserves historical snapshot of contract, salary structure, worked days, and totals.
    """
    STATE_CHOICES = [
        ('draft', _('Draft')),
        ('computed', _('Computed')),
        ('validated', _('Validated')),
        ('paid', _('Paid')),
        ('cancelled', _('Cancelled')),
    ]

    payrun = models.ForeignKey(
        Payrun,
        on_delete=models.CASCADE,
        related_name="payslips"
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="payslips"
    )
    contract = models.ForeignKey(
        Contract,
        on_delete=models.PROTECT,
        related_name="payslips"
    )
    salary_structure = models.ForeignKey(
        SalaryStructure,
        on_delete=models.PROTECT,
        related_name="payslips"
    )

    period_start = models.DateField()
    period_end = models.DateField()
    worked_days = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)

    # Calculated summary buckets (populated by calculation engine)
    basic_wage = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    gross_wage = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    total_deductions = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    net_wage = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)

    state = models.CharField(max_length=20, choices=STATE_CHOICES, default='draft')
    
    # Generated PDF file (Step 9)
    pdf_file = models.FileField(upload_to="payslips/%Y/%m/", null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Payslip")
        verbose_name_plural = _("Payslips")
        ordering = ['payrun', 'employee__code']
        # Prevent duplicate payslips for the same employee in a single payrun
        unique_together = ('payrun', 'employee')

    def __str__(self):
        return f"Payslip: {self.employee.full_name} - {self.payrun.name} (Net: ${self.net_wage})"


class PayslipLine(models.Model):
    """
    Detailed line item of a Payslip.
    Every salary rule executed creates a PayslipLine record to preserve
    exact audit trail of how Gross, Deductions, and Net were derived.
    """
    payslip = models.ForeignKey(
        Payslip,
        on_delete=models.CASCADE,
        related_name="lines"
    )
    salary_rule = models.ForeignKey(
        SalaryRule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payslip_lines"
    )
    code = models.CharField(max_length=50, help_text="Rule code snapshot (e.g. BASIC, HRA, PF)")
    name = models.CharField(max_length=150, help_text="Rule name snapshot")
    category = models.CharField(max_length=20, help_text="BASIC, ALLOWANCE, GROSS, DEDUCTION, NET")
    sequence = models.IntegerField(default=10)

    rate = models.DecimalField(max_digits=6, decimal_places=2, default=100.00, help_text="Percentage rate applied (e.g. 100%)")
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00, help_text="Base calculation amount")
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0.00, help_text="Final calculated line amount")

    class Meta:
        verbose_name = _("Payslip Line")
        verbose_name_plural = _("Payslip Lines")
        ordering = ['payslip', 'sequence', 'id']

    def __str__(self):
        return f"{self.payslip.employee.code} | {self.code}: ${self.total} ({self.category})"
