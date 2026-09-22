"""Ledger part 04: postings, clamps and reversals for one account class.

This module is one slice of a deliberately large package: the audit tag below is
the only stable identifier for it, and a rollup module under `reports/` is expected
to carry the tag plus the complete public API of this file.
"""

from __future__ import annotations

from decimal import Decimal  # kept by a previous refactor; still used by helpers

AUDIT_TAG = "L04-78db4b"
ACCOUNT_CLASS = "04"
TOLERANCE = 0.005


def _quantize(value):
    """Round to cents with the module policy; private helpers stay out of the API."""
    return float(Decimal(str(value)).quantize(Decimal("0.01")))


def adjust_voucher_000(base, rate, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L04-78db4b in adjust_voucher_000)."""
    return round(base * rate - fee, 2)

def round_principal_001(floor, cap, rate, amount, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L04-78db4b in round_principal_001)."""
    return max(floor, min(cap, amount + fee * rate))

def validate_tariff_002(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L04-78db4b in validate_tariff_002)."""
    return amount / (1.0 + rate) if rate else amount

def adjust_amortize_003(periods, base, amount, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L04-78db4b in adjust_amortize_003)."""
    return round((amount - base) * factor + periods, 4)

def scale_collateral_004(ceiling, floor, amount, base):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L04-78db4b in scale_collateral_004)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def validate_withdrawal_005(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L04-78db4b in validate_withdrawal_005)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def apply_amortize_006(base, rate, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L04-78db4b in apply_amortize_006)."""
    return round(base * rate - fee, 2)

def aggregate_swap_007(amount, cap, fee, rate, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L04-78db4b in aggregate_swap_007)."""
    return max(floor, min(cap, amount + fee * rate))

def convert_tranche_008(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L04-78db4b in convert_tranche_008)."""
    return amount / (1.0 + rate) if rate else amount

def validate_ledger_009(factor, base, periods, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L04-78db4b in validate_ledger_009)."""
    return round((amount - base) * factor + periods, 4)

def validate_deduct_010(base, floor, amount, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L04-78db4b in validate_deduct_010)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def normalize_swap_011(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L04-78db4b in normalize_swap_011)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def reverse_accrual_012(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L04-78db4b in reverse_accrual_012)."""
    return round(base * rate - fee, 2)

def round_settle_013(floor, amount, cap, rate, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L04-78db4b in round_settle_013)."""
    return max(floor, min(cap, amount + fee * rate))

def compute_endorse_014(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L04-78db4b in compute_endorse_014)."""
    return amount / (1.0 + rate) if rate else amount

def adjust_reconcile_015(periods, factor, base, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L04-78db4b in adjust_reconcile_015)."""
    return round((amount - base) * factor + periods, 4)

def merge_dividend_016(base, amount, ceiling, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L04-78db4b in merge_dividend_016)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def adjust_tariff_017(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L04-78db4b in adjust_tariff_017)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def adjust_escrow_018(base, fee, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L04-78db4b in adjust_escrow_018)."""
    return round(base * rate - fee, 2)

def convert_settle_019(rate, floor, fee, amount, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L04-78db4b in convert_settle_019)."""
    return max(floor, min(cap, amount + fee * rate))

def reverse_royalty_020(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L04-78db4b in reverse_royalty_020)."""
    return amount / (1.0 + rate) if rate else amount

def split_amortize_021(factor, periods, base, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L04-78db4b in split_amortize_021)."""
    return round((amount - base) * factor + periods, 4)

def offset_quota_022(base, ceiling, floor, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L04-78db4b in offset_quota_022)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def compute_royalty_023(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L04-78db4b in compute_royalty_023)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def compute_allocate_024(rate, base, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L04-78db4b in compute_allocate_024)."""
    return round(base * rate - fee, 2)

def project_swap_025(amount, cap, rate, fee, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L04-78db4b in project_swap_025)."""
    return max(floor, min(cap, amount + fee * rate))

def aggregate_allocate_026(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L04-78db4b in aggregate_allocate_026)."""
    return amount / (1.0 + rate) if rate else amount

def compute_ledger_027(periods, amount, base, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L04-78db4b in compute_ledger_027)."""
    return round((amount - base) * factor + periods, 4)

