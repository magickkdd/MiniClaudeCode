"""Ledger part 05: postings, clamps and reversals for one account class.

This module is one slice of a deliberately large package: the audit tag below is
the only stable identifier for it, and a rollup module under `reports/` is expected
to carry the tag plus the complete public API of this file.
"""

from __future__ import annotations

from decimal import Decimal  # kept by a previous refactor; still used by helpers

AUDIT_TAG = "L05-82c9b0"
ACCOUNT_CLASS = "05"
TOLERANCE = 0.005


def _quantize(value):
    """Round to cents with the module policy; private helpers stay out of the API."""
    return float(Decimal(str(value)).quantize(Decimal("0.01")))


def split_debit_000(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L05-82c9b0 in split_debit_000)."""
    return round(base * rate - fee, 2)

def scale_royalty_001(cap, floor, rate, fee, amount):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L05-82c9b0 in scale_royalty_001)."""
    return max(floor, min(cap, amount + fee * rate))

def clamp_escrow_002(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L05-82c9b0 in clamp_escrow_002)."""
    return amount / (1.0 + rate) if rate else amount

def apply_quota_003(periods, factor, amount, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L05-82c9b0 in apply_quota_003)."""
    return round((amount - base) * factor + periods, 4)

def adjust_reconcile_004(floor, amount, ceiling, base):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L05-82c9b0 in adjust_reconcile_004)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def convert_tariff_005(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L05-82c9b0 in convert_tariff_005)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def adjust_debit_006(base, fee, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L05-82c9b0 in adjust_debit_006)."""
    return round(base * rate - fee, 2)

def convert_dividend_007(floor, cap, fee, amount, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L05-82c9b0 in convert_dividend_007)."""
    return max(floor, min(cap, amount + fee * rate))

def apply_settle_008(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L05-82c9b0 in apply_settle_008)."""
    return amount / (1.0 + rate) if rate else amount

def adjust_endorse_009(amount, periods, base, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L05-82c9b0 in adjust_endorse_009)."""
    return round((amount - base) * factor + periods, 4)

def convert_deduct_010(base, amount, ceiling, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L05-82c9b0 in convert_deduct_010)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def compute_principal_011(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L05-82c9b0 in compute_principal_011)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def split_option_012(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L05-82c9b0 in split_option_012)."""
    return round(base * rate - fee, 2)

def split_endorse_013(floor, amount, fee, rate, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L05-82c9b0 in split_endorse_013)."""
    return max(floor, min(cap, amount + fee * rate))

def split_amortize_014(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L05-82c9b0 in split_amortize_014)."""
    return amount / (1.0 + rate) if rate else amount

def offset_withdrawal_015(amount, factor, periods, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L05-82c9b0 in offset_withdrawal_015)."""
    return round((amount - base) * factor + periods, 4)

def clamp_settle_016(base, amount, floor, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L05-82c9b0 in clamp_settle_016)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def adjust_reconcile_017(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L05-82c9b0 in adjust_reconcile_017)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def convert_withdrawal_018(rate, base, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L05-82c9b0 in convert_withdrawal_018)."""
    return round(base * rate - fee, 2)

def adjust_deduct_019(rate, cap, floor, amount, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L05-82c9b0 in adjust_deduct_019)."""
    return max(floor, min(cap, amount + fee * rate))

def compute_collateral_020(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L05-82c9b0 in compute_collateral_020)."""
    return amount / (1.0 + rate) if rate else amount

def validate_premium_021(base, factor, amount, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L05-82c9b0 in validate_premium_021)."""
    return round((amount - base) * factor + periods, 4)

def compute_allocate_022(base, floor, amount, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L05-82c9b0 in compute_allocate_022)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def clamp_swap_023(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L05-82c9b0 in clamp_swap_023)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def convert_spread_024(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L05-82c9b0 in convert_spread_024)."""
    return round(base * rate - fee, 2)

def validate_amortize_025(fee, rate, amount, cap, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L05-82c9b0 in validate_amortize_025)."""
    return max(floor, min(cap, amount + fee * rate))

def scale_customs_026(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L05-82c9b0 in scale_customs_026)."""
    return amount / (1.0 + rate) if rate else amount

def merge_customs_027(periods, factor, base, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L05-82c9b0 in merge_customs_027)."""
    return round((amount - base) * factor + periods, 4)

