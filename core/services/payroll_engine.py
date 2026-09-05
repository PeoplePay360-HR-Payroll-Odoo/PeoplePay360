import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, List, Optional
from django.db import transaction
from django.db.models import Q
from core.models import (
    Employee,
    Contract,
    SalaryStructure,
    SalaryRule,
    SalaryStructureRule,
    Payrun,
    Payslip,
    PayslipLine,
)


class PayrollCalculationError(Exception):
    """Raised when payroll calculation fails due to missing contracts, formula errors, or missing rules."""
    pass


class RulesContext(dict):
    """
    A dictionary wrapper for evaluated salary rules in formulas.
    Allows easy lookups like rules.get('BASIC', Decimal('0.00')) or rules['BASIC']
    with automatic Decimal conversion.
    """
    def __getitem__(self, key: str) -> Decimal:
        val = super().get(key, Decimal('0.00'))
        return Decimal(str(val)) if not isinstance(val, Decimal) else val

    def get(self, key: str, default: Any = Decimal('0.00')) -> Decimal:
        val = super().get(key, default)
        return Decimal(str(val)) if not isinstance(val, Decimal) else val


class RuleComputationResult:
    """Represents the evaluated outcome of an individual SalaryRule."""
    def __init__(
        self,
        rule: SalaryRule,
        code: str,
        name: str,
        category: str,
        sequence: int,
        rate: Decimal,
        amount: Decimal,
        total: Decimal
    ):
        self.rule = rule
        self.code = code
        self.name = name
        self.category = category
        self.sequence = sequence
        self.rate = rate.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        self.amount = amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        self.total = total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    def __repr__(self):
        return f"<RuleResult {self.code}: ${self.total} ({self.category})>"


