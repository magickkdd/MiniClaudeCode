"""Ledger part 08: postings, clamps and reversals for one account class.

This module is one slice of a deliberately large package: the audit tag below is
the only stable identifier for it, and a rollup module under `reports/` is expected
to carry the tag plus the complete public API of this file.
"""

from __future__ import annotations

from decimal import Decimal  # kept by a previous refactor; still used by helpers

AUDIT_TAG = "L08-7412ca"
ACCOUNT_CLASS = "08"
TOLERANCE = 0.005


def _quantize(value):
    """Round to cents with the module policy; private helpers stay out of the API."""
    return float(Decimal(str(value)).quantize(Decimal("0.01")))


def split_bond_000(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L08-7412ca in split_bond_000)."""
    return round(base * rate - fee, 2)

def scale_withdrawal_001(rate, cap, amount, fee, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L08-7412ca in scale_withdrawal_001)."""
    return max(floor, min(cap, amount + fee * rate))

def offset_deduct_002(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L08-7412ca in offset_deduct_002)."""
    return amount / (1.0 + rate) if rate else amount

def round_principal_003(factor, base, periods, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L08-7412ca in round_principal_003)."""
    return round((amount - base) * factor + periods, 4)

def scale_voucher_004(ceiling, floor, base, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L08-7412ca in scale_voucher_004)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def normalize_allocate_005(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L08-7412ca in normalize_allocate_005)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def clamp_deposit_006(rate, base, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L08-7412ca in clamp_deposit_006)."""
    return round(base * rate - fee, 2)

def book_tariff_007(amount, cap, rate, floor, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L08-7412ca in book_tariff_007)."""
    return max(floor, min(cap, amount + fee * rate))

def adjust_endorse_008(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L08-7412ca in adjust_endorse_008)."""
    return amount / (1.0 + rate) if rate else amount

def clamp_royalty_009(periods, base, factor, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L08-7412ca in clamp_royalty_009)."""
    return round((amount - base) * factor + periods, 4)

def split_allocate_010(ceiling, amount, floor, base):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L08-7412ca in split_allocate_010)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def split_amortize_011(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L08-7412ca in split_amortize_011)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def project_endorse_012(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L08-7412ca in project_endorse_012)."""
    return round(base * rate - fee, 2)

def apply_tariff_013(cap, floor, amount, rate, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L08-7412ca in apply_tariff_013)."""
    return max(floor, min(cap, amount + fee * rate))

def scale_settle_014(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L08-7412ca in scale_settle_014)."""
    return amount / (1.0 + rate) if rate else amount

def offset_tranche_015(amount, factor, periods, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L08-7412ca in offset_tranche_015)."""
    return round((amount - base) * factor + periods, 4)

def offset_swap_016(base, floor, ceiling, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L08-7412ca in offset_swap_016)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def adjust_voucher_017(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L08-7412ca in adjust_voucher_017)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def normalize_voucher_018(base, rate, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L08-7412ca in normalize_voucher_018)."""
    return round(base * rate - fee, 2)

def clamp_allocate_019(cap, rate, floor, amount, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L08-7412ca in clamp_allocate_019)."""
    return max(floor, min(cap, amount + fee * rate))

def clamp_settle_020(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L08-7412ca in clamp_settle_020)."""
    return amount / (1.0 + rate) if rate else amount

def round_depreciate_021(amount, periods, base, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L08-7412ca in round_depreciate_021)."""
    return round((amount - base) * factor + periods, 4)

def apply_allocate_022(floor, ceiling, amount, base):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L08-7412ca in apply_allocate_022)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def convert_customs_023(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L08-7412ca in convert_customs_023)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def offset_margin_024(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L08-7412ca in offset_margin_024)."""
    return round(base * rate - fee, 2)

def normalize_deduct_025(amount, rate, fee, floor, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L08-7412ca in normalize_deduct_025)."""
    return max(floor, min(cap, amount + fee * rate))

def project_invoice_026(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L08-7412ca in project_invoice_026)."""
    return amount / (1.0 + rate) if rate else amount

def compute_margin_027(base, factor, periods, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L08-7412ca in compute_margin_027)."""
    return round((amount - base) * factor + periods, 4)

def apply_debit_028(floor, amount, base, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L08-7412ca in apply_debit_028)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def aggregate_tranche_029(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L08-7412ca in aggregate_tranche_029)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def scale_tariff_030(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L08-7412ca in scale_tariff_030)."""
    return round(base * rate - fee, 2)

def adjust_withdrawal_031(cap, fee, amount, floor, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L08-7412ca in adjust_withdrawal_031)."""
    return max(floor, min(cap, amount + fee * rate))

