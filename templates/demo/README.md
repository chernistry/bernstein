# Demo Flask App

A minimal Flask web application created by `bernstein demo`.
It ships with four intentional bugs that Bernstein agents fix during the demo.

## Bugs (pre-fix)

| # | Location | Bug |
|---|----------|-----|
| 1 | `app.py` `get_item()` | Off-by-one — `ITEMS[n]` should be `ITEMS[n - 1]` |
| 2 | `app.py` imports | `request` used in `/echo` but not imported |
| 3 | `app.py` `health()` | Returns HTTP 201 instead of 200 |
| 4 | `tests/test_app.py` | `test_hello_returns_200` asserts `status_code == 404` |

## Running

```bash
pip install -r requirements.txt
python app.py
```

## Testing

```bash
pytest tests/ -q
```

Run `pytest tests/ -q` before and after `bernstein demo` to see all four
tests go from failing to passing.
