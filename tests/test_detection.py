"""Automated verification test suite for Grand Blue Macro detection modules."""

import os
import sys
import unittest
from PIL import Image

# Locate pictures and src directory relative to project root
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
PICTURES_DIR = os.path.join(BASE_DIR, "pictures")

from src.detector import GrandBlueDetector, ReelGameState


class TestGrandBlueDetection(unittest.TestCase):
    def setUp(self):
        self.detector = GrandBlueDetector(PICTURES_DIR)

    def test_red_fish_detection(self):
        img_path = os.path.join(PICTURES_DIR, "reel_bar.png")
        img = Image.open(img_path)
        state = self.detector.analyze_reel_game(img)
        self.assertTrue(state.is_active, "Reel minigame should be detected as active")
        self.assertFalse(state.is_green, "Fish in reel_bar.png should be RED (not green)")
        self.assertIsNotNone(state.fish_x, "Red fish X should be located")
        self.assertGreater(state.fish_x, 500, "Red fish should be near x=560")
        self.assertIsNotNone(state.slider_center_x, "Slider center X should be resolved")

    def test_green_fish_detection(self):
        img_path = os.path.join(PICTURES_DIR, "reel_bar_green.png")
        img = Image.open(img_path)
        state = self.detector.analyze_reel_game(img)
        self.assertTrue(state.is_active, "Reel minigame should be detected as active")
        self.assertTrue(state.is_green, "Fish in reel_bar_green.png should be GREEN (overlapping)")
        self.assertIsNotNone(state.fish_x, "Green fish X should be located")
        self.assertTrue(state.progress_gain, "Progress gain should be True when green")

    def test_shake_button_detection(self):
        img_path = os.path.join(PICTURES_DIR, "shake.png")
        img = Image.open(img_path)
        center = self.detector.find_shake_button(img)
        self.assertIsNotNone(center, "SHAKE button should be detected")
        cx, cy = center
        self.assertTrue(60 <= cx <= 90, f"Shake center X {cx} should be near center (74)")
        self.assertTrue(60 <= cy <= 90, f"Shake center Y {cy} should be near center (75)")

    def test_slider_correctly_on_left_when_fish_on_right(self):
        img_path = os.path.join(PICTURES_DIR, "reel_bar.png")
        img = Image.open(img_path)
        state = self.detector.analyze_reel_game(img)
        self.assertIsNotNone(state.slider_center_x, "Slider center X should be resolved")
        self.assertIsNotNone(state.fish_x, "Fish X should be resolved")
        # Slider is on the left (~98px) while fish is on the right (~520px)
        self.assertLess(state.slider_center_x, 150, "Slider center should be on the left (< 150)")
        self.assertGreater(state.fish_x, state.slider_center_x, "Fish should be to the right of the slider")

    def test_no_false_positive_shake_on_full_screen(self):
        img_path = os.path.join(PICTURES_DIR, "game_full_screen.png")
        img = Image.open(img_path)
        center = self.detector.find_shake_button(img)
        self.assertIsNone(center, "Full screen HUD/numbers should NOT trigger false positive SHAKE clicks")

    def test_rapid_click_pulsed_steering(self):
        """Verifies that when closing in on the fish (error <= 75), cruise pulses and micro-taps
        are emitted to prevent momentum overshoot."""
        import time
        from src.controller import ReelingController
        from src.detector import ReelGameState

        class MockInput:
            def __init__(self):
                self.is_m1_down = False
                self.clicks = 0
            def mouse_down(self, btn):
                self.is_m1_down = True
            def mouse_up(self, btn):
                if self.is_m1_down:
                    self.clicks += 1
                self.is_m1_down = False
            def release_all(self):
                self.is_m1_down = False

        mock_inp = MockInput()
        ctrl = ReelingController(mock_inp, {"fishing": {"reeling": {"steering_mode": "pulsed"}}})

        # Test fish closing in to the right (fish_x=250, slider_x=200, error=50): should emit pulsed clicks
        state = ReelGameState(is_active=True, is_green=False, fish_x=250.0, slider_center_x=200.0)
        t_start = time.time()
        while time.time() - t_start < 0.35:
            ctrl.update(state)
            time.sleep(0.005)

        self.assertGreater(mock_inp.clicks, 0, "Pulsed steering when closing in should emit rapid clicks to prevent overshoot")

    def test_far_chase_continuous_hold(self):
        """Verifies that when the red fish is far away (error > 75), continuous M1 hold is maintained
        to beat in-game gravity acceleration and rapidly close the distance."""
        import time
        from src.controller import ReelingController
        from src.detector import ReelGameState

        class MockInput:
            def __init__(self):
                self.is_m1_down = False
                self.clicks = 0
            def mouse_down(self, btn):
                self.is_m1_down = True
            def mouse_up(self, btn):
                if self.is_m1_down:
                    self.clicks += 1
                self.is_m1_down = False
            def release_all(self):
                self.is_m1_down = False

        mock_inp = MockInput()
        ctrl = ReelingController(mock_inp, {"fishing": {"reeling": {"steering_mode": "pulsed"}}})

        # Far chase (fish_x=500, slider_x=200, error=300): should hold M1 continuously without pulse decay
        state = ReelGameState(is_active=True, is_green=False, fish_x=500.0, slider_center_x=200.0)
        t_start = time.time()
        while time.time() - t_start < 0.25:
            ctrl.update(state)
            time.sleep(0.005)

        self.assertTrue(mock_inp.is_m1_down, "Far chase must maintain continuous M1 hold to beat gravity")
        self.assertEqual(mock_inp.clicks, 0, "Far chase must not release M1 every 60ms")

    def test_coasting_throttle_cutoff(self):
        """Verifies that when closing rapidly on the fish, throttle is cut to let gravity
        decelerate the slider smoothly into the sweet spot without overshooting."""
        import time
        from src.controller import ReelingController
        from src.detector import ReelGameState

        class MockInput:
            def __init__(self):
                self.is_m1_down = False
                self.clicks = 0
            def mouse_down(self, btn):
                self.is_m1_down = True
            def mouse_up(self, btn):
                if self.is_m1_down:
                    self.clicks += 1
                self.is_m1_down = False
            def release_all(self):
                self.is_m1_down = False

        mock_inp = MockInput()
        ctrl = ReelingController(mock_inp, {"fishing": {"reeling": {"steering_mode": "pulsed"}}})

        # Frame 1: Slider at 300, fish at 500 (error = 200)
        state1 = ReelGameState(is_active=True, is_green=False, fish_x=500.0, slider_center_x=300.0)
        ctrl.update(state1)
        time.sleep(0.05)

        # Frame 2: Slider moved to 450 in 50ms (v = 3000 px/s!), error = 50px
        # Because closing speed is high, throttle must be cut (M1 released) to coast smoothly!
        state2 = ReelGameState(is_active=True, is_green=False, fish_x=500.0, slider_center_x=450.0)
        ctrl.update(state2)

        self.assertFalse(mock_inp.is_m1_down, "Throttle must be cut when closing at high speed to prevent overshoot")

    def test_position_aware_green_control(self):
        """Verifies that when the fish is green but at the left edge of the slider,
        M1 is released so gravity brings the slider back onto the fish, rather than blind-pulsing right."""
        from src.controller import ReelingController
        from src.detector import ReelGameState

        class MockInput:
            def __init__(self):
                self.is_m1_down = True
            def mouse_down(self, btn):
                self.is_m1_down = True
            def mouse_up(self, btn):
                self.is_m1_down = False
            def release_all(self):
                self.is_m1_down = False

        mock_inp = MockInput()
        ctrl = ReelingController(mock_inp, {"fishing": {"reeling": {"steering_mode": "pulsed"}}})

        # Fish is at 450, slider is at 500 (error = -50: slider is to the right of fish!)
        state = ReelGameState(is_active=True, is_green=True, fish_x=450.0, slider_center_x=500.0)
        ctrl.update(state)

        self.assertFalse(mock_inp.is_m1_down, "When slider is right of green fish, M1 must be released")

    def test_left_falling_gravity_braking(self):
        """Verifies that when the slider is falling left towards the fish and closing in,
        braking pulses are emitted to kill gravity acceleration and prevent overshooting the fish."""
        import time
        from src.controller import ReelingController

        class MockInput:
            def __init__(self):
                self.clicks = 0
                self.is_m1_down = False
            def mouse_down(self, btn):
                self.is_m1_down = True
            def mouse_up(self, btn):
                self.is_m1_down = False
                self.clicks += 1
            def release_all(self):
                self.is_m1_down = False

        mock_inp = MockInput()
        ctrl = ReelingController(mock_inp, {"fishing": {"reeling": {"steering_mode": "pulsed"}}})

        # Fish is at 300, slider is falling from 360 (error = -60): should tap brake!
        state = ReelGameState(is_active=True, is_green=False, fish_x=300.0, slider_center_x=360.0)
        t_start = time.time()
        while time.time() - t_start < 0.35:
            ctrl.update(state)
            time.sleep(0.005)

        self.assertGreater(mock_inp.clicks, 0, "Approaching fish from right should emit tap braking to cushion landing")

    def test_multi_scale_shake_detection_down_to_40_percent(self):
        """Verifies that SHAKE circles scaled from 40% to 100% in 3D perspective are reliably detected."""
        img_path = os.path.join(PICTURES_DIR, "shake.png")
        orig = Image.open(img_path)

        for scale in [0.40, 0.50, 0.75, 1.00]:
            w = int(orig.width * scale)
            h = int(orig.height * scale)
            scaled = orig.resize((w, h), Image.Resampling.BILINEAR)
            bg = Image.new("RGB", (w + 80, h + 80), (25, 75, 115))
            bg.paste(scaled, (40, 40))
            center = self.detector.find_shake_button(bg)
            self.assertIsNotNone(center, f"SHAKE prompt at scale {scale} should be detected")

    def test_custom_roi_shake_detection(self):
        """Verifies detection inside a cropped custom bounding region."""
        img_path = os.path.join(PICTURES_DIR, "shake.png")
        img = Image.open(img_path)
        center = self.detector.find_shake_button(img)
        self.assertIsNotNone(center)
        cx, cy = center
        crop = img.crop((cx - 60, cy - 60, cx + 60, cy + 60))
        crop_center = self.detector.find_shake_button(crop)
        self.assertIsNotNone(crop_center, "SHAKE button should be detected when restricted to custom cropped area")

    def test_hud_stamina_bar_rejection(self):
        """Verifies that horizontal HUD bars (such as player stamina / sprint bars)
        are completely rejected and never trigger false positive clicks."""
        # Synthesize a horizontal white bar with health/HUD colors
        bar_img = Image.new("RGB", (300, 100), (20, 60, 110))
        # Add white stamina bar of aspect ratio 8.0
        for y in range(45, 55):
            for x in range(30, 180):
                bar_img.putpixel((x, y), (255, 255, 255))
        center = self.detector.find_shake_button(bar_img)
        self.assertIsNone(center, "HUD stamina bar must be rejected and not trigger SHAKE click")


