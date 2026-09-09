import os
import sys
import time
import json

try:
    from .screen import ScreenCapture
    from .detector import GrandBlueDetector
except ImportError:
    from screen import ScreenCapture
    from detector import GrandBlueDetector

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PICTURES_DIR = os.path.join(BASE_DIR, "pictures")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")


def main():
    print("==================================================")
    print("   Grand Blue Macro - Interactive Calibration     ")
    print("==================================================")

    sc = ScreenCapture()
    detector = GrandBlueDetector(PICTURES_DIR)
    sw, sh = sc.screen_size
    print(f"[Screen] Virtual desktop dimensions: {sw}x{sh}")

    # Step 1: Detect Window
    print("\n[Step 1] Searching for game window ('Roblox' / 'Sober')...")
    win_geom = sc.find_window(["Roblox", "Sober"])
    if win_geom:
        gx, gy, gw, gh = win_geom
        print(f" -> Found Game Window: x={gx}, y={gy}, width={gw}, height={gh}")
    else:
        print(" -> No window with 'Roblox' or 'Sober' in title found.")
        print(" -> Checking active window...")
        active = sc.get_active_window()
        if active:
            gx, gy, gw, gh = active
            print(f" -> Active window geometry: x={gx}, y={gy}, width={gw}, height={gh}")
        else:
            gx, gy, gw, gh = (0, 0, sw, sh)
            print(f" -> Defaulting to full desktop: x={gx}, y={gy}, width={gw}, height={gh}")

    # Step 2: Test live capture
    print("\n[Step 2] Capturing game region...")
    t0 = time.time()
    img = sc.capture_roi(gx, gy, gw, gh)
    dt = (time.time() - t0) * 1000
    print(f" -> Capture completed in {dt:.2f} ms")

    # Step 3: Run live detection checks
    print("\n[Step 3] Testing live detectors against current screen:")

    # Shake check
    shake_pt = detector.find_shake_button(img)
    if shake_pt:
        print(f" -> SHAKE Button detected at: ({gx + shake_pt[0]}, {gy + shake_pt[1]})")
    else:
        print(" -> No SHAKE button detected on screen currently.")

    # Reel bar check
    bar_roi = sc.capture_roi(gx + int(0.15 * gw), gy + int(0.40 * gh), int(0.70 * gw), int(0.45 * gh))
    reel_state = detector.analyze_reel_game(bar_roi)
    if reel_state.is_active:
        color_str = "GREEN (overlapping)" if reel_state.is_green else "RED (outside slider)"
        print(f" -> Reel Minigame ACTIVE! Fish status: {color_str}")
        print(f"    Fish X: {reel_state.fish_x}, Slider Center X: {reel_state.slider_center_x}")
    else:
        print(" -> Reel minigame bar not detected currently.")

    # Hotbar equipped check
    try:
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}

    slot = cfg.get("fishing", {}).get("rod_slot", "9")
    hotbar_img = sc.capture_roi(gx, gy, gw, int(0.20 * gh))
    is_eq = detector.is_slot_equipped(hotbar_img, slot)
    print(f" -> Hotbar Slot {slot} equipped check: {'EQUIPPED (White border detected)' if is_eq else 'NOT equipped'}")

    print("\n==================================================")
    print("Calibration test complete!")
    print("To start the macro, run: python3 gui.py (or python3 main.py)")
    print("==================================================")


if __name__ == "__main__":
    main()
