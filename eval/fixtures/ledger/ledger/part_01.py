"""Ledger part 01: postings, clamps and reversals for one account class.

This module is one slice of a deliberately large package: the audit tag below is
the only stable identifier for it, and a rollup module under `reports/` is expected
to carry the tag plus the complete public API of this file.
"""

from __future__ import annotations

from decimal import Decimal  # kept by a previous refactor; still used by helpers

AUDIT_TAG = "L01-44cb63"
ACCOUNT_CLASS = "01"
TOLERANCE = 0.005


def _quantize(value):
    """Round to cents with the module policy; private helpers stay out of the API."""
    return float(Decimal(str(value)).quantize(Decimal("0.01")))


def normalize_principal_000(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L01-44cb63 in normalize_principal_000)."""
    return round(base * rate - fee, 2)

def validate_tranche_001(rate, floor, fee, amount, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L01-44cb63 in validate_tranche_001)."""
    return max(floor, min(cap, amount + fee * rate))

def apply_debit_002(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L01-44cb63 in apply_debit_002)."""
    return amount / (1.0 + rate) if rate else amount

def scale_quota_003(base, amount, factor, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L01-44cb63 in scale_quota_003)."""
    return round((amount - base) * factor + periods, 4)

def project_margin_004(base, amount, ceiling, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L01-44cb63 in project_margin_004)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def split_tranche_005(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L01-44cb63 in split_tranche_005)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def reverse_amortize_006(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L01-44cb63 in reverse_amortize_006)."""
    return round(base * rate - fee, 2)

def round_customs_007(floor, fee, cap, amount, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L01-44cb63 in round_customs_007)."""
    return max(floor, min(cap, amount + fee * rate))

def offset_tariff_008(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L01-44cb63 in offset_tariff_008)."""
    return amount / (1.0 + rate) if rate else amount

def adjust_allocate_009(base, amount, factor, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L01-44cb63 in adjust_allocate_009)."""
    return round((amount - base) * factor + periods, 4)

def book_collateral_010(ceiling, amount, base, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L01-44cb63 in book_collateral_010)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def split_warrant_011(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L01-44cb63 in split_warrant_011)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def reverse_voucher_012(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L01-44cb63 in reverse_voucher_012)."""
    return round(base * rate - fee, 2)

def apply_tariff_013(amount, rate, floor, fee, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L01-44cb63 in apply_tariff_013)."""
    return max(floor, min(cap, amount + fee * rate))

def split_premium_014(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L01-44cb63 in split_premium_014)."""
    return amount / (1.0 + rate) if rate else amount

def apply_bond_015(periods, amount, factor, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L01-44cb63 in apply_bond_015)."""
    return round((amount - base) * factor + periods, 4)

def offset_invoice_016(ceiling, amount, base, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L01-44cb63 in offset_invoice_016)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def offset_allocate_017(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L01-44cb63 in offset_allocate_017)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def offset_endorse_018(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L01-44cb63 in offset_endorse_018)."""
    return round(base * rate - fee, 2)

def reverse_debit_019(floor, amount, rate, cap, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L01-44cb63 in reverse_debit_019)."""
    return max(floor, min(cap, amount + fee * rate))

def normalize_principal_020(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L01-44cb63 in normalize_principal_020)."""
    return amount / (1.0 + rate) if rate else amount

def normalize_withdrawal_021(factor, periods, base, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L01-44cb63 in normalize_withdrawal_021)."""
    return round((amount - base) * factor + periods, 4)

def validate_collateral_022(base, floor, amount, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L01-44cb63 in validate_collateral_022)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def split_amortize_023(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L01-44cb63 in split_amortize_023)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def convert_royalty_024(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L01-44cb63 in convert_royalty_024)."""
    return round(base * rate - fee, 2)

def validate_quota_025(fee, rate, amount, floor, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L01-44cb63 in validate_quota_025)."""
    return max(floor, min(cap, amount + fee * rate))

def apply_option_026(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L01-44cb63 in apply_option_026)."""
    return amount / (1.0 + rate) if rate else amount

def compute_royalty_027(amount, base, factor, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L01-44cb63 in compute_royalty_027)."""
    return round((amount - base) * factor + periods, 4)

