# Personal Assistant

Talk to Claude Code by voice. Claude can see your screen and write or draw on it to explain things.
Works on Windows 11 and macOS 14 or later.

> Under construction. See [PLAN.md](PLAN.md) for the build order and [SPEC.md](SPEC.md) for what the app does.

## Development

You need Python 3.11.

```
python -m venv .venv
.venv\Scripts\activate                # macOS: source .venv/bin/activate
pip install -e ".[dev]"
```

| Check | Command |
|---|---|
| Tests | `pytest` |
| Lint | `ruff check .` |
| Types | `mypy src` |

CI runs all three on Windows and macOS for every push and pull request.

## Branches

- `feature/<component>`: work in progress.
- `Dev`: finished, tested components.
- `main`: public releases.
