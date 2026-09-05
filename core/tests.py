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
)
from core.services.payroll_engine import PayrollEngine, PayrollCalculationError
from core.services.payrun_service import PayrunService, PayrunWorkflowError
from core.services.pdf_generator import PayslipPDFGenerator


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


class WorkingScheduleCRUDTestCase(TestCase):
    def setUp(self):
        self.schedule = WorkingSchedule.objects.create(
            name="Engineering 40h",
            timezone="Company Timezone",
            is_active=True,
            average_hours_per_day=Decimal("8.00")
        )
        from core.models import ScheduleDay
        import datetime
        for i in range(5):
            ScheduleDay.objects.create(
                schedule=self.schedule,
                day_of_week=i,
                work_from=datetime.time(9, 0),
                work_to=datetime.time(18, 0),
                break_hours=Decimal("1.00"),
                hours=Decimal("8.00")
            )

        self.inactive_schedule = WorkingSchedule.objects.create(
            name="Weekend Only",
            timezone="UTC",
            is_active=False,
            average_hours_per_day=Decimal("6.00")
        )

    def test_working_schedule_list_view(self):
        response = self.client.get('/schedules/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'working_schedules/working_schedule_list.html')
        self.assertContains(response, "Engineering 40h")
        self.assertContains(response, "Weekend Only")
        self.assertContains(response, "Working Schedules")

    def test_working_schedule_search_and_filter(self):
        # Search for Engineering
        resp = self.client.get('/schedules/?q=Engineering')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Engineering 40h")
        self.assertNotContains(resp, "Weekend Only")

        # Filter by active
        resp_active = self.client.get('/schedules/?status=active')
        self.assertEqual(resp_active.status_code, 200)
        self.assertContains(resp_active, "Engineering 40h")
        self.assertNotContains(resp_active, "Weekend Only")

        # Filter by inactive
        resp_inactive = self.client.get('/schedules/?status=inactive')
        self.assertEqual(resp_inactive.status_code, 200)
        self.assertNotContains(resp_inactive, "Engineering 40h")
        self.assertContains(resp_inactive, "Weekend Only")

    def test_working_schedule_create_view_get_and_post(self):
        # GET create form
        get_resp = self.client.get('/schedules/new/')
        self.assertEqual(get_resp.status_code, 200)
        self.assertTemplateUsed(get_resp, 'working_schedules/working_schedule_form.html')

        # POST create form with 3 working days
        post_data = {
            'name': 'Part-time 3 Days',
            'timezone': 'America/New_York',
            'is_active': 'on',
            'day_of_week[]': ['0', '1', '2'],
            'work_from[]': ['09:00', '09:00', '09:00'],
            'work_to[]': ['17:00', '17:00', '17:00'],
            'break_hours[]': ['1.00', '1.00', '1.00'],
        }
        post_resp = self.client.post('/schedules/new/', post_data, follow=True)
        self.assertEqual(post_resp.status_code, 200)
        self.assertTrue(WorkingSchedule.objects.filter(name='Part-time 3 Days').exists())
        created_schedule = WorkingSchedule.objects.get(name='Part-time 3 Days')
        self.assertEqual(created_schedule.days.count(), 3)
        self.assertEqual(created_schedule.total_hours_per_week, Decimal("21.00"))  # (8 - 1) * 3 = 21h

    def test_working_schedule_edit(self):
        post_data = {
            'name': 'Engineering 40h Updated',
            'timezone': 'Europe/London',
            'is_active': 'on',
            'day_of_week[]': ['0', '1', '2', '3'],
            'work_from[]': ['10:00', '10:00', '10:00', '10:00'],
            'work_to[]': ['19:00', '19:00', '19:00', '19:00'],
            'break_hours[]': ['1.00', '1.00', '1.00', '1.00'],
        }
        resp = self.client.post(f'/schedules/{self.schedule.id}/', post_data, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.name, 'Engineering 40h Updated')
        self.assertEqual(self.schedule.timezone, 'Europe/London')
        self.assertEqual(self.schedule.days.count(), 4)
        self.assertEqual(self.schedule.total_hours_per_week, Decimal("32.00"))  # (9 - 1) * 4 = 32h

    def test_working_schedule_toggle_status(self):
        self.assertTrue(self.schedule.is_active)
        resp = self.client.post(f'/schedules/{self.schedule.id}/toggle-status/', follow=True)
        self.assertEqual(resp.status_code, 200)
        self.schedule.refresh_from_db()
        self.assertFalse(self.schedule.is_active)

        # Toggle back
        resp2 = self.client.post(f'/schedules/{self.schedule.id}/toggle-status/', follow=True)
        self.assertEqual(resp2.status_code, 200)
        self.schedule.refresh_from_db()
        self.assertTrue(self.schedule.is_active)

    def test_working_schedule_delete(self):
        resp = self.client.post(f'/schedules/{self.schedule.id}/delete/', follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(WorkingSchedule.objects.filter(id=self.schedule.id).exists())

    def test_navbar_contains_working_schedules(self):
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'href="/schedules/"')
        self.assertContains(resp, 'Working Schedules')


class ContractCRUDTestCase(TestCase):
    def setUp(self):
        self.schedule = WorkingSchedule.objects.create(
            name="Standard 40 Hours/Week",
            average_hours_per_day=Decimal("8.00")
        )
        self.structure = SalaryStructure.objects.create(
            code="CORP_STD",
            name="Standard Corporate Salary Structure",
            is_active=True
        )
        self.employee1 = Employee.objects.create(
            code="EMP101",
            first_name="Aarav",
            last_name="Mehta",
            email="aarav.mehta@example.com",
            department="Finance",
            job_title="Payroll Specialist",
            date_of_joining=datetime.date(2025, 1, 1),
            is_active=True
        )
        self.employee2 = Employee.objects.create(
            code="EMP102",
            first_name="Sura",
            last_name="Khan",
            email="sura.khan@example.com",
            department="Operations",
            job_title="Operations Lead",
            date_of_joining=datetime.date(2025, 3, 1),
            is_active=True
        )
        self.contract1 = Contract.objects.create(
            name="CON/2026/0042",
            employee=self.employee1,
            wage=Decimal("85000.00"),
            wage_type="monthly",
            working_schedule=self.schedule,
            salary_structure=self.structure,
            start_date=datetime.date(2026, 1, 1),
            end_date=None,
            state="active"
        )
        self.contract2 = Contract.objects.create(
            name="CON/2025/0018",
            employee=self.employee1,
            wage=Decimal("78000.00"),
            wage_type="monthly",
            working_schedule=self.schedule,
            salary_structure=self.structure,
            start_date=datetime.date(2025, 7, 1),
            end_date=datetime.date(2025, 12, 31),
            state="expired"
        )

    def test_contract_list_view(self):
        resp = self.client.get('/contracts/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Contracts")
        self.assertContains(resp, "List view of employee contracts")
        self.assertContains(resp, "CON/2026/0042")
        self.assertContains(resp, "CON/2025/0018")
        self.assertContains(resp, "Aarav Mehta")
        self.assertContains(resp, "Active")
        self.assertContains(resp, "Expired")
        self.assertContains(resp, "NEW")
        # Ensure notes are NOT in UI
        self.assertNotContains(resp, "Useful note:")
        self.assertNotContains(resp, "Salary Structure / Notes")

    def test_contract_list_htmx_search_and_filters(self):
        # Search for Sura (doesn't have contract yet)
        resp = self.client.get('/contracts/?q=Sura', HTTP_HX_REQUEST='true')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No contracts found")

        # Search for Aarav
        resp = self.client.get('/contracts/?q=Aarav', HTTP_HX_REQUEST='true')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "CON/2026/0042")

        # Filter by status: active
        resp = self.client.get('/contracts/?status=active', HTTP_HX_REQUEST='true')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "CON/2026/0042")
        self.assertNotContains(resp, "CON/2025/0018")

        # Filter by status: expired
        resp = self.client.get('/contracts/?status=expired', HTTP_HX_REQUEST='true')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "CON/2025/0018")
        self.assertNotContains(resp, "CON/2026/0042")

    def test_contract_list_employee_filter(self):
        resp = self.client.get(f'/contracts/?employee={self.employee1.id}')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Aarav Mehta")
        self.assertContains(resp, "CON/2026/0042")

    def test_contract_detail_view_get(self):
        resp = self.client.get(f'/contracts/{self.contract1.id}/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Contract / CON/2026/0042")
        self.assertContains(resp, "Form view of one contract")
        # Back button check
        self.assertContains(resp, 'href="/contracts/"')
        self.assertContains(resp, "Back")
        # Field checks
        self.assertContains(resp, "Finance")
        self.assertContains(resp, "Payroll Specialist")
        self.assertContains(resp, "85000.00")
        # Verify notes are NOT included in the UI
        self.assertNotContains(resp, "Salary Structure / Notes")
        self.assertNotContains(resp, "Useful note:")

    def test_contract_detail_view_update_post(self):
        post_data = {
            'name': 'CON/2026/0042-UPDATED',
            'employee_id': self.employee1.id,
            'start_date': '2026-01-01',
            'end_date': '2026-12-31',
            'wage': '90000.00',
            'wage_type': 'monthly',
            'state': 'active',
            'working_schedule_id': self.schedule.id,
            'salary_structure_id': self.structure.id,
        }
        resp = self.client.post(f'/contracts/{self.contract1.id}/', post_data, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.contract1.refresh_from_db()
        self.assertEqual(self.contract1.name, 'CON/2026/0042-UPDATED')
        self.assertEqual(self.contract1.wage, Decimal('90000.00'))
        self.assertEqual(self.contract1.end_date, datetime.date(2026, 12, 31))

    def test_contract_create_view(self):
        # GET form
        resp = self.client.get('/contracts/new/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "New Contract")
        self.assertContains(resp, "Back")

        # POST creation
        post_data = {
            'name': 'CON/2026/0031',
            'employee_id': self.employee2.id,
            'start_date': '2026-01-01',
            'end_date': '',
            'wage': '95000.00',
            'wage_type': 'monthly',
            'state': 'active',
            'working_schedule_id': self.schedule.id,
            'salary_structure_id': self.structure.id,
        }
        resp = self.client.post('/contracts/new/', post_data, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Contract.objects.filter(name='CON/2026/0031').exists())
        new_c = Contract.objects.get(name='CON/2026/0031')
        self.assertEqual(new_c.employee, self.employee2)
        self.assertEqual(new_c.wage, Decimal('95000.00'))
        self.assertEqual(new_c.state, 'active')

    def test_overlapping_active_contract_validation(self):
        # Employee 1 already has active contract CON/2026/0042 starting 2026-01-01 with open end
        # Trying to create another active contract for Employee 1 in 2026 should be blocked
        post_data = {
            'name': 'CON/2026/DUPLICATE',
            'employee_id': self.employee1.id,
            'start_date': '2026-06-01',
            'end_date': '2026-12-31',
            'wage': '80000.00',
            'wage_type': 'monthly',
            'state': 'active',
            'working_schedule_id': self.schedule.id,
            'salary_structure_id': self.structure.id,
        }
        resp = self.client.post('/contracts/new/', post_data, follow=True)
        self.assertEqual(resp.status_code, 200)
        # Check error message flashed
        self.assertContains(resp, "already has an active contract")
        self.assertFalse(Contract.objects.filter(name='CON/2026/DUPLICATE').exists())

        # Creating a draft or expired contract in the same period should succeed
        post_data['state'] = 'draft'
        resp2 = self.client.post('/contracts/new/', post_data, follow=True)
        self.assertEqual(resp2.status_code, 200)
        self.assertTrue(Contract.objects.filter(name='CON/2026/DUPLICATE').exists())

    def test_contract_delete_view(self):
        resp = self.client.post(f'/contracts/{self.contract2.id}/delete/', follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Contract.objects.filter(id=self.contract2.id).exists())

    def test_navbar_and_employee_smart_button_links(self):
        # Base navbar
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'href="/contracts/"')
        self.assertContains(resp, "Contracts")

        # Employee profile smart button
        resp_emp = self.client.get(f'/employee/{self.employee1.id}/')
        self.assertEqual(resp_emp.status_code, 200)
        self.assertContains(resp_emp, f'href="/contracts/?employee={self.employee1.id}"')