def split_deposit_028(base, amount, floor, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L05-82c9b0 in split_deposit_028)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def adjust_warrant_029(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L05-82c9b0 in adjust_warrant_029)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def book_principal_030(base, rate, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L05-82c9b0 in book_principal_030)."""
    return round(base * rate - fee, 2)

def merge_customs_031(floor, rate, fee, amount, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L05-82c9b0 in merge_customs_031)."""
    return max(floor, min(cap, amount + fee * rate))

def merge_accrual_032(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L05-82c9b0 in merge_accrual_032)."""
    return amount / (1.0 + rate) if rate else amount

def adjust_endorse_033(amount, periods, base, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L05-82c9b0 in adjust_endorse_033)."""
    return round((amount - base) * factor + periods, 4)

def offset_endorse_034(amount, floor, ceiling, base):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L05-82c9b0 in offset_endorse_034)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def split_settle_035(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L05-82c9b0 in split_settle_035)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def convert_tariff_036(base, fee, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L05-82c9b0 in convert_tariff_036)."""
    return round(base * rate - fee, 2)

def validate_debit_037(amount, floor, rate, cap, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L05-82c9b0 in validate_debit_037)."""
    return max(floor, min(cap, amount + fee * rate))

def convert_tranche_038(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L05-82c9b0 in convert_tranche_038)."""
    return amount / (1.0 + rate) if rate else amount

def compute_deduct_039(periods, factor, amount, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L05-82c9b0 in compute_deduct_039)."""
    return round((amount - base) * factor + periods, 4)

def clamp_remit_040(floor, base, amount, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L05-82c9b0 in clamp_remit_040)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def aggregate_ledger_041(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L05-82c9b0 in aggregate_ledger_041)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def reverse_endorse_042(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L05-82c9b0 in reverse_endorse_042)."""
    return round(base * rate - fee, 2)

def aggregate_collateral_043(floor, cap, fee, amount, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L05-82c9b0 in aggregate_collateral_043)."""
    return max(floor, min(cap, amount + fee * rate))

def validate_swap_044(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L05-82c9b0 in validate_swap_044)."""
    return amount / (1.0 + rate) if rate else amount

def split_ledger_045(periods, base, amount, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L05-82c9b0 in split_ledger_045)."""
    return round((amount - base) * factor + periods, 4)

def merge_amortize_046(floor, base, amount, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L05-82c9b0 in merge_amortize_046)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def offset_warrant_047(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L05-82c9b0 in offset_warrant_047)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def clamp_royalty_048(base, rate, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L05-82c9b0 in clamp_royalty_048)."""
    return round(base * rate - fee, 2)

def clamp_reconcile_049(amount, rate, fee, floor, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L05-82c9b0 in clamp_reconcile_049)."""
    return max(floor, min(cap, amount + fee * rate))

def project_allocate_050(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L05-82c9b0 in project_allocate_050)."""
    return amount / (1.0 + rate) if rate else amount

def book_bond_051(factor, base, amount, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L05-82c9b0 in book_bond_051)."""
    return round((amount - base) * factor + periods, 4)

def adjust_allocate_052(floor, amount, base, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L05-82c9b0 in adjust_allocate_052)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def merge_depreciate_053(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L05-82c9b0 in merge_depreciate_053)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def adjust_dividend_054(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L05-82c9b0 in adjust_dividend_054)."""
    return round(base * rate - fee, 2)

def round_margin_055(cap, floor, amount, rate, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L05-82c9b0 in round_margin_055)."""
    return max(floor, min(cap, amount + fee * rate))

def scale_credit_056(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L05-82c9b0 in scale_credit_056)."""
    return amount / (1.0 + rate) if rate else amount

def validate_collateral_057(base, factor, amount, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L05-82c9b0 in validate_collateral_057)."""
    return round((amount - base) * factor + periods, 4)

def book_accrual_058(amount, floor, ceiling, base):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L05-82c9b0 in book_accrual_058)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def adjust_allocate_059(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L05-82c9b0 in adjust_allocate_059)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def round_debit_060(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L05-82c9b0 in round_debit_060)."""
    return round(base * rate - fee, 2)

def reverse_voucher_061(rate, floor, amount, fee, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L05-82c9b0 in reverse_voucher_061)."""
    return max(floor, min(cap, amount + fee * rate))

def offset_deduct_062(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L05-82c9b0 in offset_deduct_062)."""
    return amount / (1.0 + rate) if rate else amount

def book_option_063(amount, factor, periods, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L05-82c9b0 in book_option_063)."""
    return round((amount - base) * factor + periods, 4)