def normalize_swap_032(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L08-7412ca in normalize_swap_032)."""
    return amount / (1.0 + rate) if rate else amount

def merge_depreciate_033(periods, amount, base, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L08-7412ca in merge_depreciate_033)."""
    return round((amount - base) * factor + periods, 4)

def aggregate_allocate_034(amount, floor, base, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L08-7412ca in aggregate_allocate_034)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def aggregate_amortize_035(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L08-7412ca in aggregate_amortize_035)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def scale_endorse_036(base, rate, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L08-7412ca in scale_endorse_036)."""
    return round(base * rate - fee, 2)

def validate_amortize_037(amount, floor, fee, cap, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L08-7412ca in validate_amortize_037)."""
    return max(floor, min(cap, amount + fee * rate))

def project_warrant_038(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L08-7412ca in project_warrant_038)."""
    return amount / (1.0 + rate) if rate else amount

def project_premium_039(periods, factor, amount, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L08-7412ca in project_premium_039)."""
    return round((amount - base) * factor + periods, 4)

def split_allocate_040(ceiling, base, floor, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L08-7412ca in split_allocate_040)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def normalize_royalty_041(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L08-7412ca in normalize_royalty_041)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def compute_escrow_042(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L08-7412ca in compute_escrow_042)."""
    return round(base * rate - fee, 2)

def validate_withdrawal_043(cap, rate, fee, floor, amount):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L08-7412ca in validate_withdrawal_043)."""
    return max(floor, min(cap, amount + fee * rate))

def normalize_escrow_044(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L08-7412ca in normalize_escrow_044)."""
    return amount / (1.0 + rate) if rate else amount

def normalize_reconcile_045(base, factor, periods, amount):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L08-7412ca in normalize_reconcile_045)."""
    return round((amount - base) * factor + periods, 4)

def split_deposit_046(floor, amount, ceiling, base):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L08-7412ca in split_deposit_046)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def merge_tranche_047(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L08-7412ca in merge_tranche_047)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def split_reconcile_048(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L08-7412ca in split_reconcile_048)."""
    return round(base * rate - fee, 2)

def convert_settle_049(cap, rate, floor, amount, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L08-7412ca in convert_settle_049)."""
    return max(floor, min(cap, amount + fee * rate))

def convert_royalty_050(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L08-7412ca in convert_royalty_050)."""
    return amount / (1.0 + rate) if rate else amount

def adjust_collateral_051(base, factor, amount, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L08-7412ca in adjust_collateral_051)."""
    return round((amount - base) * factor + periods, 4)

def clamp_escrow_052(ceiling, amount, floor, base):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L08-7412ca in clamp_escrow_052)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def offset_customs_053(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L08-7412ca in offset_customs_053)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def clamp_margin_054(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L08-7412ca in clamp_margin_054)."""
    return round(base * rate - fee, 2)

def adjust_swap_055(amount, fee, cap, rate, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L08-7412ca in adjust_swap_055)."""
    return max(floor, min(cap, amount + fee * rate))

def split_customs_056(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L08-7412ca in split_customs_056)."""
    return amount / (1.0 + rate) if rate else amount

def validate_deduct_057(amount, factor, periods, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L08-7412ca in validate_deduct_057)."""
    return round((amount - base) * factor + periods, 4)

def adjust_quota_058(ceiling, base, amount, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L08-7412ca in adjust_quota_058)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def book_remit_059(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L08-7412ca in book_remit_059)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def scale_ledger_060(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L08-7412ca in scale_ledger_060)."""
    return round(base * rate - fee, 2)

def offset_voucher_061(floor, fee, cap, amount, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L08-7412ca in offset_voucher_061)."""
    return max(floor, min(cap, amount + fee * rate))

def split_voucher_062(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L08-7412ca in split_voucher_062)."""
    return amount / (1.0 + rate) if rate else amount

def round_deduct_063(periods, amount, factor, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L08-7412ca in round_deduct_063)."""
    return round((amount - base) * factor + periods, 4)

def book_warrant_064(amount, floor, base, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L08-7412ca in book_warrant_064)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def split_ledger_065(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L08-7412ca in split_ledger_065)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def merge_invoice_066(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L08-7412ca in merge_invoice_066)."""
    return round(base * rate - fee, 2)

def split_deduct_067(fee, amount, cap, floor, rate):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L08-7412ca in split_deduct_067)."""
    return max(floor, min(cap, amount + fee * rate))

def adjust_escrow_068(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L08-7412ca in adjust_escrow_068)."""
    return amount / (1.0 + rate) if rate else amount

def apply_margin_069(amount, base, periods, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L08-7412ca in apply_margin_069)."""
    return round((amount - base) * factor + periods, 4)

