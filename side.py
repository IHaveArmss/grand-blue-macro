#!/usr/bin/env python3
import time
import threading
from pynput import keyboard
from evdev import UInput, ecodes as e

# Keys to press: 1, 2, 3, 4, 5, 6
KEYS_TO_PRESS = [
    e.KEY_1,
    e.KEY_2,
    e.KEY_3,
    e.KEY_4,
    e.KEY_5,
    e.KEY_6,
    e.KEY_7,
]

# Create virtual hardware keyboard for Roblox injection
ui = UInput({e.EV_KEY: KEYS_TO_PRESS}, name="Roblox-Macro-Keyboard")
running = False

def macro_worker():
    while True:
        if running:
            for key in KEYS_TO_PRESS:
                if not running:
                    break
                # Press down
                ui.write(e.EV_KEY, key, 1)
                ui.syn()
                time.sleep(0.06)  # Game tick registration hold

                # Release
                ui.write(e.EV_KEY, key, 0)
                ui.syn()
                time.sleep(0.06)

            time.sleep(0.2)  # Delay between full 1-6 loops
        else:
            time.sleep(0.05)

def on_press(key):
    global running
    if key == keyboard.Key.f6:
        running = not running
        state = "RUNNING" if running else "STOPPED"
        print(f"[{state}] Macro toggled.")

# Start background macro loop
t = threading.Thread(target=macro_worker, daemon=True)
t.start()

print("Listening for F6 via X11... Press Ctrl+C in terminal to exit.")

with keyboard.Listener(on_press=on_press) as listener:
    try:
        listener.join()
    except KeyboardInterrupt:
        print("\nExiting.")
    finally:
        ui.close()