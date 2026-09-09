"""High-performance Reeling Controller for Grand Blue / Fisch minigames.
Supports DeepFish Precision (PD + momentum stopping distance + reachable span),
DeepFish AnkleBreak (momentum cancellation + stabilizer bursts), and legacy pulsed/hold modes.
"""

import time
from typing import Optional
try:
    from .detector import ReelGameState
    from .input_manager import InputManager
except ImportError:
    from detector import ReelGameState
    from input_manager import InputManager


class ReelingController:
    """Controls mouse input during the reeling minigame.
    
    Modes:
    - 'deepfish_precision': Continuous PD controller with EMA velocity smoothing,
      dynamic stopping distance (stop_dist = |rel_vel| * stopping_distance_multiplier),
      reachable span wall constraints, and in-zone gravity feathering. (Recommended)
    - 'deepfish_anklebreak': Momentum-based drive steps with target-crossing detection,
      counter-difference braking pulses, and stabilizer micro-bursts.
    - 'pulsed': Legacy heuristic pulse trains with quadratic coasting cutoff.
    - 'hold': Direct binary hold when slider is left of fish, release when right.
    """

    def __init__(self, input_manager: InputManager, config: dict):
        self.input = input_manager
        self.config = config.get("fishing", {}).get("reeling", {})

        # Primary steering mode
        self.steering_mode = self.config.get("steering_mode", "deepfish_precision")

        # --- DeepFish Precision Tuning Parameters ---
        self.kp = float(self.config.get("kp", 0.5))
        self.kd = float(self.config.get("kd", 0.3))
        self.velocity_smoothing = float(self.config.get("velocity_smoothing", 0.2))  # Alpha for EMA
        self.stopping_distance_multiplier = float(self.config.get("stopping_distance_multiplier", 3.0))
        self.sidebar_ratio = float(self.config.get("sidebar_ratio", 0.8))

        # --- DeepFish AnkleBreak Tuning Parameters ---
        self.stable_right_mult = float(self.config.get("stable_right_mult", 2.1))
        self.stable_right_div = float(self.config.get("stable_right_div", 1.4))
        self.stable_left_mult = float(self.config.get("stable_left_mult", 1.1))
        self.stable_left_div = float(self.config.get("stable_left_div", 1.0))
        self.ankle_break_right_mult = float(self.config.get("ankle_break_right_mult", 1.0))
        self.ankle_break_left_mult = float(self.config.get("ankle_break_left_mult", 0.9))
        self.stabilizer_loop = int(self.config.get("stabilizer_loop", 10))

        # --- Legacy Pulsed / Feather Tuning Parameters ---
        self.deadzone_px = self.config.get("deadzone_px", 15)
        self.green_feather_hold_ms = self.config.get("green_feather_hold_ms", 35)
        self.green_feather_release_ms = self.config.get("green_feather_release_ms", 50)

        # Kinematic state tracking (DeepFish Precision)
        self.bar_vel = 0.0
        self.fish_vel = 0.0
        self.rel_vel = 0.0
        self.pos_error = 0.0
        self.stop_dist = 0.0
        self.control_out = 0.0
        self.last_want_down = False

        self._prev_bar_x: Optional[float] = None
        self._prev_fish_x: Optional[float] = None
        self._prev_time: Optional[float] = None

        # Pulsed mode state tracking
        self._last_pulse_time = 0.0
        self._pulse_state_hold = False
        self._prev_error: Optional[float] = None

        # AnkleBreak state tracking
        self._ab_phase = "IDLE"  # 'DRIVE', 'BRAKE', 'STABILIZE'
        self._ab_phase_start = 0.0
        self._ab_target_duration = 0.0
        self._ab_counter_duration = 0.0
        self._ab_direction = "Right"  # Current drive direction
        self._ab_stabilize_count = 0

    def update(self, state: ReelGameState):
        """Processes one game frame during the reeling minigame and controls M1."""
        if not state.is_active:
            self.input.release_all()
            self.reset()
            return

        fish_x = state.fish_x
        slider_x = state.slider_center_x

        if fish_x is None or slider_x is None:
            # Fallback if positions cannot be uniquely resolved
            if state.is_green:
                self._apply_fallback_green_feather()
            else:
                self._set_mouse_state(False)
            return

        if self.steering_mode == "deepfish_precision":
            self._update_deepfish_precision(state, fish_x, slider_x)
        elif self.steering_mode == "deepfish_anklebreak":
            self._update_deepfish_anklebreak(state, fish_x, slider_x)
        elif self.steering_mode == "hold":
            self._update_hold(fish_x, slider_x)
        else:
            # Default to legacy pulsed
            self._update_pulsed(state, fish_x, slider_x)

    # =========================================================================
    # DeepFish Precision Mode (PD Controller + Stopping Distance + Span Bounds)
    # =========================================================================

    def _update_deepfish_precision(self, state: ReelGameState, fish_x: float, slider_x: float):
        now_sec = time.time()
        bar_x = slider_x

        # 1. Kinematic Velocity Filtering (EMA with outlier rejection)
        if self._prev_time is not None and self._prev_bar_x is not None and self._prev_fish_x is not None:
            dt = now_sec - self._prev_time
            if dt > 0.0005:
                raw_bar_vel = (bar_x - self._prev_bar_x) / dt
                raw_fish_vel = (fish_x - self._prev_fish_x) / dt

                # Reject teleport / single-frame visual glitch jumps
                if abs(fish_x - self._prev_fish_x) > 100:
                    raw_fish_vel = 0.0
                    self.fish_vel = 0.0

                alpha = max(0.01, min(1.0, self.velocity_smoothing))
                self.bar_vel = alpha * raw_bar_vel + (1.0 - alpha) * self.bar_vel
                self.fish_vel = alpha * raw_fish_vel + (1.0 - alpha) * self.fish_vel

                self._prev_bar_x = bar_x
                self._prev_fish_x = fish_x
                self._prev_time = now_sec
        else:
            self._prev_bar_x = bar_x
            self._prev_fish_x = fish_x
            self._prev_time = now_sec

        # 2. Position Error & Relative Velocity
        # PosError > 0 means slider is to the RIGHT of fish (needs left/release)
        # PosError < 0 means slider is to the LEFT of fish (needs right/hold)
        self.pos_error = bar_x - fish_x
        self.rel_vel = self.bar_vel - self.fish_vel

        # 3. Dynamic Stopping Distance
        self.stop_dist = abs(self.rel_vel) * self.stopping_distance_multiplier

        # 4. Reachable Span & Wall Constraints
        slider_w = state.slider_width or 155.0
        half_w = slider_w / 2.0
        bar_left = state.bar_left_x if state.bar_left_x is not None else 0.0
        bar_right = state.bar_right_x if state.bar_right_x is not None else (bar_left + 700.0)

        min_reach = bar_left + half_w
        max_reach = bar_right - half_w

        want_down = False

        if fish_x < min_reach:
            # Target is pinned against the extreme left wall! Force M1 release so gravity carries slider to left wall
            want_down = False
        elif fish_x > max_reach:
            # Target is pinned against the extreme right wall! Force M1 hold so slider reaches right wall
            want_down = True
        elif state.is_green or (state.slider_left_x is not None and state.slider_right_x is not None
                                and state.slider_left_x <= fish_x <= state.slider_right_x):
            # --- In-Zone / Catch Hover Physics ---
            if self.pos_error < -self.stop_dist:
                # Slider is left of fish by more than stopping distance: push right
                want_down = True
            elif self.pos_error > self.stop_dist:
                # Slider is right of fish by more than stopping distance: release left
                want_down = False
            elif self.rel_vel > 0:
                # Within stopping distance and already closing rightward: cut throttle to avoid overshoot!
                want_down = False
            else:
                # Within stopping distance, stationary or falling left: hold M1 to counter in-game gravity!
                want_down = True
        else:
            # --- Out-of-Zone / Chase Physics (PD Control) ---
            self.control_out = self.kp * self.pos_error + self.kd * self.rel_vel
            want_down = (self.control_out <= 0)

        self.last_want_down = want_down
        self._set_mouse_state(want_down)

    # =========================================================================
    # DeepFish AnkleBreak Mode (SleepTrack + Counter-Braking + Stabilizer Bursts)
    # =========================================================================

    def _update_deepfish_anklebreak(self, state: ReelGameState, fish_x: float, slider_x: float):
        now = time.time()
        bar_x = slider_x

        slider_w = state.slider_width or 155.0
        half_w = slider_w / 2.0
        bar_left = state.bar_left_x if state.bar_left_x is not None else 0.0
        bar_right = state.bar_right_x if state.bar_right_x is not None else (bar_left + 700.0)
        zone_inset = min(slider_w * self.sidebar_ratio, (bar_right - bar_left) * 0.4)
        max_left_bar = bar_left + zone_inset
        max_right_bar = bar_right - zone_inset

        # Wall edge limits
        if fish_x < max_left_bar:
            self._set_mouse_state(False)
            self._ab_phase = "IDLE"
            return
        elif fish_x > max_right_bar:
            self._set_mouse_state(True)
            self._ab_phase = "IDLE"
            return

        # AnkleBreak State Machine
        if self._ab_phase == "IDLE":
            if bar_x > fish_x:
                # Move Left (release M1)
                diff = (bar_x - fish_x) * self.stable_left_mult
                counter_diff = diff / max(0.1, self.stable_left_div)
                self._ab_direction = "Left"
                self._ab_target_duration = max(0.015, diff / 1000.0)
                self._ab_counter_duration = max(0.01, counter_diff / 1000.0)
                self._set_mouse_state(False)
                self._ab_phase = "DRIVE"
                self._ab_phase_start = now
            else:
                # Move Right (hold M1)
                diff = (fish_x - bar_x) * self.stable_right_mult
                counter_diff = diff / max(0.1, self.stable_right_div)
                self._ab_direction = "Right"
                self._ab_target_duration = max(0.015, diff / 1000.0)
                self._ab_counter_duration = max(0.01, counter_diff / 1000.0)
                self._set_mouse_state(True)
                self._ab_phase = "DRIVE"
                self._ab_phase_start = now

        elif self._ab_phase == "DRIVE":
            elapsed = now - self._ab_phase_start
            crossed = (self._ab_direction == "Left" and bar_x <= fish_x) or \
                      (self._ab_direction == "Right" and bar_x >= fish_x)

            if crossed or elapsed >= self._ab_target_duration:
                held_fraction = min(1.0, elapsed / max(1e-4, self._ab_target_duration))
                self._ab_counter_duration *= held_fraction

                brake_down = (self._ab_direction == "Left")
                self._set_mouse_state(brake_down)
                self._ab_phase = "BRAKE"
                self._ab_phase_start = now

        elif self._ab_phase == "BRAKE":
            elapsed = now - self._ab_phase_start
            if elapsed >= self._ab_counter_duration:
                self._ab_phase = "STABILIZE"
                self._ab_stabilize_count = 0
                self._ab_phase_start = now

        elif self._ab_phase == "STABILIZE":
            if self._ab_stabilize_count < self.stabilizer_loop:
                if self.input.is_m1_down:
                    self.input.mouse_up(1)
                else:
                    self.input.mouse_down(1)
                self._ab_stabilize_count += 1
            else:
                self._set_mouse_state(False)
                self._ab_phase = "IDLE"

    # =========================================================================
    # Legacy Modes: Direct Hold and Pulsed Cruise Control
    # =========================================================================

    def _update_hold(self, fish_x: float, slider_x: float):
        error = fish_x - slider_x
        if error < -self.deadzone_px:
            self._set_mouse_state(False)
        elif error > self.deadzone_px:
            self._set_mouse_state(True)

    def _update_pulsed(self, state: ReelGameState, fish_x: float, slider_x: float):
        now_ms = time.time() * 1000.0
        now_sec = time.time()
        error = fish_x - slider_x

        closing_speed = 0.0
        if self._prev_error is not None and self._prev_time is not None:
            dt = max(now_sec - self._prev_time, 1e-3)
            closing_speed = (self._prev_error - error) / dt
        self._prev_error = error
        self._prev_time = now_sec

        # Case A: Fish is to the LEFT of slider -> Slider needs to move LEFT (under gravity)
        if error < -self.deadzone_px:
            if error < -70 or closing_speed < -50:
                self._pulse_state_hold = False
                self._set_mouse_state(False)
                return
            elif error < -25:
                hold_ms, release_ms = 20, 45
            else:
                hold_ms, release_ms = 20, 35

            elapsed = now_ms - self._last_pulse_time
            if self._pulse_state_hold:
                if elapsed >= hold_ms:
                    self._set_mouse_state(False)
                    self._pulse_state_hold = False
                    self._last_pulse_time = now_ms
            else:
                if self.input.is_m1_down:
                    self._set_mouse_state(False)
                    self._last_pulse_time = now_ms
                elif elapsed >= release_ms:
                    self._set_mouse_state(True)
                    self._pulse_state_hold = True
                    self._last_pulse_time = now_ms
            return

        # Case B: Fish is to the RIGHT of slider -> Accelerate slider rightwards
        if error > self.deadzone_px:
            d_coast = (max(0.0, closing_speed) ** 2) / 4000.0
            if closing_speed > 200 and error <= d_coast + 20:
                self._pulse_state_hold = False
                self._set_mouse_state(False)
                return

            if error > 65:
                self._pulse_state_hold = True
                self._set_mouse_state(True)
                self._last_pulse_time = now_ms
                return
            elif error > 25:
                hold_ms, release_ms = (40, 15) if not state.is_green else (35, 20)
            else:
                hold_ms, release_ms = 20, 35

            elapsed = now_ms - self._last_pulse_time
            if self._pulse_state_hold:
                if elapsed >= hold_ms:
                    self._set_mouse_state(False)
                    self._pulse_state_hold = False
                    self._last_pulse_time = now_ms
            else:
                if elapsed >= release_ms:
                    self._set_mouse_state(True)
                    self._pulse_state_hold = True
                    self._last_pulse_time = now_ms
            return

        # Case C: Centered inside deadzone
        elapsed = now_ms - self._last_pulse_time
        if self._pulse_state_hold:
            if elapsed >= 20:
                self._set_mouse_state(False)
                self._pulse_state_hold = False
                self._last_pulse_time = now_ms
        else:
            if elapsed >= 50:
                self._set_mouse_state(True)
                self._pulse_state_hold = True
                self._last_pulse_time = now_ms

    def _apply_fallback_green_feather(self):
        now_ms = time.time() * 1000.0
        elapsed = now_ms - self._last_pulse_time
        if self._pulse_state_hold:
            if elapsed >= self.green_feather_hold_ms:
                self._set_mouse_state(False)
                self._pulse_state_hold = False
                self._last_pulse_time = now_ms
        else:
            if elapsed >= self.green_feather_release_ms:
                self._set_mouse_state(True)
                self._pulse_state_hold = True
                self._last_pulse_time = now_ms

    def _set_mouse_state(self, down: bool):
        if down:
            if not self.input.is_m1_down:
                self.input.mouse_down(1)
        else:
            if self.input.is_m1_down:
                self.input.mouse_up(1)

    def reset(self):
        """Resets all controller internal states and releases mouse buttons."""
        self._pulse_state_hold = False
        self._last_pulse_time = 0.0
        self._prev_error = None
        self._prev_bar_x = None
        self._prev_fish_x = None
        self._prev_time = None
        self.bar_vel = 0.0
        self.fish_vel = 0.0
        self.rel_vel = 0.0
        self.pos_error = 0.0
        self.stop_dist = 0.0
        self.control_out = 0.0
        self._ab_phase = "IDLE"
        self._ab_stabilize_count = 0
        self.input.release_all()