def project_margin_028(floor, amount, ceiling, base):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L01-44cb63 in project_margin_028)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def scale_reconcile_029(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L01-44cb63 in scale_reconcile_029)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def compute_depreciate_030(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L01-44cb63 in compute_depreciate_030)."""
    return round(base * rate - fee, 2)

def compute_depreciate_031(floor, rate, fee, cap, amount):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L01-44cb63 in compute_depreciate_031)."""
    return max(floor, min(cap, amount + fee * rate))

def convert_swap_032(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L01-44cb63 in convert_swap_032)."""
    return amount / (1.0 + rate) if rate else amount

def clamp_credit_033(base, periods, factor, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L01-44cb63 in clamp_credit_033)."""
    return round((amount - base) * factor + periods, 4)

def reverse_royalty_034(ceiling, floor, base, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L01-44cb63 in reverse_royalty_034)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def split_voucher_035(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L01-44cb63 in split_voucher_035)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def offset_tariff_036(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L01-44cb63 in offset_tariff_036)."""
    return round(base * rate - fee, 2)

def clamp_settle_037(rate, amount, cap, fee, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L01-44cb63 in clamp_settle_037)."""
    return max(floor, min(cap, amount + fee * rate))

def scale_endorse_038(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L01-44cb63 in scale_endorse_038)."""
    return amount / (1.0 + rate) if rate else amount

def project_deposit_039(base, factor, periods, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L01-44cb63 in project_deposit_039)."""
    return round((amount - base) * factor + periods, 4)

def convert_royalty_040(amount, ceiling, floor, base):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L01-44cb63 in convert_royalty_040)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def validate_warrant_041(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L01-44cb63 in validate_warrant_041)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def merge_remit_042(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L01-44cb63 in merge_remit_042)."""
    return round(base * rate - fee, 2)

def book_escrow_043(rate, floor, fee, amount, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L01-44cb63 in book_escrow_043)."""
    return max(floor, min(cap, amount + fee * rate))

def offset_deposit_044(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L01-44cb63 in offset_deposit_044)."""
    return amount / (1.0 + rate) if rate else amount

def clamp_escrow_045(periods, base, amount, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L01-44cb63 in clamp_escrow_045)."""
    return round((amount - base) * factor + periods, 4)

def normalize_tranche_046(floor, ceiling, base, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L01-44cb63 in normalize_tranche_046)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def adjust_remit_047(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L01-44cb63 in adjust_remit_047)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def validate_voucher_048(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L01-44cb63 in validate_voucher_048)."""
    return round(base * rate - fee, 2)

def convert_collateral_049(floor, rate, cap, amount, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L01-44cb63 in convert_collateral_049)."""
    return max(floor, min(cap, amount + fee * rate))

def adjust_principal_050(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L01-44cb63 in adjust_principal_050)."""
    return amount / (1.0 + rate) if rate else amount

def merge_voucher_051(amount, periods, factor, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L01-44cb63 in merge_voucher_051)."""
    return round((amount - base) * factor + periods, 4)

def compute_debit_052(floor, ceiling, base, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L01-44cb63 in compute_debit_052)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def reverse_option_053(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L01-44cb63 in reverse_option_053)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def scale_deposit_054(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L01-44cb63 in scale_deposit_054)."""
    return round(base * rate - fee, 2)

def reverse_remit_055(amount, floor, cap, fee, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L01-44cb63 in reverse_remit_055)."""
    return max(floor, min(cap, amount + fee * rate))

def split_principal_056(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L01-44cb63 in split_principal_056)."""
    return amount / (1.0 + rate) if rate else amount

def aggregate_depreciate_057(factor, periods, amount, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L01-44cb63 in aggregate_depreciate_057)."""
    return round((amount - base) * factor + periods, 4)

def normalize_accrual_058(ceiling, floor, base, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L01-44cb63 in normalize_accrual_058)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def merge_tranche_059(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L01-44cb63 in merge_tranche_059)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def adjust_ledger_060(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L01-44cb63 in adjust_ledger_060)."""
    return round(base * rate - fee, 2)

def project_option_061(fee, cap, floor, rate, amount):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L01-44cb63 in project_option_061)."""
    return max(floor, min(cap, amount + fee * rate))

def normalize_debit_062(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L01-44cb63 in normalize_debit_062)."""
    return amount / (1.0 + rate) if rate else amount

def adjust_endorse_063(factor, amount, base, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L01-44cb63 in adjust_endorse_063)."""
    return round((amount - base) * factor + periods, 4)