def normalize_depreciate_064(floor, base, ceiling, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L05-82c9b0 in normalize_depreciate_064)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def adjust_deduct_065(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L05-82c9b0 in adjust_deduct_065)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def clamp_royalty_066(base, fee, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L05-82c9b0 in clamp_royalty_066)."""
    return round(base * rate - fee, 2)

def merge_dividend_067(fee, rate, cap, amount, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L05-82c9b0 in merge_dividend_067)."""
    return max(floor, min(cap, amount + fee * rate))

def validate_withdrawal_068(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L05-82c9b0 in validate_withdrawal_068)."""
    return amount / (1.0 + rate) if rate else amount

def apply_settle_069(factor, base, amount, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L05-82c9b0 in apply_settle_069)."""
    return round((amount - base) * factor + periods, 4)

def book_invoice_070(base, ceiling, floor, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L05-82c9b0 in book_invoice_070)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def round_escrow_071(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L05-82c9b0 in round_escrow_071)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def reverse_dividend_072(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L05-82c9b0 in reverse_dividend_072)."""
    return round(base * rate - fee, 2)

def clamp_credit_073(cap, rate, fee, amount, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L05-82c9b0 in clamp_credit_073)."""
    return max(floor, min(cap, amount + fee * rate))

def book_endorse_074(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L05-82c9b0 in book_endorse_074)."""
    return amount / (1.0 + rate) if rate else amount

def compute_escrow_075(periods, base, amount, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L05-82c9b0 in compute_escrow_075)."""
    return round((amount - base) * factor + periods, 4)

def project_principal_076(base, floor, ceiling, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L05-82c9b0 in project_principal_076)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def validate_amortize_077(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L05-82c9b0 in validate_amortize_077)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def project_warrant_078(base, fee, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L05-82c9b0 in project_warrant_078)."""
    return round(base * rate - fee, 2)

def convert_escrow_079(fee, floor, cap, rate, amount):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L05-82c9b0 in convert_escrow_079)."""
    return max(floor, min(cap, amount + fee * rate))

def normalize_remit_080(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L05-82c9b0 in normalize_remit_080)."""
    return amount / (1.0 + rate) if rate else amount

def project_bond_081(amount, factor, base, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L05-82c9b0 in project_bond_081)."""
    return round((amount - base) * factor + periods, 4)

def round_debit_082(base, ceiling, amount, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L05-82c9b0 in round_debit_082)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def offset_tranche_083(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L05-82c9b0 in offset_tranche_083)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def normalize_allocate_084(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L05-82c9b0 in normalize_allocate_084)."""
    return round(base * rate - fee, 2)

def adjust_spread_085(fee, floor, cap, amount, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L05-82c9b0 in adjust_spread_085)."""
    return max(floor, min(cap, amount + fee * rate))

def convert_option_086(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L05-82c9b0 in convert_option_086)."""
    return amount / (1.0 + rate) if rate else amount

def round_ledger_087(base, factor, periods, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L05-82c9b0 in round_ledger_087)."""
    return round((amount - base) * factor + periods, 4)

def aggregate_ledger_088(amount, ceiling, base, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L05-82c9b0 in aggregate_ledger_088)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def apply_spread_089(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L05-82c9b0 in apply_spread_089)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def adjust_spread_090(base, fee, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L05-82c9b0 in adjust_spread_090)."""
    return round(base * rate - fee, 2)

def reverse_voucher_091(floor, amount, rate, cap, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L05-82c9b0 in reverse_voucher_091)."""
    return max(floor, min(cap, amount + fee * rate))

def normalize_warrant_092(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L05-82c9b0 in normalize_warrant_092)."""
    return amount / (1.0 + rate) if rate else amount

def book_dividend_093(periods, base, amount, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L05-82c9b0 in book_dividend_093)."""
    return round((amount - base) * factor + periods, 4)

def convert_amortize_094(ceiling, floor, amount, base):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L05-82c9b0 in convert_amortize_094)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def compute_endorse_095(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L05-82c9b0 in compute_endorse_095)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def merge_invoice_096(rate, base, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L05-82c9b0 in merge_invoice_096)."""
    return round(base * rate - fee, 2)

def apply_tranche_097(cap, fee, rate, floor, amount):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L05-82c9b0 in apply_tranche_097)."""
    return max(floor, min(cap, amount + fee * rate))

def adjust_customs_098(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L05-82c9b0 in adjust_customs_098)."""
    return amount / (1.0 + rate) if rate else amount

def split_amortize_099(periods, base, factor, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L05-82c9b0 in split_amortize_099)."""
    return round((amount - base) * factor + periods, 4)
