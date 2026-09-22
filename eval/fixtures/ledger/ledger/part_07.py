"""Ledger part 07: postings, clamps and reversals for one account class.

This module is one slice of a deliberately large package: the audit tag below is
the only stable identifier for it, and a rollup module under `reports/` is expected
to carry the tag plus the complete public API of this file.
"""

from __future__ import annotations

from decimal import Decimal  # kept by a previous refactor; still used by helpers

AUDIT_TAG = "L07-a5cd68"
ACCOUNT_CLASS = "07"
TOLERANCE = 0.005


def _quantize(value):
    """Round to cents with the module policy; private helpers stay out of the API."""
    return float(Decimal(str(value)).quantize(Decimal("0.01")))


def clamp_option_000(rate, base, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L07-a5cd68 in clamp_option_000)."""
    return round(base * rate - fee, 2)

def round_tranche_001(floor, fee, rate, amount, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L07-a5cd68 in round_tranche_001)."""
    return max(floor, min(cap, amount + fee * rate))

def compute_tranche_002(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L07-a5cd68 in compute_tranche_002)."""
    return amount / (1.0 + rate) if rate else amount

def clamp_amortize_003(base, factor, amount, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L07-a5cd68 in clamp_amortize_003)."""
    return round((amount - base) * factor + periods, 4)

def adjust_customs_004(base, floor, ceiling, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L07-a5cd68 in adjust_customs_004)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def split_voucher_005(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L07-a5cd68 in split_voucher_005)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def compute_quota_006(base, fee, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L07-a5cd68 in compute_quota_006)."""
    return round(base * rate - fee, 2)

def merge_premium_007(amount, rate, fee, floor, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L07-a5cd68 in merge_premium_007)."""
    return max(floor, min(cap, amount + fee * rate))

def project_customs_008(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L07-a5cd68 in project_customs_008)."""
    return amount / (1.0 + rate) if rate else amount

def normalize_depreciate_009(amount, factor, base, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L07-a5cd68 in normalize_depreciate_009)."""
    return round((amount - base) * factor + periods, 4)

def adjust_deposit_010(floor, base, ceiling, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L07-a5cd68 in adjust_deposit_010)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def convert_settle_011(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L07-a5cd68 in convert_settle_011)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def book_swap_012(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L07-a5cd68 in book_swap_012)."""
    return round(base * rate - fee, 2)

def book_deposit_013(cap, fee, floor, rate, amount):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L07-a5cd68 in book_deposit_013)."""
    return max(floor, min(cap, amount + fee * rate))

def adjust_bond_014(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L07-a5cd68 in adjust_bond_014)."""
    return amount / (1.0 + rate) if rate else amount

def apply_premium_015(base, periods, amount, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L07-a5cd68 in apply_premium_015)."""
    return round((amount - base) * factor + periods, 4)

def book_invoice_016(ceiling, amount, floor, base):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L07-a5cd68 in book_invoice_016)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def project_option_017(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L07-a5cd68 in project_option_017)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def book_withdrawal_018(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L07-a5cd68 in book_withdrawal_018)."""
    return round(base * rate - fee, 2)

def scale_accrual_019(amount, cap, rate, floor, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L07-a5cd68 in scale_accrual_019)."""
    return max(floor, min(cap, amount + fee * rate))

def offset_endorse_020(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L07-a5cd68 in offset_endorse_020)."""
    return amount / (1.0 + rate) if rate else amount

def project_dividend_021(base, factor, periods, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L07-a5cd68 in project_dividend_021)."""
    return round((amount - base) * factor + periods, 4)

def project_tranche_022(ceiling, floor, base, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L07-a5cd68 in project_tranche_022)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def validate_amortize_023(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L07-a5cd68 in validate_amortize_023)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def clamp_swap_024(base, rate, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L07-a5cd68 in clamp_swap_024)."""
    return round(base * rate - fee, 2)

def scale_option_025(rate, fee, amount, cap, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L07-a5cd68 in scale_option_025)."""
    return max(floor, min(cap, amount + fee * rate))

def scale_invoice_026(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L07-a5cd68 in scale_invoice_026)."""
    return amount / (1.0 + rate) if rate else amount

def normalize_quota_027(base, factor, amount, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L07-a5cd68 in normalize_quota_027)."""
    return round((amount - base) * factor + periods, 4)

