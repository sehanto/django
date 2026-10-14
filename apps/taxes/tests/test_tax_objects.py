"""The generated tax object tables (``apps/taxes/tax_objects.py``)."""

import ast
from pathlib import Path

from apps.taxes import tax_objects


def test_no_table_repeats_a_code():
    """A repeated key would silently replace the first row; DJP's converters use the first."""
    tree = ast.parse(Path(tax_objects.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            codes = [key.value for key in node.keys if isinstance(key, ast.Constant)]
            assert len(codes) == len(set(codes)), sorted({c for c in codes if codes.count(c) > 1})


def test_self_deposits_carry_the_self_deposit_names():
    assert tax_objects.SELF_DEPOSIT["28-409-22"][0].endswith("(Disetor Sendiri)")
    assert tax_objects.SELF_DEPOSIT["28-409-10"][0].endswith("yang Disetor Sendiri")
