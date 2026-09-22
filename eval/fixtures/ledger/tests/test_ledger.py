"""Behaviour tests for the ledger package. Green at baseline."""

from ledger import part_01, part_02, part_03, part_04, part_05, part_06, part_07, part_08


def test_part_01_audit_tag():
    assert part_01.AUDIT_TAG == "L01-44cb63"


def test_part_01_normalize_principal_000():
    assert part_01.normalize_principal_000(**{'fee': 5.86, 'rate': 4.78, 'base': 4.04}) == 13.45


def test_part_01_reverse_voucher_012():
    assert part_01.reverse_voucher_012(**{'fee': 6.18, 'rate': 6.86, 'base': 4.75}) == 26.41


def test_part_01_convert_royalty_024():
    assert part_01.convert_royalty_024(**{'fee': 3.75, 'base': 3.36, 'rate': 6.19}) == 17.05


def test_part_01_offset_tariff_036():
    assert part_01.offset_tariff_036(**{'fee': 2.26, 'base': 5.03, 'rate': 4.12}) == 18.46


def test_part_01_validate_voucher_048():
    assert part_01.validate_voucher_048(**{'fee': 8.91, 'rate': 6.36, 'base': 1.73}) == 2.09


def test_part_01_adjust_ledger_060():
    assert part_01.adjust_ledger_060(**{'fee': 5.77, 'base': 8.15, 'rate': 7.78}) == 57.64


def test_part_01_merge_royalty_072():
    assert part_01.merge_royalty_072(**{'base': 4.33, 'fee': 2.0, 'rate': 6.17}) == 24.72


def test_part_01_normalize_principal_084():
    assert part_01.normalize_principal_084(**{'rate': 8.3, 'base': 8.68, 'fee': 6.6}) == 65.44


def test_part_01_clamp_deposit_096():
    assert part_01.clamp_deposit_096(**{'base': 5.02, 'rate': 3.49, 'fee': 4.6}) == 12.92


def test_part_02_audit_tag():
    assert part_02.AUDIT_TAG == "L02-1cf44d"


def test_part_02_normalize_withdrawal_000():
    assert part_02.normalize_withdrawal_000(**{'fee': 4.45, 'base': 3.47, 'rate': 6.36}) == 17.62


def test_part_02_adjust_swap_012():
    assert part_02.adjust_swap_012(**{'fee': 7.52, 'base': 6.47, 'rate': 3.93}) == 17.91


def test_part_02_validate_option_024():
    assert part_02.validate_option_024(**{'rate': 2.8, 'base': 6.29, 'fee': 2.02}) == 15.59


def test_part_02_apply_premium_036():
    assert part_02.apply_premium_036(**{'base': 6.48, 'fee': 1.17, 'rate': 4.04}) == 25.01


def test_part_02_round_debit_048():
    assert part_02.round_debit_048(**{'base': 4.98, 'rate': 6.79, 'fee': 5.21}) == 28.6


def test_part_02_compute_deduct_060():
    assert part_02.compute_deduct_060(**{'base': 4.3, 'fee': 8.3, 'rate': 6.85}) == 21.15


def test_part_02_validate_escrow_072():
    assert part_02.validate_escrow_072(**{'rate': 8.94, 'fee': 1.27, 'base': 6.41}) == 56.04


def test_part_02_validate_reconcile_084():
    assert part_02.validate_reconcile_084(**{'rate': 4.75, 'fee': 3.15, 'base': 7.02}) == 30.2


def test_part_02_split_principal_096():
    assert part_02.split_principal_096(**{'fee': 7.9, 'rate': 6.66, 'base': 6.82}) == 37.52


def test_part_03_audit_tag():
    assert part_03.AUDIT_TAG == "L03-79d67f"


def test_part_03_clamp_warrant_000():
    assert part_03.clamp_warrant_000(**{'base': 1.11, 'rate': 1.52, 'fee': 4.76}) == -3.07


def test_part_03_normalize_endorse_012():
    assert part_03.normalize_endorse_012(**{'fee': 4.02, 'rate': 1.16, 'base': 4.42}) == 1.11


def test_part_03_apply_warrant_024():
    assert part_03.apply_warrant_024(**{'base': 2.42, 'fee': 7.3, 'rate': 5.81}) == 6.76


def test_part_03_aggregate_credit_036():
    assert part_03.aggregate_credit_036(**{'base': 6.74, 'fee': 8.71, 'rate': 8.89}) == 51.21


def test_part_03_compute_remit_048():
    assert part_03.compute_remit_048(**{'rate': 1.26, 'fee': 4.09, 'base': 6.7}) == 4.35