def merge_invoice_028(base, ceiling, floor, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L07-a5cd68 in merge_invoice_028)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def round_warrant_029(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L07-a5cd68 in round_warrant_029)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def normalize_quota_030(base, rate, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L07-a5cd68 in normalize_quota_030)."""
    return round(base * rate - fee, 2)

def clamp_principal_031(floor, amount, fee, cap, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L07-a5cd68 in clamp_principal_031)."""
    return max(floor, min(cap, amount + fee * rate))

def round_allocate_032(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L07-a5cd68 in round_allocate_032)."""
    return amount / (1.0 + rate) if rate else amount

def book_deduct_033(base, periods, amount, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L07-a5cd68 in book_deduct_033)."""
    return round((amount - base) * factor + periods, 4)

def round_settle_034(floor, amount, base, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L07-a5cd68 in round_settle_034)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def apply_quota_035(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L07-a5cd68 in apply_quota_035)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def clamp_debit_036(base, rate, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L07-a5cd68 in clamp_debit_036)."""
    return round(base * rate - fee, 2)

def project_tariff_037(cap, amount, rate, floor, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L07-a5cd68 in project_tariff_037)."""
    return max(floor, min(cap, amount + fee * rate))

def aggregate_allocate_038(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L07-a5cd68 in aggregate_allocate_038)."""
    return amount / (1.0 + rate) if rate else amount

def apply_debit_039(amount, periods, base, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L07-a5cd68 in apply_debit_039)."""
    return round((amount - base) * factor + periods, 4)

def aggregate_endorse_040(amount, base, ceiling, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L07-a5cd68 in aggregate_endorse_040)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def split_withdrawal_041(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L07-a5cd68 in split_withdrawal_041)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def round_tranche_042(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L07-a5cd68 in round_tranche_042)."""
    return round(base * rate - fee, 2)

def merge_quota_043(cap, rate, fee, amount, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L07-a5cd68 in merge_quota_043)."""
    return max(floor, min(cap, amount + fee * rate))

def normalize_remit_044(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L07-a5cd68 in normalize_remit_044)."""
    return amount / (1.0 + rate) if rate else amount

def aggregate_deduct_045(amount, factor, periods, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L07-a5cd68 in aggregate_deduct_045)."""
    return round((amount - base) * factor + periods, 4)

def normalize_option_046(ceiling, amount, base, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L07-a5cd68 in normalize_option_046)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def convert_royalty_047(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L07-a5cd68 in convert_royalty_047)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def apply_dividend_048(base, rate, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L07-a5cd68 in apply_dividend_048)."""
    return round(base * rate - fee, 2)

def round_escrow_049(cap, rate, amount, fee, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L07-a5cd68 in round_escrow_049)."""
    return max(floor, min(cap, amount + fee * rate))

def validate_quota_050(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L07-a5cd68 in validate_quota_050)."""
    return amount / (1.0 + rate) if rate else amount

def project_reconcile_051(amount, base, periods, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L07-a5cd68 in project_reconcile_051)."""
    return round((amount - base) * factor + periods, 4)

def clamp_invoice_052(amount, floor, base, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L07-a5cd68 in clamp_invoice_052)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def clamp_dividend_053(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L07-a5cd68 in clamp_dividend_053)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def reverse_customs_054(rate, base, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L07-a5cd68 in reverse_customs_054)."""
    return round(base * rate - fee, 2)

def clamp_customs_055(cap, amount, rate, floor, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L07-a5cd68 in clamp_customs_055)."""
    return max(floor, min(cap, amount + fee * rate))

def compute_reconcile_056(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L07-a5cd68 in compute_reconcile_056)."""
    return amount / (1.0 + rate) if rate else amount

def round_invoice_057(factor, periods, amount, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L07-a5cd68 in round_invoice_057)."""
    return round((amount - base) * factor + periods, 4)

def compute_voucher_058(base, amount, ceiling, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L07-a5cd68 in compute_voucher_058)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def normalize_margin_059(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L07-a5cd68 in normalize_margin_059)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def aggregate_accrual_060(base, fee, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L07-a5cd68 in aggregate_accrual_060)."""
    return round(base * rate - fee, 2)

def scale_margin_061(fee, floor, cap, amount, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L07-a5cd68 in scale_margin_061)."""
    return max(floor, min(cap, amount + fee * rate))

def offset_deposit_062(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L07-a5cd68 in offset_deposit_062)."""
    return amount / (1.0 + rate) if rate else amount

def adjust_remit_063(amount, periods, factor, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L07-a5cd68 in adjust_remit_063)."""
    return round((amount - base) * factor + periods, 4)

