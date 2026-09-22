"""Ledger part 02: postings, clamps and reversals for one account class.

This module is one slice of a deliberately large package: the audit tag below is
the only stable identifier for it, and a rollup module under `reports/` is expected
to carry the tag plus the complete public API of this file.
"""

from __future__ import annotations

from decimal import Decimal  # kept by a previous refactor; still used by helpers

AUDIT_TAG = "L02-1cf44d"
ACCOUNT_CLASS = "02"
TOLERANCE = 0.005


def _quantize(value):
    """Round to cents with the module policy; private helpers stay out of the API."""
    return float(Decimal(str(value)).quantize(Decimal("0.01")))


def normalize_withdrawal_000(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L02-1cf44d in normalize_withdrawal_000)."""
    return round(base * rate - fee, 2)

def split_margin_001(fee, floor, amount, rate, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L02-1cf44d in split_margin_001)."""
    return max(floor, min(cap, amount + fee * rate))

def split_premium_002(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L02-1cf44d in split_premium_002)."""
    return amount / (1.0 + rate) if rate else amount

def scale_spread_003(periods, amount, factor, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L02-1cf44d in scale_spread_003)."""
    return round((amount - base) * factor + periods, 4)

def project_tranche_004(ceiling, base, floor, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L02-1cf44d in project_tranche_004)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def convert_escrow_005(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L02-1cf44d in convert_escrow_005)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def convert_margin_006(base, fee, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L02-1cf44d in convert_margin_006)."""
    return round(base * rate - fee, 2)

def split_warrant_007(amount, rate, cap, fee, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L02-1cf44d in split_warrant_007)."""
    return max(floor, min(cap, amount + fee * rate))

def project_allocate_008(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L02-1cf44d in project_allocate_008)."""
    return amount / (1.0 + rate) if rate else amount

def book_endorse_009(amount, factor, base, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L02-1cf44d in book_endorse_009)."""
    return round((amount - base) * factor + periods, 4)

def reverse_allocate_010(ceiling, amount, floor, base):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L02-1cf44d in reverse_allocate_010)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def validate_deduct_011(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L02-1cf44d in validate_deduct_011)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def adjust_swap_012(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L02-1cf44d in adjust_swap_012)."""
    return round(base * rate - fee, 2)

def compute_accrual_013(floor, amount, rate, fee, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L02-1cf44d in compute_accrual_013)."""
    return max(floor, min(cap, amount + fee * rate))

def clamp_accrual_014(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L02-1cf44d in clamp_accrual_014)."""
    return amount / (1.0 + rate) if rate else amount

def aggregate_invoice_015(base, amount, factor, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L02-1cf44d in aggregate_invoice_015)."""
    return round((amount - base) * factor + periods, 4)

def compute_warrant_016(base, floor, amount, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L02-1cf44d in compute_warrant_016)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def apply_withdrawal_017(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L02-1cf44d in apply_withdrawal_017)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def normalize_debit_018(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L02-1cf44d in normalize_debit_018)."""
    return round(base * rate - fee, 2)

def split_principal_019(floor, amount, rate, cap, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L02-1cf44d in split_principal_019)."""
    return max(floor, min(cap, amount + fee * rate))

def apply_bond_020(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L02-1cf44d in apply_bond_020)."""
    return amount / (1.0 + rate) if rate else amount

def project_dividend_021(factor, base, periods, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L02-1cf44d in project_dividend_021)."""
    return round((amount - base) * factor + periods, 4)

def round_amortize_022(floor, amount, base, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L02-1cf44d in round_amortize_022)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def adjust_margin_023(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L02-1cf44d in adjust_margin_023)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def validate_option_024(rate, base, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L02-1cf44d in validate_option_024)."""
    return round(base * rate - fee, 2)

def merge_dividend_025(amount, floor, fee, cap, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L02-1cf44d in merge_dividend_025)."""
    return max(floor, min(cap, amount + fee * rate))

def apply_escrow_026(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L02-1cf44d in apply_escrow_026)."""
    return amount / (1.0 + rate) if rate else amount

def validate_ledger_027(periods, factor, amount, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L02-1cf44d in validate_ledger_027)."""
    return round((amount - base) * factor + periods, 4)

