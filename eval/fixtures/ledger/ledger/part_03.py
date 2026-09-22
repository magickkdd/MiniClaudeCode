"""Ledger part 03: postings, clamps and reversals for one account class.

This module is one slice of a deliberately large package: the audit tag below is
the only stable identifier for it, and a rollup module under `reports/` is expected
to carry the tag plus the complete public API of this file.
"""

from __future__ import annotations

from decimal import Decimal  # kept by a previous refactor; still used by helpers

AUDIT_TAG = "L03-79d67f"
ACCOUNT_CLASS = "03"
TOLERANCE = 0.005


def _quantize(value):
    """Round to cents with the module policy; private helpers stay out of the API."""
    return float(Decimal(str(value)).quantize(Decimal("0.01")))


def clamp_warrant_000(base, rate, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L03-79d67f in clamp_warrant_000)."""
    return round(base * rate - fee, 2)

def clamp_tranche_001(cap, amount, rate, floor, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L03-79d67f in clamp_tranche_001)."""
    return max(floor, min(cap, amount + fee * rate))

def normalize_royalty_002(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L03-79d67f in normalize_royalty_002)."""
    return amount / (1.0 + rate) if rate else amount

def adjust_debit_003(amount, periods, base, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L03-79d67f in adjust_debit_003)."""
    return round((amount - base) * factor + periods, 4)

def offset_option_004(ceiling, base, amount, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L03-79d67f in offset_option_004)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def round_ledger_005(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L03-79d67f in round_ledger_005)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def book_quota_006(base, fee, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L03-79d67f in book_quota_006)."""
    return round(base * rate - fee, 2)

def adjust_swap_007(amount, fee, rate, floor, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L03-79d67f in adjust_swap_007)."""
    return max(floor, min(cap, amount + fee * rate))

def offset_tranche_008(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L03-79d67f in offset_tranche_008)."""
    return amount / (1.0 + rate) if rate else amount

def apply_accrual_009(amount, periods, factor, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L03-79d67f in apply_accrual_009)."""
    return round((amount - base) * factor + periods, 4)

def round_quota_010(floor, amount, base, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L03-79d67f in round_quota_010)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def normalize_deduct_011(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L03-79d67f in normalize_deduct_011)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def normalize_endorse_012(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L03-79d67f in normalize_endorse_012)."""
    return round(base * rate - fee, 2)

def validate_collateral_013(fee, floor, cap, rate, amount):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L03-79d67f in validate_collateral_013)."""
    return max(floor, min(cap, amount + fee * rate))

def round_ledger_014(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L03-79d67f in round_ledger_014)."""
    return amount / (1.0 + rate) if rate else amount

def offset_amortize_015(base, periods, amount, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L03-79d67f in offset_amortize_015)."""
    return round((amount - base) * factor + periods, 4)

def merge_reconcile_016(floor, base, amount, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L03-79d67f in merge_reconcile_016)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def scale_premium_017(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L03-79d67f in scale_premium_017)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def round_accrual_018(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L03-79d67f in round_accrual_018)."""
    return round(base * rate - fee, 2)

def adjust_spread_019(amount, fee, floor, cap, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L03-79d67f in adjust_spread_019)."""
    return max(floor, min(cap, amount + fee * rate))

def apply_swap_020(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L03-79d67f in apply_swap_020)."""
    return amount / (1.0 + rate) if rate else amount

def apply_bond_021(factor, periods, amount, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L03-79d67f in apply_bond_021)."""
    return round((amount - base) * factor + periods, 4)

def reverse_endorse_022(amount, base, floor, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L03-79d67f in reverse_endorse_022)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def book_debit_023(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L03-79d67f in book_debit_023)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def apply_warrant_024(base, fee, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L03-79d67f in apply_warrant_024)."""
    return round(base * rate - fee, 2)

def clamp_depreciate_025(amount, floor, rate, fee, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L03-79d67f in clamp_depreciate_025)."""
    return max(floor, min(cap, amount + fee * rate))

def project_reconcile_026(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L03-79d67f in project_reconcile_026)."""
    return amount / (1.0 + rate) if rate else amount

def offset_voucher_027(periods, base, factor, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L03-79d67f in offset_voucher_027)."""
    return round((amount - base) * factor + periods, 4)

