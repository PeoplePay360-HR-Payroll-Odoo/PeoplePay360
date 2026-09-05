import json
import datetime
from decimal import Decimal
from django.test import TestCase
from core.models import (
    WorkingSchedule,
    Employee,
    SalaryStructure,
    SalaryRule,
    SalaryStructureRule,
    Contract,
    Payrun,
    Payslip,
    PayslipLine,
    LeaveType,
    LeaveRequest,
)
from core.services.payroll_engine import PayrollEngine, PayrollCalculationError
from core.services.payrun_service import PayrunService, PayrunWorkflowError
from core.services.pdf_generator import PayslipPDFGenerator
from core.services.leave_service import LeaveService, LeaveValidationError


class PayrollEngineTestCase(TestCase):
    def setUp(self):
        # 1. Working Schedule
        self.schedule = WorkingSchedule.objects.create(
            name="Standard 40h",
            average_hours_per_day=Decimal("8.00")
        )

        # 2. Salary Structure
        self.structure = SalaryStructure.objects.create(
            code="TEST_CORP",
            name="Test Corporate Structure",
            is_active=True
        )

        # 3. Salary Rules
        self.rule_basic = SalaryRule.objects.create(
            code="BASIC",
            name="Basic Salary",
            category="BASIC",
            sequence=10,
            amount_type="percentage",
            percentage_base_code="WAGE",
            percentage=Decimal("50.00"),
        )
        self.rule_hra = SalaryRule.objects.create(
            code="HRA",
            name="House Rent Allowance",
            category="ALLOWANCE",
            sequence=20,
            amount_type="percentage",
            percentage_base_code="BASIC",
            percentage=Decimal("40.00"),
        )
        self.rule_conv = SalaryRule.objects.create(
            code="CONV",
            name="Conveyance Allowance",
            category="ALLOWANCE",
            sequence=30,
            amount_type="fixed",
            fixed_amount=Decimal("200.00"),
        )
        self.rule_special = SalaryRule.objects.create(
            code="SPECIAL",
            name="Special Allowance",
            category="ALLOWANCE",
            sequence=40,
            amount_type="formula",
            formula="max(0, contract.wage - (rules.get('BASIC', 0) + rules.get('HRA', 0) + rules.get('CONV', 0)))",
        )
        self.rule_gross = SalaryRule.objects.create(
            code="GROSS",
            name="Gross Earnings",
            category="GROSS",
            sequence=50,
            amount_type="formula",
            formula="rules.get('BASIC', 0) + rules.get('HRA', 0) + rules.get('CONV', 0) + rules.get('SPECIAL', 0)",
        )
        self.rule_pf = SalaryRule.objects.create(
            code="PF",
            name="Provident Fund",
            category="DEDUCTION",
            sequence=60,
            amount_type="percentage",
            percentage_base_code="BASIC",
            percentage=Decimal("12.00"),
        )
        self.rule_tax = SalaryRule.objects.create(
            code="TAX",
            name="Income Tax",
            category="DEDUCTION",
            sequence=70,
            amount_type="formula",
            formula="rules.get('GROSS', 0) * Decimal('0.10')",
        )
        self.rule_net = SalaryRule.objects.create(
            code="NET",
            name="Net Salary",
            category="NET",
            sequence=100,
            amount_type="formula",
            formula="rules.get('GROSS', 0) - (rules.get('PF', 0) + rules.get('TAX', 0))",
        )

        # Attach rules to structure
        for rule in [
            self.rule_basic, self.rule_hra, self.rule_conv,
            self.rule_special, self.rule_gross, self.rule_pf,
            self.rule_tax, self.rule_net
        ]:
            SalaryStructureRule.objects.create(
                structure=self.structure,
                rule=rule,
                sequence=rule.sequence
            )

        # 4. Employee & Contract
        self.employee = Employee.objects.create(
            code="EMP100",
            first_name="Alice",
            last_name="Johnson",
            email="alice@example.com",
            bank_name="Test Bank",
            bank_account_number="11223344",
            bank_ifsc_or_swift="TEST1234",
            date_of_joining=datetime.date(2024, 1, 1),
            is_active=True
        )
        self.contract = Contract.objects.create(
            employee=self.employee,
            name="Contract Alice",
            wage=Decimal("6000.00"),
            wage_type="monthly",
            working_schedule=self.schedule,
            salary_structure=self.structure,
            start_date=datetime.date(2024, 1, 1),
            state="active"
        )

        # 5. Payrun
        self.payrun = Payrun.objects.create(
            name="September 2026",
            salary_structure=self.structure,
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2026, 9, 30),
            state="draft"
        )

    def test_payroll_calculation_math(self):
        """Verify dynamic rule calculation produces exact expected numbers."""
        res = PayrollEngine.calculate_structure_rules(
            structure=self.structure,
            contract=self.contract,
            employee=self.employee
        )

        self.assertEqual(res['basic_wage'], Decimal('3000.00'))  # 50% of 6000
        self.assertEqual(res['gross_wage'], Decimal('6000.00'))  # Basic + HRA (1200) + Conv (200) + Special (1600)
        self.assertEqual(res['total_deductions'], Decimal('960.00'))  # PF (360) + Tax (600)
        self.assertEqual(res['net_wage'], Decimal('5040.00'))  # Gross (6000) - Deductions (960)

    def test_compute_payslip_and_lines_creation(self):
        """Verify payslip and individual line items are created and linked in DB."""
        payslip = PayrollEngine.compute_payslip(
            payrun=self.payrun,
            employee=self.employee
        )

        self.assertEqual(payslip.state, 'computed')
        self.assertEqual(payslip.net_wage, Decimal('5040.00'))
        self.assertEqual(payslip.lines.count(), 8)

        basic_line = payslip.lines.get(code='BASIC')
        self.assertEqual(basic_line.total, Decimal('3000.00'))

        net_line = payslip.lines.get(code='NET')
        self.assertEqual(net_line.total, Decimal('5040.00'))

    def test_uncontracted_employee_raises_error(self):
        """Verify calculation fails cleanly when employee has no active contract."""
        uncontracted = Employee.objects.create(
            code="EMP999",
            first_name="Ghost",
            last_name="Worker",
            date_of_joining=datetime.date(2025, 1, 1),
            is_active=True
        )

        with self.assertRaises(PayrollCalculationError):
            PayrollEngine.compute_payslip(
                payrun=self.payrun,
                employee=uncontracted
            )

    def test_idempotent_recalculation(self):
        """Verify that recalculating a payslip updates in place without orphaned lines."""
        payslip_first = PayrollEngine.compute_payslip(
            payrun=self.payrun,
            employee=self.employee
        )
        first_id = payslip_first.id
        self.assertEqual(payslip_first.lines.count(), 8)

        # Recalculate
        payslip_second = PayrollEngine.compute_payslip(
            payrun=self.payrun,
            employee=self.employee
        )
        self.assertEqual(payslip_second.id, first_id)
        self.assertEqual(payslip_second.lines.count(), 8)
        self.assertEqual(Payslip.objects.filter(payrun=self.payrun, employee=self.employee).count(), 1)

    def test_invalid_formula_raises_error(self):
        """Verify that syntax errors in rule formulas raise PayrollCalculationError."""
        bad_rule = SalaryRule.objects.create(
            code="BAD",
            name="Broken Formula",
            category="ALLOWANCE",
            sequence=15,
            amount_type="formula",
            formula="syntax error here !!!",
        )
        SalaryStructureRule.objects.create(
            structure=self.structure,
            rule=bad_rule,
            sequence=15
        )

        with self.assertRaises(PayrollCalculationError):
            PayrollEngine.calculate_structure_rules(
                structure=self.structure,
                contract=self.contract,
                employee=self.employee
            )

    def test_payrun_workflow_lifecycle(self):
        """Test complete lifecycle: Draft -> Computed -> Validated -> Paid."""
        # 1. Compute
        result = PayrunService.compute_payrun(self.payrun)
        self.assertEqual(result['computed_count'], 1)
        self.assertEqual(self.payrun.state, 'computed')
        self.assertEqual(self.payrun.total_gross, Decimal('6000.00'))
        self.assertEqual(self.payrun.total_net, Decimal('5040.00'))

        # 2. Validate
        PayrunService.validate_payrun(self.payrun)
        self.assertEqual(self.payrun.state, 'validated')
        self.assertEqual(self.payrun.payslips.first().state, 'validated')

        # 3. Mark Paid
        PayrunService.mark_payrun_paid(self.payrun)
        self.assertEqual(self.payrun.state, 'paid')
        self.assertIsNotNone(self.payrun.payment_date)
        self.assertEqual(self.payrun.payslips.first().state, 'paid')

    def test_missing_bank_details_generates_warning(self):
        """Test that employees missing bank details generate a pre-flight warning."""
        emp_nobank = Employee.objects.create(
            code="EMP_NOBANK",
            first_name="Bob",
            last_name="NoBank",
            date_of_joining=datetime.date(2024, 1, 1),
            is_active=True,
            bank_name="",
            bank_account_number="",
            bank_ifsc_or_swift=""
        )
        Contract.objects.create(
            employee=emp_nobank,
            name="Contract Bob",
            wage=Decimal("4000.00"),
            wage_type="monthly",
            working_schedule=self.schedule,
            salary_structure=self.structure,
            start_date=datetime.date(2024, 1, 1),
            state="active"
        )

        res = PayrunService.compute_payrun(self.payrun)
        report = res['validation_report']
        self.assertTrue(report.is_valid)  # Warnings are non-blocking
        warning_messages = [w.message for w in report.warnings]
        self.assertTrue(any("missing banking details" in m for m in warning_messages))

    def test_invalid_state_transition_blocked(self):
        """Test that skipping states (e.g. Draft directly to Paid) is strictly blocked."""
        with self.assertRaises(PayrunWorkflowError):
            PayrunService.mark_payrun_paid(self.payrun)  # Currently in draft!

    def test_payslip_pdf_generation(self):
        """Test that ReportLab generates a valid PDF binary with %PDF- header."""
        payslip = PayrollEngine.compute_payslip(self.payrun, self.employee)
        pdf_bytes = PayslipPDFGenerator.generate_pdf_bytes(payslip)
        self.assertTrue(pdf_bytes.startswith(b'%PDF-'))
        self.assertGreater(len(pdf_bytes), 1000)

    def test_api_payruns_list_and_detail(self):
        """Test GET /api/payruns/ and GET /api/payruns/<id>/ endpoints."""
        response = self.client.get('/api/payruns/')
        self.assertEqual(response.status_code, 200)
        json_data = response.json()
        self.assertIn('payruns', json_data)
        self.assertGreaterEqual(len(json_data['payruns']), 1)

        detail_resp = self.client.get(f'/api/payruns/{self.payrun.id}/')
        self.assertEqual(detail_resp.status_code, 200)
        detail_json = detail_resp.json()
        self.assertEqual(detail_json['id'], self.payrun.id)

    def test_api_payslip_pdf_endpoint(self):
        """Test streaming PDF via GET /api/payslips/<id>/pdf/."""
        payslip = PayrollEngine.compute_payslip(self.payrun, self.employee)
        response = self.client.get(f'/api/payslips/{payslip.id}/pdf/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF-'))

    def test_leave_balances_and_application(self):
        """Test applying for leave and balance deduction."""
        pto = LeaveType.objects.create(
            name="PTO Vacation",
            code="PTO_TEST",
            is_paid=True,
            max_days_per_year=Decimal("15.00"),
        )
        bal_initial = LeaveService.get_leave_balance(self.employee, pto, year=2026)
        self.assertEqual(bal_initial['remaining_days'], 15.0)

        # Apply for 3 days
        req = LeaveService.apply_leave(
            employee=self.employee,
            leave_type=pto,
            start_date=datetime.date(2026, 7, 1),
            end_date=datetime.date(2026, 7, 3),
            reason="Summer trip"
        )
        self.assertEqual(req.status, 'submitted')
        self.assertEqual(req.number_of_days, Decimal('3.00'))

        # Approve
        LeaveService.approve_leave(req)
        self.assertEqual(req.status, 'approved')

        bal_after = LeaveService.get_leave_balance(self.employee, pto, year=2026)
        self.assertEqual(bal_after['used_days'], 3.0)
        self.assertEqual(bal_after['remaining_days'], 12.0)

    def test_insufficient_leave_balance_fails(self):
        """Test that requesting more days than quota raises LeaveValidationError."""
        sick = LeaveType.objects.create(
            name="Sick Time",
            code="SICK_TEST",
            is_paid=True,
            max_days_per_year=Decimal("5.00"),
        )
        # Attempt to apply for 10 days
        with self.assertRaises(LeaveValidationError):
            LeaveService.apply_leave(
                employee=self.employee,
                leave_type=sick,
                start_date=datetime.date(2026, 8, 1),
                end_date=datetime.date(2026, 8, 10),
            )

    def test_unpaid_leave_prorates_payroll(self):
        """
        Verify that approved unpaid leaves (Loss of Pay) automatically
        deduct worked days and prorate payroll in compute_payslip.
        """
        unpaid = LeaveType.objects.create(
            name="Loss of Pay",
            code="LOP_TEST",
            is_paid=False,
            max_days_per_year=Decimal("0.00"),
        )
        # 3 days unpaid leave in September (30 days total) -> 27 worked days (90%)
        leave_req = LeaveService.apply_leave(
            employee=self.employee,
            leave_type=unpaid,
            start_date=datetime.date(2026, 9, 10),
            end_date=datetime.date(2026, 9, 12),
        )
        LeaveService.approve_leave(leave_req)

        payslip = PayrollEngine.compute_payslip(self.payrun, self.employee)
        self.assertEqual(payslip.worked_days, Decimal('27.00'))
        # Base wage $6000 * (27/30) = $5400
        self.assertEqual(payslip.gross_wage, Decimal('5400.00'))
        self.assertEqual(payslip.basic_wage, Decimal('2700.00'))
        self.assertEqual(payslip.net_wage, Decimal('4536.00'))

    def test_leave_api_endpoints(self):
        """Test GET /api/leaves/types/, GET /api/leaves/balances/, POST /api/leaves/requests/."""
        pto = LeaveType.objects.create(
            name="Annual Holiday",
            code="HOLIDAY",
            is_paid=True,
            max_days_per_year=Decimal("12.00"),
        )
        # Test types list
        resp = self.client.get('/api/leaves/types/')
        self.assertEqual(resp.status_code, 200)

        # Test balance
        bal_resp = self.client.get(f'/api/leaves/balances/?employee_id={self.employee.id}')
        self.assertEqual(bal_resp.status_code, 200)
        self.assertIn('balances', bal_resp.json())

        # Test submit request via API
        post_resp = self.client.post('/api/leaves/requests/', data=json.dumps({
            'employee_id': self.employee.id,
            'leave_type_id': pto.id,
            'start_date': '2026-10-01',
            'end_date': '2026-10-02',
            'reason': 'API test leave'
        }), content_type='application/json')
        self.assertEqual(post_resp.status_code, 201)