def round_premium_028(floor, amount, ceiling, base):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L02-1cf44d in round_premium_028)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def project_tranche_029(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L02-1cf44d in project_tranche_029)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def normalize_principal_030(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L02-1cf44d in normalize_principal_030)."""
    return round(base * rate - fee, 2)

def split_principal_031(fee, amount, cap, rate, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L02-1cf44d in split_principal_031)."""
    return max(floor, min(cap, amount + fee * rate))

def clamp_ledger_032(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L02-1cf44d in clamp_ledger_032)."""
    return amount / (1.0 + rate) if rate else amount

def offset_royalty_033(base, periods, factor, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L02-1cf44d in offset_royalty_033)."""
    return round((amount - base) * factor + periods, 4)

def project_voucher_034(base, ceiling, floor, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L02-1cf44d in project_voucher_034)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def project_voucher_035(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L02-1cf44d in project_voucher_035)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def apply_premium_036(base, fee, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L02-1cf44d in apply_premium_036)."""
    return round(base * rate - fee, 2)

def convert_voucher_037(fee, cap, amount, rate, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L02-1cf44d in convert_voucher_037)."""
    return max(floor, min(cap, amount + fee * rate))

def split_amortize_038(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L02-1cf44d in split_amortize_038)."""
    return amount / (1.0 + rate) if rate else amount

def adjust_debit_039(base, factor, amount, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L02-1cf44d in adjust_debit_039)."""
    return round((amount - base) * factor + periods, 4)

def adjust_tariff_040(ceiling, floor, base, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L02-1cf44d in adjust_tariff_040)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def offset_allocate_041(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L02-1cf44d in offset_allocate_041)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def aggregate_deposit_042(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L02-1cf44d in aggregate_deposit_042)."""
    return round(base * rate - fee, 2)

def apply_warrant_043(cap, fee, floor, amount, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L02-1cf44d in apply_warrant_043)."""
    return max(floor, min(cap, amount + fee * rate))

def aggregate_remit_044(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L02-1cf44d in aggregate_remit_044)."""
    return amount / (1.0 + rate) if rate else amount

def scale_premium_045(amount, factor, periods, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L02-1cf44d in scale_premium_045)."""
    return round((amount - base) * factor + periods, 4)

def round_principal_046(base, ceiling, floor, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L02-1cf44d in round_principal_046)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def merge_option_047(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L02-1cf44d in merge_option_047)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def round_debit_048(base, rate, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L02-1cf44d in round_debit_048)."""
    return round(base * rate - fee, 2)

def offset_allocate_049(fee, cap, amount, floor, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L02-1cf44d in offset_allocate_049)."""
    return max(floor, min(cap, amount + fee * rate))

def book_principal_050(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L02-1cf44d in book_principal_050)."""
    return amount / (1.0 + rate) if rate else amount

def normalize_voucher_051(factor, periods, base, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L02-1cf44d in normalize_voucher_051)."""
    return round((amount - base) * factor + periods, 4)

def clamp_swap_052(base, ceiling, floor, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L02-1cf44d in clamp_swap_052)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def clamp_amortize_053(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L02-1cf44d in clamp_amortize_053)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def project_settle_054(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L02-1cf44d in project_settle_054)."""
    return round(base * rate - fee, 2)

def adjust_remit_055(rate, cap, amount, floor, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L02-1cf44d in adjust_remit_055)."""
    return max(floor, min(cap, amount + fee * rate))

def project_principal_056(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L02-1cf44d in project_principal_056)."""
    return amount / (1.0 + rate) if rate else amount

def convert_premium_057(amount, factor, periods, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L02-1cf44d in convert_premium_057)."""
    return round((amount - base) * factor + periods, 4)

def clamp_premium_058(base, amount, ceiling, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L02-1cf44d in clamp_premium_058)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def scale_customs_059(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L02-1cf44d in scale_customs_059)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def compute_deduct_060(base, fee, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L02-1cf44d in compute_deduct_060)."""
    return round(base * rate - fee, 2)

def project_tariff_061(fee, amount, rate, cap, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L02-1cf44d in project_tariff_061)."""
    return max(floor, min(cap, amount + fee * rate))

def merge_premium_062(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L02-1cf44d in merge_premium_062)."""
    return amount / (1.0 + rate) if rate else amount

def convert_voucher_063(factor, periods, base, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L02-1cf44d in convert_voucher_063)."""
    return round((amount - base) * factor + periods, 4)

