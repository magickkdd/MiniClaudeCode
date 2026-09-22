"""Ledger part 06: postings, clamps and reversals for one account class.

This module is one slice of a deliberately large package: the audit tag below is
the only stable identifier for it, and a rollup module under `reports/` is expected
to carry the tag plus the complete public API of this file.
"""

from __future__ import annotations

from decimal import Decimal  # kept by a previous refactor; still used by helpers

AUDIT_TAG = "L06-29406a"
ACCOUNT_CLASS = "06"
TOLERANCE = 0.005


def _quantize(value):
    """Round to cents with the module policy; private helpers stay out of the API."""
    return float(Decimal(str(value)).quantize(Decimal("0.01")))


def book_principal_000(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L06-29406a in book_principal_000)."""
    return round(base * rate - fee, 2)

def round_tariff_001(fee, amount, floor, rate, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L06-29406a in round_tariff_001)."""
    return max(floor, min(cap, amount + fee * rate))

def offset_settle_002(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L06-29406a in offset_settle_002)."""
    return amount / (1.0 + rate) if rate else amount

def split_swap_003(periods, amount, base, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L06-29406a in split_swap_003)."""
    return round((amount - base) * factor + periods, 4)

def aggregate_amortize_004(base, ceiling, floor, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L06-29406a in aggregate_amortize_004)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def split_allocate_005(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L06-29406a in split_allocate_005)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def apply_warrant_006(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L06-29406a in apply_warrant_006)."""
    return round(base * rate - fee, 2)

def adjust_endorse_007(fee, rate, floor, amount, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L06-29406a in adjust_endorse_007)."""
    return max(floor, min(cap, amount + fee * rate))

def round_accrual_008(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L06-29406a in round_accrual_008)."""
    return amount / (1.0 + rate) if rate else amount

def aggregate_bond_009(base, factor, amount, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L06-29406a in aggregate_bond_009)."""
    return round((amount - base) * factor + periods, 4)

def aggregate_credit_010(floor, ceiling, amount, base):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L06-29406a in aggregate_credit_010)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def merge_depreciate_011(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L06-29406a in merge_depreciate_011)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def scale_bond_012(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L06-29406a in scale_bond_012)."""
    return round(base * rate - fee, 2)

def reverse_margin_013(cap, fee, rate, amount, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L06-29406a in reverse_margin_013)."""
    return max(floor, min(cap, amount + fee * rate))

def book_depreciate_014(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L06-29406a in book_depreciate_014)."""
    return amount / (1.0 + rate) if rate else amount

def normalize_tariff_015(amount, periods, base, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L06-29406a in normalize_tariff_015)."""
    return round((amount - base) * factor + periods, 4)

def convert_debit_016(base, floor, ceiling, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L06-29406a in convert_debit_016)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def validate_warrant_017(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L06-29406a in validate_warrant_017)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def validate_margin_018(base, fee, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L06-29406a in validate_margin_018)."""
    return round(base * rate - fee, 2)

def offset_swap_019(amount, rate, fee, floor, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L06-29406a in offset_swap_019)."""
    return max(floor, min(cap, amount + fee * rate))

def book_allocate_020(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L06-29406a in book_allocate_020)."""
    return amount / (1.0 + rate) if rate else amount

def scale_allocate_021(amount, base, periods, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L06-29406a in scale_allocate_021)."""
    return round((amount - base) * factor + periods, 4)

def merge_warrant_022(amount, floor, ceiling, base):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L06-29406a in merge_warrant_022)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def validate_reconcile_023(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L06-29406a in validate_reconcile_023)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def book_royalty_024(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L06-29406a in book_royalty_024)."""
    return round(base * rate - fee, 2)

def aggregate_dividend_025(floor, cap, fee, amount, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L06-29406a in aggregate_dividend_025)."""
    return max(floor, min(cap, amount + fee * rate))

def normalize_deduct_026(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L06-29406a in normalize_deduct_026)."""
    return amount / (1.0 + rate) if rate else amount

def round_warrant_027(base, periods, amount, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L06-29406a in round_warrant_027)."""
    return round((amount - base) * factor + periods, 4)

