# grand-blue-macro

Automated fishing, mining, and skill gain macro utility for **Grand Blue** on Linux (X11).

## Setup & Environment

A virtual environment is already prepared at `.venv/`.
All library dependencies are documented in `requirements.txt`:
```bash
# Optional manual venv activation:
source .venv/bin/activate
```

*(Note: `./main.py` will also automatically detect and use `.venv/bin/python3` if executed without manual activation!)*

## Quick Start (Exposed Main Launcher)
You can directly run the main executable from the root directory:
```bash
./main.py              # Opens the 3-Menu Desktop GUI (Default)
```
or:
```bash
python3 main.py        # Opens the 3-Menu Desktop GUI (Default)
python3 main.py --cli  # Headless console CLI
python3 main.py -c     # Calibration tool
python3 main.py -t     # Run automated test suite
```

## Features & Menus
1. 🎣 **Fishing Macro**: Full 3-stage automated fishing (Hotbar rod verification, cast meter top-detection, shake button clicker, and red/green dual-state reeling tracker).
2. ⛏️ **Mining Macro**: Placeholder tab, ready for future expansion.
3. ⚔️ **Skill Macroing**: Placeholder tab, ready for future expansion.

For detailed architecture, configuration reference, and mechanics, see [fish.md](fish.md).
