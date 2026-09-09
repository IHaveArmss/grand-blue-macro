"""Input simulation and global hotkey manager (cross-platform Linux X11 and Windows)."""

import sys
import time
import threading
from typing import Callable, Dict, Optional

if sys.platform != "win32":
    try:
        from Xlib import display, X, XK
        from Xlib.ext import xtest
    except ImportError:
        display = None
        X = None
        XK = None
        xtest = None


class LinuxInputManager:
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


class WindowsInputManager:
    """High-speed native input simulation and global hotkey manager for Windows using Win32 API."""

    def __init__(self, display_name: Optional[str] = None):
        import ctypes
        self.user32 = ctypes.windll.user32
        self._is_m1_down = False
        self._hotkey_callbacks: Dict[int, Callable[[], None]] = {}
        self._hotkey_thread: Optional[threading.Thread] = None
        self._running_hotkeys = False
        self._hotkey_id_map: Dict[int, int] = {}  # id -> vk_code

        # Win32 input flags
        self.MOUSEEVENTF_LEFTDOWN = 0x0002
        self.MOUSEEVENTF_LEFTUP = 0x0004
        self.KEYEVENTF_KEYUP = 0x0002

    def mouse_move(self, x: int, y: int, jitter: bool = False):
        if jitter:
            self.user32.SetCursorPos(int(x) + 1, int(y))
            time.sleep(0.001)
        self.user32.SetCursorPos(int(x), int(y))

    def mouse_down(self, button: int = 1):
        if button == 1:
            self._is_m1_down = True
            self.user32.mouse_event(self.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        elif button == 2:
            self.user32.mouse_event(0x0008, 0, 0, 0, 0)  # RIGHTDOWN

    def mouse_up(self, button: int = 1):
        if button == 1:
            self._is_m1_down = False
            self.user32.mouse_event(self.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        elif button == 2:
            self.user32.mouse_event(0x0010, 0, 0, 0, 0)  # RIGHTUP

    def mouse_click(self, x: Optional[int] = None, y: Optional[int] = None, button: int = 1, delay: float = 0.04):
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
        if self._is_m1_down:
            self.mouse_up(1)

    def _get_vk_code(self, key_str: str) -> int:
        k = key_str.upper().strip()
        # Function keys F1-F12
        if k.startswith("F") and k[1:].isdigit():
            f_num = int(k[1:])
            if 1 <= f_num <= 12:
                return 0x70 + (f_num - 1)  # VK_F1 is 0x70
        # Digits '0'-'9' (ASCII / VK code 0x30..0x39)
        if len(k) == 1 and k.isdigit():
            return 0x30 + int(k)
        # Letters 'A'-'Z'
        if len(k) == 1 and k.isalpha():
            return ord(k)
        # Defaults
        vk_map = {
            "SPACE": 0x20, "ENTER": 0x0D, "RETURN": 0x0D, "ESC": 0x1B, "ESCAPE": 0x1B,
            "TAB": 0x09, "SHIFT": 0x10, "CTRL": 0x11, "ALT": 0x12
        }
        return vk_map.get(k, 0)

    def press_key(self, key_str: str):
        vk = self._get_vk_code(key_str)
        if vk:
            self.user32.keybd_event(vk, 0, 0, 0)

    def release_key(self, key_str: str):
        vk = self._get_vk_code(key_str)
        if vk:
            self.user32.keybd_event(vk, 0, self.KEYEVENTF_KEYUP, 0)

    def tap_key(self, key_str: str, delay: float = 0.05):
        vk = self._get_vk_code(key_str)
        if vk:
            self.user32.keybd_event(vk, 0, 0, 0)
            time.sleep(delay)
            self.user32.keybd_event(vk, 0, self.KEYEVENTF_KEYUP, 0)

    def register_hotkey(self, key_name: str, callback: Callable[[], None]):
        vk = self._get_vk_code(key_name)
        if vk:
            self._hotkey_callbacks[vk] = callback

    def start_hotkey_listener(self):
        if self._running_hotkeys:
            return
        self._running_hotkeys = True
        self._hotkey_thread = threading.Thread(target=self._hotkey_loop, daemon=True)
        self._hotkey_thread.start()

    def stop_hotkey_listener(self):
        self._running_hotkeys = False
        try:
            for hk_id in list(self._hotkey_id_map.keys()):
                self.user32.UnregisterHotKey(None, hk_id)
        except Exception:
            pass
        self._hotkey_id_map.clear()

    def _hotkey_loop(self):
        import ctypes
        from ctypes import wintypes
        try:
            hk_id = 1
            for vk in self._hotkey_callbacks.keys():
                # 0x4000 = MOD_NOREPEAT
                res = self.user32.RegisterHotKey(None, hk_id, 0x4000, vk)
                if not res:
                    res = self.user32.RegisterHotKey(None, hk_id, 0, vk)
                self._hotkey_id_map[hk_id] = vk
                hk_id += 1

            msg = wintypes.MSG()
            while self._running_hotkeys:
                if self.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE = 1
                    if msg.message == 0x0312:  # WM_HOTKEY
                        triggered_id = msg.wParam
                        vk = self._hotkey_id_map.get(triggered_id)
                        if vk:
                            cb = self._hotkey_callbacks.get(vk)
                            if cb:
                                threading.Thread(target=cb, daemon=True).start()
                    self.user32.TranslateMessage(ctypes.byref(msg))
                    self.user32.DispatchMessageW(ctypes.byref(msg))
                else:
                    time.sleep(0.015)
        except Exception as e:
            print(f"[InputManager] Hotkey listener error: {e}")
        finally:
            self.stop_hotkey_listener()


# Export platform-appropriate InputManager class
if sys.platform == "win32":
    InputManager = WindowsInputManager
else:
    InputManager = LinuxInputManager


if __name__ == "__main__":
    im = InputManager()
    print("Testing InputManager initialized successfully.")