class PayrollEngine:
    """
    Core calculation service for PeoplePay360.
    Implements dynamic salary rule evaluation, applicable contract resolution,
    and payslip line generation based on Odoo-style payroll principles.
    """

    SAFE_EVAL_BUILTINS = {
        'abs': abs,
        'min': min,
        'max': max,
        'round': round,
        'int': int,
        'float': float,
        'Decimal': Decimal,
    }

    @classmethod
    def quantize(cls, value: Any) -> Decimal:
        """Helper to ensure all money amounts are 2-decimal rounded Decimals."""
        if not isinstance(value, Decimal):
            value = Decimal(str(value))
        return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    @classmethod
    def find_applicable_contract(
        cls,
        employee: Employee,
        start_date: datetime.date,
        end_date: datetime.date
    ) -> Contract:
        """
        Finds the active contract for an employee applicable to the pay period.
        A contract is applicable if:
        1. State is 'active'
        2. Contract start_date <= period end_date
        3. Contract end_date is None OR contract end_date >= period start_date
        """
        contracts = Contract.objects.filter(
            employee=employee,
            state='active',
            start_date__lte=end_date,
        ).filter(
            Q(end_date__isnull=True) | Q(end_date__gte=start_date)
        ).select_related('salary_structure', 'working_schedule').order_by('-start_date')

        # Refine search in python to avoid complex Q objects across unions
        applicable = None
        for contract in contracts:
            if contract.is_applicable_for(start_date, end_date):
                applicable = contract
                break

        if not applicable:
            raise PayrollCalculationError(
                f"No active contract found for employee {employee.code} ({employee.full_name}) "
                f"covering the payrun period {start_date} to {end_date}."
            )

        return applicable

    @classmethod
    def evaluate_rule(
        cls,
        rule: SalaryRule,
        contract: Contract,
        employee: Employee,
        rules_context: RulesContext,
        worked_days: Decimal = Decimal('30.00'),
        total_days: Decimal = Decimal('30.00'),
    ) -> RuleComputationResult:
        """
        Evaluates a single SalaryRule in context of the employee, contract, and already computed rules.
        Supports:
        - fixed: Flat numerical amount
        - percentage: Percentage of another rule (or contract wage)
        - formula: Safe Python expression
        """
        amount_type = rule.amount_type
        rate = Decimal('100.00')
        base_amount = Decimal('0.00')
        total = Decimal('0.00')

        if amount_type == 'fixed':
            rate = Decimal('100.00')
            base_amount = rule.fixed_amount
            total = rule.fixed_amount

        elif amount_type == 'percentage':
            base_code = (rule.percentage_base_code or 'WAGE').upper()
            if base_code in ('WAGE', 'BASE', 'CONTRACT'):
                base_amount = contract.wage
            else:
                base_amount = rules_context.get(base_code, Decimal('0.00'))

            rate = rule.percentage
            total = (base_amount * rate) / Decimal('100.00')

        elif amount_type == 'formula':
            formula_str = (rule.formula or '').strip()
            if not formula_str:
                total = Decimal('0.00')
            else:
                eval_locals = {
                    'contract': contract,
                    'employee': employee,
                    'wage': contract.wage,
                    'worked_days': worked_days,
                    'total_days': total_days,
                    'rules': rules_context,
                    'Decimal': Decimal,
                }
                try:
                    raw_result = eval(
                        formula_str,
                        {"__builtins__": cls.SAFE_EVAL_BUILTINS},
                        eval_locals
                    )
                    total = cls.quantize(raw_result)
                except Exception as e:
                    raise PayrollCalculationError(
                        f"Error executing formula for rule '{rule.code}' ({rule.name}): {str(e)}"
                    )

            rate = Decimal('100.00')
            base_amount = total

        else:
            raise PayrollCalculationError(f"Unknown amount_type '{amount_type}' on rule '{rule.code}'.")

        # Sanitize against negative amounts for earnings/allowances/net
        if rule.category in ('BASIC', 'ALLOWANCE', 'GROSS', 'NET') and total < Decimal('0.00'):
            total = Decimal('0.00')

        return RuleComputationResult(
            rule=rule,
            code=rule.code,
            name=rule.name,
            category=rule.category,
            sequence=rule.sequence,
            rate=rate,
            amount=base_amount,
            total=total
        )

    @classmethod
    def calculate_structure_rules(
        cls,
        structure: SalaryStructure,
        contract: Contract,
        employee: Employee,
        worked_days: Decimal = Decimal('30.00'),
        total_days: Decimal = Decimal('30.00'),
    ) -> Dict[str, Any]:
        """
        Sequentially calculates all active rules linked to a SalaryStructure.
        Maintains an execution context so later rules can reference earlier results.
        Returns a dictionary containing summary totals and ordered line results.
        """
        structure_rules = (
            SalaryStructureRule.objects
            .filter(structure=structure, rule__is_active=True)
            .select_related('rule')
            .order_by('sequence', 'rule__sequence', 'rule__code')
        )

        if not structure_rules.exists():
            raise PayrollCalculationError(
                f"Salary structure '{structure.code}' ({structure.name}) has no active rules assigned."
            )

        rules_context = RulesContext()
        line_results: List[RuleComputationResult] = []

        for struct_rule in structure_rules:
            rule = struct_rule.rule
            result = cls.evaluate_rule(
                rule=rule,
                contract=contract,
                employee=employee,
                rules_context=rules_context,
                worked_days=worked_days,
                total_days=total_days
            )
            # Store in context for subsequent rules to reference by code
            rules_context[result.code] = result.total
            line_results.append(result)

        # Aggregate standard summary buckets
        basic_wage = Decimal('0.00')
        gross_wage = Decimal('0.00')
        total_deductions = Decimal('0.00')
        net_wage = Decimal('0.00')

        has_explicit_gross = False
        has_explicit_net = False

        for res in line_results:
            cat = res.category
            if cat == 'BASIC':
                basic_wage += res.total
            elif cat == 'DEDUCTION':
                total_deductions += res.total
            elif cat == 'GROSS':
                gross_wage = res.total
                has_explicit_gross = True
            elif cat == 'NET':
                net_wage = res.total
                has_explicit_net = True

        # Fallback if no explicit GROSS rule was defined
        if not has_explicit_gross:
            allowances = sum(
                res.total for res in line_results if res.category == 'ALLOWANCE'
            )
            gross_wage = basic_wage + allowances

        # Fallback if no explicit NET rule was defined
        if not has_explicit_net:
            net_wage = max(Decimal('0.00'), gross_wage - total_deductions)

        return {
            'basic_wage': cls.quantize(basic_wage),
            'gross_wage': cls.quantize(gross_wage),
            'total_deductions': cls.quantize(total_deductions),
            'net_wage': cls.quantize(net_wage),
            'rules_context': rules_context,
            'line_results': line_results,
        }

    @classmethod
    def compute_payslip(
        cls,
        payrun: Payrun,
        employee: Employee,
        contract: Optional[Contract] = None,
        worked_days: Optional[Decimal] = None,
    ) -> Payslip:
        """
        Full end-to-end payslip calculation for a single employee under a payrun.
        Resolves contract, calculates structure rules, and writes immutable
        Payslip and PayslipLine records in an atomic transaction.
        """
        # 1. Resolve contract
        if not contract:
            contract = cls.find_applicable_contract(
                employee=employee,
                start_date=payrun.start_date,
                end_date=payrun.end_date
            )

        # 2. Determine Salary Structure (priority: payrun structure or contract structure)
        structure = payrun.salary_structure or contract.salary_structure
        if not structure:
            raise PayrollCalculationError(
                f"No salary structure specified on Payrun '{payrun.name}' or Contract '{contract.name}'."
            )

        # 3. Determine worked days (default to 30.00 for monthly)
        if worked_days is None:
            # Standard month default
            period_length = (payrun.end_date - payrun.start_date).days + 1
            worked_days = Decimal(str(period_length))

        total_days = Decimal(str((payrun.end_date - payrun.start_date).days + 1))

        # 4. Run calculation
        calc_result = cls.calculate_structure_rules(
            structure=structure,
            contract=contract,
            employee=employee,
            worked_days=worked_days,
            total_days=total_days,
        )

        # 5. Atomically persist to database
        with transaction.atomic():
            payslip, _ = Payslip.objects.update_or_create(
                payrun=payrun,
                employee=employee,
                defaults={
                    'contract': contract,
                    'salary_structure': structure,
                    'period_start': payrun.start_date,
                    'period_end': payrun.end_date,
                    'worked_days': worked_days,
                    'basic_wage': calc_result['basic_wage'],
                    'gross_wage': calc_result['gross_wage'],
                    'total_deductions': calc_result['total_deductions'],
                    'net_wage': calc_result['net_wage'],
                    'state': 'computed',
                }
            )

            # Clear old lines to ensure idempotency when recomputing
            payslip.lines.all().delete()

            # Bulk create new PayslipLine audit items
            lines_to_create = [
                PayslipLine(
                    payslip=payslip,
                    salary_rule=item.rule,
                    code=item.code,
                    name=item.name,
                    category=item.category,
                    sequence=item.sequence,
                    rate=item.rate,
                    amount=item.amount,
                    total=item.total,
                )
                for item in calc_result['line_results']
            ]
            PayslipLine.objects.bulk_create(lines_to_create)

        return payslip
