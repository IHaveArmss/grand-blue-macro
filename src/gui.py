"""Desktop Graphical User Interface for Grand Blue Macro using GTK 3."""

import os
import sys
import json
import threading
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Gdk
import cairo

try:
    from .screen import ScreenCapture
    from .input_manager import InputManager
    from .detector import GrandBlueDetector
    from .fishing_bot import FishingBot
except ImportError:
    from screen import ScreenCapture
    from input_manager import InputManager
    from detector import GrandBlueDetector
    from fishing_bot import FishingBot


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
PICTURES_DIR = os.path.join(BASE_DIR, "pictures")


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}


def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


class AreaSelectorOverlay(Gtk.Window):
    """Semi-transparent fullscreen overlay for click-and-drag screen region selection."""

    def __init__(self, target_rect, callback):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.target_rect = target_rect  # (x, y, w, h)
        self.callback = callback
        self.start_x = None
        self.start_y = None
        self.curr_x = None
        self.curr_y = None
        self.is_dragging = False

        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_keep_above(True)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.set_visual(visual)

        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK |
            Gdk.EventMask.KEY_PRESS_MASK
        )

        self.connect("draw", self._on_draw)
        self.connect("button-press-event", self._on_press)
        self.connect("button-release-event", self._on_release)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("key-press-event", self._on_key)

        tx, ty, tw, th = target_rect
        self.move(tx, ty)
        self.set_default_size(tw, th)
        self.resize(tw, th)

    def _on_draw(self, widget, cr):
        # Semi-transparent dark tint across entire screen
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.35)
        cr.paint()

        # Instructions banner at top
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.95)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(16.0)
        cr.move_to(30, 45)
        cr.show_text("Click & Drag a box over your water / SHAKE area. Press ESC to cancel.")

        # Draw clear cut-out selection box with bright cyan border
        if self.start_x is not None and self.curr_x is not None:
            rx = min(self.start_x, self.curr_x)
            ry = min(self.start_y, self.curr_y)
            rw = abs(self.curr_x - self.start_x)
            rh = abs(self.curr_y - self.start_y)

            # Clear cut-out to show the game underneath
            cr.set_operator(cairo.OPERATOR_CLEAR)
            cr.rectangle(rx, ry, rw, rh)
            cr.fill()

            # Glowing cyan outline
            cr.set_operator(cairo.OPERATOR_OVER)
            cr.set_source_rgba(0.0, 0.85, 1.0, 0.95)
            cr.set_line_width(2.5)
            cr.rectangle(rx, ry, rw, rh)
            cr.stroke()

        return False

    def _on_press(self, widget, event):
        if event.button == 1:
            self.start_x = event.x
            self.start_y = event.y
            self.curr_x = event.x
            self.curr_y = event.y
            self.start_root_x = event.x_root
            self.start_root_y = event.y_root
            self.is_dragging = True
            self.queue_draw()
        return True

    def _on_motion(self, widget, event):
        if self.is_dragging:
            self.curr_x = event.x
            self.curr_y = event.y
            self.queue_draw()
        return True

    def _on_release(self, widget, event):
        if event.button == 1 and self.is_dragging:
            self.is_dragging = False
            root_start_x = int(getattr(self, "start_root_x", event.x_root))
            root_start_y = int(getattr(self, "start_root_y", event.y_root))
            root_end_x = int(event.x_root)
            root_end_y = int(event.y_root)
            rx = min(root_start_x, root_end_x)
            ry = min(root_start_y, root_end_y)
            rw = abs(root_end_x - root_start_x)
            rh = abs(root_end_y - root_start_y)
            self.destroy()
            if rw > 20 and rh > 20:
                self.callback(rx, ry, rw, rh)
        return True

    def _on_key(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.destroy()
        return True


class GrandBlueMacroGUI(Gtk.Window):
    def __init__(self):
        super().__init__(title="Grand Blue Macro")
        self.set_default_size(560, 740)
        self.set_position(Gtk.WindowPosition.CENTER)

        self.config = load_config()

        # Core backend objects
        self.screen = ScreenCapture()
        self.input_mgr = InputManager()
        self.detector = GrandBlueDetector(PICTURES_DIR)

        self._build_ui()

        self.bot = FishingBot(
            self.screen, self.input_mgr, self.detector, self.config,
            status_callback=self._on_bot_status,
            log_callback=self._append_log
        )

        self._setup_hotkeys()

        self.connect("destroy", self._on_window_close)

    def _build_ui(self):
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        main_box.set_margin_top(12)
        main_box.set_margin_bottom(12)
        main_box.set_margin_start(14)
        main_box.set_margin_end(14)
        self.add(main_box)

        # Header Title
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        title_label = Gtk.Label()
        title_label.set_markup("<span size='x-large' weight='bold'>🌊 Grand Blue Macro</span>")
        header_box.pack_start(title_label, False, False, 0)
        main_box.pack_start(header_box, False, False, 0)

        # Notebook (3 Menus)
        self.notebook = Gtk.Notebook()
        main_box.pack_start(self.notebook, True, True, 0)

        # 1. Fishing Tab
        tab_fishing = self._build_fishing_tab()
        label_fishing = Gtk.Label(label="🎣 Fishing")
        self.notebook.append_page(tab_fishing, label_fishing)

        # 2. Mining Tab (Empty placeholder)
        tab_mining = self._build_placeholder_tab("⛏️ Mining Macro", "Mining automation features are currently under development.")
        label_mining = Gtk.Label(label="⛏️ Mining")
        self.notebook.append_page(tab_mining, label_mining)

        # 3. Skill Macroing Tab (Empty placeholder)
        tab_skill = self._build_placeholder_tab("⚔️ Skill Macroing", "Skill gain automation features are currently under development.")
        label_skill = Gtk.Label(label="⚔️ Skill Macroing")
        self.notebook.append_page(tab_skill, label_skill)

        # Global Bottom Bar
        bottom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.btn_toggle = Gtk.Button(label="▶ Start Macro (F6)")
        self.btn_toggle.connect("clicked", self._on_toggle_clicked)
        bottom_box.pack_start(self.btn_toggle, True, True, 0)

        self.btn_calib = Gtk.Button(label="🎯 Calibrate (F7)")
        self.btn_calib.connect("clicked", self._on_calib_clicked)
        bottom_box.pack_start(self.btn_calib, False, False, 0)

        main_box.pack_start(bottom_box, False, False, 0)

    def _build_fishing_tab(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_margin_top(12)
        vbox.set_margin_bottom(12)
        vbox.set_margin_start(10)
        vbox.set_margin_end(10)

        # --- Section: Hotbar Settings ---
        frame_hotbar = Gtk.Frame(label=" 🎒 Hotbar & Rod Settings ")
        box_hotbar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box_hotbar.set_margin_top(8)
        box_hotbar.set_margin_bottom(8)
        box_hotbar.set_margin_start(10)
        box_hotbar.set_margin_end(10)
        frame_hotbar.add(box_hotbar)

        # Slot Selector Row
        row_slot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        lbl_slot = Gtk.Label(label="Fishing Rod Slot:")
        row_slot.pack_start(lbl_slot, False, False, 0)

        self.combo_slot = Gtk.ComboBoxText()
        slots = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"]
        for s in slots:
            self.combo_slot.append_text(f"Slot {s}")
        current_slot = str(self.config.get("fishing", {}).get("rod_slot", "9"))
        if current_slot in slots:
            self.combo_slot.set_active(slots.index(current_slot))
        else:
            self.combo_slot.set_active(8)  # default slot 9
        self.combo_slot.connect("changed", self._on_slot_changed)
        row_slot.pack_start(self.combo_slot, False, False, 0)
        box_hotbar.pack_start(row_slot, False, False, 0)

        # Assume Rod in Hand Switch (Prevents unequipping & drowning)
        row_assume = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        lbl_assume = Gtk.Label(label="Assume Rod In Hand on Start:")
        lbl_assume.set_tooltip_text("Prevents pressing slot hotkey if rod is already in hand, preventing accidental unequip and punching.")
        row_assume.pack_start(lbl_assume, False, False, 0)
        self.switch_assume = Gtk.Switch()
        self.switch_assume.set_active(self.config.get("fishing", {}).get("assume_rod_equipped_on_start", True))
        self.switch_assume.connect("notify::active", self._on_assume_rod_changed)
        row_assume.pack_end(self.switch_assume, False, False, 0)
        box_hotbar.pack_start(row_assume, False, False, 0)

        # Monitor Target Selector Row
        row_disp = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        lbl_disp = Gtk.Label(label="Target Screen:")
        row_disp.pack_start(lbl_disp, False, False, 0)

        self.combo_disp = Gtk.ComboBoxText()
        self.disp_options = [
            ("cursor_monitor", "🎯 Current Monitor (Follow Cursor on F6)"),
        ]
        for idx, m in enumerate(self.screen.get_monitors()):
            name = m.get("name", f"Screen {idx+1}")
            w, h = m.get("w", 1920), m.get("h", 1080)
            key = f"monitor_{name}"
            self.disp_options.append((key, f"🖥️ {name} ({w}x{h})"))
        self.disp_options.append(("window", "🔍 Auto-Detect Window (Roblox/Sober)"))

        for key, text in self.disp_options:
            self.combo_disp.append_text(text)

        cur_mode = self.config.get("display", {}).get("mode", "cursor_monitor")
        cur_keys = [k for k, _ in self.disp_options]
        if cur_mode in cur_keys:
            self.combo_disp.set_active(cur_keys.index(cur_mode))
        else:
            self.combo_disp.set_active(0)

        self.combo_disp.connect("changed", self._on_disp_changed)
        row_disp.pack_start(self.combo_disp, True, True, 0)
        box_hotbar.pack_start(row_disp, False, False, 0)

        vbox.pack_start(frame_hotbar, False, False, 0)

        # --- Section: Mechanics & Sensitivity ---
        frame_mech = Gtk.Frame(label=" ⚙️ Detection & Mechanics ")
        box_mech = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box_mech.set_margin_top(8)
        box_mech.set_margin_bottom(8)
        box_mech.set_margin_start(10)
        box_mech.set_margin_end(10)
        frame_mech.add(box_mech)

        # Cast target fill & lead time
        row_cast_pct = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        lbl_cast_pct = Gtk.Label(label="Cast Target Fill (%):")
        row_cast_pct.pack_start(lbl_cast_pct, False, False, 0)
        target_pct = self.config.get("fishing", {}).get("cast", {}).get("lead_release_pct", 92.0)
        self.scale_cast_pct = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 75.0, 98.0, 1.0)
        self.scale_cast_pct.set_value(target_pct)
        self.scale_cast_pct.connect("value-changed", self._on_cast_target_pct_changed)
        row_cast_pct.pack_start(self.scale_cast_pct, True, True, 0)
        box_mech.pack_start(row_cast_pct, False, False, 0)

        row_cast_lead = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        lbl_cast_lead = Gtk.Label(label="Cast Lead Time (ms):")
        row_cast_lead.pack_start(lbl_cast_lead, False, False, 0)
        lead_ms = self.config.get("fishing", {}).get("cast", {}).get("lead_time_ms", 45.0)
        self.scale_cast_lead = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, 120.0, 5.0)
        self.scale_cast_lead.set_value(lead_ms)
        self.scale_cast_lead.connect("value-changed", self._on_cast_lead_changed)
        row_cast_lead.pack_start(self.scale_cast_lead, True, True, 0)
        box_mech.pack_start(row_cast_lead, False, False, 0)

        # Cast max hold
        row_cast = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        lbl_cast = Gtk.Label(label="Cast Max Hold (sec):")
        row_cast.pack_start(lbl_cast, False, False, 0)
        max_h = self.config.get("fishing", {}).get("cast", {}).get("max_hold_time", 1.4)
        self.scale_cast = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.5, 2.5, 0.1)
        self.scale_cast.set_value(max_h)
        self.scale_cast.connect("value-changed", self._on_cast_scale_changed)
        row_cast.pack_start(self.scale_cast, True, True, 0)
        box_mech.pack_start(row_cast, False, False, 0)

        # Shake speed
        row_shake = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        lbl_shake = Gtk.Label(label="Shake Click Delay (sec):")
        row_shake.pack_start(lbl_shake, False, False, 0)
        shake_d = self.config.get("fishing", {}).get("shake", {}).get("click_delay", 0.08)
        self.scale_shake = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.03, 0.20, 0.01)
        self.scale_shake.set_value(shake_d)
        self.scale_shake.connect("value-changed", self._on_shake_scale_changed)
        row_shake.pack_start(self.scale_shake, True, True, 0)
        box_mech.pack_start(row_shake, False, False, 0)

        # Reeling Steering Mode Selector
        row_steer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lbl_steer = Gtk.Label(label="Reeling Algorithm:")
        lbl_steer.set_xalign(0.0)
        self.combo_steer = Gtk.ComboBoxText()
        self.combo_steer.append("deepfish_precision", "DeepFish Precision (PD + Momentum)")
        self.combo_steer.append("deepfish_anklebreak", "DeepFish AnkleBreak (SleepTrack)")
        self.combo_steer.append("pulsed", "Legacy Rapid Click Pulses")
        self.combo_steer.append("hold", "Direct Hold")

        current_steering = self.config.get("fishing", {}).get("reeling", {}).get("steering_mode", "deepfish_precision")
        self.combo_steer.set_active_id(current_steering)
        self.combo_steer.connect("changed", self._on_steering_changed)
        row_steer.pack_start(lbl_steer, False, False, 0)
        row_steer.pack_start(self.combo_steer, True, True, 0)
        box_mech.pack_start(row_steer, False, False, 0)

        # Minigame Target Detection Mode
        row_target = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lbl_target = Gtk.Label(label="Minigame Target:")
        lbl_target.set_xalign(0.0)
        self.combo_target = Gtk.ComboBoxText()
        self.combo_target.append("fish_sprite", "Red / Green Fish (Grand Blue)")
        self.combo_target.append("line_scan", "Vertical Line Scan (Fisch)")

        current_target = self.config.get("fishing", {}).get("reeling", {}).get("target_mode", "fish_sprite")
        self.combo_target.set_active_id(current_target)
        self.combo_target.connect("changed", self._on_target_mode_changed)
        row_target.pack_start(lbl_target, False, False, 0)
        row_target.pack_start(self.combo_target, True, True, 0)
        box_mech.pack_start(row_target, False, False, 0)

        vbox.pack_start(frame_mech, False, False, 0)

        # --- Section: Custom Shake Detection Region ---
        frame_roi = Gtk.Frame(label=" 🎯 Shake Detection Region ")
        box_roi = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box_roi.set_margin_top(8)
        box_roi.set_margin_bottom(8)
        box_roi.set_margin_start(10)
        box_roi.set_margin_end(10)
        frame_roi.add(box_roi)

        self.lbl_roi_status = Gtk.Label()
        self.lbl_roi_status.set_xalign(0.0)
        box_roi.pack_start(self.lbl_roi_status, False, False, 0)
        self._update_roi_label()

        row_roi_btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_drag = Gtk.Button(label="📐 Drag-Select")
        btn_drag.set_tooltip_text("Click & drag a box on your game screen to limit SHAKE detection")
        btn_drag.connect("clicked", self._on_drag_select_clicked)
        row_roi_btns.pack_start(btn_drag, True, True, 0)

        btn_corners = Gtk.Button(label="🎯 Pick 2 Corners")
        btn_corners.set_tooltip_text("Move cursor to top-left corner, press Enter, then bottom-right corner")
        btn_corners.connect("clicked", self._on_pick_corners_clicked)
        row_roi_btns.pack_start(btn_corners, True, True, 0)

        btn_reset_roi = Gtk.Button(label="↺ Reset Area")
        btn_reset_roi.set_tooltip_text("Reset to scanning the entire water play area")
        btn_reset_roi.connect("clicked", self._on_reset_roi_clicked)
        row_roi_btns.pack_start(btn_reset_roi, False, False, 0)

        box_roi.pack_start(row_roi_btns, False, False, 0)
        vbox.pack_start(frame_roi, False, False, 0)

        # --- Section: Live Status Dashboard ---
        frame_status = Gtk.Frame(label=" 📊 Live Status ")
        box_status = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box_status.set_margin_top(8)
        box_status.set_margin_bottom(8)
        box_status.set_margin_start(10)
        self.lbl_state = Gtk.Label()
        self.lbl_state.set_markup("<b>Status:</b> <span foreground='#888888'>IDLE (Press F6 to Start)</span>")
        box_status.pack_start(self.lbl_state, False, False, 0)

        self.lbl_fish = Gtk.Label()
        self.lbl_fish.set_markup("<b>Fish Alignment:</b> <span foreground='#888888'>--</span>")
        box_status.pack_start(self.lbl_fish, False, False, 0)

        self.lbl_catches = Gtk.Label()
        self.lbl_catches.set_markup("<b>Fish Caught:</b> <span weight='bold'>0</span>")
        box_status.pack_start(self.lbl_catches, False, False, 0)
        frame_status.add(box_status)

        # --- Section: Live Activity Log ---
        frame_log = Gtk.Frame(label=" 📝 Live Activity Log ")
        self.scrolled_log = Gtk.ScrolledWindow()
        self.scrolled_log.set_min_content_height(140)
        self.scrolled_log.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self.text_log = Gtk.TextView()
        self.text_log.set_editable(False)
        self.text_log.set_cursor_visible(False)
        self.text_log.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log_buffer = self.text_log.get_buffer()
        self.scrolled_log.add(self.text_log)
        frame_log.add(self.scrolled_log)

        vbox.pack_start(frame_status, False, False, 0)
        vbox.pack_start(frame_log, True, True, 0)
        return vbox

    def _append_log(self, text: str):
        def _insert():
            try:
                end_iter = self.log_buffer.get_end_iter()
                self.log_buffer.insert(end_iter, text + "\n")
                line_count = self.log_buffer.get_line_count()
                if line_count > 250:
                    start_iter = self.log_buffer.get_start_iter()
                    cutoff_iter = self.log_buffer.get_iter_at_line(line_count - 250)
                    self.log_buffer.delete(start_iter, cutoff_iter)
                adj = self.scrolled_log.get_vadjustment()
                adj.set_value(adj.get_upper())
            except Exception:
                pass
            return False

        GLib.idle_add(_insert)

    def _build_placeholder_tab(self, title_text, desc_text):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)

        lbl_title = Gtk.Label()
        lbl_title.set_markup(f"<span size='large' weight='bold'>{title_text}</span>")
        box.pack_start(lbl_title, False, False, 0)

        lbl_desc = Gtk.Label(label=desc_text)
        lbl_desc.set_line_wrap(True)
        box.pack_start(lbl_desc, False, False, 0)

        lbl_sub = Gtk.Label()
        lbl_sub.set_markup("<span foreground='#888888'>Configure your fishing settings in the Fishing tab.</span>")
        box.pack_start(lbl_sub, False, False, 0)
        return box

    def _setup_hotkeys(self):
        hotkeys = self.config.get("hotkeys", {})
        hk_toggle = hotkeys.get("toggle_macro", "F6")
        hk_calib = hotkeys.get("calibrate", "F7")
        hk_quit = hotkeys.get("quit", "F8")

        self.input_mgr.register_hotkey(hk_toggle, lambda: GLib.idle_add(self._toggle_macro))
        self.input_mgr.register_hotkey(hk_calib, lambda: GLib.idle_add(self._on_calib_clicked, None))
        self.input_mgr.register_hotkey(hk_quit, lambda: GLib.idle_add(self._on_window_close, None))
        self.input_mgr.start_hotkey_listener()

    # --- UI Callbacks ---

    def _on_slot_changed(self, combo):
        txt = combo.get_active_text()
        if txt:
            slot = txt.replace("Slot ", "").strip()
            self.config.setdefault("fishing", {})["rod_slot"] = slot
            save_config(self.config)

    def _on_verify_toggled(self, chk):
        val = chk.get_active()
        self.config.setdefault("fishing", {})["verify_rod_equipped"] = val
        save_config(self.config)

    def _on_assume_rod_changed(self, switch, gparam):
        val = switch.get_active()
        self.config.setdefault("fishing", {})["assume_rod_equipped_on_start"] = val
        save_config(self.config)
        self._append_log(f"[CONFIG] Assume rod in hand on start set to: {val}")

    def _on_cast_target_pct_changed(self, scale):
        val = round(scale.get_value(), 1)
        self.config.setdefault("fishing", {}).setdefault("cast", {})["lead_release_pct"] = val
        save_config(self.config)

    def _on_cast_lead_changed(self, scale):
        val = round(scale.get_value(), 1)
        self.config.setdefault("fishing", {}).setdefault("cast", {})["lead_time_ms"] = val
        save_config(self.config)

    def _on_cast_scale_changed(self, scale):
        val = round(scale.get_value(), 2)
        self.config.setdefault("fishing", {}).setdefault("cast", {})["max_hold_time"] = val
        save_config(self.config)

    def _on_shake_scale_changed(self, scale):
        val = round(scale.get_value(), 3)
        self.config.setdefault("fishing", {}).setdefault("shake", {})["click_delay"] = val
        save_config(self.config)

    def _on_steering_changed(self, combo):
        mode = combo.get_active_id() or "deepfish_precision"
        self.config.setdefault("fishing", {}).setdefault("reeling", {})["steering_mode"] = mode
        save_config(self.config)
        if hasattr(self.bot, "controller"):
            self.bot.controller.steering_mode = mode
        desc = combo.get_active_text()
        self._append_log(f"[CONFIG] Reeling algorithm set to: {desc}")

    def _on_target_mode_changed(self, combo):
        target = combo.get_active_id() or "fish_sprite"
        self.config.setdefault("fishing", {}).setdefault("reeling", {})["target_mode"] = target
        save_config(self.config)
        desc = combo.get_active_text()
        self._append_log(f"[CONFIG] Minigame target detection set to: {desc}")

    def _update_roi_label(self):
        roi = self.config.get("fishing", {}).get("shake", {}).get("custom_roi")
        if roi and len(roi) == 4:
            self.lbl_roi_status.set_markup(
                f"<b>Area:</b> <span foreground='#2ecc71'>Custom Region</span> "
                f"(X: {roi[0]}, Y: {roi[1]}, Size: {roi[2]}x{roi[3]} px)"
            )
        else:
            self.lbl_roi_status.set_markup(
                "<b>Area:</b> <span foreground='#3498db'>Auto (Full Water Play Area)</span>"
            )

    def _on_roi_selected(self, x, y, w, h):
        self.config.setdefault("fishing", {}).setdefault("shake", {})["custom_roi"] = [x, y, w, h]
        save_config(self.config)
        self._update_roi_label()
        self._append_log(f"[CONFIG] Custom SHAKE region saved: pos=({x}, {y}), size={w}x{h}.")

    def _on_drag_select_clicked(self, btn):
        # Determine target screen geometry (locked monitor or monitor under cursor)
        cur_mode = self.config.get("display", {}).get("mode", "cursor_monitor")
        target_rect = None
        if cur_mode.startswith("monitor_"):
            m_name = cur_mode.replace("monitor_", "")
            for m in self.screen.get_monitors():
                if m.get("name") == m_name:
                    target_rect = (m.get("x", 0), m.get("y", 0), m.get("w", self.screen.screen_size[0]), m.get("h", self.screen.screen_size[1]))
                    break
        if not target_rect:
            target_rect = self.screen.get_monitor_under_cursor()

        self._append_log("[GUI] Opening drag overlay. Click and drag a box over your water area (ESC to cancel)...")
        overlay = AreaSelectorOverlay(target_rect, self._on_roi_selected)
        overlay.show_all()

    def _on_pick_corners_clicked(self, btn):
        # Step 1: Top-Left
        dlg1 = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text="Pick Top-Left Corner"
        )
        dlg1.format_secondary_text(
            "1. Move your mouse cursor over the TOP-LEFT corner of your water area.\n"
            "2. Press Enter or click OK to capture."
        )
        res1 = dlg1.run()
        dlg1.destroy()
        if res1 != Gtk.ResponseType.OK:
            return

        x1, y1 = self.screen.get_cursor_position()

        # Step 2: Bottom-Right
        dlg2 = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text="Pick Bottom-Right Corner"
        )
        dlg2.format_secondary_text(
            f"Top-Left captured at ({x1}, {y1}).\n\n"
            "1. Move your mouse cursor over the BOTTOM-RIGHT corner of your water area.\n"
            "2. Press Enter or click OK to finish."
        )
        res2 = dlg2.run()
        dlg2.destroy()
        if res2 != Gtk.ResponseType.OK:
            return

        x2, y2 = self.screen.get_cursor_position()

        rx = min(x1, x2)
        ry = min(y1, y2)
        rw = abs(x2 - x1)
        rh = abs(y2 - y1)

        if rw < 20 or rh < 20:
            self._append_log("[ERROR] Selected area is too small (< 20px). Please pick two distinct corners.")
            return

        self._on_roi_selected(rx, ry, rw, rh)

    def _on_reset_roi_clicked(self, btn):
        self.config.setdefault("fishing", {}).setdefault("shake", {})["custom_roi"] = None
        save_config(self.config)
        self._update_roi_label()
        self._append_log("[CONFIG] Custom SHAKE region cleared. Restored to Full Water Play Area (Default).")

    def _on_toggle_clicked(self, btn):
        self._toggle_macro()

    def _toggle_macro(self):
        if self.bot.is_running:
            self.bot.stop()
            self.btn_toggle.set_label("▶ Start Macro (F6)")
            self.lbl_state.set_markup("<b>Status:</b> <span foreground='#888888'>STOPPED</span>")
        else:
            self.bot.start()
            self.btn_toggle.set_label("⏹ Stop Macro (F6)")
            self.lbl_state.set_markup("<b>Status:</b> <span foreground='#2ecc71'>RUNNING</span>")

    def _on_disp_changed(self, combo):
        idx = combo.get_active()
        if 0 <= idx < len(self.disp_options):
            key = self.disp_options[idx][0]
            self.config.setdefault("display", {})["mode"] = key
            save_config(self.config)
            self._append_log(f"[CONFIG] Target Screen set to: {self.disp_options[idx][1]}")

    def _on_calib_clicked(self, btn):
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Screen Calibration"
        )
        geom = self.screen.find_window(["Roblox", "Sober"]) or self.screen.get_active_window()
        if geom:
            dialog.format_secondary_text(f"Detected Game Window:\nX={geom[0]}, Y={geom[1]}, Width={geom[2]}, Height={geom[3]}")
        else:
            sw, sh = self.screen.screen_size
            dialog.format_secondary_text(f"No specific Roblox/Sober window found. Defaulting to virtual desktop: {sw}x{sh}")
        dialog.run()
        dialog.destroy()

    def _on_bot_status(self, state: str, data: dict):
        def _update():
            # State badge
            color_map = {
                "IDLE": "#888888",
                "STARTING": "#3498db",
                "EQUIPPING_ROD": "#f39c12",
                "CASTING": "#9b59b6",
                "LURING_SHAKE": "#e67e22",
                "REELING": "#2ecc71",
                "FISH_CAUGHT": "#27ae60",
                "COOLDOWN": "#7f8c8d",
                "STOPPED": "#e74c3c"
            }
            color = color_map.get(state, "#ffffff")
            self.lbl_state.set_markup(f"<b>Status:</b> <span foreground='{color}' weight='bold'>{state}</span>")

            # Fish state
            fish_color = data.get("fish_state")
            if fish_color == "GREEN":
                self.lbl_fish.set_markup("<b>Fish Alignment:</b> <span foreground='#2ecc71' weight='bold'>GREEN (INSIDE BAR)</span>")
            elif fish_color == "RED":
                self.lbl_fish.set_markup("<b>Fish Alignment:</b> <span foreground='#e74c3c' weight='bold'>RED (OUTSIDE BAR)</span>")
            else:
                self.lbl_fish.set_markup("<b>Fish Alignment:</b> <span foreground='#888888'>--</span>")

            # Catches
            catches = data.get("catches", 0)
            self.lbl_catches.set_markup(f"<b>Fish Caught:</b> <span weight='bold' size='large'>{catches}</span>")

        GLib.idle_add(_update)

    def _on_window_close(self, *args):
        if self.bot.is_running:
            self.bot.stop()
        self.input_mgr.stop_hotkey_listener()
        Gtk.main_quit()


def main():
    app = GrandBlueMacroGUI()
    app.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
