"""SM-06: the rule evaluator refuses memory/CPU blow-ups and never raises past
the catalog boundary."""
from __future__ import annotations

import pytest

from app.model import catalog
from app.model.catalog import HardwareCatalog, _SafeExprError, safe_eval_bool


@pytest.mark.parametrize("expr", [
    "'a' * 300000000 == ''",
    "[0] * 300000000 == []",
    "300000000 * 'a' == ''",
    "x * 2 == ''",  # x is a long string below
    "'%999999999d' % 1 == ''",
    "'ab' * 'cd' == ''",
    "'a' * 1.5 == ''",
    "1000000000000 * 1000000000000 * 1000000000000 * 1000000000000 * 1000000000000 > 0",
    "1 + " * 60 + "1 > 0",          # node cap
    "x == '" + "a" * 600 + "'",     # length cap
])
def test_resource_abuse_is_rejected(expr):
    with pytest.raises(_SafeExprError):
        safe_eval_bool(expr, {"x": "a" * 6000})


def test_normal_rules_still_work():
    names = {"ram_total_gb": 0, "storage_count": 3, "tags": ["a", "b"]}
    assert safe_eval_bool("ram_total_gb == 0 and storage_count > 2", names)
    assert safe_eval_bool("storage_count * 4 + 1 == 13", names)
    assert safe_eval_bool("'a' in tags", names)
    assert safe_eval_bool("'ab' * 3 == 'ababab'", names)
    assert not safe_eval_bool("tags + ['c'] == ['a']", names)


def test_malformed_profile_never_raises_out_of_validate():
    cat = HardwareCatalog({"combination_rules": [
        {"id": "r", "rule": "ram_total_gb == 0", "message": "m"}]})
    # non-dict module items and a non-numeric count used to raise outside the try (500).
    assert cat.validate_hardware("vm", {"ram_modules": ["x"], "storage_disks": [{"count": "n"}]}) == []
    assert catalog.MAX_EXPR_LENGTH >= 100