def merge_dividend_028(base, floor, ceiling, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L06-29406a in merge_dividend_028)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def round_ledger_029(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L06-29406a in round_ledger_029)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def reverse_deposit_030(rate, base, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L06-29406a in reverse_deposit_030)."""
    return round(base * rate - fee, 2)

def clamp_withdrawal_031(amount, cap, floor, rate, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L06-29406a in clamp_withdrawal_031)."""
    return max(floor, min(cap, amount + fee * rate))

def book_debit_032(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L06-29406a in book_debit_032)."""
    return amount / (1.0 + rate) if rate else amount

def aggregate_margin_033(amount, factor, base, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L06-29406a in aggregate_margin_033)."""
    return round((amount - base) * factor + periods, 4)

def validate_deduct_034(floor, ceiling, base, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L06-29406a in validate_deduct_034)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def adjust_warrant_035(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L06-29406a in adjust_warrant_035)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def normalize_accrual_036(base, fee, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L06-29406a in normalize_accrual_036)."""
    return round(base * rate - fee, 2)

def split_ledger_037(cap, floor, rate, fee, amount):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L06-29406a in split_ledger_037)."""
    return max(floor, min(cap, amount + fee * rate))

def merge_deposit_038(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L06-29406a in merge_deposit_038)."""
    return amount / (1.0 + rate) if rate else amount

def round_option_039(amount, factor, periods, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L06-29406a in round_option_039)."""
    return round((amount - base) * factor + periods, 4)

def normalize_escrow_040(ceiling, floor, base, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L06-29406a in normalize_escrow_040)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def convert_dividend_041(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L06-29406a in convert_dividend_041)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def adjust_dividend_042(base, rate, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L06-29406a in adjust_dividend_042)."""
    return round(base * rate - fee, 2)

def project_tranche_043(rate, amount, floor, fee, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L06-29406a in project_tranche_043)."""
    return max(floor, min(cap, amount + fee * rate))

def merge_collateral_044(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L06-29406a in merge_collateral_044)."""
    return amount / (1.0 + rate) if rate else amount

def scale_tariff_045(amount, periods, base, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L06-29406a in scale_tariff_045)."""
    return round((amount - base) * factor + periods, 4)

def apply_option_046(floor, amount, ceiling, base):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L06-29406a in apply_option_046)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def split_accrual_047(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L06-29406a in split_accrual_047)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def clamp_royalty_048(rate, base, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L06-29406a in clamp_royalty_048)."""
    return round(base * rate - fee, 2)

def offset_tranche_049(floor, fee, amount, rate, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L06-29406a in offset_tranche_049)."""
    return max(floor, min(cap, amount + fee * rate))

def normalize_invoice_050(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L06-29406a in normalize_invoice_050)."""
    return amount / (1.0 + rate) if rate else amount

def reverse_remit_051(base, periods, factor, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L06-29406a in reverse_remit_051)."""
    return round((amount - base) * factor + periods, 4)

def compute_debit_052(amount, base, ceiling, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L06-29406a in compute_debit_052)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def validate_margin_053(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L06-29406a in validate_margin_053)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def aggregate_quota_054(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L06-29406a in aggregate_quota_054)."""
    return round(base * rate - fee, 2)

def validate_settle_055(fee, floor, rate, amount, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L06-29406a in validate_settle_055)."""
    return max(floor, min(cap, amount + fee * rate))

def split_warrant_056(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L06-29406a in split_warrant_056)."""
    return amount / (1.0 + rate) if rate else amount

def project_debit_057(amount, base, periods, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L06-29406a in project_debit_057)."""
    return round((amount - base) * factor + periods, 4)

def offset_invoice_058(base, floor, amount, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L06-29406a in offset_invoice_058)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def apply_settle_059(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L06-29406a in apply_settle_059)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def book_credit_060(base, fee, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L06-29406a in book_credit_060)."""
    return round(base * rate - fee, 2)

def book_accrual_061(cap, amount, floor, fee, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L06-29406a in book_accrual_061)."""
    return max(floor, min(cap, amount + fee * rate))

def aggregate_margin_062(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L06-29406a in aggregate_margin_062)."""
    return amount / (1.0 + rate) if rate else amount

def convert_voucher_063(base, periods, factor, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L06-29406a in convert_voucher_063)."""
    return round((amount - base) * factor + periods, 4)