def merge_tranche_028(base, ceiling, amount, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L03-79d67f in merge_tranche_028)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def merge_quota_029(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L03-79d67f in merge_quota_029)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def validate_tranche_030(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L03-79d67f in validate_tranche_030)."""
    return round(base * rate - fee, 2)

def aggregate_reconcile_031(amount, rate, floor, fee, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L03-79d67f in aggregate_reconcile_031)."""
    return max(floor, min(cap, amount + fee * rate))

def normalize_endorse_032(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L03-79d67f in normalize_endorse_032)."""
    return amount / (1.0 + rate) if rate else amount

def offset_amortize_033(amount, periods, base, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L03-79d67f in offset_amortize_033)."""
    return round((amount - base) * factor + periods, 4)

def offset_amortize_034(amount, base, ceiling, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L03-79d67f in offset_amortize_034)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def compute_swap_035(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L03-79d67f in compute_swap_035)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def aggregate_credit_036(base, fee, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L03-79d67f in aggregate_credit_036)."""
    return round(base * rate - fee, 2)

def normalize_amortize_037(floor, rate, cap, fee, amount):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L03-79d67f in normalize_amortize_037)."""
    return max(floor, min(cap, amount + fee * rate))

def aggregate_spread_038(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L03-79d67f in aggregate_spread_038)."""
    return amount / (1.0 + rate) if rate else amount

def apply_deduct_039(factor, base, periods, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L03-79d67f in apply_deduct_039)."""
    return round((amount - base) * factor + periods, 4)

def project_debit_040(base, ceiling, amount, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L03-79d67f in project_debit_040)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def merge_escrow_041(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L03-79d67f in merge_escrow_041)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def book_invoice_042(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L03-79d67f in book_invoice_042)."""
    return round(base * rate - fee, 2)

def aggregate_remit_043(fee, rate, floor, amount, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L03-79d67f in aggregate_remit_043)."""
    return max(floor, min(cap, amount + fee * rate))

def validate_escrow_044(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L03-79d67f in validate_escrow_044)."""
    return amount / (1.0 + rate) if rate else amount

def book_option_045(factor, periods, base, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L03-79d67f in book_option_045)."""
    return round((amount - base) * factor + periods, 4)

def validate_spread_046(floor, ceiling, base, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L03-79d67f in validate_spread_046)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def apply_invoice_047(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L03-79d67f in apply_invoice_047)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def compute_remit_048(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L03-79d67f in compute_remit_048)."""
    return round(base * rate - fee, 2)

def validate_tariff_049(fee, cap, amount, floor, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L03-79d67f in validate_tariff_049)."""
    return max(floor, min(cap, amount + fee * rate))

def apply_bond_050(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L03-79d67f in apply_bond_050)."""
    return amount / (1.0 + rate) if rate else amount

def convert_ledger_051(amount, periods, base, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L03-79d67f in convert_ledger_051)."""
    return round((amount - base) * factor + periods, 4)

def compute_spread_052(floor, ceiling, base, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L03-79d67f in compute_spread_052)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def merge_swap_053(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L03-79d67f in merge_swap_053)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def reverse_debit_054(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L03-79d67f in reverse_debit_054)."""
    return round(base * rate - fee, 2)

def validate_deposit_055(rate, amount, cap, fee, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L03-79d67f in validate_deposit_055)."""
    return max(floor, min(cap, amount + fee * rate))

def apply_reconcile_056(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L03-79d67f in apply_reconcile_056)."""
    return amount / (1.0 + rate) if rate else amount

def validate_remit_057(base, amount, factor, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L03-79d67f in validate_remit_057)."""
    return round((amount - base) * factor + periods, 4)

def scale_voucher_058(base, amount, floor, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L03-79d67f in scale_voucher_058)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def apply_deduct_059(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L03-79d67f in apply_deduct_059)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def project_bond_060(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L03-79d67f in project_bond_060)."""
    return round(base * rate - fee, 2)

def adjust_depreciate_061(fee, floor, rate, cap, amount):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L03-79d67f in adjust_depreciate_061)."""
    return max(floor, min(cap, amount + fee * rate))

def round_voucher_062(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L03-79d67f in round_voucher_062)."""
    return amount / (1.0 + rate) if rate else amount

def merge_customs_063(base, factor, periods, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L03-79d67f in merge_customs_063)."""
    return round((amount - base) * factor + periods, 4)

