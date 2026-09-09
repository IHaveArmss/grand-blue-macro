#!/usr/bin/env python3
"""Grand Blue Macro - Main Executable Entrypoint

Usage:
  ./main.py              # Launch the 3-Menu Desktop GUI (Default)
  ./main.py --cli        # Launch in headless console / CLI mode
  ./main.py --calibrate  # Launch screen calibration & live detector tester
  ./main.py --test       # Run automated unit tests against test assets
"""

import sys
import os

# Ensure project root is on python module path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Auto-switch to .venv if available and not already in a virtualenv
if sys.platform == "win32":
    venv_python = os.path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe")
else:
    venv_python = os.path.join(PROJECT_ROOT, ".venv", "bin", "python3")

if os.path.exists(venv_python) and sys.prefix == sys.base_prefix:
    os.execv(venv_python, [venv_python] + sys.argv)


def load_dotenv(dotenv_path=None):
    """Load key-value pairs from .env into os.environ."""
    path = dotenv_path or os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("\"'")
                        if k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass


load_dotenv()


def run_gui():
    try:
        from src.gui import main as gui_main
        gui_main()
    except (ImportError, ModuleNotFoundError) as e:
        print(f"[NOTE] GUI framework (PyGObject/GTK3) is not available: {e}")
        print("[NOTE] Automatically falling back to CLI mode...\n")
        run_cli()


def run_cli():
    import json
    import time
    import signal
    from src.screen import ScreenCapture
    from src.input_manager import InputManager
    from src.detector import GrandBlueDetector
    from src.fishing_bot import FishingBot
    from src.calibrate import main as run_calib

    config_path = os.path.join(PROJECT_ROOT, "config.json")
    pictures_dir = os.path.join(PROJECT_ROOT, "pictures")

    with open(config_path, "r") as f:
        config = json.load(f)

    print("==================================================")
    print("           Grand Blue Fishing Macro (CLI)         ")
    print("==================================================")
    hotkeys = config.get("hotkeys", {})
    hk_toggle = hotkeys.get("toggle_macro", "F6")
    hk_calib = hotkeys.get("calibrate", "F7")
    hk_quit = hotkeys.get("quit", "F8")

    rod_slot = config.get("fishing", {}).get("rod_slot", "9")
    print(f"Rod Hotbar Slot: [{rod_slot}]")
    print(f"Hotkeys: [{hk_toggle}] Start/Stop  |  [{hk_calib}] Calibrate  |  [{hk_quit}] Quit")
    print("==================================================")

    screen = ScreenCapture()
    input_mgr = InputManager()
    detector = GrandBlueDetector(pictures_dir)

    def on_status(state, data):
        catches = data.get("catches", 0)
        fish_st = data.get("fish_state", "")
        extra = f" | Fish: {fish_st}" if fish_st else ""
        sys.stdout.write(f"\r[STATUS: {state:<14}] Catches: {catches:<4}{extra:<20}    ")
        sys.stdout.flush()

    bot = FishingBot(screen, input_mgr, detector, config, status_callback=on_status)

    def toggle_action():
        if bot.is_running:
            print("\n[Macro] Stopping bot...")
            bot.stop()
        else:
            print(f"\n[Macro] Starting bot on slot {config.get('fishing', {}).get('rod_slot', '9')}...")
            bot.start()

    def calib_action():
        print("\n[Macro] Running calibration...")
        run_calib()

    def quit_action():
        print("\n[Macro] Quitting...")
        bot.stop()
        input_mgr.stop_hotkey_listener()
        os._exit(0)

    input_mgr.register_hotkey(hk_toggle, toggle_action)
    input_mgr.register_hotkey(hk_calib, calib_action)
    input_mgr.register_hotkey(hk_quit, quit_action)
    input_mgr.start_hotkey_listener()

    print(f"\nMacro initialized. Press {hk_toggle} to START/STOP anytime.")

    def sig_handler(sig, frame):
        quit_action()

    signal.signal(signal.SIGINT, sig_handler)

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        quit_action()


def run_calibrate():
    from src.calibrate import main as calib_main
    calib_main()


def run_tests():
    import unittest
    loader = unittest.TestLoader()
    suite = loader.discover(os.path.join(PROJECT_ROOT, "tests"))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return

    if "--cli" in sys.argv:
        run_cli()
    elif "--calibrate" in sys.argv or "-c" in sys.argv:
        run_calibrate()
    elif "--test" in sys.argv or "-t" in sys.argv:
        run_tests()
    else:
        # Default action: launch the GUI!
        run_gui()


if __name__ == "__main__":
    main()