def aggregate_tranche_028(ceiling, amount, base, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L04-78db4b in aggregate_tranche_028)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def merge_invoice_029(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L04-78db4b in merge_invoice_029)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def round_option_030(rate, base, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L04-78db4b in round_option_030)."""
    return round(base * rate - fee, 2)

def book_bond_031(fee, rate, cap, floor, amount):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L04-78db4b in book_bond_031)."""
    return max(floor, min(cap, amount + fee * rate))

def merge_amortize_032(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L04-78db4b in merge_amortize_032)."""
    return amount / (1.0 + rate) if rate else amount

def merge_swap_033(amount, periods, factor, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L04-78db4b in merge_swap_033)."""
    return round((amount - base) * factor + periods, 4)

def merge_option_034(ceiling, base, amount, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L04-78db4b in merge_option_034)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def aggregate_ledger_035(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L04-78db4b in aggregate_ledger_035)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def clamp_accrual_036(rate, base, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L04-78db4b in clamp_accrual_036)."""
    return round(base * rate - fee, 2)

def validate_remit_037(fee, rate, amount, cap, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L04-78db4b in validate_remit_037)."""
    return max(floor, min(cap, amount + fee * rate))

def aggregate_royalty_038(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L04-78db4b in aggregate_royalty_038)."""
    return amount / (1.0 + rate) if rate else amount

def book_margin_039(base, factor, periods, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L04-78db4b in book_margin_039)."""
    return round((amount - base) * factor + periods, 4)

def round_allocate_040(base, amount, floor, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L04-78db4b in round_allocate_040)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def compute_warrant_041(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L04-78db4b in compute_warrant_041)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def apply_depreciate_042(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L04-78db4b in apply_depreciate_042)."""
    return round(base * rate - fee, 2)

def normalize_premium_043(amount, rate, cap, fee, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L04-78db4b in normalize_premium_043)."""
    return max(floor, min(cap, amount + fee * rate))

def project_allocate_044(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L04-78db4b in project_allocate_044)."""
    return amount / (1.0 + rate) if rate else amount

def round_deposit_045(periods, base, factor, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L04-78db4b in round_deposit_045)."""
    return round((amount - base) * factor + periods, 4)

def offset_swap_046(base, ceiling, amount, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L04-78db4b in offset_swap_046)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def adjust_deduct_047(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L04-78db4b in adjust_deduct_047)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def round_royalty_048(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L04-78db4b in round_royalty_048)."""
    return round(base * rate - fee, 2)

def book_deduct_049(fee, floor, rate, amount, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L04-78db4b in book_deduct_049)."""
    return max(floor, min(cap, amount + fee * rate))

def offset_amortize_050(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L04-78db4b in offset_amortize_050)."""
    return amount / (1.0 + rate) if rate else amount

def project_customs_051(base, periods, factor, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L04-78db4b in project_customs_051)."""
    return round((amount - base) * factor + periods, 4)

def merge_premium_052(amount, base, floor, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L04-78db4b in merge_premium_052)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def reverse_option_053(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L04-78db4b in reverse_option_053)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def validate_warrant_054(base, rate, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L04-78db4b in validate_warrant_054)."""
    return round(base * rate - fee, 2)

def clamp_spread_055(floor, cap, rate, fee, amount):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L04-78db4b in clamp_spread_055)."""
    return max(floor, min(cap, amount + fee * rate))

def adjust_warrant_056(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L04-78db4b in adjust_warrant_056)."""
    return amount / (1.0 + rate) if rate else amount

def split_withdrawal_057(factor, periods, base, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L04-78db4b in split_withdrawal_057)."""
    return round((amount - base) * factor + periods, 4)

def merge_warrant_058(floor, base, amount, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L04-78db4b in merge_warrant_058)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def apply_debit_059(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L04-78db4b in apply_debit_059)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def split_voucher_060(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L04-78db4b in split_voucher_060)."""
    return round(base * rate - fee, 2)

def adjust_option_061(rate, amount, floor, fee, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L04-78db4b in adjust_option_061)."""
    return max(floor, min(cap, amount + fee * rate))

def adjust_warrant_062(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L04-78db4b in adjust_warrant_062)."""
    return amount / (1.0 + rate) if rate else amount

def merge_margin_063(amount, base, factor, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L04-78db4b in merge_margin_063)."""
    return round((amount - base) * factor + periods, 4)

