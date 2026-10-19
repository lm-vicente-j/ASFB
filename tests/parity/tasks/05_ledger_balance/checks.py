from ledger import LedgerEntry, running_balance


def test_credits_and_debits_accumulate():
    entries = [
        LedgerEntry("credit", 500),
        LedgerEntry("debit", 200),
        LedgerEntry("credit", 50),
    ]
    assert running_balance(entries) == [500, 300, 350]


def test_empty_ledger_gives_empty_list():
    assert running_balance([]) == []


def test_balance_may_go_negative():
    assert running_balance([LedgerEntry("debit", 100)]) == [-100]


def test_single_credit():
    assert running_balance([LedgerEntry("credit", 1)]) == [1]


def test_unknown_kind_raises_value_error():
    entries = [LedgerEntry("credit", 10), LedgerEntry("transfer", 5)]
    try:
        running_balance(entries)
    except ValueError:
        return
    assert False, "expected ValueError for unknown kind"
