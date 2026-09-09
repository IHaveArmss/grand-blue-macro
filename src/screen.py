"""Screen capture and window management utility for X11 on Linux."""

import time
from typing import Optional, Tuple, List, Union
from PIL import Image
from Xlib import display, X, Xatom


class ScreenCapture:
    def __init__(self, display_name: Optional[str] = None):
        self.d = display.Display(display_name)
        self.screen = self.d.screen()
        self.root = self.screen.root
        self._screen_width = self.root.get_geometry().width
        self._screen_height = self.root.get_geometry().height

    @property
    def screen_size(self) -> Tuple[int, int]:
        return self._screen_width, self._screen_height

    def get_monitors(self) -> List[dict]:
        """Queries xrandr for connected monitors and their geometries."""
        monitors = []
        try:
            import subprocess
            out = subprocess.check_output(["xrandr", "--listmonitors"], stderr=subprocess.DEVNULL).decode("utf-8")
            for line in out.splitlines():
                parts = line.strip().split()
                if len(parts) >= 3 and parts[0].endswith(":"):
                    name = parts[-1]
                    geom_str = parts[2]
                    if "+" in geom_str:
                        tokens = geom_str.split("+")
                        res = tokens[0]
                        x_off, y_off = int(tokens[1]), int(tokens[2])
                        w_str = res.split("/")[0] if "/" in res else res.split("x")[0]
                        h_part = res.split("x")[1]
                        h_str = h_part.split("/")[0] if "/" in h_part else h_part
                        monitors.append({"name": name, "x": x_off, "y": y_off, "w": int(w_str), "h": int(h_str)})
        except Exception:
            pass

        if not monitors:
            monitors.append({"name": "Default", "x": 0, "y": 0, "w": self._screen_width, "h": self._screen_height})
        return monitors

    def get_cursor_position(self) -> Tuple[int, int]:
        qp = self.root.query_pointer()
        return qp.root_x, qp.root_y

    def get_monitor_under_cursor(self) -> Tuple[int, int, int, int]:
        cx, cy = self.get_cursor_position()
        for m in self.get_monitors():
            if m["x"] <= cx < (m["x"] + m["w"]) and m["y"] <= cy < (m["y"] + m["h"] + 200):
                return m["x"], m["y"], m["w"], m["h"]
        m0 = self.get_monitors()[0]
        return m0["x"], m0["y"], m0["w"], m0["h"]

    def capture_roi(self, x: int, y: int, width: int, height: int) -> Image.Image:
        """Capture a sub-rectangle of the screen in sub-millisecond time.
        Clamps coordinates to screen boundaries.
        """
        x = max(0, min(int(x), self._screen_width - 1))
        y = max(0, min(int(y), self._screen_height - 1))
        width = max(1, min(int(width), self._screen_width - x))
        height = max(1, min(int(height), self._screen_height - y))

        raw = self.root.get_image(x, y, width, height, X.ZPixmap, 0xFFFFFFFF)
        return Image.frombytes("RGB", (width, height), raw.data, "raw", "BGRX")

    def capture_full(self) -> Image.Image:
        """Capture the entire virtual desktop."""
        return self.capture_roi(0, 0, self._screen_width, self._screen_height)

    def find_window_with_obj(self, title_filters=("Roblox", "Sober")):
        """Search for a top-level window whose title or WM_CLASS contains any of title_filters.
        Returns (window_obj, (x, y, width, height)) or (None, None).
        """
        if isinstance(title_filters, str):
            filters = [title_filters.lower()]
        else:
            filters = [str(f).lower() for f in title_filters]

        def _to_str(val):
            if val is None:
                return ""
            if isinstance(val, bytes):
                try:
                    return val.decode("utf-8", errors="ignore")
                except Exception:
                    return str(val)
            return str(val)

        def _search(win):
            try:
                name = _to_str(win.get_wm_name()).lower()
                if any(f in name for f in filters):
                    return win
                wm_class = win.get_wm_class()
                if wm_class:
                    class_str = " ".join(_to_str(c).lower() for c in wm_class)
                    if any(f in class_str for f in filters):
                        return win
                for child in win.query_tree().children:
                    res = _search(child)
                    if res:
                        return res
            except Exception:
                pass
            return None

        target = _search(self.root)
        if target:
            geom = self.get_window_geometry(target)
            return target, geom
        return None, None

    def find_window(self, title_filters=("Roblox", "Sober")) -> Optional[Tuple[int, int, int, int]]:
        """Convenience wrapper returning only (x, y, width, height)."""
        _, geom = self.find_window_with_obj(title_filters)
        return geom

    def focus_window(self, win):
        """Sets input focus to the given window."""
        try:
            win.set_input_focus(X.RevertToParent, X.CurrentTime)
            self.d.sync()
        except Exception:
            pass

    def get_active_window(self) -> Optional[Tuple[int, int, int, int]]:
        """Get the geometry of the currently focused / active window."""
        try:
            atom = self.d.intern_atom("_NET_ACTIVE_WINDOW")
            prop = self.root.get_full_property(atom, X.AnyPropertyType)
            if prop and prop.value:
                win_id = prop.value[0]
                win = self.d.create_resource_object("window", win_id)
                return self.get_window_geometry(win)
        except Exception:
            pass
        return None

    def get_window_geometry(self, win) -> Tuple[int, int, int, int]:
        """Translates a window's relative coordinates into absolute desktop coordinates."""
        try:
            geom = win.get_geometry()
            coords = self.root.translate_coords(win, 0, 0)
            if coords:
                return coords.x, coords.y, geom.width, geom.height
            return geom.x, geom.y, geom.width, geom.height
        except Exception:
            geom = win.get_geometry()
            return geom.x, geom.y, geom.width, geom.height


if __name__ == "__main__":
    sc = ScreenCapture()
    print(f"Screen dimensions: {sc.screen_size}")
    w_obj, geom = sc.find_window_with_obj(["Roblox", "Sober"])
    print(f"Found game window: {w_obj}, geometry: {geom}")
