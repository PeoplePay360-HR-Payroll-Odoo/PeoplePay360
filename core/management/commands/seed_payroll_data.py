import datetime
from decimal import Decimal
# pyrefly: ignore [missing-import]
from django.core.management.base import BaseCommand
# pyrefly: ignore [missing-import]
from django.contrib.auth import get_user_model
from core.models import (
    WorkingSchedule,
    ScheduleDay,
    Employee,
    SalaryStructure,
    SalaryRule,
    SalaryStructureRule,
    Contract,
    LeaveType,
    LeaveRequest,
)


class Command(BaseCommand):
    help = "Seeds standard initial data for PeoplePay360: admin user, schedules, salary structures, rules, employees, and contracts."

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("==> Seeding PeoplePay360 database..."))

        # 1. Superuser / Admin
        User = get_user_model()
        admin_user, created = User.objects.get_or_create(
            username="admin",
            defaults={"email": "admin@peoplepay360.com", "is_staff": True, "is_superuser": True}
        )
        if created:
            admin_user.set_password("admin123")
            admin_user.save()
            self.stdout.write(self.style.SUCCESS("  [+] Superuser created: admin / admin123"))
        else:
            self.stdout.write(self.style.WARNING("  [-] Superuser 'admin' already exists."))

        # 2. Working Schedules: matching mockup
        schedules_data = [
            {
                "name": "40 Hours / Week",
                "is_active": True,
                "days": [
                    (0, datetime.time(9, 0), datetime.time(18, 0), Decimal("1.00"), Decimal("8.00")),
                    (1, datetime.time(9, 0), datetime.time(18, 0), Decimal("1.00"), Decimal("8.00")),
                    (2, datetime.time(9, 0), datetime.time(18, 0), Decimal("1.00"), Decimal("8.00")),
                    (3, datetime.time(9, 0), datetime.time(18, 0), Decimal("1.00"), Decimal("8.00")),
                    (4, datetime.time(9, 0), datetime.time(18, 0), Decimal("1.00"), Decimal("8.00")),
                ]
            },
            {
                "name": "Night Shift",
                "is_active": True,
                "days": [
                    (0, datetime.time(22, 0), datetime.time(7, 0), Decimal("1.00"), Decimal("8.00")),
                    (1, datetime.time(22, 0), datetime.time(7, 0), Decimal("1.00"), Decimal("8.00")),
                    (2, datetime.time(22, 0), datetime.time(7, 0), Decimal("1.00"), Decimal("8.00")),
                    (3, datetime.time(22, 0), datetime.time(7, 0), Decimal("1.00"), Decimal("8.00")),
                    (4, datetime.time(22, 0), datetime.time(7, 0), Decimal("1.00"), Decimal("8.00")),
                ]
            },
            {
                "name": "Rotated Weekend",
                "is_active": True,
                "days": [
                    (1, datetime.time(9, 0), datetime.time(18, 0), Decimal("1.00"), Decimal("8.00")),
                    (2, datetime.time(9, 0), datetime.time(18, 0), Decimal("1.00"), Decimal("8.00")),
                    (3, datetime.time(9, 0), datetime.time(18, 0), Decimal("1.00"), Decimal("8.00")),
                    (4, datetime.time(9, 0), datetime.time(18, 0), Decimal("1.00"), Decimal("8.00")),
                    (5, datetime.time(9, 0), datetime.time(18, 0), Decimal("1.00"), Decimal("8.00")),
                ]
            },
            {
                "name": "Flexible Hybrid",
                "is_active": True,
                "days": [
                    (0, datetime.time(9, 0), datetime.time(17, 30), Decimal("1.00"), Decimal("7.50")),
                    (1, datetime.time(9, 0), datetime.time(17, 30), Decimal("1.00"), Decimal("7.50")),
                    (2, datetime.time(9, 0), datetime.time(17, 30), Decimal("1.00"), Decimal("7.50")),
                    (3, datetime.time(9, 0), datetime.time(17, 30), Decimal("1.00"), Decimal("7.50")),
                    (4, datetime.time(9, 0), datetime.time(17, 30), Decimal("1.00"), Decimal("7.50")),
                ]
            },
            {
                "name": "Part-time 20h",
                "is_active": False,
                "days": [
                    (0, datetime.time(9, 0), datetime.time(14, 0), Decimal("0.00"), Decimal("5.00")),
                    (1, datetime.time(9, 0), datetime.time(14, 0), Decimal("0.00"), Decimal("5.00")),
                    (2, datetime.time(9, 0), datetime.time(14, 0), Decimal("0.00"), Decimal("5.00")),
                    (3, datetime.time(9, 0), datetime.time(14, 0), Decimal("0.00"), Decimal("5.00")),
                ]
            },
        ]

        # Also keep or rename "Standard 40 Hours/Week" if existing
        schedule, _ = WorkingSchedule.objects.get_or_create(
            name="Standard 40 Hours/Week",
            defaults={"average_hours_per_day": Decimal("8.00"), "is_active": True}
        )
        for day_idx in range(5):
            ScheduleDay.objects.get_or_create(
                schedule=schedule,
                day_of_week=day_idx,
                defaults={
                    "work_from": datetime.time(9, 0),
                    "work_to": datetime.time(18, 0),
                    "break_hours": Decimal("1.00"),
                    "hours": Decimal("8.00"),
                }
            )

        for s_data in schedules_data:
            s_obj, _ = WorkingSchedule.objects.get_or_create(
                name=s_data["name"],
                defaults={
                    "average_hours_per_day": Decimal("8.00"),
                    "is_active": s_data["is_active"],
                    "timezone": "UTC",
                }
            )
            for d_idx, w_from, w_to, brk, hrs in s_data["days"]:
                ScheduleDay.objects.get_or_create(
                    schedule=s_obj,
                    day_of_week=d_idx,
                    work_from=w_from,
                    defaults={
                        "work_to": w_to,
                        "break_hours": brk,
                        "hours": hrs,
                    }
                )
        self.stdout.write(self.style.SUCCESS("  [+] Working Schedules created / verified."))

        # 3. Salary Structure
        structure, _ = SalaryStructure.objects.get_or_create(
            code="STD_CORP",
            defaults={
                "name": "Standard Corporate Salary Structure",
                "description": "Standard corporate compensation with Basic, Allowances, PF, and Tax calculations.",
                "is_active": True,
            }
        )
        self.stdout.write(self.style.SUCCESS(f"  [+] Salary Structure '{structure.name}' ready."))

        # 4. Salary Rules
        rules_data = [
            {
                "code": "BASIC",
                "name": "Basic Salary",
                "category": "BASIC",
                "sequence": 10,
                "amount_type": "percentage",
                "percentage_base_code": "WAGE",
                "percentage": Decimal("50.00"),
                "formula": "",
            },
            {
                "code": "HRA",
                "name": "House Rent Allowance (HRA)",
                "category": "ALLOWANCE",
                "sequence": 20,
                "amount_type": "percentage",
                "percentage_base_code": "BASIC",
                "percentage": Decimal("40.00"),
                "formula": "",
            },
            {
                "code": "CONV",
                "name": "Conveyance Allowance",
                "category": "ALLOWANCE",
                "sequence": 30,
                "amount_type": "fixed",
                "fixed_amount": Decimal("200.00"),
                "formula": "",
            },
            {
                "code": "SPECIAL",
                "name": "Special Allowance",
                "category": "ALLOWANCE",
                "sequence": 40,
                "amount_type": "formula",
                "formula": "max(0, contract.wage - (rules.get('BASIC', 0) + rules.get('HRA', 0) + rules.get('CONV', 0)))",
            },
            {
                "code": "GROSS",
                "name": "Gross Earnings",
                "category": "GROSS",
                "sequence": 50,
                "amount_type": "formula",
                "formula": "rules.get('BASIC', 0) + rules.get('HRA', 0) + rules.get('CONV', 0) + rules.get('SPECIAL', 0)",
            },
            {
                "code": "PF",
                "name": "Provident Fund (PF)",
                "category": "DEDUCTION",
                "sequence": 60,
                "amount_type": "percentage",
                "percentage_base_code": "BASIC",
                "percentage": Decimal("12.00"),
                "formula": "",
            },
            {
                "code": "TAX",
                "name": "Professional Tax / Withholding",
                "category": "DEDUCTION",
                "sequence": 70,
                "amount_type": "formula",
                "formula": "rules.get('GROSS', 0) * Decimal('0.10') if rules.get('GROSS', 0) > 4000 else Decimal('150.00')",
            },
            {
                "code": "NET",
                "name": "Net Salary",
                "category": "NET",
                "sequence": 100,
                "amount_type": "formula",
                "formula": "rules.get('GROSS', 0) - (rules.get('PF', 0) + rules.get('TAX', 0))",
            },
        ]

        for r_dict in rules_data:
            code = r_dict.pop("code")
            rule, _ = SalaryRule.objects.update_or_create(
                code=code,
                defaults=r_dict
            )
            SalaryStructureRule.objects.get_or_create(
                structure=structure,
                rule=rule,
                defaults={"sequence": rule.sequence}
            )

        self.stdout.write(self.style.SUCCESS(f"  [+] Configured {len(rules_data)} Salary Rules linked to '{structure.code}'."))

        # 5. Employees
        employees_data = [
            {
                "code": "EMP001",
                "first_name": "John",
                "last_name": "Doe",
                "email": "john.doe@example.com",
                "department": "Engineering",
                "job_title": "Lead Software Engineer",
                "bank_name": "Chase Bank",
                "bank_account_number": "123456789012",
                "bank_ifsc_or_swift": "CHASUS33",
                "date_of_joining": datetime.date(2024, 1, 15),
                "is_active": True,
                "created_by": admin_user,
                "updated_by": admin_user,
            },
            {
                "code": "EMP002",
                "first_name": "Jane",
                "last_name": "Smith",
                "email": "jane.smith@example.com",
                "department": "Product",
                "job_title": "Senior Product Manager",
                "bank_name": "Bank of America",
                "bank_account_number": "987654321098",
                "bank_ifsc_or_swift": "BOFAUS3N",
                "date_of_joining": datetime.date(2024, 3, 1),
                "is_active": True,
                "created_by": admin_user,
                "updated_by": admin_user,
            },
            {
                # Missing bank details (Useful for Step 8 Validation testing!)
                "code": "EMP003",
                "first_name": "Alex",
                "last_name": "Miller",
                "email": "alex.miller@example.com",
                "department": "Design",
                "job_title": "UI/UX Designer",
                "bank_name": "",
                "bank_account_number": "",
                "bank_ifsc_or_swift": "",
                "date_of_joining": datetime.date(2025, 6, 10),
                "is_active": True,
                "created_by": admin_user,
                "updated_by": admin_user,
            },
            {
                # Employee with no active contract (Useful for Step 8 Uncontracted Employee testing!)
                "code": "EMP004",
                "first_name": "Sam",
                "last_name": "Wilson",
                "email": "sam.wilson@example.com",
                "department": "Sales",
                "job_title": "Sales Representative",
                "bank_name": "Wells Fargo",
                "bank_account_number": "556677889900",
                "bank_ifsc_or_swift": "WFBIUS6S",
                "date_of_joining": datetime.date(2026, 2, 1),
                "is_active": True,
                "created_by": admin_user,
                "updated_by": admin_user,
            },
        ]

        emp_objs = {}
        for emp_info in employees_data:
            code = emp_info.pop("code")
            emp, _ = Employee.objects.update_or_create(code=code, defaults=emp_info)
            emp_objs[code] = emp

        self.stdout.write(self.style.SUCCESS(f"  [+] Created/Updated {len(emp_objs)} test employees (with created_by/updated_by)."))

        # 6. Contracts
        contracts_data = [
            {
                "employee": emp_objs["EMP001"],
                "name": "Employment Contract - John Doe",
                "wage": Decimal("6000.00"),
                "wage_type": "monthly",
                "working_schedule": schedule,
                "salary_structure": structure,
                "start_date": datetime.date(2024, 1, 15),
                "end_date": None,
                "state": "active",
            },
            {
                "employee": emp_objs["EMP002"],
                "name": "Employment Contract - Jane Smith",
                "wage": Decimal("5000.00"),
                "wage_type": "monthly",
                "working_schedule": schedule,
                "salary_structure": structure,
                "start_date": datetime.date(2024, 3, 1),
                "end_date": None,
                "state": "active",
            },
            {
                "employee": emp_objs["EMP003"],
                "name": "Employment Contract - Alex Miller",
                "wage": Decimal("4200.00"),
                "wage_type": "monthly",
                "working_schedule": schedule,
                "salary_structure": structure,
                "start_date": datetime.date(2025, 6, 10),
                "end_date": None,
                "state": "active",
            },
            {
                "employee": emp_objs["EMP004"],
                "name": "Draft Contract - Sam Wilson",
                "wage": Decimal("3500.00"),
                "wage_type": "monthly",
                "working_schedule": schedule,
                "salary_structure": structure,
                "start_date": datetime.date(2026, 2, 1),
                "end_date": None,
                "state": "draft",  # Note: in draft state, not active!
            },
        ]

        for c_dict in contracts_data:
            Contract.objects.update_or_create(
                employee=c_dict["employee"],
                name=c_dict["name"],
                defaults=c_dict
            )

        self.stdout.write(self.style.SUCCESS("  [+] Created/Updated test contracts."))

        # 7. Leave Types
        leave_types_data = [
            {
                "code": "PTO",
                "name": "Paid Time Off (Annual Vacation)",
                "is_paid": True,
                "max_days_per_year": Decimal("18.00"),
                "color": "#2563EB",
            },
            {
                "code": "SICK",
                "name": "Sick Leave",
                "is_paid": True,
                "max_days_per_year": Decimal("10.00"),
                "color": "#EF4444",
            },
            {
                "code": "CASUAL",
                "name": "Casual Leave",
                "is_paid": True,
                "max_days_per_year": Decimal("7.00"),
                "color": "#F59E0B",
            },
            {
                "code": "UNPAID",
                "name": "Unpaid Leave (Loss of Pay)",
                "is_paid": False,
                "max_days_per_year": Decimal("0.00"),  # Unlimited
                "color": "#6B7280",
            },
        ]

        lt_objs = {}
        for lt_info in leave_types_data:
            code = lt_info.pop("code")
            lt, _ = LeaveType.objects.update_or_create(code=code, defaults=lt_info)
            lt_objs[code] = lt

        self.stdout.write(self.style.SUCCESS(f"  [+] Configured {len(lt_objs)} Leave Types (PTO, Sick, Casual, Unpaid)."))

        # 8. Sample Leave Requests
        # John Doe: Approved 2 days PTO in August
        LeaveRequest.objects.get_or_create(
            employee=emp_objs["EMP001"],
            leave_type=lt_objs["PTO"],
            start_date=datetime.date(2026, 8, 10),
            end_date=datetime.date(2026, 8, 11),
            defaults={
                "number_of_days": Decimal("2.00"),
                "reason": "Family vacation",
                "status": "approved",
                "approved_by": admin_user,
                "approved_at": datetime.datetime(2026, 8, 5, 10, 0),
            }
        )
        # Jane Smith: Pending 1 day Sick leave
        LeaveRequest.objects.get_or_create(
            employee=emp_objs["EMP002"],
            leave_type=lt_objs["SICK"],
            start_date=datetime.date(2026, 9, 15),
            end_date=datetime.date(2026, 9, 15),
            defaults={
                "number_of_days": Decimal("1.00"),
                "reason": "Doctor appointment",
                "status": "submitted",
            }
        )
        self.stdout.write(self.style.SUCCESS("  [+] Created sample Leave Requests."))
        self.stdout.write(self.style.SUCCESS("==> Seeding completed successfully!"))