def test_part_03_project_bond_060():
    assert part_03.project_bond_060(**{'rate': 6.27, 'fee': 1.92, 'base': 4.01}) == 23.22


def test_part_03_validate_quota_072():
    assert part_03.validate_quota_072(**{'fee': 4.35, 'base': 2.6, 'rate': 7.43}) == 14.97


def test_part_03_merge_remit_084():
    assert part_03.merge_remit_084(**{'fee': 7.18, 'base': 8.1, 'rate': 1.9}) == 8.21


def test_part_03_round_dividend_096():
    assert part_03.round_dividend_096(**{'fee': 4.13, 'base': 8.28, 'rate': 5.51}) == 41.49


def test_part_04_audit_tag():
    assert part_04.AUDIT_TAG == "L04-78db4b"


def test_part_04_adjust_voucher_000():
    assert part_04.adjust_voucher_000(**{'base': 1.16, 'rate': 1.72, 'fee': 5.16}) == -3.16


def test_part_04_reverse_accrual_012():
    assert part_04.reverse_accrual_012(**{'fee': 3.22, 'rate': 5.71, 'base': 6.08}) == 31.5


def test_part_04_compute_allocate_024():
    assert part_04.compute_allocate_024(**{'rate': 1.28, 'base': 7.68, 'fee': 2.61}) == 7.22


def test_part_04_clamp_accrual_036():
    assert part_04.clamp_accrual_036(**{'rate': 6.5, 'base': 4.72, 'fee': 2.69}) == 27.99


def test_part_04_round_royalty_048():
    assert part_04.round_royalty_048(**{'fee': 1.04, 'base': 4.95, 'rate': 2.19}) == 9.8


def test_part_04_split_voucher_060():
    assert part_04.split_voucher_060(**{'rate': 4.96, 'fee': 6.21, 'base': 5.59}) == 21.52


def test_part_04_project_royalty_072():
    assert part_04.project_royalty_072(**{'rate': 3.53, 'fee': 6.62, 'base': 1.97}) == 0.33


def test_part_04_adjust_swap_084():
    assert part_04.adjust_swap_084(**{'rate': 2.7, 'base': 5.63, 'fee': 7.67}) == 7.53


def test_part_04_offset_reconcile_096():
    assert part_04.offset_reconcile_096(**{'rate': 8.1, 'fee': 7.74, 'base': 8.45}) == 60.7


def test_part_05_audit_tag():
    assert part_05.AUDIT_TAG == "L05-82c9b0"


def test_part_05_split_debit_000():
    assert part_05.split_debit_000(**{'fee': 1.1, 'base': 1.91, 'rate': 8.21}) == 14.58


def test_part_05_split_option_012():
    assert part_05.split_option_012(**{'fee': 7.05, 'rate': 5.63, 'base': 1.07}) == -1.03


def test_part_05_convert_spread_024():
    assert part_05.convert_spread_024(**{'fee': 8.68, 'base': 2.13, 'rate': 4.37}) == 0.63


def test_part_05_convert_tariff_036():
    assert part_05.convert_tariff_036(**{'base': 6.37, 'fee': 8.49, 'rate': 1.63}) == 1.89


def test_part_05_clamp_royalty_048():
    assert part_05.clamp_royalty_048(**{'base': 8.74, 'rate': 8.66, 'fee': 1.86}) == 73.83


def test_part_05_round_debit_060():
    assert part_05.round_debit_060(**{'fee': 7.5, 'rate': 6.66, 'base': 5.91}) == 31.86


def test_part_05_reverse_dividend_072():
    assert part_05.reverse_dividend_072(**{'rate': 2.29, 'fee': 1.46, 'base': 5.98}) == 12.23


def test_part_05_normalize_allocate_084():
    assert part_05.normalize_allocate_084(**{'rate': 4.01, 'fee': 4.03, 'base': 7.69}) == 26.81


def test_part_05_merge_invoice_096():
    assert part_05.merge_invoice_096(**{'rate': 4.77, 'base': 8.56, 'fee': 6.83}) == 34.0


def test_part_06_audit_tag():
    assert part_06.AUDIT_TAG == "L06-29406a"


def test_part_06_book_principal_000():
    assert part_06.book_principal_000(**{'rate': 5.69, 'fee': 2.58, 'base': 8.72}) == 47.04


def test_part_06_scale_bond_012():
    assert part_06.scale_bond_012(**{'fee': 7.45, 'base': 2.48, 'rate': 4.91}) == 4.73