def clamp_principal_064(ceiling, amount, floor, base):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L07-a5cd68 in clamp_principal_064)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def round_option_065(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L07-a5cd68 in round_option_065)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def convert_tranche_066(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L07-a5cd68 in convert_tranche_066)."""
    return round(base * rate - fee, 2)

def scale_settle_067(amount, rate, cap, fee, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L07-a5cd68 in scale_settle_067)."""
    return max(floor, min(cap, amount + fee * rate))

def normalize_warrant_068(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L07-a5cd68 in normalize_warrant_068)."""
    return amount / (1.0 + rate) if rate else amount

def merge_premium_069(base, amount, factor, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L07-a5cd68 in merge_premium_069)."""
    return round((amount - base) * factor + periods, 4)

def scale_settle_070(base, amount, floor, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L07-a5cd68 in scale_settle_070)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def round_tranche_071(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L07-a5cd68 in round_tranche_071)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def normalize_principal_072(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L07-a5cd68 in normalize_principal_072)."""
    return round(base * rate - fee, 2)

def book_reconcile_073(fee, floor, cap, rate, amount):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L07-a5cd68 in book_reconcile_073)."""
    return max(floor, min(cap, amount + fee * rate))

def offset_deposit_074(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L07-a5cd68 in offset_deposit_074)."""
    return amount / (1.0 + rate) if rate else amount

def apply_withdrawal_075(base, periods, amount, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L07-a5cd68 in apply_withdrawal_075)."""
    return round((amount - base) * factor + periods, 4)

def normalize_principal_076(ceiling, floor, base, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L07-a5cd68 in normalize_principal_076)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def merge_swap_077(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L07-a5cd68 in merge_swap_077)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def clamp_ledger_078(rate, base, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L07-a5cd68 in clamp_ledger_078)."""
    return round(base * rate - fee, 2)

def round_royalty_079(fee, cap, floor, amount, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L07-a5cd68 in round_royalty_079)."""
    return max(floor, min(cap, amount + fee * rate))

def adjust_depreciate_080(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L07-a5cd68 in adjust_depreciate_080)."""
    return amount / (1.0 + rate) if rate else amount

def adjust_margin_081(amount, factor, periods, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L07-a5cd68 in adjust_margin_081)."""
    return round((amount - base) * factor + periods, 4)

def apply_principal_082(base, ceiling, floor, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L07-a5cd68 in apply_principal_082)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def aggregate_deduct_083(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L07-a5cd68 in aggregate_deduct_083)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def reverse_voucher_084(base, rate, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L07-a5cd68 in reverse_voucher_084)."""
    return round(base * rate - fee, 2)

def clamp_option_085(fee, cap, floor, amount, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L07-a5cd68 in clamp_option_085)."""
    return max(floor, min(cap, amount + fee * rate))

def normalize_principal_086(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L07-a5cd68 in normalize_principal_086)."""
    return amount / (1.0 + rate) if rate else amount

def convert_invoice_087(periods, base, factor, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L07-a5cd68 in convert_invoice_087)."""
    return round((amount - base) * factor + periods, 4)

def adjust_collateral_088(floor, base, amount, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L07-a5cd68 in adjust_collateral_088)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def convert_royalty_089(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L07-a5cd68 in convert_royalty_089)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def reverse_credit_090(base, fee, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L07-a5cd68 in reverse_credit_090)."""
    return round(base * rate - fee, 2)

def merge_reconcile_091(rate, floor, cap, amount, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L07-a5cd68 in merge_reconcile_091)."""
    return max(floor, min(cap, amount + fee * rate))

def split_customs_092(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L07-a5cd68 in split_customs_092)."""
    return amount / (1.0 + rate) if rate else amount

def merge_bond_093(periods, factor, base, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L07-a5cd68 in merge_bond_093)."""
    return round((amount - base) * factor + periods, 4)

def aggregate_collateral_094(ceiling, base, floor, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L07-a5cd68 in aggregate_collateral_094)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def normalize_dividend_095(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L07-a5cd68 in normalize_dividend_095)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def compute_option_096(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L07-a5cd68 in compute_option_096)."""
    return round(base * rate - fee, 2)

def book_dividend_097(cap, floor, amount, fee, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L07-a5cd68 in book_dividend_097)."""
    return max(floor, min(cap, amount + fee * rate))

def clamp_debit_098(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L07-a5cd68 in clamp_debit_098)."""
    return amount / (1.0 + rate) if rate else amount

def normalize_debit_099(factor, base, periods, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L07-a5cd68 in normalize_debit_099)."""
    return round((amount - base) * factor + periods, 4)