class TestAi4Regressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.detector = GrandBlueDetector(PICTURES_DIR)
        cls.scratch_dir = "/home/ayan/.gemini/antigravity/brain/1a41f2e7-bd29-4746-b610-810ae5cf9de3/scratch"

    def test_hotbar_slot9_equipped_on_frame68(self):
        frame68_path = os.path.join(self.scratch_dir, "ai4_frames", "frame_0068.png")
        if not os.path.exists(frame68_path):
            self.skipTest("ai4_frames not present")
        img = Image.open(frame68_path)
        gw, gh = img.size
        hx = int(0.30 * gw)
        hy = int(0.85 * gh)
        hw = int(0.46 * gw)
        hh = gh - hy
        hotbar = img.crop((hx, hy, hx + hw, hy + hh))
        is_eq, white_cnt, _ = self.detector.is_slot_equipped(hotbar, "9")
        self.assertTrue(is_eq, "Slot 9 must be detected as EQUIPPED on frame 68")
        self.assertGreaterEqual(white_cnt, 50, f"Slot 9 white border count should be >= 50 (got {white_cnt})")

    def test_end_of_reel_inactivity_on_frame68_and_70(self):
        if not os.path.exists(os.path.join(self.scratch_dir, "ai4_frames", "frame_0068.png")):
            self.skipTest("ai4_frames not present")
        gw, gh = 1800, 1200
        bar_roi = (int(0.15 * gw), int(0.40 * gh), int(0.70 * gw), int(0.45 * gh))
        for fname in ["frame_0068.png", "frame_0070.png"]:
            img = Image.open(os.path.join(self.scratch_dir, "ai4_frames", fname))
            crop = img.crop((bar_roi[0], bar_roi[1], bar_roi[0] + bar_roi[2], bar_roi[1] + bar_roi[3]))
            state = self.detector.analyze_reel_game(crop)
            self.assertFalse(state.is_active, f"{fname} must be detected as INACTIVE (no blue water bar)")

    def test_shake_detection_with_cursor_occlusion(self):
        f72_path = os.path.join(self.scratch_dir, "ai4_frames", "frame_0072.png")
        if not os.path.exists(f72_path):
            self.skipTest("ai4_frames not present")
        img = Image.open(f72_path)
        shake_roi = (int(0.08 * 1800), int(0.04 * 1200), int(0.90 * 1800), int(0.89 * 1200))
        crop = img.crop((shake_roi[0], shake_roi[1], shake_roi[0] + shake_roi[2], shake_roi[1] + shake_roi[3]))
        
        # Plain detection
        pt1 = self.detector.find_shake_button(crop)
        self.assertIsNotNone(pt1, "Frame 72 shake button must be detected")
        self.assertEqual(pt1, (444, 468))

        # Occlude with simulated white mouse cursor pointer directly on the center
        from PIL import ImageDraw
        crop_cursor = crop.copy()
        draw = ImageDraw.Draw(crop_cursor)
        draw.polygon([(444, 468), (456, 480), (449, 480), (452, 486), (449, 487), (446, 481), (442, 483)], fill=(255, 255, 255), outline=(0, 0, 0))
        pt2 = self.detector.find_shake_button(crop_cursor)
        self.assertIsNotNone(pt2, "SHAKE prompt with cursor sitting on center must still be detected via adaptive navy disk check")
        self.assertTrue(abs(pt2[0] - 444) <= 2 and abs(pt2[1] - 468) <= 2, f"Expected center near (444, 468), got {pt2}")

    def test_frame127_hard_fish_slider_span_selection(self):
        f127_path = os.path.join(self.scratch_dir, "ai4_frames", "frame_0127.png")
        if not os.path.exists(f127_path):
            self.skipTest("ai4_frames not present")
        img = Image.open(f127_path)
        gw, gh = img.size
        bar_roi = (int(0.15 * gw), int(0.40 * gh), int(0.70 * gw), int(0.45 * gh))
        crop = img.crop((bar_roi[0], bar_roi[1], bar_roi[0] + bar_roi[2], bar_roi[1] + bar_roi[3]))
        state = self.detector.analyze_reel_game(crop)
        self.assertTrue(state.is_active, "Frame 127 reel game must be active")
        self.assertFalse(state.is_green, "Frame 127 fish should be red")
        # True slider is on the left (~385px), NOT the 60px edge post at ~1117px
        self.assertIsNotNone(state.slider_center_x)
        self.assertLess(state.slider_center_x, 500, f"Slider must be detected on left (<500px, got {state.slider_center_x})")
        self.assertTrue(350 <= state.slider_center_x <= 420, f"Slider center should be near 385px (got {state.slider_center_x})")


