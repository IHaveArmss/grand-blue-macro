"""High-performance computer vision detector for Grand Blue fishing mechanics."""

import os
from dataclasses import dataclass
from typing import Optional, Tuple, List
from PIL import Image


@dataclass
class ReelGameState:
    is_active: bool = False
    is_green: bool = False  # True when slider overlaps fish and fish turns green
    fish_x: Optional[float] = None  # X coordinate of the fish centroid
    fish_y: Optional[float] = None
    slider_left_x: Optional[float] = None  # Left edge of the movable dark bar
    slider_center_x: Optional[float] = None  # Center of the movable bar (~left + 90)
    slider_right_x: Optional[float] = None  # Right edge (~left + 180)
    slider_width: Optional[float] = None  # Measured pixel width of the slider
    bar_left_x: Optional[float] = None  # Physical left boundary of the water bar
    bar_right_x: Optional[float] = None  # Physical right boundary of the water bar
    progress_gain: bool = False  # Whether green progress text is visible


class GrandBlueDetector:
    def __init__(self, templates_dir: str = "pictures"):
        self.templates_dir = templates_dir
        self.slider_width = 180  # Default pixel width of the dark slider bar

        # Load reference templates if available
        self.template_shake = self._load_template("shake.png")
        self.template_shake_text = self._load_template("shake_text.png")
        self.template_fish_red = self._load_template("fish_template.png")
        self.template_fish_green = self._load_template("fish_template_green.png")
        self.template_cast_capsule = self._load_template("cast_bar_capsule.png")

        # Precompute binary mask for fast template verification
        if self.template_shake_text:
            gray = self.template_shake_text.convert("L")
            self._shake_tw, self._shake_th = gray.size
            self._shake_tmpl_mask = [1 if gray.getpixel((x, y)) > 160 else 0 
                                     for y in range(self._shake_th) for x in range(self._shake_tw)]
            self._shake_white_count = max(1, sum(self._shake_tmpl_mask))
        else:
            self._shake_tmpl_mask = None
            self._shake_white_count = 1

    def _load_template(self, filename: str) -> Optional[Image.Image]:
        path = os.path.join(self.templates_dir, filename)
        if os.path.exists(path):
            try:
                return Image.open(path).convert("RGB")
            except Exception:
                return None
        return None

    # --- 1. Hotbar Rod Equipped Verification ---

    def is_slot_equipped(self, hotbar_img: Image.Image, slot_number: str = "9") -> Tuple[bool, int, float]:
        """Determines if a hotbar slot (1-9, 0) has the bright white selection border.
        Can take either a full 10-slot hotbar strip or a single cropped slot.
        Returns (is_equipped, white_pixels, ratio).
        """
        img = hotbar_img.convert("RGB")
        w, h = img.size

        # If wide strip (> 3.0 aspect ratio), slice the target slot
        if w / max(1, h) > 3.0:
            slot_map = {"1": 0, "2": 1, "3": 2, "4": 3, "5": 4,
                        "6": 5, "7": 6, "8": 7, "9": 8, "0": 9}
            idx = slot_map.get(str(slot_number), 8)
            slot_w = w / 10.0
            x_start = int(idx * slot_w)
            x_end = int((idx + 1) * slot_w)
            slot_img = img.crop((x_start, 0, x_end, h))
        else:
            slot_img = img

        sw, sh = slot_img.size
        bot_cnt = sum(1 for y in range(max(0, sh - 14), sh - 2) for x in range(3, sw - 3)
                      if all(c > 220 for c in slot_img.getpixel((x, y))[:3]))
        top_cnt = sum(1 for y in range(int(0.15 * sh), int(0.32 * sh)) for x in range(3, sw - 3)
                      if all(c > 220 for c in slot_img.getpixel((x, y))[:3]))

        border_white = bot_cnt + top_cnt
        total_tested = (12 * max(1, sw - 6)) + (int(0.17 * sh) * max(1, sw - 6))
        ratio = border_white / max(1, total_tested)

        # Equipped slots have a bright continuous white border (>40 px vs <=2 px unequipped)
        is_eq = (bot_cnt >= 25) or (border_white >= 40)
        return is_eq, border_white, ratio

    # --- 2. Cast Bar Top Fill Monitor ---

    def check_cast_fill(self, cast_img: Image.Image, fill_threshold: int = 16) -> Tuple[bool, int, int]:
        """Monitors the vertical cast capsule fill.
        Detects:
        1. Glowing white cap at the top of the capsule (>225 RGB).
        2. Vertical column of cyan liquid rising inside the capsule.
        Returns (top_reached, cyan_row_count, white_cap_pixels).
        """
        img = cast_img.convert("RGB")
        w, h = img.size

        cyan_rows = 0
        white_cap_pixels = 0
        total_cyan_px = 0

        for y in range(0, h, 2):
            cyan_in_row = 0
            white_in_row = 0
            for x in range(0, w, 2):
                r, g, b = img.getpixel((x, y))
                # Cyan fill
                if 120 < g < 256 and 160 < b < 256 and r < 225:
                    cyan_in_row += 1
                    total_cyan_px += 1
                # Glowing white cap
                elif r > 225 and g > 225 and b > 225:
                    white_in_row += 1

            if cyan_in_row >= 5:
                cyan_rows += 1
            if white_in_row >= 3:
                white_cap_pixels += white_in_row

        top_reached = (
            (white_cap_pixels >= 8 and cyan_rows >= 12) or
            (cyan_rows >= max(14, fill_threshold)) or
            (total_cyan_px >= 420)
        )
        return top_reached, cyan_rows, white_cap_pixels

    def is_cast_top_reached(self, cast_img: Image.Image, fill_threshold: int = 16) -> bool:
        reached, _, _ = self.check_cast_fill(cast_img, fill_threshold)
        return reached

    # --- 3. Shake Button Detection ---

    def find_shake_button(self, screen_img: Image.Image,
                          min_confidence: float = 0.50) -> Optional[Tuple[int, int]]:
        """Scans image for circular SHAKE prompt buttons with white 'SHAKE' text inside.
        Rejects HUD numbers, health bars, chat text, or leaderboards, functioning down to 40% scale
        across all backgrounds (water, dock wood, player clothes).
        Returns (center_x, center_y) relative to the input image, or None.
        """
        img = screen_img.convert("RGB")
        w, h = img.size
        raw = img.tobytes()

        # Step 1: Scan for bright neutral text pixels (white/light grey across lighting)
        white_pts = []
        step = 2
        for y in range(8, h - 8, step):
            row_offset = y * w * 3
            for x in range(8, w - 8, step):
                idx = row_offset + x * 3
                r, g, b = raw[idx], raw[idx + 1], raw[idx + 2]
                if r > 195 and g > 195 and b > 195:
                    white_pts.append((x, y))

        if len(white_pts) < 14:
            return None

        # Step 2: Cluster white pixels belonging to the word 'SHAKE'
        clusters: List[List] = []
        for pt in white_pts:
            matched = False
            for cluster in clusters:
                cx, cy = cluster[0], cluster[1]
                if abs(pt[0] - cx) < 65 and abs(pt[1] - cy) < 16:
                    pts = cluster[2]
                    pts.append(pt)
                    cluster[0] = sum(p[0] for p in pts) / len(pts)
                    cluster[1] = sum(p[1] for p in pts) / len(pts)
                    matched = True
                    break
            if not matched:
                clusters.append([pt[0], pt[1], [pt]])

        def is_navy(r: int, g: int, b: int) -> bool:
            return b > 35 and (b - r > 15) and (b - g > 10) and r < 40 and g < 60

        best_pt = None
        best_score = 0.0

        # Step 3: Find best cluster matching the word 'SHAKE'
        for cluster in sorted(clusters, key=lambda c: len(c[2]), reverse=True):
            pts = cluster[2]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            c_w = max(xs) - min(xs)
            c_h = max(ys) - min(ys)
            aspect = c_w / max(1, c_h)

            # Word 'SHAKE' is a single word: width 20..130, height 4..35, aspect 2.0..5.8, min 14 points
            # Completely rejects HUD stamina/health bars (which have aspect ratio >= 8.0)
            if not (20 <= c_w <= 130 and 4 <= c_h <= 35 and 2.0 <= aspect <= 5.8 and len(pts) >= 14):
                continue

            avg_x = int(cluster[0])
            avg_y = int(cluster[1])

            # Reject extended white bars: if pixels immediately to the left/right of the word are also white,
            # it is part of an ongoing horizontal UI bar (e.g. stamina bar), not the standalone word 'SHAKE'.
            dx = int(c_w * 0.65)
            if avg_x - dx >= 0:
                lr, lg, lb = img.getpixel((avg_x - dx, avg_y))[:3]
                if lr > 190 and lg > 190 and lb > 190:
                    continue
            if avg_x + dx < w:
                rr, rg, rb = img.getpixel((avg_x + dx, avg_y))[:3]
                if rr > 190 and rg > 190 and rb > 190:
                    continue

            # Primary verification: match against precomputed 'SHAKE' text mask
            if self._shake_tmpl_mask:
                crop = img.crop((min(xs), min(ys), max(xs) + 1, max(ys) + 1))
                resized = crop.resize((self._shake_tw, self._shake_th), Image.Resampling.BILINEAR).convert("L")
                score = 0
                penalty = 0
                for y in range(self._shake_th):
                    for x in range(self._shake_tw):
                        val = resized.getpixel((x, y))
                        if self._shake_tmpl_mask[y * self._shake_tw + x]:
                            if val > 165:
                                score += 1
                        else:
                            if val > 195:
                                penalty += 1.0  # Penalize non-letter white fill

                total_bg = self._shake_th * self._shake_tw - self._shake_white_count
                pen_ratio = penalty / max(1, total_bg)
                if pen_ratio > 0.35:  # Solid white UI bars have >50% non-letter white fill!
                    continue

                match_sc = (score - penalty * 1.0) / self._shake_white_count

                # If mouse cursor partially covers the letters, check navy circular disk
                navy_verified = False
                if match_sc >= 0.28:
                    navy_verified = (
                        (avg_y - 20 >= 0 and is_navy(*img.getpixel((avg_x, avg_y - 20))[:3])) and
                        (avg_y + 20 < h and is_navy(*img.getpixel((avg_x, avg_y + 20))[:3]))
                    )

                is_valid = (match_sc >= min_confidence) or (match_sc >= 0.28 and navy_verified)
                if is_valid and match_sc > best_score:
                    best_score = match_sc
                    best_pt = (avg_x, avg_y)
            else:
                # Fallback verification: 4-way circular disk checks
                dy = max(5, int(1.15 * c_h))
                top_navy = sum(1 for dx in [-int(c_w * 0.25), 0, int(c_w * 0.25)]
                               if 0 <= avg_x + dx < w and 0 <= avg_y - dy < h
                               and is_navy(*img.getpixel((avg_x + dx, avg_y - dy))[:3]))
                bot_navy = sum(1 for dx in [-int(c_w * 0.25), 0, int(c_w * 0.25)]
                               if 0 <= avg_x + dx < w and 0 <= avg_y + dy < h
                               and is_navy(*img.getpixel((avg_x + dx, avg_y + dy))[:3]))

                if top_navy >= 2 and bot_navy >= 2:
                    left_navy = sum(1 for ddx in [int(c_w * 0.55), int(c_w * 0.65)]
                                    if 0 <= avg_x - ddx < w and is_navy(*img.getpixel((avg_x - ddx, avg_y))[:3]))
                    right_navy = sum(1 for ddx in [int(c_w * 0.55), int(c_w * 0.65)]
                                     if 0 <= avg_x + ddx < w and is_navy(*img.getpixel((avg_x + ddx, avg_y))[:3]))
                    if left_navy >= 1 and right_navy >= 1:
                        return avg_x, avg_y

        return best_pt

    # --- 4. Reeling Minigame Detection & Fish Tracking ---

    def analyze_reel_game(self, screen_or_bar_img: Image.Image) -> ReelGameState:
        """Analyzes the reeling bar area.
        Detects:
        - Whether the minigame is active (red or green fish MUST be present AND blue water bar must exist)
        - Fish position (X, Y) and whether it is GREEN (overlapping) or RED (not overlapping)
        - Slider position (X) via contiguous dark column density (ignoring dock/clothes below)
        """
        img = screen_or_bar_img.convert("RGB")
        w, h = img.size

        red_pts = []
        green_pts = []

        y_start = int(0.15 * h)
        y_end = int(0.85 * h)

        # Step 1: Scan for fish pixels
        for y in range(y_start, y_end, 2):
            for x in range(0, w, 2):
                r, g, b = img.getpixel((x, y))

                # Red fish (outside slider)
                if r > 160 and g < 75 and b < 75:
                    red_pts.append((x, y))

                # Green fish (inside slider)
                elif g > 160 and r < 105 and b < 105:
                    green_pts.append((x, y))

        is_green = len(green_pts) >= 20 and len(green_pts) > len(red_pts)
        is_red = len(red_pts) >= 20 and len(red_pts) >= len(green_pts)

        # Fish sprite must be present
        if not (is_green or is_red):
            return ReelGameState(is_active=False)

        pts = green_pts if is_green else red_pts

        # Filter out stray pixels, health bar, or floating text via spatial clustering
        if len(pts) > 60:
            clusters: List[List[Tuple[int, int]]] = []
            for pt in pts:
                matched = False
                for c in clusters:
                    if abs(pt[0] - c[0][0]) < 40 and abs(pt[1] - c[0][1]) < 30:
                        c.append(pt)
                        matched = True
                        break
                if not matched:
                    clusters.append([pt])
            # Largest cluster is the fish sprite
            if clusters:
                pts = max(clusters, key=len)

        fish_x = sum(p[0] for p in pts) / len(pts)
        fish_y = sum(p[1] for p in pts) / len(pts)

        # Verify physical blue water bar exists along the fish corridor
        # This completely rejects false triggers from catch popups, clothing, and water reflections!
        y_bar_min = max(0, int(fish_y - 14))
        y_bar_max = min(h, int(fish_y + 14))
        blue_xs = [x for y in range(y_bar_min, y_bar_max, 2) for x in range(0, w, 2)
                   if img.getpixel((x, y))[2] > 120 and img.getpixel((x, y))[0] < 50]
        if len(blue_xs) < 250:
            return ReelGameState(is_active=False)

        # Step 2: Search for dark slider via contiguous column span strictly inside the water track
        y_min = max(0, int(fish_y - 12))
        y_max = min(h, int(fish_y + 12))
        total_rows = max(1, (y_max - y_min) // 2)

        col_counts = [0] * w
        for y in range(y_min, y_max, 2):
            for x in range(0, w, 2):
                r, g, b = img.getpixel((x, y))
                if r < 50 and g < 50 and b < 50:
                    col_counts[x] += 1
                    col_counts[x + 1] += 1

        thresh = max(3, total_rows * 0.35)
        spans: List[Tuple[int, int, int]] = []
        current_start = None
        gap = 0
        for x in range(w):
            if col_counts[x] >= thresh:
                if current_start is None:
                    current_start = x
                gap = 0
            else:
                if current_start is not None:
                    gap += 1
                    if gap > 6:
                        span_end = x - gap
                        span_w = span_end - current_start + 1
                        spans.append((current_start, span_end, span_w))
                        current_start = None
                        gap = 0
        if current_start is not None:
            span_end = w - 1 - gap
            span_w = span_end - current_start + 1
            spans.append((current_start, span_end, span_w))

        slider_center = None
        slider_left = None
        slider_right = None
        slider_width = None
        best_span = None

        if spans:
            if is_green:
                # When green, the fish is inside the slider.
                valid_spans = [
                    s for s in spans
                    if s[2] <= 260 and abs((s[0] + s[1]) / 2.0 - fish_x) <= 90
                ]
                if valid_spans:
                    best_span = min(valid_spans, key=lambda s: abs(s[2] - 155))
                    slider_center = (best_span[0] + best_span[1]) / 2.0
                else:
                    slider_center = fish_x
            else:
                # Slider width is ~140..185px. Require span_w >= 75 to reject narrow edge posts (~50-60px)
                valid_spans = [
                    s for s in spans
                    if 75 <= s[2] <= 280
                ]
                if valid_spans:
                    # Choose span closest to ideal slider width (~155px)
                    best_span = min(valid_spans, key=lambda s: abs(s[2] - 155))
                    slider_center = (best_span[0] + best_span[1]) / 2.0
                else:
                    # Fallback if no span >= 75px: choose span closest to ideal slider width
                    best_span = min(spans, key=lambda s: abs(s[2] - 155))
                    slider_center = (best_span[0] + best_span[1]) / 2.0
        elif is_green:
            slider_center = fish_x

        if best_span is not None:
            slider_left = float(best_span[0])
            slider_right = float(best_span[1])
            slider_width = float(best_span[2])
            slider_center = (slider_left + slider_right) / 2.0
        elif is_green and slider_center is not None:
            slider_width = 155.0
            slider_left = slider_center - (slider_width / 2.0)
            slider_right = slider_center + (slider_width / 2.0)

        # Calculate physical corridor bounds enclosing blue cylinder, fish, and slider
        corridor_xs = list(blue_xs) + [p[0] for p in pts]
        if slider_left is not None and slider_right is not None:
            corridor_xs.extend([int(slider_left), int(slider_right)])
        bar_left_x = float(min(corridor_xs)) if corridor_xs else 0.0
        bar_right_x = float(max(corridor_xs)) if corridor_xs else float(w)

        return ReelGameState(
            is_active=True,
            is_green=is_green,
            fish_x=fish_x,
            fish_y=fish_y,
            slider_left_x=slider_left,
            slider_center_x=slider_center,
            slider_right_x=slider_right,
            slider_width=slider_width,
            bar_left_x=bar_left_x,
            bar_right_x=bar_right_x,
            progress_gain=is_green
        )

    def detect_reel_lines(self, screen_or_bar_img: Image.Image) -> ReelGameState:
        """DeepFish LineScan algorithm for line-based minigames (e.g. Fisch).
        Detects vertical lines via 1D horizontal contrast gradients (d > 54) sampled across rows.
        A line pair with gap 4..25 px represents the fish; outer lines with sep >= 40 px define the slider bar.
        """
        img = screen_or_bar_img.convert("RGB")
        w, h = img.size
        raw = img.tobytes()

        hits = [0] * w
        rows = 0
        for y in range(0, h, 3):
            row_off = y * w * 3
            prev_sum = raw[row_off] + raw[row_off + 1] + raw[row_off + 2]
            for x in range(1, w):
                idx = row_off + x * 3
                cur_sum = raw[idx] + raw[idx + 1] + raw[idx + 2]
                d = abs(cur_sum - prev_sum)
                if d > 54:
                    hits[x] += 1
                prev_sum = cur_sum
            rows += 1

        need = max(2, round(rows * 0.5))
        line_list: List[float] = []
        s = -1
        p = -1
        for x in range(1, w):
            if hits[x] >= need:
                if s < 0:
                    s = x
                    p = x
                elif x - p <= 3:
                    p = x
                else:
                    line_list.append((s + p) / 2.0)
                    s = x
                    p = x
        if s >= 0:
            line_list.append((s + p) / 2.0)

        best_gap = 999.0
        fi = -1
        for i in range(len(line_list) - 1):
            gap = line_list[i + 1] - line_list[i]
            if 4 <= gap <= 25 and gap < best_gap:
                best_gap = gap
                fi = i

        if fi < 0:
            return ReelGameState(is_active=False)

        fish_cx = (line_list[fi] + line_list[fi + 1]) / 2.0
        others = [line_list[i] for i in range(len(line_list)) if i != fi and i != fi + 1]

        lo, hi = None, None
        best_score = 999999.0
        for oi in range(len(others)):
            for oj in range(oi + 1, len(others)):
                sep = others[oj] - others[oi]
                if sep >= 40:
                    score = abs(sep - 160.0)
                    if score < best_score:
                        best_score = score
                        lo = others[oi]
                        hi = others[oj]

        slider_center = None
        slider_left = None
        slider_right = None
        slider_width = None
        is_green = False

        if lo is not None and hi is not None:
            slider_left = float(lo)
            slider_right = float(hi)
            slider_width = float(hi - lo + 1)
            slider_center = (slider_left + slider_right) / 2.0
            is_green = slider_left <= fish_cx <= slider_right
        else:
            slider_center = fish_cx
            slider_width = 155.0
            slider_left = fish_cx - 77.5
            slider_right = fish_cx + 77.5
            is_green = True

        return ReelGameState(
            is_active=True,
            is_green=is_green,
            fish_x=fish_cx,
            fish_y=float(h // 2),
            slider_left_x=slider_left,
            slider_center_x=slider_center,
            slider_right_x=slider_right,
            slider_width=slider_width,
            bar_left_x=0.0,
            bar_right_x=float(w),
            progress_gain=is_green
        )