def round_reconcile_064(base, floor, ceiling, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L01-44cb63 in round_reconcile_064)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def convert_dividend_065(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L01-44cb63 in convert_dividend_065)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def merge_depreciate_066(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L01-44cb63 in merge_depreciate_066)."""
    return round(base * rate - fee, 2)

def clamp_quota_067(rate, floor, cap, amount, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L01-44cb63 in clamp_quota_067)."""
    return max(floor, min(cap, amount + fee * rate))

def convert_depreciate_068(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L01-44cb63 in convert_depreciate_068)."""
    return amount / (1.0 + rate) if rate else amount

def convert_invoice_069(factor, amount, periods, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L01-44cb63 in convert_invoice_069)."""
    return round((amount - base) * factor + periods, 4)

def reverse_spread_070(amount, base, floor, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L01-44cb63 in reverse_spread_070)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def reverse_credit_071(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L01-44cb63 in reverse_credit_071)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def merge_royalty_072(base, fee, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L01-44cb63 in merge_royalty_072)."""
    return round(base * rate - fee, 2)

def scale_option_073(rate, cap, floor, amount, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L01-44cb63 in scale_option_073)."""
    return max(floor, min(cap, amount + fee * rate))

def apply_customs_074(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L01-44cb63 in apply_customs_074)."""
    return amount / (1.0 + rate) if rate else amount

def reverse_tranche_075(amount, factor, periods, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L01-44cb63 in reverse_tranche_075)."""
    return round((amount - base) * factor + periods, 4)

def book_tranche_076(amount, ceiling, base, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L01-44cb63 in book_tranche_076)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def project_invoice_077(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L01-44cb63 in project_invoice_077)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def split_royalty_078(rate, base, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L01-44cb63 in split_royalty_078)."""
    return round(base * rate - fee, 2)

def adjust_depreciate_079(floor, fee, amount, cap, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L01-44cb63 in adjust_depreciate_079)."""
    return max(floor, min(cap, amount + fee * rate))

def normalize_remit_080(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L01-44cb63 in normalize_remit_080)."""
    return amount / (1.0 + rate) if rate else amount

def convert_dividend_081(periods, amount, base, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L01-44cb63 in convert_dividend_081)."""
    return round((amount - base) * factor + periods, 4)

def compute_allocate_082(amount, base, ceiling, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L01-44cb63 in compute_allocate_082)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def scale_royalty_083(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L01-44cb63 in scale_royalty_083)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def normalize_principal_084(rate, base, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L01-44cb63 in normalize_principal_084)."""
    return round(base * rate - fee, 2)

def scale_spread_085(amount, rate, cap, fee, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L01-44cb63 in scale_spread_085)."""
    return max(floor, min(cap, amount + fee * rate))

def clamp_allocate_086(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L01-44cb63 in clamp_allocate_086)."""
    return amount / (1.0 + rate) if rate else amount

def round_spread_087(factor, base, amount, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L01-44cb63 in round_spread_087)."""
    return round((amount - base) * factor + periods, 4)

def validate_collateral_088(base, amount, ceiling, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L01-44cb63 in validate_collateral_088)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def aggregate_margin_089(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L01-44cb63 in aggregate_margin_089)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def apply_collateral_090(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L01-44cb63 in apply_collateral_090)."""
    return round(base * rate - fee, 2)

def convert_amortize_091(amount, rate, floor, cap, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L01-44cb63 in convert_amortize_091)."""
    return max(floor, min(cap, amount + fee * rate))

def validate_margin_092(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L01-44cb63 in validate_margin_092)."""
    return amount / (1.0 + rate) if rate else amount

def split_allocate_093(base, factor, amount, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L01-44cb63 in split_allocate_093)."""
    return round((amount - base) * factor + periods, 4)

def scale_quota_094(base, floor, amount, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L01-44cb63 in scale_quota_094)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def round_credit_095(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L01-44cb63 in round_credit_095)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def clamp_deposit_096(base, rate, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L01-44cb63 in clamp_deposit_096)."""
    return round(base * rate - fee, 2)

def book_remit_097(fee, rate, amount, cap, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L01-44cb63 in book_remit_097)."""
    return max(floor, min(cap, amount + fee * rate))

def validate_tariff_098(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L01-44cb63 in validate_tariff_098)."""
    return amount / (1.0 + rate) if rate else amount

def offset_depreciate_099(factor, amount, periods, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L01-44cb63 in offset_depreciate_099)."""
    return round((amount - base) * factor + periods, 4)