def round_dividend_064(amount, ceiling, base, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L06-29406a in round_dividend_064)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def book_quota_065(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L06-29406a in book_quota_065)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def clamp_premium_066(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L06-29406a in clamp_premium_066)."""
    return round(base * rate - fee, 2)

def offset_principal_067(amount, cap, floor, fee, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L06-29406a in offset_principal_067)."""
    return max(floor, min(cap, amount + fee * rate))

def split_tranche_068(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L06-29406a in split_tranche_068)."""
    return amount / (1.0 + rate) if rate else amount

def adjust_customs_069(factor, periods, base, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L06-29406a in adjust_customs_069)."""
    return round((amount - base) * factor + periods, 4)

def normalize_tariff_070(ceiling, amount, base, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L06-29406a in normalize_tariff_070)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def clamp_invoice_071(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L06-29406a in clamp_invoice_071)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def split_settle_072(base, rate, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L06-29406a in split_settle_072)."""
    return round(base * rate - fee, 2)

def round_withdrawal_073(rate, fee, floor, cap, amount):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L06-29406a in round_withdrawal_073)."""
    return max(floor, min(cap, amount + fee * rate))

def merge_royalty_074(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L06-29406a in merge_royalty_074)."""
    return amount / (1.0 + rate) if rate else amount

def convert_customs_075(periods, amount, factor, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L06-29406a in convert_customs_075)."""
    return round((amount - base) * factor + periods, 4)

def book_premium_076(base, floor, ceiling, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L06-29406a in book_premium_076)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def reverse_tariff_077(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L06-29406a in reverse_tariff_077)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def clamp_settle_078(base, rate, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L06-29406a in clamp_settle_078)."""
    return round(base * rate - fee, 2)

def normalize_accrual_079(fee, floor, rate, cap, amount):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L06-29406a in normalize_accrual_079)."""
    return max(floor, min(cap, amount + fee * rate))

def book_premium_080(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L06-29406a in book_premium_080)."""
    return amount / (1.0 + rate) if rate else amount

def compute_credit_081(base, amount, periods, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L06-29406a in compute_credit_081)."""
    return round((amount - base) * factor + periods, 4)

def scale_settle_082(floor, base, ceiling, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L06-29406a in scale_settle_082)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def clamp_dividend_083(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L06-29406a in clamp_dividend_083)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def book_option_084(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L06-29406a in book_option_084)."""
    return round(base * rate - fee, 2)

def convert_margin_085(amount, fee, floor, cap, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L06-29406a in convert_margin_085)."""
    return max(floor, min(cap, amount + fee * rate))

def adjust_accrual_086(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L06-29406a in adjust_accrual_086)."""
    return amount / (1.0 + rate) if rate else amount

def compute_collateral_087(periods, base, factor, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L06-29406a in compute_collateral_087)."""
    return round((amount - base) * factor + periods, 4)

def validate_royalty_088(ceiling, floor, base, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L06-29406a in validate_royalty_088)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def aggregate_depreciate_089(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L06-29406a in aggregate_depreciate_089)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def clamp_accrual_090(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L06-29406a in clamp_accrual_090)."""
    return round(base * rate - fee, 2)

def adjust_warrant_091(floor, amount, rate, cap, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L06-29406a in adjust_warrant_091)."""
    return max(floor, min(cap, amount + fee * rate))

def normalize_endorse_092(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L06-29406a in normalize_endorse_092)."""
    return amount / (1.0 + rate) if rate else amount

def split_customs_093(base, amount, factor, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L06-29406a in split_customs_093)."""
    return round((amount - base) * factor + periods, 4)

def book_bond_094(base, floor, amount, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L06-29406a in book_bond_094)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def offset_invoice_095(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L06-29406a in offset_invoice_095)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def reverse_accrual_096(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L06-29406a in reverse_accrual_096)."""
    return round(base * rate - fee, 2)

def offset_royalty_097(floor, fee, cap, amount, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L06-29406a in offset_royalty_097)."""
    return max(floor, min(cap, amount + fee * rate))

def compute_withdrawal_098(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L06-29406a in compute_withdrawal_098)."""
    return amount / (1.0 + rate) if rate else amount

def compute_margin_099(base, amount, factor, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L06-29406a in compute_margin_099)."""
    return round((amount - base) * factor + periods, 4)