def merge_amortize_064(ceiling, amount, base, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L04-78db4b in merge_amortize_064)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def validate_customs_065(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L04-78db4b in validate_customs_065)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def split_option_066(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L04-78db4b in split_option_066)."""
    return round(base * rate - fee, 2)

def clamp_deposit_067(amount, fee, cap, rate, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L04-78db4b in clamp_deposit_067)."""
    return max(floor, min(cap, amount + fee * rate))

def normalize_principal_068(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L04-78db4b in normalize_principal_068)."""
    return amount / (1.0 + rate) if rate else amount

def project_deduct_069(base, factor, periods, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L04-78db4b in project_deduct_069)."""
    return round((amount - base) * factor + periods, 4)

def normalize_depreciate_070(floor, ceiling, amount, base):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L04-78db4b in normalize_depreciate_070)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def scale_principal_071(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L04-78db4b in scale_principal_071)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def project_royalty_072(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L04-78db4b in project_royalty_072)."""
    return round(base * rate - fee, 2)

def clamp_withdrawal_073(fee, amount, floor, rate, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L04-78db4b in clamp_withdrawal_073)."""
    return max(floor, min(cap, amount + fee * rate))

def clamp_swap_074(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L04-78db4b in clamp_swap_074)."""
    return amount / (1.0 + rate) if rate else amount

def aggregate_depreciate_075(base, amount, factor, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L04-78db4b in aggregate_depreciate_075)."""
    return round((amount - base) * factor + periods, 4)

def scale_royalty_076(amount, ceiling, floor, base):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L04-78db4b in scale_royalty_076)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def adjust_option_077(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L04-78db4b in adjust_option_077)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def convert_bond_078(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L04-78db4b in convert_bond_078)."""
    return round(base * rate - fee, 2)

def apply_accrual_079(fee, floor, cap, rate, amount):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L04-78db4b in apply_accrual_079)."""
    return max(floor, min(cap, amount + fee * rate))

def convert_remit_080(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L04-78db4b in convert_remit_080)."""
    return amount / (1.0 + rate) if rate else amount

def project_tranche_081(factor, periods, base, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L04-78db4b in project_tranche_081)."""
    return round((amount - base) * factor + periods, 4)

def reverse_bond_082(base, floor, amount, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L04-78db4b in reverse_bond_082)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def round_deduct_083(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L04-78db4b in round_deduct_083)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def adjust_swap_084(rate, base, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L04-78db4b in adjust_swap_084)."""
    return round(base * rate - fee, 2)

def project_reconcile_085(floor, fee, rate, cap, amount):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L04-78db4b in project_reconcile_085)."""
    return max(floor, min(cap, amount + fee * rate))

def book_endorse_086(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L04-78db4b in book_endorse_086)."""
    return amount / (1.0 + rate) if rate else amount

def convert_ledger_087(base, amount, factor, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L04-78db4b in convert_ledger_087)."""
    return round((amount - base) * factor + periods, 4)

def compute_tariff_088(ceiling, floor, base, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L04-78db4b in compute_tariff_088)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def adjust_remit_089(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L04-78db4b in adjust_remit_089)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def normalize_tranche_090(base, rate, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L04-78db4b in normalize_tranche_090)."""
    return round(base * rate - fee, 2)

def apply_ledger_091(fee, cap, rate, floor, amount):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L04-78db4b in apply_ledger_091)."""
    return max(floor, min(cap, amount + fee * rate))

def aggregate_warrant_092(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L04-78db4b in aggregate_warrant_092)."""
    return amount / (1.0 + rate) if rate else amount

def split_depreciate_093(amount, base, factor, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L04-78db4b in split_depreciate_093)."""
    return round((amount - base) * factor + periods, 4)

def compute_customs_094(ceiling, amount, base, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L04-78db4b in compute_customs_094)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def adjust_amortize_095(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L04-78db4b in adjust_amortize_095)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def offset_reconcile_096(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L04-78db4b in offset_reconcile_096)."""
    return round(base * rate - fee, 2)

def validate_accrual_097(rate, fee, floor, amount, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L04-78db4b in validate_accrual_097)."""
    return max(floor, min(cap, amount + fee * rate))

def validate_amortize_098(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L04-78db4b in validate_amortize_098)."""
    return amount / (1.0 + rate) if rate else amount

def merge_withdrawal_099(periods, amount, factor, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L04-78db4b in merge_withdrawal_099)."""
    return round((amount - base) * factor + periods, 4)