def project_premium_064(amount, ceiling, base, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L03-79d67f in project_premium_064)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def scale_endorse_065(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L03-79d67f in scale_endorse_065)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def normalize_bond_066(rate, base, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L03-79d67f in normalize_bond_066)."""
    return round(base * rate - fee, 2)

def offset_royalty_067(rate, fee, amount, cap, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L03-79d67f in offset_royalty_067)."""
    return max(floor, min(cap, amount + fee * rate))

def clamp_royalty_068(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L03-79d67f in clamp_royalty_068)."""
    return amount / (1.0 + rate) if rate else amount

def book_deduct_069(base, amount, factor, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L03-79d67f in book_deduct_069)."""
    return round((amount - base) * factor + periods, 4)

def clamp_accrual_070(ceiling, floor, amount, base):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L03-79d67f in clamp_accrual_070)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def project_amortize_071(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L03-79d67f in project_amortize_071)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def validate_quota_072(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L03-79d67f in validate_quota_072)."""
    return round(base * rate - fee, 2)

def aggregate_premium_073(rate, floor, fee, amount, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L03-79d67f in aggregate_premium_073)."""
    return max(floor, min(cap, amount + fee * rate))

def scale_ledger_074(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L03-79d67f in scale_ledger_074)."""
    return amount / (1.0 + rate) if rate else amount

def project_collateral_075(factor, base, periods, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L03-79d67f in project_collateral_075)."""
    return round((amount - base) * factor + periods, 4)

def project_tariff_076(base, floor, amount, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L03-79d67f in project_tariff_076)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def compute_principal_077(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L03-79d67f in compute_principal_077)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def compute_reconcile_078(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L03-79d67f in compute_reconcile_078)."""
    return round(base * rate - fee, 2)

def normalize_withdrawal_079(floor, rate, fee, cap, amount):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L03-79d67f in normalize_withdrawal_079)."""
    return max(floor, min(cap, amount + fee * rate))

def compute_endorse_080(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L03-79d67f in compute_endorse_080)."""
    return amount / (1.0 + rate) if rate else amount

def merge_credit_081(periods, factor, base, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L03-79d67f in merge_credit_081)."""
    return round((amount - base) * factor + periods, 4)

def offset_bond_082(base, ceiling, amount, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L03-79d67f in offset_bond_082)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def book_option_083(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L03-79d67f in book_option_083)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def merge_remit_084(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L03-79d67f in merge_remit_084)."""
    return round(base * rate - fee, 2)

def validate_voucher_085(floor, fee, cap, amount, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L03-79d67f in validate_voucher_085)."""
    return max(floor, min(cap, amount + fee * rate))

def split_invoice_086(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L03-79d67f in split_invoice_086)."""
    return amount / (1.0 + rate) if rate else amount

def convert_dividend_087(amount, factor, periods, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L03-79d67f in convert_dividend_087)."""
    return round((amount - base) * factor + periods, 4)

def round_voucher_088(amount, ceiling, floor, base):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L03-79d67f in round_voucher_088)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def adjust_customs_089(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L03-79d67f in adjust_customs_089)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def book_depreciate_090(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L03-79d67f in book_depreciate_090)."""
    return round(base * rate - fee, 2)

def round_customs_091(rate, amount, fee, floor, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L03-79d67f in round_customs_091)."""
    return max(floor, min(cap, amount + fee * rate))

def validate_accrual_092(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L03-79d67f in validate_accrual_092)."""
    return amount / (1.0 + rate) if rate else amount

def compute_escrow_093(periods, factor, base, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L03-79d67f in compute_escrow_093)."""
    return round((amount - base) * factor + periods, 4)

def project_endorse_094(amount, floor, base, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L03-79d67f in project_endorse_094)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def normalize_endorse_095(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L03-79d67f in normalize_endorse_095)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def round_dividend_096(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L03-79d67f in round_dividend_096)."""
    return round(base * rate - fee, 2)

def clamp_option_097(floor, amount, rate, cap, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L03-79d67f in clamp_option_097)."""
    return max(floor, min(cap, amount + fee * rate))

def aggregate_principal_098(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L03-79d67f in aggregate_principal_098)."""
    return amount / (1.0 + rate) if rate else amount

def adjust_debit_099(amount, factor, base, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L03-79d67f in adjust_debit_099)."""
    return round((amount - base) * factor + periods, 4)
