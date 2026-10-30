# tally

Small bookkeeping helpers for a shop inventory: money parsing and
formatting, SKU slugs, an in-memory inventory and plain-text reports.
Standard library only.

## Install

```
pip install tally==0.3.0
```

## Usage

```python
from tally import Inventory, format_cents

inv = Inventory()
inv.add("mug-blue", "Blue Mug", 1250, qty=4)
print(format_cents(5000))  # $50.00
```

## Running the tests

```
python -I -m pytest -q -p no:cacheprovider --noconftest -c tests/pytest.ini tests/test_money.py
```
