import datetime
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import (
    Employee,
    WorkingSchedule,
    ScheduleDay,
    Contract,
    Attendance,
)


class Command(BaseCommand):
    help = "Seeds initial attendance data matching the wireframe mockups and specifications."

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("==> Seeding Attendance data..."))

        # 1. Update/Ensure Sara Khan as Manager for Aarav Mehta
        sara = Employee.objects.filter(code='EMP006').first()
        if sara:
            sara.first_name = "Sara"
            sara.last_name = "Khan"
            sara.department = "Finance"
            sara.job_title = "Finance Manager"
            sara.save()
            self.stdout.write(self.style.SUCCESS("  [+] Updated EMP006 to Sara Khan"))

        aarav = Employee.objects.filter(code='EMP005').first()
        if aarav:
            aarav.department = "Finance"
            aarav.job_title = "Payroll Specialist"
            if sara:
                aarav.manager = sara
            aarav.save()
            self.stdout.write(self.style.SUCCESS("  [+] Set Sara Khan as manager for Aarav Mehta"))

        # 2. Ensure Neha Patel exists (from mockup)
        neha, created = Employee.objects.get_or_create(
            code="EMP007",
            defaults={
                "first_name": "Neha",
                "last_name": "Patel",
                "email": "neha.patel@example.com",
                "department": "Finance",
                "job_title": "Accountant",
                "date_of_joining": datetime.date(2025, 4, 1),
                "is_active": True,
                "manager": sara,
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS("  [+] Created employee Neha Patel (EMP007)"))

        john = Employee.objects.filter(code='EMP001').first()
        jane = Employee.objects.filter(code='EMP002').first()
        alex = Employee.objects.filter(code='EMP003').first()

        # 3. Ensure Aarav's contract has working schedule with 8.50h for Wednesday to match 0.58h OT on 9.08h worked
        sched = WorkingSchedule.objects.filter(name="Standard 40 Hours/Week").first()
        if sched:
            # Wednesday is day_of_week=2
            wed = sched.days.filter(day_of_week=2).first()
            if wed:
                wed.hours = Decimal("8.50")
                wed.save()
            # Saturday is day_of_week=5 (today: 2026-09-05 is Saturday)
            # Let's ensure today Saturday has schedule day or handles correctly
            sat_day, _ = sched.days.get_or_create(
                day_of_week=5,
                defaults={
                    "work_from": datetime.time(9, 0),
                    "work_to": datetime.time(18, 0),
                    "break_hours": Decimal("1.00"),
                    "hours": Decimal("8.00"),
                }
            )

        tz = timezone.get_current_timezone()

        # 4. Attendance data for 02-Sep-2026 (exact mockup records)
        d_sep2 = datetime.date(2026, 9, 2)
        records_sep2 = [
            {
                "employee": aarav,
                "date": d_sep2,
                "check_in": timezone.make_aware(datetime.datetime(2026, 9, 2, 9, 5), tz),
                "check_out": timezone.make_aware(datetime.datetime(2026, 9, 2, 18, 10), tz),
                "status": "present",
            },
            {
                "employee": sara,
                "date": d_sep2,
                "check_in": timezone.make_aware(datetime.datetime(2026, 9, 2, 9, 15), tz),
                "check_out": timezone.make_aware(datetime.datetime(2026, 9, 2, 18, 2), tz),
                "status": "present",
            },
            {
                "employee": john,
                "date": d_sep2,
                "check_in": timezone.make_aware(datetime.datetime(2026, 9, 2, 9, 22), tz),
                "check_out": timezone.make_aware(datetime.datetime(2026, 9, 2, 18, 20), tz),
                "status": "present",
            },
            {
                "employee": neha,
                "date": d_sep2,
                "check_in": None,
                "check_out": None,
                "status": "absent",
            },
        ]

        # 5. Attendance data for Today (2026-09-05) - Default view when opening page
        d_today = datetime.date(2026, 9, 5)
        records_today = [
            {
                "employee": aarav,
                "date": d_today,
                "check_in": timezone.make_aware(datetime.datetime(2026, 9, 5, 9, 5), tz),
                "check_out": timezone.make_aware(datetime.datetime(2026, 9, 5, 18, 10), tz),
                "status": "present",
            },
            {
                "employee": sara,
                "date": d_today,
                "check_in": timezone.make_aware(datetime.datetime(2026, 9, 5, 9, 15), tz),
                "check_out": timezone.make_aware(datetime.datetime(2026, 9, 5, 18, 2), tz),
                "status": "present",
            },
            {
                "employee": john,
                "date": d_today,
                "check_in": timezone.make_aware(datetime.datetime(2026, 9, 5, 9, 22), tz),
                "check_out": timezone.make_aware(datetime.datetime(2026, 9, 5, 18, 20), tz),
                "status": "present",
            },
            {
                "employee": neha,
                "date": d_today,
                "check_in": None,
                "check_out": None,
                "status": "absent",
            },
            {
                "employee": jane,
                "date": d_today,
                "check_in": timezone.make_aware(datetime.datetime(2026, 9, 5, 9, 0), tz),
                "check_out": timezone.make_aware(datetime.datetime(2026, 9, 5, 17, 30), tz),
                "status": "present",
            },
            {
                "employee": alex,
                "date": d_today,
                "check_in": timezone.make_aware(datetime.datetime(2026, 9, 5, 9, 30), tz),
                "check_out": None,  # Checked in, hasn't checked out yet! Demonstrates live worked_hours since check_in!
                "status": "present",
            }
        ]

        # 6. Attendance data for Yesterday (2026-09-04)
        d_yest = datetime.date(2026, 9, 4)
        records_yest = [
            {
                "employee": aarav,
                "date": d_yest,
                "check_in": timezone.make_aware(datetime.datetime(2026, 9, 4, 9, 0), tz),
                "check_out": timezone.make_aware(datetime.datetime(2026, 9, 4, 18, 0), tz),
                "status": "present",
            },
            {
                "employee": sara,
                "date": d_yest,
                "check_in": timezone.make_aware(datetime.datetime(2026, 9, 4, 9, 10), tz),
                "check_out": timezone.make_aware(datetime.datetime(2026, 9, 4, 18, 5), tz),
                "status": "present",
            },
            {
                "employee": john,
                "date": d_yest,
                "check_in": timezone.make_aware(datetime.datetime(2026, 9, 4, 9, 0), tz),
                "check_out": timezone.make_aware(datetime.datetime(2026, 9, 4, 17, 0), tz),
                "status": "present",
            },
        ]

        all_records = records_sep2 + records_today + records_yest
        created_count = 0
        for r in all_records:
            if not r["employee"]:
                continue
            att, created = Attendance.objects.update_or_create(
                employee=r["employee"],
                date=r["date"],
                defaults={
                    "check_in": r["check_in"],
                    "check_out": r["check_out"],
                    "status": r["status"],
                }
            )
            # save computes worked_hours and overtime_hours automatically
            att.save()
            created_count += 1

        self.stdout.write(self.style.SUCCESS(f"  [+] Created/Updated {created_count} attendance records successfully."))
