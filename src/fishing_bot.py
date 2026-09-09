"""Main state machine and autonomous loop for Grand Blue fishing with detailed state logging."""

import time
import threading
from typing import Optional, Callable
try:
    from .screen import ScreenCapture
    from .input_manager import InputManager
    from .detector import GrandBlueDetector, ReelGameState
    from .controller import ReelingController
except ImportError:
    from screen import ScreenCapture
    from input_manager import InputManager
    from detector import GrandBlueDetector, ReelGameState
    from controller import ReelingController


class FishingBot:
    def __init__(self, screen: ScreenCapture, input_mgr: InputManager,
                 detector: GrandBlueDetector, config: dict,
                 status_callback: Optional[Callable[[str, dict], None]] = None,
                 log_callback: Optional[Callable[[str], None]] = None):
        self.screen = screen
        self.input = input_mgr
        self.detector = detector
        self.config = config
        self.status_cb = status_callback
        self.log_cb = log_callback

        self.controller = ReelingController(self.input, self.config)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._game_win = None
        self._rod_equipped = self.config.get("fishing", {}).get("assume_rod_equipped_on_start", True)

        # Stats
        self.catches = 0
        self.current_state = "IDLE"

    def log(self, message: str):
        """Dispatches a log message to stdout and the registered log callback."""
        now_str = time.strftime("%H:%M:%S")
        formatted = f"[{now_str}] {message}"
        print(formatted)
        if self.log_cb:
            try:
                self.log_cb(formatted)
            except Exception:
                pass

    def set_status(self, state: str, extra: Optional[dict] = None):
        self.current_state = state
        if self.status_cb:
            payload = {"state": state, "catches": self.catches}
            if extra:
                payload.update(extra)
            self.status_cb(state, payload)

    def start(self):
        if self._running:
            return
        self._running = True
        self._rod_equipped = self.config.get("fishing", {}).get("assume_rod_equipped_on_start", True)
        self._locked_rect = self._resolve_game_rect()
        gx, gy, gw, gh = self._locked_rect
        self.log(f"[BOT] Macro STARTED (F6 pressed). Screen LOCKED: pos=({gx}, {gy}), size=({gw}x{gh}).")
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self.input.release_all()
        self.controller.reset()
        self.set_status("STOPPED")
        self.log("[BOT] Macro STOPPED (F6 pressed). All inputs released.")

    @property
    def is_running(self) -> bool:
        return self._running

    def _resolve_game_rect(self):
        """Resolves target region from config (cursor monitor, specific monitor, or window search)."""
        cfg_display = self.config.get("display", {})
        mode = cfg_display.get("mode", "cursor_monitor")

        # Mode 1: Search for game window if auto_detect_window is enabled (prioritized)
        if cfg_display.get("auto_detect_window", True):
            title_filter = cfg_display.get("window_title_filter", ["Roblox", "Sober"])
            win_obj, win_geom = self.screen.find_window_with_obj(title_filter)
            if win_geom:
                self._game_win = win_obj
                self.log(f"[WINDOW] Locked to game window '{title_filter}': {win_geom}")
                return win_geom

        # Mode 2: Follow cursor monitor (recommended when starting macro with window highlighted)
        if mode == "cursor_monitor":
            m_rect = self.screen.get_monitor_under_cursor()
            return m_rect

        # Mode 3: Explicit monitor name or index (e.g. 'monitor_DP-0', 'monitor_1')
        if mode.startswith("monitor_"):
            target = mode.replace("monitor_", "").strip()
            monitors = self.screen.get_monitors()
            for m in monitors:
                if m["name"].lower() == target.lower():
                    return m["x"], m["y"], m["w"], m["h"]
            try:
                idx = int(target)
                if 0 <= idx < len(monitors):
                    m = monitors[idx]
                    return m["x"], m["y"], m["w"], m["h"]
            except Exception:
                pass

        # Mode 4: Explicit target rectangle
        cfg_rect = cfg_display.get("target_region")
        if cfg_rect and len(cfg_rect) == 4:
            return cfg_rect

        # Mode 5: Search for game window fallback
        title_filter = cfg_display.get("window_title_filter", ["Roblox", "Sober"])
        win_obj, win_geom = self.screen.find_window_with_obj(title_filter)
        if win_geom:
            self._game_win = win_obj
            return win_geom

        # Fallback to cursor monitor
        return self.screen.get_monitor_under_cursor()

    def _focus_game(self, game_rect):
        """Ensures the game window has input focus without disruptive mouse clicks."""
        if self._game_win:
            self.screen.focus_window(self._game_win)
        time.sleep(0.02)

    def _run_loop(self):
        self.set_status("STARTING")
        time.sleep(0.3)

        while self._running:
            try:
                rect = self._locked_rect
                gx, gy, gw, gh = rect

                # Stage 0: Verify / Equip Rod
                self._handle_equip_rod(rect)
                if not self._running:
                    break

                # Stage 1: Cast
                cast_ok = self._handle_cast(rect)
                if not self._running:
                    break

                # If cast failed (e.g. rod was not in hand or click did not register), retry equip safely
                if not cast_ok:
                    self.log("[BOT] Cast failed. Skipping lure/shake phase and retrying rod equip...")
                    time.sleep(0.5)
                    continue

                # Stage 2: Lure & Shake
                reeling_ready = self._handle_shake(rect)
                if not self._running:
                    break

                # Stage 3: Reeling Minigame
                if reeling_ready:
                    self._handle_reeling(rect)
                    if not self._running:
                        break

                # Stage 4: Cooldown
                self.set_status("COOLDOWN")
                cooldown = self.config.get("fishing", {}).get("cooldown_between_casts", 1.5)
                self.log(f"[COOLDOWN] Waiting {cooldown}s for catch animation to clear...")
                time.sleep(cooldown)

            except Exception as e:
                self.log(f"[ERROR] Exception in fishing loop: {e}")
                time.sleep(1.0)

        self.input.release_all()
        self.controller.reset()

    def _handle_equip_rod(self, game_rect):
        """Verifies rod slot is equipped, taps key if needed without accidental unequipping."""
        self.set_status("EQUIPPING_ROD")
        fishing_cfg = self.config.get("fishing", {})
        rod_slot = str(fishing_cfg.get("rod_slot", "9"))
        verify = fishing_cfg.get("verify_rod_equipped", True)
        equip_delay = fishing_cfg.get("equip_key_delay", 0.25)
        auto_equip = fishing_cfg.get("auto_equip_on_start", False)

        # In Roblox, pressing a tool's number key when it is ALREADY equipped UNEQUIPS it!
        # If rod was already confirmed equipped or assumed on start, never press the hotkey.
        if self._rod_equipped:
            self.log(f"[ROD] Fishing rod is in hand (slot {rod_slot}). Ready to cast.")
            return

        gx, gy, gw, gh = game_rect

        # Roblox hotbar is centered horizontally at the bottom of the window
        hw = int(0.44 * gw)
        hx = gx + (gw // 2) - (hw // 2)
        hy = gy + int(0.85 * gh)
        hh = gh - (hy - gy)

        if verify:
            hotbar_img = self.screen.capture_roi(hx, hy, hw, hh)
            is_eq, white_cnt, ratio = self.detector.is_slot_equipped(hotbar_img, rod_slot)

            if is_eq:
                self.log(f"[ROD] Slot {rod_slot} verified EQUIPPED (border white pixels: {white_cnt}).")
                self._rod_equipped = True
                return

            if auto_equip:
                self.log(f"[ROD] Slot {rod_slot} NOT equipped (border white pixels: {white_cnt}).")
                self.log(f"[ROD] Focusing game window and sending key '{rod_slot}' to equip rod...")
                self._focus_game(game_rect)
                self.input.tap_key(rod_slot)
                time.sleep(equip_delay)
                self._rod_equipped = True
            else:
                self.log(f"[ROD] Slot {rod_slot} border verification returned {white_cnt}px. Rod assumed ready (auto_equip disabled).")
                self._rod_equipped = True
        else:
            if auto_equip:
                self.log(f"[ROD] Verify disabled. Sending key '{rod_slot}' to equip rod...")
                self._focus_game(game_rect)
                self.input.tap_key(rod_slot)
                time.sleep(equip_delay)
            self._rod_equipped = True

    def _handle_cast(self, game_rect) -> bool:
        """Holds M1 and releases when the vertical meter fills to the top.
        Returns True if cast was successful (meter detected and released), False otherwise.
        """
        self.set_status("CASTING")
        cast_cfg = self.config.get("fishing", {}).get("cast", {})
        max_hold = cast_cfg.get("max_hold_time", 1.4)
        min_hold = cast_cfg.get("min_hold_time", 0.25)
        fill_thresh = cast_cfg.get("fill_threshold", 16)

        gx, gy, gw, gh = game_rect
        center_x = gx + (gw // 2)
        center_y = gy + int(gh * 0.45)

        # Ensure window is focused before interacting
        self._focus_game(game_rect)

        # Check where cursor is right now
        cx, cy = self.screen.get_cursor_position()
        # Ensure cursor is positioned safely in the central water play area (30-70% width, 30-65% height)
        in_water_zone = (
            (gx + int(0.30 * gw) <= cx <= gx + int(0.70 * gw)) and
            (gy + int(0.30 * gh) <= cy <= gy + int(0.65 * gh))
        )
        if not in_water_zone:
            self.log(f"[CAST] Placing cursor in water play area ({center_x}, {center_y}) & charging cast...")
            self.input.mouse_move(center_x, center_y)
            time.sleep(0.04)
        else:
            self.log(f"[CAST] Mouse positioned at ({cx}, {cy}). Charging cast (holding M1)...")

        # Cast ROI: vertical strip where cast capsule appears
        cast_roi_x = gx + int(0.38 * gw)
        cast_roi_y = gy + int(0.18 * gh)
        cast_roi_w = int(0.24 * gw)
        cast_roi_h = int(0.62 * gh)

        t_start = time.time()
        self.input.mouse_down(1)
        last_log_t = 0.0
        best_fill = 0
        stable_count = 0
        cast_success = False

        while self._running:
            elapsed = time.time() - t_start
            if elapsed >= max_hold:
                if best_fill >= 8:
                    self.log(f"[CAST] Reached max hold time ({max_hold}s) with fill={best_fill}. Releasing M1.")
                    cast_success = True
                else:
                    self.log(f"[CAST] Max hold time ({max_hold}s) reached with NO cast meter detected (best={best_fill}). Releasing M1.")
                    cast_success = False
                break

            cast_crop = self.screen.capture_roi(cast_roi_x, cast_roi_y, cast_roi_w, cast_roi_h)
            reached, cyan_rows, white_px = self.detector.check_cast_fill(cast_crop, fill_thresh)

            if elapsed >= min_hold:
                if reached:
                    self.log(f"[CAST] Top of meter REACHED (cyan_rows={cyan_rows}, white_cap={white_px})! Releasing M1.")
                    cast_success = True
                    break
                if cyan_rows > best_fill:
                    best_fill = cyan_rows
                    stable_count = 0
                elif best_fill >= 14:
                    stable_count += 1
                    if stable_count >= 3:
                        self.log(f"[CAST] Cast meter reached peak ({best_fill} rows)! Releasing M1.")
                        cast_success = True
                        break

            if elapsed - last_log_t > 0.25:
                if cyan_rows > 0 or white_px > 0:
                    self.log(f"[CAST] Meter rising... cyan_rows={cyan_rows}, white_cap={white_px} (elapsed: {elapsed:.2f}s).")
                last_log_t = elapsed

            time.sleep(0.008)

        self.input.mouse_up(1)

        if cast_success or best_fill >= 8:
            self._rod_equipped = True
            self.log("[CAST] Cast released! Waiting 0.6s for bobber to splash in water...")
            time.sleep(0.6)
            return True
        else:
            self.log("[CAST] Warning: No cast meter appeared during M1 hold! Rod may not be equipped. Aborting to re-equip.")
            self._rod_equipped = False
            return False

    def _handle_shake(self, game_rect) -> bool:
        """Clicks SHAKE buttons until reel minigame appears or timeout."""
        self.set_status("LURING_SHAKE")
        shake_cfg = self.config.get("fishing", {}).get("shake", {})
        click_delay = shake_cfg.get("click_delay", 0.08)
        max_wait = shake_cfg.get("max_wait_seconds", 25.0)

        gx, gy, gw, gh = game_rect
        t_start = time.time()
        last_log_t = 0.0
        scan_count = 0

        custom_roi = shake_cfg.get("custom_roi")
        if custom_roi and len(custom_roi) == 4:
            self.log(f"[LURE] Bobber in water. Scanning CUSTOM area (pos=({custom_roi[0]}, {custom_roi[1]}), size={custom_roi[2]}x{custom_roi[3]})...")
        else:
            self.log("[LURE] Bobber in water. Scanning play area for circular SHAKE prompts...")

        consecutive_reel_frames = 0
        last_fish_x = None

        while self._running:
            elapsed = time.time() - t_start
            if elapsed > max_wait:
                self.log(f"[LURE] Lure phase timed out ({max_wait}s without fish hook). Returning to equip/cast.")
                return False

            scan_count += 1

            # Check if reel minigame has started
            bar_roi_x = gx + int(0.15 * gw)
            bar_roi_y = gy + int(0.40 * gh)
            bar_roi_w = int(0.70 * gw)
            bar_roi_h = int(0.45 * gh)

            check_img = self.screen.capture_roi(bar_roi_x, bar_roi_y, bar_roi_w, bar_roi_h)
            reel_state = self.detector.analyze_reel_game(check_img)
            if reel_state.is_active:
                if last_fish_x is None or abs(reel_state.fish_x - last_fish_x) < 80:
                    consecutive_reel_frames += 1
                else:
                    consecutive_reel_frames = 1
                last_fish_x = reel_state.fish_x

                # Require 3 consecutive frames with consistent fish position to prevent transient misdetections
                if consecutive_reel_frames >= 3:
                    self.log(f"[LURE] Reel minigame CONFIRMED (3 frames, fish at {reel_state.fish_x:.0f}px)! Hooked fish! Transitioning to Reeling...")
                    try:
                        import os
                        diag_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scratch")
                        os.makedirs(diag_dir, exist_ok=True)
                        check_img.save(os.path.join(diag_dir, "last_reel_transition.png"))
                    except Exception:
                        pass
                    return True
            else:
                if consecutive_reel_frames > 0:
                    self.log(f"[LURE] Deflected transient reel detection ({consecutive_reel_frames}/3 frames). Continuing shake...")
                consecutive_reel_frames = 0
                last_fish_x = None

            # Determine Shake ROI: Custom Region or Default Water Play Area
            if custom_roi and len(custom_roi) == 4:
                shake_roi_x, shake_roi_y, shake_roi_w, shake_roi_h = [int(v) for v in custom_roi]
            else:
                shake_roi_x = gx + int(0.08 * gw)
                shake_roi_y = gy + int(0.04 * gh)
                shake_roi_w = int(0.90 * gw)
                shake_roi_h = int(0.89 * gh)

            shake_crop = self.screen.capture_roi(shake_roi_x, shake_roi_y, shake_roi_w, shake_roi_h)
            shake_pt = self.detector.find_shake_button(shake_crop)

            if shake_pt:
                click_x = shake_roi_x + shake_pt[0]
                click_y = shake_roi_y + shake_pt[1]
                self.log(f"[LURE] SHAKE circle DETECTED at ({click_x}, {click_y})! Auto-clicking center...")
                self.input.mouse_click(click_x, click_y, button=1, delay=click_delay)
                # Immediately nudge mouse cursor away so subsequent prompts in the same spot are never occluded!
                self.input.mouse_move(click_x + 60, click_y + 40)
                time.sleep(0.12)  # Quick debounce so subsequent prompts are clicked immediately
            else:
                if elapsed - last_log_t > 2.0:
                    self.log(f"[LURE] SHAKE prompt NOT detected yet (scan #{scan_count}, elapsed: {elapsed:.1f}s). Waiting...")
                    last_log_t = elapsed
                time.sleep(0.012)

        return False

    def _handle_reeling(self, game_rect):
        """Controls M1 during the reeling minigame to stay on the fish."""
        self.set_status("REELING", {"fish_state": "TRACKING"})
        reel_cfg = self.config.get("fishing", {}).get("reeling", {})
        update_interval = reel_cfg.get("update_interval", 0.01)
        max_duration = reel_cfg.get("max_duration_seconds", 45.0)

        gx, gy, gw, gh = game_rect
        bar_roi_x = gx + int(0.15 * gw)
        bar_roi_y = gy + int(0.40 * gh)
        bar_roi_w = int(0.70 * gw)
        bar_roi_h = int(0.45 * gh)

        inactive_frames = 0
        max_inactive_frames = 25  # ~250ms of no blue water bar -> fish caught / ended
        last_color_state = None
        last_log_t = 0.0
        t_reel_start = time.time()

        mode_map = {
            "deepfish_precision": "DeepFish Precision (PD + Momentum)",
            "deepfish_anklebreak": "DeepFish AnkleBreak (SleepTrack)",
            "pulsed": "Legacy Rapid Click Pulses",
            "hold": "Direct Hold"
        }
        current_mode = getattr(self.controller, "steering_mode", "deepfish_precision")
        mode_name = mode_map.get(current_mode, current_mode)
        target_mode = self.config.get("fishing", {}).get("reeling", {}).get("target_mode", "fish_sprite")
        self.log(f"[REEL] Starting real-time tracking ({mode_name} | Target: {target_mode})...")

        while self._running:
            if time.time() - t_reel_start > max_duration:
                self.log(f"[REEL] Reeling watchdog safety timeout ({max_duration}s reached). Ending reeling phase.")
                break

            bar_img = self.screen.capture_roi(bar_roi_x, bar_roi_y, bar_roi_w, bar_roi_h)
            if target_mode == "line_scan":
                state = self.detector.detect_reel_lines(bar_img)
            else:
                state = self.detector.analyze_reel_game(bar_img)

            if not state.is_active:
                inactive_frames += 1
                if inactive_frames >= max_inactive_frames:
                    self.catches += 1
                    self.log(f"[CATCH] Reel bar disappeared. Fish successfully caught! Total catches: {self.catches}")
                    self.set_status("FISH_CAUGHT", {"catches": self.catches})
                    break
            else:
                inactive_frames = 0
                self.controller.update(state)
                fish_color = "GREEN" if state.is_green else "RED"

                # Log state changes or periodic updates
                now_t = time.time()
                if fish_color != last_color_state or (now_t - last_log_t > 1.5):
                    last_color_state = fish_color
                    last_log_t = now_t
                    fx = f"{state.fish_x:.1f}" if state.fish_x is not None else "--"
                    sx = f"{state.slider_center_x:.1f}" if state.slider_center_x is not None else "--"

                    if current_mode == "deepfish_precision":
                        v_rel = getattr(self.controller, "rel_vel", 0.0)
                        s_dist = getattr(self.controller, "stop_dist", 0.0)
                        p_err = getattr(self.controller, "pos_error", 0.0)
                        action = "HOLD M1" if getattr(self.controller, "last_want_down", False) else "RELEASE M1"
                        self.log(f"[REEL] Fish: {fish_color} (Fish={fx}, Slider={sx}, err={p_err:+.1f}px, v_rel={v_rel:+.0f}px/s, d_stop={s_dist:.1f}px) -> {action}")
                    elif state.is_green:
                        self.log(f"[REEL] Fish: GREEN (OVERLAPPING at X={fx}). Feathering M1 to maintain balance...")
                    else:
                        action_desc = "rapid clicks" if current_mode == "pulsed" else "holding M1"
                        direction = f"RIGHT ({action_desc})" if (state.fish_x or 0) > (state.slider_center_x or 0) else "LEFT (releasing M1)"
                        self.log(f"[REEL] Fish: RED (at X={fx}, Slider at X={sx}). Steering {direction}...")

                self.set_status("REELING", {
                    "fish_state": fish_color,
                    "fish_x": state.fish_x,
                    "slider_x": state.slider_center_x,
                    "catches": self.catches
                })

            time.sleep(update_interval)

        self.input.release_all()
        self.controller.reset()
