# 🐟 Grand Blue Fishing Macro Documentation (`fish.md`)

A high-performance, automated fishing bot and multi-purpose macro utility designed specifically for **Grand Blue** (and similar Roblox fishing games) on Linux (X11).

---

## 📑 Table of Contents
1. [Key Features](#-key-features)
2. [Current State & Where We Left Off (Proven Discoveries & Fixes)](#-current-state--where-we-left-off)
3. [Comparative Analysis of Online Fishing Macros](#-comparative-analysis-of-online-fishing-macros)
4. [How the Fishing Cycle Works](#-how-the-fishing-cycle-works)
5. [Reeling Physics & Controller Mechanics](#-reeling-physics--controller-mechanics)
6. [Configuration Reference (`config.json`)](#-configuration-reference-configjson)
7. [Automated Test Suite & Regressions (`tests/test_detection.py`)](#-automated-test-suite--regressions)
8. [Quick Start & Usage](#-quick-start--usage)

---

## 🌟 Key Features

- **Sub-Millisecond Native Screen Capture (`src/screen.py`)**: Utilizes raw X11 ZPixmap byte buffer transfers (~0.2–0.4ms) without OpenCV, ffmpeg, or external screenshot overhead.
- **Sober / Wine RawInput Compatibility**: Injects 1-pixel micro-motion deltas prior to clicks to ensure game windows using RawInput register hover states and pointer events reliably.
- **Physical Water Bar Ground Truth**: Requires physical blue minigame cylinder presence ($\ge 250$ sampled blue pixels in fish corridor) to declare the minigame active. Completely immune to catch popups, character clothing, green hair, or chat messages.
- **Anti-Occlusion Shake Prompt Clicker**: Detects circular navy-blue **SHAKE** buttons across 40%–100% 3D perspective scales. Immediately nudges the cursor 60px away post-click, ensuring repeated prompts at the exact same location remain fully visible.
- **Adaptive Navy Perimeter Checks**: Combines precomputed binary letter masks with 4-way circular disk checks, allowing detection even under partial pointer or visual obstruction.
- **Kinematic Pulsed Reeling Controller (`src/controller.py`)**:
  - Distance-based pulse adaptation: full holds for far chases ( > 65$), pulsed cruise for approach (5 < error \le 65$), and micro-taps for soft landing ( \le 25$).
  - Physics-based coasting cutoff: calculates stopping distance {coast} = v^2 / 4000$ to cut throttle when momentum will carry the slider smoothly onto the fish.
  - Left-fall acceleration: eliminates resistive braking when the fish darts left, allowing full gravity acceleration.
- **Hotbar Slot Verification (`src/fishing_bot.py`)**: Inspects the hotbar across  \in [0.30, 0.76] \cdot gw$ and  \in [0.85, 1.0] \cdot gh$. Automatically detects the white selection box around Slot 9 without toggle-off regressions.
- **3-Menu GTK 3 Desktop GUI (`src/gui.py`)**: Fishing controls, sensitivity sliders, monitor selector, and tabs for upcoming Mining and Skill macros.

---

## 🧠 Current State & Where We Left Off

Extensive frame-by-frame diagnostics on footage (`ai.mkv`, `ai-2.mkv`, `ai-3.mkv`, `ai-4.mkv`) uncovered three subtle edge cases that have now been permanently resolved:

### 1. Catch / End-of-Reel Detection Desync
- **The Issue**: After catching a fish, the player character returned to holding the rod ready to throw the bobber ("look of throwing the bobber"), but the script remained stuck in the "catching fish / reeling" stage or rapidly jumped back to it.
- **The Root Cause**:
  1. `analyze_reel_game` previously evaluated minigame status solely based on the presence of $\ge 20$ red or green pixels in `bar_roi`. When the minigame ended, catch notifications (e.g. green `"You successfully caught a..."` text) or character features (e.g. green hair or red backpack) triggered false positive active states.
  2. In `_handle_equip_rod`, the hotbar crop stopped at  = 0.68 \cdot gw$ and  = 0.99 \cdot gh$. At 1800x1200, Slot 9 numbers and border reside at  \in [0.69, 0.75] \cdot gw$ and  \in [0.985, 1.0] \cdot gh$. The crop missed Slot 9 entirely, saw 0 white pixels, and tapped key '9' to equip—which actually **unequipped** the rod!
- **The Solution**:
  1. `analyze_reel_game` now checks for the physical blue water cylinder ($\ge 250$ sampled blue pixels in the fish corridor). During active reeling, blue pixels measure 6,000–11,000; the millisecond the minigame finishes, blue pixels drop to 0.
  2. `_handle_equip_rod` crop was enlarged to  = 0.30 \cdot gw$,  = 0.85 \cdot gh$,  = 0.46 \cdot gw$, and  = gh - hy$. Slot 9 now verifies with 98 white border pixels.
  3. Added a 45-second safety watchdog timeout to `_handle_reeling` to prevent infinite loops under any unexpected visual glitch.

### 2. Consecutive Shake Buttons in the Same Spot
- **The Issue**: When two shake prompts appeared consecutively at the exact same location, the second prompt was completely ignored.
- **The Root Cause**: When `mouse_click` fired, the mouse cursor remained resting directly over the prompt center. In Roblox/OS captures, the white mouse pointer sitting on top of the white letters "SHAKE" distorted the cluster aspect ratio and template matching score, dropping confidence below 0.50.
- **The Solution**:
  1. `_handle_shake` now executes an immediate 60px cursor offset (`mouse_move(click_x + 60, click_y + 40)`) right after clicking.
  2. `find_shake_button` now features adaptive navy circle perimeter validation (`b > 35, b - r > 15, b - g > 10, r < 40, g < 60`). If the template score is partially degraded ($\ge 0.28$), confirming top and bottom navy pixels confirms the button.

### 3. Hard Fish Left/Right Tracking & Ghost Slider at  \approx 1117$
- **The Issue**: For hard fish with high movement speed and erratic left/right reversals, the slider lost tracking and pinned itself to the left, resulting in `Streak Lost`.
- **The Root Cause**:
  1. In `analyze_reel_game`, candidate spans were filtered by `abs(center - fish_x) <= 500`. In `ai-4.mkv` Frame 127, the fish darted to the far right ( = 1020.8$) while the slider fell to the left ( = 385.5$). The distance was 35.3 ($> 500), so the real 160px slider was discarded!
  2. A 60px dark dock post graphic at the far right edge ( = 1117.5$) happened to be within 100px of the fish, so it was chosen as the slider.
  3. Because 117.5 > 1020.8$, the macro believed the slider was to the right of the fish and commanded M1 release. The real slider remained stuck on the left until the fish escaped.
- **The Solution**:
  1. Removed the artificial 500px distance restriction.
  2. Enforced a minimum candidate span width of 75px (slider is $\sim 140\dots 185, dock edge is $\le 60).
  3. Candidate spans are ranked by closeness to ideal slider width ($\min |span\_w - 155|$). In Frame 127, Span 2 has $|160 - 155| = 5$, cleanly beating the edge artifact ($|60 - 155| = 95$).
  4. In `ReelingController`, when the fish is pulling away to the left (`closing_speed < -50`), resistive micro-braking is eliminated to allow full freefall under gravity.

---

## 🌐 Comparative Analysis of Online Fishing Macros

Several fishing macros exist online for Roblox titles (such as *Fisch*, *Deepwoken*, and *Grand Blue*). Below is a breakdown of common architectures, trade-offs, and why our system is designed the way it is:

| Macro Type / Architecture | Mechanism | Common Flaws & Failures | Grand Blue Macro Approach |
|---|---|---|---|
| **AutoHotkey PixelSearch Macros** (e.g. FischMacro, Deepwoken AHK) | Checks single-pixel coordinates or 1D vertical line sweeps for white/red colors. Uses static sleep delays (`Sleep 50`). | • Breaks whenever lighting changes (day/night cycles, shadows).<br>• Zero momentum awareness: overshoots fish repeatedly on high-tier rods.<br>• Cannot detect dynamic 3D shake bubbles. | **Full 2D Spatial Clustering & Column Integration**: Scans column density across the bar, computes closing velocity $\frac{d(error)}{dt}$, and cuts throttle dynamically before overshoot. |
| **OpenCV Python Macros** (`cv2.matchTemplate`) | Captures screen via MSS or PyAutoGUI, converts to grayscale, and runs template matching with Canny edges. | • High CPU latency (15–40ms per frame), causing slider lag.<br>• Scale-sensitive: breaks when user zooms camera in/out.<br>• Fails completely when cursor hovers over target. | **Pure In-Memory Raw X11 Scanning (< 0.5ms)**: No OpenCV dependency. Precomputed binary masks with non-letter fill penalties and adaptive circular boundary checks that operate down to 40% scale. |
| **OCR-Based Macros** (Tesseract / EasyOCR) | Runs OCR on cropped regions looking for "SHAKE" text. | • Extreme latency (200–600ms per frame). Completely unusable for real-time fish tracking. | **Clustered Binary Letter Mask**: Evaluates word bounding box, aspect ratio (2.0–5.8), and letter pixel distribution in < 1ms. |
| **Bang-Bang (Full Hold) Macros** | Holds M1 continuously when  > 0$, releases when  < 0$. | • Game slider has acceleration physics: full hold builds massive rightward momentum that carries the slider way past the fish. | **Kinematic Pulsed Cruise Controller**: Full hold for distance closing, but modulates duty cycle (40ms hold / 15ms release) and cuts throttle using stopping distance equation {coast} = \frac{v^2}{4000}$. |

---

## 🎮 How the Fishing Cycle Works

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Equip Rod Slot (Slot 9, verified via white border)       │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. Cast Rod: Hold M1 -> Monitor Cyan Capsule -> Release     │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. Lure Phase: Detect & Click "SHAKE" Prompts (with Nudge)  │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. Reeling Minigame (Physical Blue Water Bar Active)         │
│    - Red Fish: Direction-aware steering (Hold right / Free left)
│    - Green Fish: Feather M1 (35ms hold / 50ms release)      │
│    - Watchdog: 45s safety timeout                           │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. Catch Complete: Blue bar disappears -> 1.5s Cooldown     │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔬 Reeling Physics & Controller Mechanics

In Grand Blue, reeling slider physics are asymmetric:
- **Rightward Movement**: Driven by user input (holding M1). The longer M1 is held, the higher the slider's rightward velocity.
- **Leftward Movement**: Driven entirely by in-game gravity and drag. Releasing M1 lets the slider fall leftward.
- **Overlapping State (Green Fish)**: When the slider is on the fish, progress fills. If the slider leaves the fish, the fish turns red and progress decays.

### Controller Algorithms (`src/controller.py`)

The macro features multiple interchangeable reeling algorithms:

#### 1. DeepFish Precision Controller (`"deepfish_precision"`, Default & Recommended)
Ported from the advanced kinematic control engine of DeepFish:
1. **Exponential Moving Average (EMA) Velocity Smoothing**:
   $$\bar{v}_{bar} = \alpha \cdot v_{bar} + (1 - \alpha) \cdot \bar{v}_{bar}$$
   $$\bar{v}_{fish} = \alpha \cdot v_{fish} + (1 - \alpha) \cdot \bar{v}_{fish}$$
   Includes single-frame outlier rejection ($\Delta > 100\text{px}$) to eliminate visual teleport spikes.
2. **Dynamic Momentum Stopping Distance**:
   $$d_{stop} = |v_{rel}| \times \text{StoppingDistanceMultiplier}$$
   where $v_{rel} = \bar{v}_{bar} - \bar{v}_{fish}$.
3. **Reachable Span & Wall Constraints**:
   Calculates $MinReach = BarLeft + \frac{W}{2}$ and $MaxReach = BarRight - \frac{W}{2}$.
   - If fish is pinned at left boundary ($< MinReach$), M1 is forced released to let gravity carry the slider smoothly to the left edge.
   - If fish is pinned at right boundary ($> MaxReach$), M1 is held continuously.
4. **In-Zone (Catch Window) Gravity Feathering**:
   - If $PosError < -d_{stop}$, throttle is applied.
   - If $PosError > d_{stop}$, throttle is cut.
   - If $v_{rel} > 0$, throttle is cut to prevent overshooting.
   - Otherwise, throttle is applied to counteract in-game gravity and hover in place.
5. **Out-of-Zone Chase PD Control**:
   $$u = K_p \cdot PosError + K_d \cdot v_{rel}$$
   Commands M1 hold when $u \le 0$, and release when $u > 0$.

#### 2. DeepFish AnkleBreak Controller (`"deepfish_anklebreak"`)
- Calculates directional distance using separate left/right multipliers and divisions.
- Executes real-time target crossing detection (equivalent to DeepFish's `SleepTrack`), cutting drive immediately upon crossing the fish.
- Emits counter-difference braking pulses and stabilizer bursts to freeze slider drift.

#### 3. Legacy Pulsed & Hold Controllers (`"pulsed"`, `"hold"`)
- Heuristic duty-cycle pulse trains and quadratic coasting cutoff ($d_{coast} = \frac{v^2}{4000}$) preserved for backwards compatibility.

---

## ⚙️ Configuration Reference (`config.json`)

```json
{
  "hotkeys": {
    "toggle_macro": "F6",
    "calibrate": "F7",
    "quit": "F8"
  },
  "fishing": {
    "rod_slot": "9",
    "verify_rod_equipped": true,
    "equip_key_delay": 0.25,
    "cast": {
      "max_hold_time": 1.4,
      "min_hold_time": 0.3,
      "fill_threshold": 190,
      "check_interval": 0.005
    },
    "shake": {
      "click_delay": 0.08,
      "max_wait_seconds": 25.0,
      "scan_interval": 0.015,
      "custom_roi": null
    },
    "reeling": {
      "steering_mode": "deepfish_precision",
      "target_mode": "fish_sprite",
      "kp": 0.5,
      "kd": 0.3,
      "velocity_smoothing": 0.2,
      "stopping_distance_multiplier": 3.0,
      "sidebar_ratio": 0.8,
      "stable_right_mult": 2.1,
      "stable_right_div": 1.4,
      "stable_left_mult": 1.1,
      "stable_left_div": 1.0,
      "ankle_break_right_mult": 1.0,
      "ankle_break_left_mult": 0.9,
      "deadzone_px": 15,
      "green_feather_hold_ms": 35,
      "green_feather_release_ms": 50,
      "red_direction_hold_step_ms": 30,
      "update_interval": 0.01,
      "max_duration_seconds": 45.0
    },
    "cooldown_between_casts": 1.5
  },
  "display": {
    "mode": "monitor_DP-2",
    "window_title_filter": ["Roblox", "Sober"],
    "auto_detect_window": true,
    "target_region": null
  },
  "active_module": "fishing"
}
```

---

## 🧪 Automated Test Suite & Regressions

The test suite in [`tests/test_detection.py`](tests/test_detection.py) runs 17 automated tests verifying every aspect of detection and controller logic:

```bash
python3 main.py --test
```
or:
```bash
python3 -m unittest -v tests/test_detection.py
```

### Test Coverage Breakdown:
1. `test_red_fish_detection`: Verifies red fish coordinates on `pictures/reel_bar.png`.
2. `test_green_fish_detection`: Verifies green fish coordinates on `pictures/reel_bar_green.png`.
3. `test_shake_button_detection`: Verifies shake button detection on standard template.
4. `test_slider_correctly_on_left_when_fish_on_right`: Verifies slider resolved on left while fish is right.
5. `test_no_false_positive_shake_on_full_screen`: Ensures stamina/health bars do not trigger clicks.
6. `test_rapid_click_pulsed_steering`: Verifies pulse trains when approaching the fish.
7. `test_far_chase_continuous_hold`: Verifies continuous M1 hold when far from fish ( > 65$).
8. `test_coasting_throttle_cutoff`: Verifies throttle cut when coasting on high velocity.
9. `test_position_aware_green_control`: Verifies bias correction when green fish touches slider edge.
10. `test_left_falling_gravity_braking`: Verifies tap-braking cushion when falling left into deadzone.
11. `test_multi_scale_shake_detection_down_to_40_percent`: Verifies 3D scale invariance from 40% to 100%.
12. `test_custom_roi_shake_detection`: Verifies detection within user-configured bounding boxes.
13. `test_hud_stamina_bar_rejection`: Rejection of elongated white UI bars.
14. `test_hotbar_slot9_equipped_on_frame68`: Verifies Slot 9 detection on full-resolution gameplay.
15. `test_end_of_reel_inactivity_on_frame68_and_70`: Verifies `is_active=False` when reel bar disappears.
16. `test_shake_detection_with_cursor_occlusion`: Verifies detection when cursor sits directly on prompt.
17. `test_frame127_hard_fish_slider_span_selection`: Verifies selection of true 160px slider over 60px edge artifact.

---

## 🚀 Quick Start & Usage

```bash
# Launch Desktop GUI (Default)
./main.py

# Headless Terminal Mode
python3 main.py --cli

# Interactive Calibration
python3 main.py --calibrate

# Run Test Suite
python3 main.py --test
```

### Hotkey Controls:
- **`F6`**: Toggle macro ON / OFF.
- **`F7`**: Launch calibration window.
- **`F8`**: Emergency shutdown / Exit macro.