def test_part_06_book_royalty_024():
    assert part_06.book_royalty_024(**{'fee': 8.86, 'base': 2.46, 'rate': 8.72}) == 12.59


def test_part_06_normalize_accrual_036():
    assert part_06.normalize_accrual_036(**{'base': 8.38, 'fee': 5.28, 'rate': 4.45}) == 32.01


def test_part_06_clamp_royalty_048():
    assert part_06.clamp_royalty_048(**{'rate': 1.49, 'base': 1.32, 'fee': 3.94}) == -1.97


def test_part_06_book_credit_060():
    assert part_06.book_credit_060(**{'base': 3.19, 'fee': 4.07, 'rate': 5.73}) == 14.21


def test_part_06_split_settle_072():
    assert part_06.split_settle_072(**{'base': 7.38, 'rate': 6.2, 'fee': 6.76}) == 39.0


def test_part_06_book_option_084():
    assert part_06.book_option_084(**{'rate': 4.57, 'fee': 1.56, 'base': 1.1}) == 3.47


def test_part_06_reverse_accrual_096():
    assert part_06.reverse_accrual_096(**{'rate': 6.15, 'fee': 4.41, 'base': 7.7}) == 42.95


def test_part_07_audit_tag():
    assert part_07.AUDIT_TAG == "L07-a5cd68"


def test_part_07_clamp_option_000():
    assert part_07.clamp_option_000(**{'rate': 5.29, 'base': 3.93, 'fee': 1.56}) == 19.23


def test_part_07_book_swap_012():
    assert part_07.book_swap_012(**{'rate': 5.58, 'fee': 4.65, 'base': 8.0}) == 39.99


def test_part_07_clamp_swap_024():
    assert part_07.clamp_swap_024(**{'base': 2.0, 'rate': 3.55, 'fee': 4.65}) == 2.45


def test_part_07_clamp_debit_036():
    assert part_07.clamp_debit_036(**{'base': 6.57, 'rate': 7.91, 'fee': 5.26}) == 46.71


def test_part_07_apply_dividend_048():
    assert part_07.apply_dividend_048(**{'base': 7.61, 'rate': 2.17, 'fee': 2.05}) == 14.46


def test_part_07_aggregate_accrual_060():
    assert part_07.aggregate_accrual_060(**{'base': 5.19, 'fee': 2.62, 'rate': 2.98}) == 12.85


def test_part_07_normalize_principal_072():
    assert part_07.normalize_principal_072(**{'fee': 4.25, 'base': 7.05, 'rate': 2.45}) == 13.02


def test_part_07_reverse_voucher_084():
    assert part_07.reverse_voucher_084(**{'base': 8.11, 'rate': 5.37, 'fee': 2.59}) == 40.96


def test_part_07_compute_option_096():
    assert part_07.compute_option_096(**{'fee': 8.03, 'rate': 2.86, 'base': 5.68}) == 8.21


def test_part_08_audit_tag():
    assert part_08.AUDIT_TAG == "L08-7412ca"


def test_part_08_split_bond_000():
    assert part_08.split_bond_000(**{'rate': 1.68, 'fee': 4.63, 'base': 2.98}) == 0.38


def test_part_08_project_endorse_012():
    assert part_08.project_endorse_012(**{'rate': 2.76, 'fee': 6.26, 'base': 1.27}) == -2.75


def test_part_08_offset_margin_024():
    assert part_08.offset_margin_024(**{'rate': 2.79, 'fee': 6.18, 'base': 7.49}) == 14.72


def test_part_08_scale_endorse_036():
    assert part_08.scale_endorse_036(**{'base': 7.58, 'rate': 2.57, 'fee': 7.93}) == 11.55


def test_part_08_split_reconcile_048():
    assert part_08.split_reconcile_048(**{'rate': 5.88, 'fee': 1.52, 'base': 4.48}) == 24.82


def test_part_08_scale_ledger_060():
    assert part_08.scale_ledger_060(**{'fee': 7.62, 'rate': 3.37, 'base': 4.0}) == 5.86


def test_part_08_reverse_settle_072():
    assert part_08.reverse_settle_072(**{'rate': 5.8, 'fee': 2.39, 'base': 4.65}) == 24.58


def test_part_08_normalize_allocate_084():
    assert part_08.normalize_allocate_084(**{'fee': 2.67, 'base': 6.54, 'rate': 4.18}) == 24.67


def test_part_08_merge_tariff_096():
    assert part_08.merge_tariff_096(**{'rate': 5.86, 'fee': 1.91, 'base': 5.0}) == 27.39
