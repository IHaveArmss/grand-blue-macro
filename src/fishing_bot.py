"""Main state machine and autonomous loop for Grand Blue fishing with detailed state logging."""

import os
import time
import threading
from typing import Optional, Callable, Tuple
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

    def _get_shake_roi(self, game_rect) -> Tuple[int, int, int, int]:
        """Resolves the shake scanning area (custom user ROI or default play area)."""
        shake_cfg = self.config.get("fishing", {}).get("shake", {})
        custom_roi = shake_cfg.get("custom_roi")
        if custom_roi and len(custom_roi) == 4:
            return tuple(int(v) for v in custom_roi)
        gx, gy, gw, gh = game_rect
        return (
            gx + int(0.08 * gw),
            gy + int(0.04 * gh),
            int(0.90 * gw),
            int(0.89 * gh),
        )

    def _get_reel_roi(self, game_rect) -> Tuple[int, int, int, int]:
        """Resolves the reeling minigame bar area."""
        gx, gy, gw, gh = game_rect
        return (
            gx + int(0.15 * gw),
            gy + int(0.40 * gh),
            int(0.70 * gw),
            int(0.45 * gh),
        )

    def _run_loop(self):
        self.set_status("STARTING")
        time.sleep(0.3)

        while self._running:
            try:
                rect = self._locked_rect
                gx, gy, gw, gh = rect

                reel_roi = self._get_reel_roi(rect)
                shake_roi = self._get_shake_roi(rect)

                # Preemption Check 1: If reel minigame is active, reel immediately!
                pre_reel = self.screen.capture_roi(*reel_roi)
                if self.detector.analyze_reel_game(pre_reel).is_active:
                    self.log("[LOOP] Active reel minigame detected before cast! Jumping directly to Reeling...")
                    self._handle_reeling(rect)
                    continue

                # Preemption Check 2: If SHAKE prompt is active, shake immediately!
                pre_shake = self.screen.capture_roi(*shake_roi)
                if self.detector.find_shake_button(pre_shake):
                    self.log("[LOOP] Active SHAKE prompt detected before cast! Rod is in water. Jumping to Lure/Shake...")
                    reeling_ready = self._handle_shake(rect)
                    if reeling_ready and self._running:
                        self._handle_reeling(rect)
                    continue

                # Stage 0: Verify / Equip Rod
                self._handle_equip_rod(rect)
                if not self._running:
                    break

                # Post-equip Preemption Checks
                if self.detector.analyze_reel_game(self.screen.capture_roi(*reel_roi)).is_active:
                    self.log("[LOOP] Active reel minigame detected after equip! Jumping directly to Reeling...")
                    self._handle_reeling(rect)
                    continue

                if self.detector.find_shake_button(self.screen.capture_roi(*shake_roi)):
                    self.log("[LOOP] Active SHAKE prompt detected after equip! Rod is in water. Jumping to Lure/Shake...")
                    reeling_ready = self._handle_shake(rect)
                    if reeling_ready and self._running:
                        self._handle_reeling(rect)
                    continue

                # Stage 1: Cast
                cast_result = self._handle_cast(rect)
                if not self._running:
                    break

                if cast_result == "REELING":
                    self._handle_reeling(rect)
                    continue

                if cast_result == "SHAKE":
                    reeling_ready = self._handle_shake(rect)
                    if reeling_ready and self._running:
                        self._handle_reeling(rect)
                    continue

                # Stage 2: Lure & Shake (Always proceed to shake, never skip it!)
                uncertain = (cast_result is False)
                reeling_ready = self._handle_shake(rect, uncertain_cast=uncertain)
                if not self._running:
                    break

                # If lure/shake timed out without fish hook and cast was uncertain, retry equip
                if not reeling_ready:
                    if uncertain:
                        self.log("[BOT] No shake prompt appeared after uncertain cast. Retrying rod equip...")
                        time.sleep(0.5)
                    continue

                # Stage 3: Reeling Minigame
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
        Uses real-time velocity tracking and predictive lead release to compensate
        for input and rendering latency before overshooting the top white cap.
        Returns True if cast was successful (meter detected and released), False otherwise.
        """
        self.set_status("CASTING")
        cast_cfg = self.config.get("fishing", {}).get("cast", {})
        max_hold = cast_cfg.get("max_hold_time", 1.4)
        min_hold = cast_cfg.get("min_hold_time", 0.18)
        target_fill_pct = float(cast_cfg.get("lead_release_pct", 92.0))
        lead_time_ms = float(cast_cfg.get("lead_time_ms", 45.0))
        lead_time_sec = max(0.0, lead_time_ms / 1000.0)

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

        # Cast ROI: generously covers 40% to 75% of window width and 18% to 83% of height
        # Grand Blue's cast capsule sits at ~62% of window width on the right of the character
        cast_roi_x = gx + int(0.40 * gw)
        cast_roi_y = gy + int(0.18 * gh)
        cast_roi_w = int(0.35 * gw)
        cast_roi_h = int(0.65 * gh)

        reel_roi = self._get_reel_roi(game_rect)
        shake_roi = self._get_shake_roi(game_rect)

        # Preemption Check 1: Is the reel minigame ALREADY active on screen before pressing M1?
        pre_bar = self.screen.capture_roi(*reel_roi)
        if self.detector.analyze_reel_game(pre_bar).is_active:
            self._rod_equipped = True
            self.log("[CAST] Reel minigame already active on screen! Skipping cast to Reel immediately...")
            return "REELING"

        # Preemption Check 2: Is a SHAKE prompt ALREADY visible on screen?
        pre_shake = self.screen.capture_roi(*shake_roi)
        if self.detector.find_shake_button(pre_shake):
            self._rod_equipped = True
            self.log("[CAST] SHAKE prompt already visible on screen! Rod is in water. Entering Lure/Shake immediately...")
            return "SHAKE"

        t_start = time.time()
        self.input.mouse_down(1)
        last_log_t = 0.0
        best_fill_pct = 0.0
        cast_success = False
        first_detect_saved = False

        # Velocity tracking: v = d(fill_pct) / dt (% per second)
        last_t = t_start
        last_pct = 0.0
        fill_velocity = 0.0

        while self._running:
            now = time.time()
            elapsed = now - t_start
            if elapsed >= max_hold:
                if best_fill_pct >= 30.0:
                    self.log(f"[CAST] Max hold time reached ({max_hold:.2f}s) with best fill={best_fill_pct:.1f}%. Releasing M1.")
                    cast_success = True
                else:
                    self.log(f"[CAST] Max hold time ({max_hold:.2f}s) reached with NO cast meter detected (best={best_fill_pct:.1f}%). Releasing M1.")
                    cast_success = False
                break

            # Watchdog during M1 hold: sample periodically
            if elapsed >= 0.10 and int(elapsed * 100) % 7 == 0:
                bar_crop = self.screen.capture_roi(*reel_roi)
                if self.detector.analyze_reel_game(bar_crop).is_active:
                    self.input.mouse_up(1)
                    self._rod_equipped = True
                    self.log("[CAST] Reel minigame detected during cast hold! Fish hooked! Transitioning to Reeling...")
                    return "REELING"

                shake_crop = self.screen.capture_roi(*shake_roi)
                if self.detector.find_shake_button(shake_crop):
                    self.input.mouse_up(1)
                    self._rod_equipped = True
                    self.log("[CAST] SHAKE prompt detected during cast hold! Rod is in water. Transitioning to Shake...")
                    return "SHAKE"

            cast_crop = self.screen.capture_roi(cast_roi_x, cast_roi_y, cast_roi_w, cast_roi_h)
            state = self.detector.analyze_cast_progress(cast_crop)

            if state.detected:
                # Diagnostic snapshot on initial detection for visual verification
                if not first_detect_saved:
                    first_detect_saved = True
                    try:
                        os.makedirs("scratch", exist_ok=True)
                        cast_crop.save("scratch/last_cast_detected.png")
                    except Exception:
                        pass

                # Calculate rising velocity
                dt = now - last_t
                if dt >= 0.012:
                    instant_v = (state.fill_pct - last_pct) / dt
                    if instant_v > 0:
                        fill_velocity = 0.7 * instant_v + 0.3 * fill_velocity
                    last_pct = state.fill_pct
                    last_t = now

                # Predictive fill level accounting for lead latency
                predicted_fill = state.fill_pct + (fill_velocity * lead_time_sec)
                if state.fill_pct > best_fill_pct:
                    best_fill_pct = state.fill_pct

                if elapsed >= min_hold:
                    # Trigger 1: Predictive lead release (rising velocity will reach target within lead_time_ms)
                    if predicted_fill >= target_fill_pct:
                        self.log(
                            f"[CAST] Lead release triggered! Current fill={state.fill_pct:.1f}%, "
                            f"velocity={fill_velocity:.1f}%/s, predicted={predicted_fill:.1f}% "
                            f"(target={target_fill_pct:.1f}%, lead={lead_time_ms:.0f}ms). Releasing M1."
                        )
                        cast_success = True
                        try:
                            os.makedirs("scratch", exist_ok=True)
                            cast_crop.save("scratch/last_cast_release.png")
                        except Exception:
                            pass
                        break

                    # Trigger 2: Direct target fill reach
                    if state.fill_pct >= target_fill_pct:
                        self.log(f"[CAST] Target fill REACHED ({state.fill_pct:.1f}% >= {target_fill_pct:.1f}%)! Releasing M1.")
                        cast_success = True
                        try:
                            os.makedirs("scratch", exist_ok=True)
                            cast_crop.save("scratch/last_cast_release.png")
                        except Exception:
                            pass
                        break

                    # Trigger 3: Peak / reversal detection (meter reached top and bounced down)
                    if best_fill_pct >= 75.0 and state.fill_pct < (best_fill_pct - 3.5):
                        self.log(f"[CAST] Peak reversal detected ({best_fill_pct:.1f}% -> {state.fill_pct:.1f}%)! Releasing M1.")
                        cast_success = True
                        try:
                            os.makedirs("scratch", exist_ok=True)
                            cast_crop.save("scratch/last_cast_release.png")
                        except Exception:
                            pass
                        break

            if elapsed - last_log_t > 0.20:
                if state.detected and state.fill_pct > 0:
                    self.log(
                        f"[CAST] Meter rising... fill={state.fill_pct:.1f}% "
                        f"(dist={state.dist_px}px, v={fill_velocity:.1f}%/s, elapsed={elapsed:.2f}s)."
                    )
                last_log_t = elapsed

            time.sleep(0.005)

        self.input.mouse_up(1)

        if cast_success or best_fill_pct >= 30.0:
            self._rod_equipped = True
            self.log(f"[CAST] Cast released at {best_fill_pct:.1f}%! Entering lure phase immediately...")
            time.sleep(0.04)
            return True
        else:
            # Post-check 1: Did reel minigame appear upon release?
            post_bar = self.screen.capture_roi(*reel_roi)
            if self.detector.analyze_reel_game(post_bar).is_active:
                self._rod_equipped = True
                self.log("[CAST] Active reel minigame detected upon release! Fish hooked! Transitioning to Reeling...")
                return "REELING"

            # Post-check 2: Did a SHAKE prompt appear upon release?
            post_shake = self.screen.capture_roi(*shake_roi)
            if self.detector.find_shake_button(post_shake):
                self._rod_equipped = True
                self.log("[CAST] Active SHAKE prompt detected upon release! Rod is in water. Transitioning to Shake...")
                return "SHAKE"

            self.log("[CAST] No cast meter detected during M1 hold. Entering Lure/Shake to check for bobber...")
            return False

    def _handle_shake(self, game_rect, uncertain_cast: bool = False) -> bool:
        """Clicks SHAKE buttons until reel minigame appears or timeout."""
        self.set_status("LURING_SHAKE")
        shake_cfg = self.config.get("fishing", {}).get("shake", {})
        click_delay = shake_cfg.get("click_delay", 0.08)
        base_max_wait = shake_cfg.get("max_wait_seconds", 25.0)
        max_wait = 5.0 if uncertain_cast else base_max_wait

        gx, gy, gw, gh = game_rect
        t_start = time.time()
        last_log_t = 0.0
        scan_count = 0

        shake_roi = self._get_shake_roi(game_rect)
        reel_roi = self._get_reel_roi(game_rect)
        shake_roi_x, shake_roi_y, shake_roi_w, shake_roi_h = shake_roi

        if shake_cfg.get("custom_roi"):
            self.log(f"[LURE] Bobber in water. Scanning CUSTOM area (pos=({shake_roi_x}, {shake_roi_y}), size={shake_roi_w}x{shake_roi_h})...")
        else:
            self.log("[LURE] Bobber in water. Scanning play area for circular SHAKE prompts...")

        consecutive_reel_frames = 0
        last_fish_x = None
        last_clicked_x = None
        last_clicked_y = None
        last_clicked_t = 0.0

        while self._running:
            elapsed = time.time() - t_start
            if elapsed > max_wait:
                if uncertain_cast:
                    self.log(f"[LURE] No shake prompt detected within {max_wait:.1f}s after unconfirmed cast. Returning to equip.")
                else:
                    self.log(f"[LURE] Lure phase timed out ({max_wait:.1f}s without fish hook). Returning to equip/cast.")
                return False

            scan_count += 1
            play_img = self.screen.capture_roi(*shake_roi)

            # Step 1: Check for circular SHAKE prompts (highest priority - click immediately upon spawning!)
            shake_pt = self.detector.find_shake_button(play_img)
            if shake_pt:
                # Prompt detected! Rod is definitely cast in water: reset to full max_wait
                if uncertain_cast:
                    uncertain_cast = False
                    max_wait = base_max_wait
                click_x = shake_roi_x + shake_pt[0]
                click_y = shake_roi_y + shake_pt[1]

                # Prevent double-clicking the same prompt while it plays its fade-out animation
                now = time.time()
                is_duplicate = (
                    last_clicked_x is not None
                    and (now - last_clicked_t < 0.6)
                    and abs(click_x - last_clicked_x) < 80
                    and abs(click_y - last_clicked_y) < 80
                )

                if not is_duplicate:
                    last_clicked_x = click_x
                    last_clicked_y = click_y
                    last_clicked_t = now

                    self.log(f"[LURE] SHAKE circle DETECTED at ({click_x}, {click_y})! Auto-clicking center...")
                    self.input.mouse_click(click_x, click_y, button=1, delay=click_delay)
                    # Diagnostic snapshot of the clicked prompt
                    try:
                        import os
                        from PIL import ImageDraw
                        os.makedirs("scratch", exist_ok=True)
                        click_vis = play_img.copy()
                        draw = ImageDraw.Draw(click_vis)
                        px, py = shake_pt
                        draw.ellipse([px - 15, py - 15, px + 15, py + 15], outline=(0, 255, 0), width=3)
                        click_vis.save("scratch/last_shake_click.png")
                    except Exception:
                        pass
                    time.sleep(0.04)  # Brief yield for click to register without twitching cursor
                    continue
                else:
                    # Prompt is still fading out. Yield briefly and keep scanning for next prompt.
                    time.sleep(0.03)
                    continue

            # Step 2: Check if reel minigame has started (only when no prompt is present)
            check_img = self.screen.capture_roi(*reel_roi)

            reel_state = self.detector.analyze_reel_game(check_img)
            if reel_state.is_active:
                if last_fish_x is None or abs(reel_state.fish_x - last_fish_x) < 180:
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

            if elapsed - last_log_t > 2.0:
                self.log(f"[LURE] SHAKE prompt NOT detected yet (scan #{scan_count}, elapsed: {elapsed:.1f}s). Waiting...")
                last_log_t = elapsed
                try:
                    import os
                    os.makedirs("scratch", exist_ok=True)
                    play_img.save("scratch/last_shake_scan.png")
                except Exception:
                    pass
            time.sleep(0.010)

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
                    if time.time() - t_reel_start >= 1.0:
                        self.catches += 1
                        self.log(f"[CATCH] Reel bar disappeared. Fish successfully caught! Total catches: {self.catches}")
                        self.set_status("FISH_CAUGHT", {"catches": self.catches})
                    else:
                        self.log("[REEL] Reel minigame ended early (<1.0s). Ending reeling phase.")
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