class TestDeepFishAlgorithms(unittest.TestCase):
    def setUp(self):
        from src.detector import GrandBlueDetector
        self.detector = GrandBlueDetector()

    def _create_mock_input(self):
        import time
        class MockInput:
            def __init__(self):
                self.is_m1_down = False
                self.click_history = []
            def mouse_down(self, btn):
                self.is_m1_down = True
                self.click_history.append(("down", time.time()))
            def mouse_up(self, btn):
                self.is_m1_down = False
                self.click_history.append(("up", time.time()))
            def release_all(self):
                self.is_m1_down = False
        return MockInput()

    def test_deepfish_precision_velocity_smoothing_and_outlier_rejection(self):
        """Verifies low-pass EMA velocity filtering and outlier teleport jump rejection."""
        import time
        from src.controller import ReelingController
        from src.detector import ReelGameState

        mock_inp = self._create_mock_input()
        ctrl = ReelingController(mock_inp, {
            "fishing": {
                "reeling": {
                    "steering_mode": "deepfish_precision",
                    "velocity_smoothing": 0.5
                }
            }
        })

        # Frame 1: bar at 100, fish at 200
        state1 = ReelGameState(is_active=True, is_green=False, fish_x=200.0, slider_center_x=100.0)
        ctrl.update(state1)
        time.sleep(0.04)

        # Frame 2: bar at 120, fish at 210 (normal movement)
        state2 = ReelGameState(is_active=True, is_green=False, fish_x=210.0, slider_center_x=120.0)
        ctrl.update(state2)

        self.assertGreater(ctrl.bar_vel, 0.0, "Bar velocity must be positive when moving right")
        self.assertGreater(ctrl.fish_vel, 0.0, "Fish velocity must be positive when moving right")

        # Frame 3: sudden visual glitch/teleport jump (delta = 490 > 100px)
        time.sleep(0.02)
        state3 = ReelGameState(is_active=True, is_green=False, fish_x=700.0, slider_center_x=130.0)
        ctrl.update(state3)
        self.assertEqual(ctrl.fish_vel, 0.0, "Fish velocity must be reset to 0 upon outlier spike (>100px)")

    def test_deepfish_precision_reachable_span_clamping(self):
        """Verifies that when fish is pinned at water bar boundaries, M1 is forced up/down."""
        from src.controller import ReelingController
        from src.detector import ReelGameState

        mock_inp = self._create_mock_input()
        ctrl = ReelingController(mock_inp, {
            "fishing": {
                "reeling": {
                    "steering_mode": "deepfish_precision"
                }
            }
        })

        # MinReach: bar left=100, right=800, slider_w=150 -> MinReach = 175
        # Fish at 150 (< 175). Even if slider is at 200 (right of fish), M1 must be released
        state_left = ReelGameState(
            is_active=True, is_green=False,
            fish_x=150.0, slider_center_x=200.0, slider_width=150.0,
            bar_left_x=100.0, bar_right_x=800.0
        )
        ctrl.update(state_left)
        self.assertFalse(mock_inp.is_m1_down, "M1 must be released when fish is pinned past left boundary (min_reach)")

        # MaxReach: bar left=100, right=800, slider_w=150 -> MaxReach = 725
        # Fish at 750 (> 725). Even if slider is at 740, M1 must be held
        state_right = ReelGameState(
            is_active=True, is_green=False,
            fish_x=750.0, slider_center_x=700.0, slider_width=150.0,
            bar_left_x=100.0, bar_right_x=800.0
        )
        ctrl.update(state_right)
        self.assertTrue(mock_inp.is_m1_down, "M1 must be held when fish is pinned past right boundary (max_reach)")

    def test_deepfish_precision_in_zone_hover(self):
        """Verifies that inside the sweet spot, throttle is cut if coasting rightward,
        and held if stationary/falling to counteract gravity."""
        from src.controller import ReelingController
        from src.detector import ReelGameState

        mock_inp = self._create_mock_input()
        ctrl = ReelingController(mock_inp, {
            "fishing": {
                "reeling": {
                    "steering_mode": "deepfish_precision",
                    "stopping_distance_multiplier": 2.0
                }
            }
        })

        # Scenario A: Centered inside slider, stationary (rel_vel <= 0)
        # In Grand Blue, gravity pulls left, so M1 must be held to hover!
        ctrl.bar_vel = 0.0
        ctrl.fish_vel = 0.0
        state_hover = ReelGameState(
            is_active=True, is_green=True,
            fish_x=400.0, slider_center_x=400.0,
            slider_left_x=325.0, slider_right_x=475.0, slider_width=150.0,
            bar_left_x=50.0, bar_right_x=850.0
        )
        ctrl.update(state_hover)
        self.assertTrue(mock_inp.is_m1_down, "Must hold M1 when hovering stationary inside sweet spot to counter gravity")

        # Scenario B: Centered inside slider, but coasting rightward fast (rel_vel > 0)
        # Throttle must be CUT (M1 released) to prevent overshooting!
        ctrl.bar_vel = 250.0
        ctrl.fish_vel = 0.0
        ctrl.update(state_hover)
        self.assertFalse(mock_inp.is_m1_down, "Must release M1 when already coasting rightward inside sweet spot")

    def test_deepfish_precision_chase_pd_control(self):
        """Verifies PD output for far chases."""
        from src.controller import ReelingController
        from src.detector import ReelGameState

        mock_inp = self._create_mock_input()
        ctrl = ReelingController(mock_inp, {
            "fishing": {
                "reeling": {
                    "steering_mode": "deepfish_precision",
                    "kp": 0.5, "kd": 0.3
                }
            }
        })

        # Slider at 200, fish at 450 (pos_error = -250). M1 must be held
        state_chase_right = ReelGameState(
            is_active=True, is_green=False,
            fish_x=450.0, slider_center_x=200.0,
            slider_left_x=125.0, slider_right_x=275.0, slider_width=150.0,
            bar_left_x=50.0, bar_right_x=850.0
        )
        ctrl.update(state_chase_right)
        self.assertTrue(mock_inp.is_m1_down, "Must hold M1 when slider is far left of target")

        # Slider at 500, fish at 250 (pos_error = +250). M1 must be released
        state_chase_left = ReelGameState(
            is_active=True, is_green=False,
            fish_x=250.0, slider_center_x=500.0,
            slider_left_x=425.0, slider_right_x=575.0, slider_width=150.0,
            bar_left_x=50.0, bar_right_x=850.0
        )
        ctrl.update(state_chase_left)
        self.assertFalse(mock_inp.is_m1_down, "Must release M1 when slider is far right of target")

    def test_deepfish_anklebreak_counter_braking(self):
        """Verifies AnkleBreak state machine: DRIVE -> Target Crossing Interrupt -> BRAKE."""
        from src.controller import ReelingController
        from src.detector import ReelGameState

        mock_inp = self._create_mock_input()
        ctrl = ReelingController(mock_inp, {
            "fishing": {
                "reeling": {
                    "steering_mode": "deepfish_anklebreak"
                }
            }
        })

        # Step 1: Slider at 300, fish at 500. Should enter DRIVE phase moving right (M1 down)
        state1 = ReelGameState(
            is_active=True, is_green=False,
            fish_x=500.0, slider_center_x=300.0, slider_width=150.0,
            bar_left_x=50.0, bar_right_x=850.0
        )
        ctrl.update(state1)
        self.assertEqual(ctrl._ab_phase, "DRIVE")
        self.assertTrue(mock_inp.is_m1_down)

        # Step 2: Slider crosses fish (slider at 520 >= 500).
        # SleepTrack interrupt fires: switches to BRAKE phase with M1 released!
        state2 = ReelGameState(
            is_active=True, is_green=False,
            fish_x=500.0, slider_center_x=520.0, slider_width=150.0,
            bar_left_x=50.0, bar_right_x=850.0
        )
        ctrl.update(state2)
        self.assertEqual(ctrl._ab_phase, "BRAKE")
        self.assertFalse(mock_inp.is_m1_down, "Must release M1 to brake rightward momentum")

    def test_deepfish_linescan_detection(self):
        """Synthesizes a line minigame image and verifies detect_reel_lines extracts fish and bar."""
        from PIL import Image, ImageDraw

        # Create 600x120 synthetic minigame frame
        img = Image.new("RGB", (600, 120), color=(180, 180, 180))
        draw = ImageDraw.Draw(img)

        # Draw fish indicator: two dark vertical lines with 12px gap at x=280 and x=292
        for y in range(120):
            draw.line([(280, y), (280, y)], fill=(10, 10, 10))
            draw.line([(292, y), (292, y)], fill=(10, 10, 10))

        # Draw slider boundary lines: two dark vertical lines separated by 300px at x=150 and x=450
        for y in range(120):
            draw.line([(150, y), (150, y)], fill=(20, 20, 20))
            draw.line([(450, y), (450, y)], fill=(20, 20, 20))

        state = self.detector.detect_reel_lines(img)
        self.assertTrue(state.is_active, "LineScan must detect active minigame")
        self.assertIsNotNone(state.fish_x)
        self.assertTrue(abs(state.fish_x - 286.0) <= 2.0, f"Fish line centroid should be near 286px, got {state.fish_x}")
        self.assertIsNotNone(state.slider_left_x)
        self.assertIsNotNone(state.slider_right_x)
        self.assertTrue(abs(state.slider_left_x - 150.0) <= 2.0, f"Slider left should be near 150px, got {state.slider_left_x}")
        self.assertTrue(abs(state.slider_right_x - 450.0) <= 2.0, f"Slider right should be near 450px, got {state.slider_right_x}")
        self.assertTrue(state.is_green, "Fish at 286px should be inside slider [150..450]")


