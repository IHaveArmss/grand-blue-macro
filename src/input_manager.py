"""Input simulation and global hotkey manager for Linux X11."""

import time
import threading
from typing import Callable, Dict, Optional
from Xlib import display, X, XK
from Xlib.ext import xtest


class InputManager:
    def __init__(self, display_name: Optional[str] = None):
        self.d = display.Display(display_name)
        self.screen = self.d.screen()
        self.root = self.screen.root
        self._is_m1_down = False
        self._hotkey_callbacks: Dict[int, Callable[[], None]] = {}
        self._hotkey_thread: Optional[threading.Thread] = None
        self._running_hotkeys = False
        self._hotkey_display: Optional[display.Display] = None

    # --- Mouse Controls ---

    def mouse_move(self, x: int, y: int, jitter: bool = False):
        """Move cursor to (x, y). If jitter is True, emits a 1px motion delta to trigger RawInput/Sober hover states."""
        if jitter:
            xtest.fake_input(self.d, X.MotionNotify, x=int(x) + 1, y=int(y))
        xtest.fake_input(self.d, X.MotionNotify, x=int(x), y=int(y))
        self.d.sync()

    def mouse_down(self, button: int = 1):
        """Press mouse button (1 = Left / M1)."""
        if button == 1:
            self._is_m1_down = True
        xtest.fake_input(self.d, X.ButtonPress, button)
        self.d.sync()

    def mouse_up(self, button: int = 1):
        """Release mouse button (1 = Left / M1)."""
        if button == 1:
            self._is_m1_down = False
        xtest.fake_input(self.d, X.ButtonRelease, button)
        self.d.sync()

    def mouse_click(self, x: Optional[int] = None, y: Optional[int] = None, button: int = 1, delay: float = 0.04):
        """Click mouse button at current position or optional (x, y), triggering movement event."""
        if x is not None and y is not None:
            self.mouse_move(x, y, jitter=True)
            time.sleep(0.015)
        self.mouse_down(button)
        time.sleep(delay)
        self.mouse_up(button)

    @property
    def is_m1_down(self) -> bool:
        return self._is_m1_down

    def release_all(self):
        """Emergency release of mouse button if currently held."""
        if self._is_m1_down:
            self.mouse_up(1)

    # --- Keyboard Controls ---

    def _get_keycode(self, key_str: str) -> int:
        keysym = XK.string_to_keysym(key_str)
        if keysym == 0:
            # Fallback for uppercase/lowercase
            keysym = XK.string_to_keysym(key_str.upper())
        keycode = self.d.keysym_to_keycode(keysym)
        return keycode

    def press_key(self, key_str: str):
        """Synthesize KeyPress."""
        kc = self._get_keycode(key_str)
        if kc:
            xtest.fake_input(self.d, X.KeyPress, kc)
            self.d.sync()

    def release_key(self, key_str: str):
        """Synthesize KeyRelease."""
        kc = self._get_keycode(key_str)
        if kc:
            xtest.fake_input(self.d, X.KeyRelease, kc)
            self.d.sync()

    def tap_key(self, key_str: str, delay: float = 0.05):
        """Tap key down and up."""
        kc = self._get_keycode(key_str)
        if kc:
            xtest.fake_input(self.d, X.KeyPress, kc)
            self.d.sync()
            time.sleep(delay)
            xtest.fake_input(self.d, X.KeyRelease, kc)
            self.d.sync()

    # --- Global Hotkey Listener ---

    def register_hotkey(self, key_name: str, callback: Callable[[], None]):
        """Register a callback for a global hotkey (e.g. 'F6', 'F7', 'F8')."""
        kc = self._get_keycode(key_name)
        if kc:
            self._hotkey_callbacks[kc] = callback

    def start_hotkey_listener(self):
        """Start listening for registered hotkeys in a dedicated background thread."""
        if self._running_hotkeys:
            return
        self._running_hotkeys = True
        self._hotkey_thread = threading.Thread(target=self._hotkey_loop, daemon=True)
        self._hotkey_thread.start()

    def stop_hotkey_listener(self):
        """Stop the background hotkey listener."""
        self._running_hotkeys = False
        if self._hotkey_display:
            try:
                # Ungrab keys
                root = self._hotkey_display.screen().root
                for kc in self._hotkey_callbacks.keys():
                    root.ungrab_key(kc, X.AnyModifier)
                self._hotkey_display.close()
            except Exception:
                pass
            self._hotkey_display = None

    def _hotkey_loop(self):
        try:
            self._hotkey_display = display.Display()
            root = self._hotkey_display.screen().root
            # Grab registered keys on the separate display connection
            for kc in self._hotkey_callbacks.keys():
                root.grab_key(kc, X.AnyModifier, True, X.GrabModeAsync, X.GrabModeAsync)

            while self._running_hotkeys:
                # Check for events with timeout to allow graceful exit
                if self._hotkey_display.pending_events() > 0:
                    event = self._hotkey_display.next_event()
                    if event.type == X.KeyPress:
                        cb = self._hotkey_callbacks.get(event.detail)
                        if cb:
                            # Run callback in worker thread to prevent blocking event loop
                            threading.Thread(target=cb, daemon=True).start()
                else:
                    time.sleep(0.02)
        except Exception as e:
            print(f"[InputManager] Hotkey listener error: {e}")
        finally:
            self._running_hotkeys = False


if __name__ == "__main__":
    im = InputManager()
    print("Testing InputManager initialized successfully.")