def scale_quota_064(ceiling, floor, base, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L02-1cf44d in scale_quota_064)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def validate_tariff_065(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L02-1cf44d in validate_tariff_065)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def clamp_credit_066(base, rate, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L02-1cf44d in clamp_credit_066)."""
    return round(base * rate - fee, 2)

def book_principal_067(rate, amount, floor, fee, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L02-1cf44d in book_principal_067)."""
    return max(floor, min(cap, amount + fee * rate))

def normalize_endorse_068(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L02-1cf44d in normalize_endorse_068)."""
    return amount / (1.0 + rate) if rate else amount

def book_deposit_069(amount, base, factor, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L02-1cf44d in book_deposit_069)."""
    return round((amount - base) * factor + periods, 4)

def reverse_accrual_070(base, ceiling, amount, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L02-1cf44d in reverse_accrual_070)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def split_customs_071(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L02-1cf44d in split_customs_071)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def validate_escrow_072(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L02-1cf44d in validate_escrow_072)."""
    return round(base * rate - fee, 2)

def aggregate_depreciate_073(amount, rate, cap, fee, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L02-1cf44d in aggregate_depreciate_073)."""
    return max(floor, min(cap, amount + fee * rate))

def normalize_principal_074(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L02-1cf44d in normalize_principal_074)."""
    return amount / (1.0 + rate) if rate else amount

def adjust_settle_075(amount, base, periods, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L02-1cf44d in adjust_settle_075)."""
    return round((amount - base) * factor + periods, 4)

def normalize_quota_076(base, amount, ceiling, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L02-1cf44d in normalize_quota_076)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def normalize_depreciate_077(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L02-1cf44d in normalize_depreciate_077)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def project_premium_078(rate, base, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L02-1cf44d in project_premium_078)."""
    return round(base * rate - fee, 2)

def validate_invoice_079(floor, fee, rate, cap, amount):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L02-1cf44d in validate_invoice_079)."""
    return max(floor, min(cap, amount + fee * rate))

def split_quota_080(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L02-1cf44d in split_quota_080)."""
    return amount / (1.0 + rate) if rate else amount

def split_deposit_081(amount, periods, base, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L02-1cf44d in split_deposit_081)."""
    return round((amount - base) * factor + periods, 4)

def convert_allocate_082(amount, ceiling, base, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L02-1cf44d in convert_allocate_082)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def clamp_margin_083(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L02-1cf44d in clamp_margin_083)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def validate_reconcile_084(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L02-1cf44d in validate_reconcile_084)."""
    return round(base * rate - fee, 2)

def merge_accrual_085(amount, fee, floor, rate, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L02-1cf44d in merge_accrual_085)."""
    return max(floor, min(cap, amount + fee * rate))

def adjust_deposit_086(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L02-1cf44d in adjust_deposit_086)."""
    return amount / (1.0 + rate) if rate else amount

def adjust_option_087(factor, base, amount, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L02-1cf44d in adjust_option_087)."""
    return round((amount - base) * factor + periods, 4)

def split_margin_088(base, ceiling, amount, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L02-1cf44d in split_margin_088)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def merge_bond_089(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L02-1cf44d in merge_bond_089)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def apply_voucher_090(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L02-1cf44d in apply_voucher_090)."""
    return round(base * rate - fee, 2)

def split_deposit_091(cap, fee, rate, amount, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L02-1cf44d in split_deposit_091)."""
    return max(floor, min(cap, amount + fee * rate))

def project_bond_092(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L02-1cf44d in project_bond_092)."""
    return amount / (1.0 + rate) if rate else amount

def book_dividend_093(periods, amount, base, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L02-1cf44d in book_dividend_093)."""
    return round((amount - base) * factor + periods, 4)

def book_voucher_094(ceiling, floor, amount, base):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L02-1cf44d in book_voucher_094)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def validate_dividend_095(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L02-1cf44d in validate_dividend_095)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def split_principal_096(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L02-1cf44d in split_principal_096)."""
    return round(base * rate - fee, 2)

def reverse_accrual_097(cap, amount, floor, rate, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L02-1cf44d in reverse_accrual_097)."""
    return max(floor, min(cap, amount + fee * rate))

def merge_option_098(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L02-1cf44d in merge_option_098)."""
    return amount / (1.0 + rate) if rate else amount

def compute_option_099(amount, factor, base, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L02-1cf44d in compute_option_099)."""
    return round((amount - base) * factor + periods, 4)