class TestCastAndRodSafety(unittest.TestCase):
    def setUp(self):
        from src.detector import GrandBlueDetector
        self.detector = GrandBlueDetector(PICTURES_DIR)

    def test_cast_fill_empty_vs_full(self):
        """Verifies cast fill detection rejects empty bars and triggers at peak fill."""
        # Case 1: Empty background
        empty_img = Image.new("RGB", (100, 300), (30, 40, 50))
        reached, rows, white_px = self.detector.check_cast_fill(empty_img)
        self.assertFalse(reached, "Empty image must not trigger cast fill")
        self.assertEqual(rows, 0)

        # Case 2: Full capsule asset
        capsule_path = os.path.join(PICTURES_DIR, "cast_bar_capsule.png")
        if os.path.exists(capsule_path):
            capsule_img = Image.open(capsule_path)
            reached_cap, rows_cap, white_cap = self.detector.check_cast_fill(capsule_img)
            self.assertTrue(reached_cap, "Full cast capsule must trigger top_reached=True")
            self.assertGreaterEqual(rows_cap, 16)

    def test_slot_equipped_synthetic_border(self):
        """Verifies is_slot_equipped detects white border outlines on equipped slots."""
        # Synthesize 85x95 slot with white border on top and bottom
        slot_equipped = Image.new("RGB", (85, 95), (20, 25, 30))
        for x in range(5, 80):
            slot_equipped.putpixel((x, 20), (245, 245, 245))
            slot_equipped.putpixel((x, 90), (245, 245, 245))

        is_eq, white_cnt, _ = self.detector.is_slot_equipped(slot_equipped, "9")
        self.assertTrue(is_eq, "Synthesized slot with white borders must be detected as EQUIPPED")
        self.assertGreaterEqual(white_cnt, 40)

        # Synthesize unequipped slot
        slot_unequipped = Image.new("RGB", (85, 95), (20, 25, 30))
        is_eq_un, white_cnt_un, _ = self.detector.is_slot_equipped(slot_unequipped, "9")
        self.assertFalse(is_eq_un, "Slot without white borders must be detected as NOT EQUIPPED")
        self.assertEqual(white_cnt_un, 0)

    def test_rod_safety_never_unequips_when_equipped(self):
        """Verifies FishingBot never taps rod hotkey if rod is already in hand."""
        from src.fishing_bot import FishingBot

        class MockScreen:
            def capture_roi(self, *args):
                return Image.new("RGB", (100, 100), (0, 0, 0))
            def get_cursor_position(self):
                return (500, 500)
            def focus_window(self, win):
                pass

        class MockInput:
            def __init__(self):
                self.tapped_keys = []
            def tap_key(self, key):
                self.tapped_keys.append(key)
            def release_all(self):
                pass

        mock_input = MockInput()
        mock_screen = MockScreen()
        config = {
            "fishing": {
                "rod_slot": "9",
                "assume_rod_equipped_on_start": True,
                "verify_rod_equipped": True,
                "auto_equip_on_start": False,
            }
        }
        bot = FishingBot(mock_screen, mock_input, self.detector, config)
        self.assertTrue(bot._rod_equipped, "Rod should be assumed equipped on start")

        bot._handle_equip_rod((0, 0, 1920, 1080))
        self.assertEqual(len(mock_input.tapped_keys), 0, "Bot must NEVER tap hotkey when rod is in hand!")


if __name__ == "__main__":
    unittest.main()




