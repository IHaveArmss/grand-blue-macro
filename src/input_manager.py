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
        # Ensure consistent DPI scaling with WindowsScreenCapture
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

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
            scan = self.user32.MapVirtualKeyW(vk, 0)
            self.user32.keybd_event(vk, scan, 0, 0)

    def release_key(self, key_str: str):
        vk = self._get_vk_code(key_str)
        if vk:
            scan = self.user32.MapVirtualKeyW(vk, 0)
            self.user32.keybd_event(vk, scan, self.KEYEVENTF_KEYUP, 0)

    def tap_key(self, key_str: str, delay: float = 0.05):
        vk = self._get_vk_code(key_str)
        if vk:
            scan = self.user32.MapVirtualKeyW(vk, 0)
            self.user32.keybd_event(vk, scan, 0, 0)
            time.sleep(delay)
            self.user32.keybd_event(vk, scan, self.KEYEVENTF_KEYUP, 0)

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


class MacOSInputManager:
    """High-speed native input simulation and global hotkey manager for macOS using pynput."""

    def __init__(self, display_name: Optional[str] = None):
        from pynput.mouse import Controller as MouseController, Button
        from pynput.keyboard import Controller as KeyboardController, Key
        self.mouse = MouseController()
        self.keyboard = KeyboardController()
        self.Button = Button
        self.Key = Key

        self._is_m1_down = False
        self._hotkey_callbacks: Dict[str, Callable[[], None]] = {}
        self._hotkey_listener = None
        self._running_hotkeys = False

    def mouse_move(self, x: int, y: int, jitter: bool = False):
        if jitter:
            self.mouse.position = (int(x) + 1, int(y))
            time.sleep(0.001)
        self.mouse.position = (int(x), int(y))

    def mouse_down(self, button: int = 1):
        if button == 1:
            self._is_m1_down = True
            self.mouse.press(self.Button.left)
        elif button == 2:
            self.mouse.press(self.Button.right)

    def mouse_up(self, button: int = 1):
        if button == 1:
            self._is_m1_down = False
            self.mouse.release(self.Button.left)
        elif button == 2:
            self.mouse.release(self.Button.right)

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

    def _get_key_obj(self, key_str: str):
        k = key_str.upper().strip()
        if k.startswith("F") and k[1:].isdigit():
            f_num = int(k[1:])
            f_attr = f"f{f_num}"
            if hasattr(self.Key, f_attr):
                return getattr(self.Key, f_attr)
        special_map = {
            "SPACE": self.Key.space, "ENTER": self.Key.enter, "RETURN": self.Key.enter,
            "ESC": self.Key.esc, "ESCAPE": self.Key.esc, "TAB": self.Key.tab,
            "SHIFT": self.Key.shift, "CTRL": self.Key.ctrl, "ALT": self.Key.alt
        }
        if k in special_map:
            return special_map[k]
        if len(key_str) == 1:
            return key_str.lower()
        return None

    def press_key(self, key_str: str):
        key_obj = self._get_key_obj(key_str)
        if key_obj:
            self.keyboard.press(key_obj)

    def release_key(self, key_str: str):
        key_obj = self._get_key_obj(key_str)
        if key_obj:
            self.keyboard.release(key_obj)

    def tap_key(self, key_str: str, delay: float = 0.05):
        key_obj = self._get_key_obj(key_str)
        if key_obj:
            self.keyboard.press(key_obj)
            time.sleep(delay)
            self.keyboard.release(key_obj)

    def register_hotkey(self, key_name: str, callback: Callable[[], None]):
        self._hotkey_callbacks[key_name.upper().strip()] = callback

    def _on_key_press(self, key):
        if not self._running_hotkeys:
            return
        key_name = None
        if hasattr(key, "name") and key.name:
            key_name = key.name.upper().strip()
        elif hasattr(key, "char") and key.char:
            key_name = key.char.upper().strip()

        if key_name and key_name in self._hotkey_callbacks:
            cb = self._hotkey_callbacks[key_name]
            threading.Thread(target=cb, daemon=True).start()

    def start_hotkey_listener(self):
        if self._running_hotkeys:
            return
        try:
            from pynput.keyboard import Listener
            self._running_hotkeys = True
            self._hotkey_listener = Listener(on_press=self._on_key_press)
            self._hotkey_listener.daemon = True
            self._hotkey_listener.start()
        except Exception as e:
            self._running_hotkeys = False
            print(f"[InputManager] Warning: Could not start macOS hotkey listener ({e}).")
            print("[InputManager] Reminder: Grant Accessibility permissions to Terminal/Python in System Settings.")

    def stop_hotkey_listener(self):
        self._running_hotkeys = False
        if self._hotkey_listener:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass
            self._hotkey_listener = None


# Export platform-appropriate InputManager class
if sys.platform == "win32":
    InputManager = WindowsInputManager
elif sys.platform == "darwin":
    InputManager = MacOSInputManager
else:
    InputManager = LinuxInputManager


if __name__ == "__main__":
    im = InputManager()
    print("Testing InputManager initialized successfully.")