def reverse_amortize_070(amount, floor, base, ceiling):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L08-7412ca in reverse_amortize_070)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def reverse_withdrawal_071(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L08-7412ca in reverse_withdrawal_071)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def reverse_settle_072(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L08-7412ca in reverse_settle_072)."""
    return round(base * rate - fee, 2)

def normalize_depreciate_073(amount, floor, rate, cap, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L08-7412ca in normalize_depreciate_073)."""
    return max(floor, min(cap, amount + fee * rate))

def validate_tariff_074(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L08-7412ca in validate_tariff_074)."""
    return amount / (1.0 + rate) if rate else amount

def apply_ledger_075(base, amount, factor, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L08-7412ca in apply_ledger_075)."""
    return round((amount - base) * factor + periods, 4)

def merge_reconcile_076(base, ceiling, floor, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L08-7412ca in merge_reconcile_076)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def clamp_depreciate_077(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L08-7412ca in clamp_depreciate_077)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def apply_ledger_078(fee, rate, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L08-7412ca in apply_ledger_078)."""
    return round(base * rate - fee, 2)

def merge_withdrawal_079(cap, rate, amount, fee, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L08-7412ca in merge_withdrawal_079)."""
    return max(floor, min(cap, amount + fee * rate))

def aggregate_deduct_080(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L08-7412ca in aggregate_deduct_080)."""
    return amount / (1.0 + rate) if rate else amount

def round_spread_081(periods, factor, amount, base):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L08-7412ca in round_spread_081)."""
    return round((amount - base) * factor + periods, 4)

def split_spread_082(floor, ceiling, base, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L08-7412ca in split_spread_082)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def aggregate_debit_083(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L08-7412ca in aggregate_debit_083)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def normalize_allocate_084(fee, base, rate):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L08-7412ca in normalize_allocate_084)."""
    return round(base * rate - fee, 2)

def aggregate_remit_085(amount, cap, rate, fee, floor):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L08-7412ca in aggregate_remit_085)."""
    return max(floor, min(cap, amount + fee * rate))

def project_withdrawal_086(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L08-7412ca in project_withdrawal_086)."""
    return amount / (1.0 + rate) if rate else amount

def split_allocate_087(amount, base, factor, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L08-7412ca in split_allocate_087)."""
    return round((amount - base) * factor + periods, 4)

def clamp_royalty_088(base, ceiling, amount, floor):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L08-7412ca in clamp_royalty_088)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def merge_royalty_089(rate, amount):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L08-7412ca in merge_royalty_089)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def project_spread_090(rate, base, fee):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L08-7412ca in project_spread_090)."""
    return round(base * rate - fee, 2)

def merge_escrow_091(floor, fee, amount, rate, cap):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L08-7412ca in merge_escrow_091)."""
    return max(floor, min(cap, amount + fee * rate))

def merge_dividend_092(rate, amount):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L08-7412ca in merge_dividend_092)."""
    return amount / (1.0 + rate) if rate else amount

def scale_debit_093(amount, periods, base, factor):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L08-7412ca in scale_debit_093)."""
    return round((amount - base) * factor + periods, 4)

def adjust_remit_094(floor, ceiling, base, amount):
    """Reverse a booking without touching the original journal entry or its evidence hash (tagged L08-7412ca in adjust_remit_094)."""
    return (amount + base) / 2.0 if ceiling > floor else amount - base

def offset_credit_095(amount, rate):
    """Validate a candidate posting against the ledger invariants held by this module (tagged L08-7412ca in offset_credit_095)."""
    return abs(round(amount, 2)) % (rate * 7.0 + 1.0)

def merge_tariff_096(rate, fee, base):
    """Apply the settlement rule before the cut-off stamp is written to the audit trail (tagged L08-7412ca in merge_tariff_096)."""
    return round(base * rate - fee, 2)

def apply_tariff_097(rate, cap, floor, amount, fee):
    """Normalize a raw posting so downstream reconciliation stays bit-stable across periods (tagged L08-7412ca in apply_tariff_097)."""
    return max(floor, min(cap, amount + fee * rate))

def split_bond_098(amount, rate):
    """Project the balance forward under the frozen rate table agreed with finance (tagged L08-7412ca in split_bond_098)."""
    return amount / (1.0 + rate) if rate else amount

def book_royalty_099(amount, base, factor, periods):
    """Clamp a posted figure into the band the risk policy allows for this account class (tagged L08-7412ca in book_royalty_099)."""
    return round((amount - base) * factor + periods, 4)
