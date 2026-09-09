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


@dataclass
class CastMeterState:
    detected: bool = False
    fill_pct: float = 0.0          # 0.0 to 100.0%
    white_cap_y: Optional[int] = None
    fill_top_y: Optional[int] = None
    fill_bot_y: Optional[int] = None
    dist_px: Optional[int] = None  # Remaining pixels to top white cap
    total_height: Optional[int] = None
    mid_x: Optional[int] = None
    cyan_rows: int = 0
    white_cap_pixels: int = 0


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

    def analyze_cast_progress(self, cast_img: Image.Image) -> CastMeterState:
        """Monitors the vertical cast capsule fill, locates the white top cap,
        measures liquid height, and calculates fill percentage.
        """
        img = cast_img.convert("RGB")
        w, h = img.size
        raw = img.tobytes()

        # Step 1: Scan for columns containing the cyan liquid
        col_hits = [0] * w
        for y in range(0, h, 3):
            row_off = y * w * 3
            for x in range(2, w - 2, 2):
                idx = row_off + x * 3
                r, g, b = raw[idx], raw[idx + 1], raw[idx + 2]
                # Cyan liquid fill inside the cast capsule
                if (r < 150 and g >= 140 and b >= 195 and (g - r) >= 35 and (b - r) >= 55) or \
                   (r < 235 and g >= 200 and b >= 230 and (g - r) >= 20 and (b - r) >= 20):
                    col_hits[x] += 1

        active_xs = [x for x, cnt in enumerate(col_hits) if cnt >= 3]
        if not active_xs:
            return CastMeterState(detected=False)

        # Step 2: Group into contiguous candidate columns & filter by capsule width
        components = []
        curr = [active_xs[0]]
        for x in active_xs[1:]:
            if x - curr[-1] <= 6:
                curr.append(x)
            else:
                components.append(curr)
                curr = [x]
        components.append(curr)

        valid_caps = []
        for comp in components:
            comp_w = comp[-1] - comp[0] + 4
            # Capsule width is constrained (rejects whole-screen open water)
            if 8 <= comp_w <= 95:
                mid_x = (comp[0] + comp[-1]) // 2
                valid_caps.append((mid_x, comp_w, len(comp)))

        if not valid_caps:
            return CastMeterState(detected=False)

        # Select candidate with the strongest vertical presence
        mid_x, comp_w, _ = max(valid_caps, key=lambda c: c[2])

        # Step 3: Vertical sampling along the capsule midline
        cyan_ys = []
        for y in range(0, h, 2):
            is_cyan = False
            for dx in (0, -2, 2):
                tx = min(w - 1, max(0, mid_x + dx))
                idx = y * w * 3 + tx * 3
                r, g, b = raw[idx], raw[idx + 1], raw[idx + 2]
                if (r < 150 and g >= 140 and b >= 195 and (g - r) >= 35 and (b - r) >= 55) or \
                   (r < 235 and g >= 200 and b >= 230 and (g - r) >= 20 and (b - r) >= 20):
                    is_cyan = True
                    break
            if is_cyan:
                cyan_ys.append(y)

        if len(cyan_ys) < 3:
            return CastMeterState(detected=False)

        fill_top_y = min(cyan_ys)
        fill_bot_y = max(cyan_ys)

        # Check dark border flank (when ROI is wider than the capsule itself)
        if w > comp_w + 12:
            border_y = (fill_top_y + fill_bot_y) // 2
            lx = max(0, mid_x - comp_w // 2 - 2)
            rx = min(w - 1, mid_x + comp_w // 2 + 2)
            l_dark = raw[border_y * w * 3 + lx * 3] < 85
            r_dark = raw[border_y * w * 3 + rx * 3] < 85
            if not (l_dark and r_dark):
                return CastMeterState(detected=False)

        # Step 4: Scan upward from fill_top_y to locate glowing white top cap
        white_cap_y = None
        white_cap_pixels = 0
        search_limit = max(0, fill_bot_y - 450)
        for y in range(fill_top_y, search_limit, -1):
            w_cnt = 0
            for dx in range(-6, 7, 2):
                tx = min(w - 1, max(0, mid_x + dx))
                idx = y * w * 3 + tx * 3
                r, g, b = raw[idx], raw[idx + 1], raw[idx + 2]
                if r >= 155 and g >= 165 and b >= 175 and abs(g - r) < 25 and (b - r) < 45:
                    w_cnt += 1
            if w_cnt >= 2:
                white_cap_y = y
                white_cap_pixels += w_cnt
                break

        # Step 5: Compute fill percentage and distance to white target
        if white_cap_y is not None and fill_bot_y > white_cap_y:
            total_h = fill_bot_y - white_cap_y
            # Vertical aspect ratio constraint (capsule is tall and narrow)
            if total_h < 2.0 * comp_w or total_h < 55:
                return CastMeterState(detected=False)
            dist_px = max(0, fill_top_y - white_cap_y)
            fill_pct = max(0.0, min(100.0, (1.0 - (dist_px / total_h)) * 100.0))
        else:
            total_h = max(1, fill_bot_y - fill_top_y)
            # Vertical aspect ratio constraint
            if total_h < 2.0 * comp_w or total_h < 55:
                return CastMeterState(detected=False)
            dist_px = 0
            fill_pct = 98.0 if len(cyan_ys) >= 30 else 50.0

        return CastMeterState(
            detected=True,
            fill_pct=fill_pct,
            white_cap_y=white_cap_y,
            fill_top_y=fill_top_y,
            fill_bot_y=fill_bot_y,
            dist_px=dist_px,
            total_height=total_h,
            mid_x=mid_x,
            cyan_rows=len(cyan_ys),
            white_cap_pixels=white_cap_pixels,
        )

    def check_cast_fill(self, cast_img: Image.Image, fill_threshold: int = 16, target_pct: float = 92.0) -> Tuple[bool, int, int]:
        """Monitors the vertical cast capsule fill.
        Returns (top_reached, cyan_row_count, white_cap_pixels).
        """
        state = self.analyze_cast_progress(cast_img)
        if state.detected:
            top_reached = (state.fill_pct >= target_pct) or (state.cyan_rows >= max(14, fill_threshold) and state.fill_pct >= 85.0)
            return top_reached, state.cyan_rows, state.white_cap_pixels
        return False, 0, 0

    def is_cast_top_reached(self, cast_img: Image.Image, fill_threshold: int = 16, target_pct: float = 92.0) -> bool:
        reached, _, _ = self.check_cast_fill(cast_img, fill_threshold, target_pct)
        return reached

    # --- 3. Shake Button Detection ---

    def find_shake_button(self, screen_img: Image.Image,
                          min_confidence: float = 0.35) -> Optional[Tuple[int, int]]:
        """Scans image for circular SHAKE prompt buttons with white 'SHAKE' text inside.
        Rejects HUD numbers, health bars, chat text, or leaderboards, functioning down to 30% scale
        across all backgrounds (water, dock wood, player clothes).
        Returns (center_x, center_y) relative to the input image, or None.
        """
        img = screen_img.convert("RGB")
        w, h = img.size
        raw = img.tobytes()

        # Step 1: Scan for bright neutral text pixels (white/light grey across lighting)
        white_pts = []
        step = 2
        for y in range(4, h - 4, step):
            row_offset = y * w * 3
            for x in range(4, w - 4, step):
                idx = row_offset + x * 3
                r, g, b = raw[idx], raw[idx + 1], raw[idx + 2]
                if r > 185 and g > 185 and b > 185:
                    white_pts.append((x, y))

        if len(white_pts) < 6:
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
            return b > 35 and (b - r > 12) and (b - g > 8) and r < 55 and g < 75

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

            # Word 'SHAKE' is a single word: width 14..140, height 2..40, aspect 1.8..7.5, min 6 points
            # Completely rejects HUD stamina/health bars (which have aspect ratio >= 8.5)
            if not (14 <= c_w <= 140 and 2 <= c_h <= 40 and 1.8 <= aspect <= 7.5 and len(pts) >= 6):
                continue

            avg_x = int(cluster[0])
            avg_y = int(cluster[1])

            # Reject extended white bars: if pixels beyond the left/right of the word are also white,
            # it is part of an ongoing horizontal UI bar (e.g. stamina bar), not the standalone word 'SHAKE'.
            margin_x = max(6, int(c_w * 0.2))
            left_x = min(xs) - margin_x
            right_x = max(xs) + margin_x
            if left_x >= 0 and right_x < w:
                lr, lg, lb = img.getpixel((left_x, avg_y))[:3]
                rr, rg, rb = img.getpixel((right_x, avg_y))[:3]
                if (lr > 185 and lg > 185 and lb > 185) and (rr > 185 and rg > 185 and rb > 185):
                    continue

            # Dynamic vertical offset scaled to the cluster size (prevents sampling outside distant small disks)
            dy = max(4, int(1.1 * max(c_h, 5)))
            top_navy = (avg_y - dy >= 0 and is_navy(*img.getpixel((avg_x, avg_y - dy))[:3]))
            bot_navy = (avg_y + dy < h and is_navy(*img.getpixel((avg_x, avg_y + dy))[:3]))
            navy_verified = top_navy or bot_navy

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
                            if val > 150:
                                score += 1
                        else:
                            if val > 185:
                                penalty += 1.0  # Penalize non-letter white fill

                total_bg = self._shake_th * self._shake_tw - self._shake_white_count
                pen_ratio = penalty / max(1, total_bg)
                if pen_ratio > 0.35:  # Solid white UI bars have >50% non-letter white fill!
                    continue

                match_sc = (score - penalty * 1.0) / self._shake_white_count

                is_valid = (match_sc >= min_confidence) or (match_sc >= 0.22 and navy_verified)
                if is_valid and match_sc > best_score:
                    best_score = match_sc
                    best_pt = (avg_x, avg_y)
            else:
                # Fallback verification: 4-way circular disk checks
                top_cnt = sum(1 for dx in [-int(c_w * 0.25), 0, int(c_w * 0.25)]
                              if 0 <= avg_x + dx < w and 0 <= avg_y - dy < h
                              and is_navy(*img.getpixel((avg_x + dx, avg_y - dy))[:3]))
                bot_cnt = sum(1 for dx in [-int(c_w * 0.25), 0, int(c_w * 0.25)]
                              if 0 <= avg_x + dx < w and 0 <= avg_y + dy < h
                              and is_navy(*img.getpixel((avg_x + dx, avg_y + dy))[:3]))

                if top_cnt >= 1 and bot_cnt >= 1:
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

        is_green = len(green_pts) >= 40 and len(green_pts) > len(red_pts)
        is_red = len(red_pts) >= 40 and len(red_pts) >= len(green_pts)

        # Fish sprite must be present
        if not (is_green or is_red):
            return ReelGameState(is_active=False)

        pts = green_pts if is_green else red_pts

        # Filter out stray pixels, health bar, or floating text via spatial clustering
        clusters: List[List[Tuple[int, int]]] = []
        for pt in pts:
            matched = False
            for c in clusters:
                if abs(pt[0] - c[0][0]) < 45 and abs(pt[1] - c[0][1]) < 35:
                    c.append(pt)
                    matched = True
                    break
            if not matched:
                clusters.append([pt])

        if not clusters:
            return ReelGameState(is_active=False)

        best_cluster = max(clusters, key=len)
        # A real fish sprite has >= 40 sampled points (real sprite has 600+), rejecting single-frame clicks/sparks
        if len(best_cluster) < 40:
            return ReelGameState(is_active=False)

        c_xs = [p[0] for p in best_cluster]
        c_ys = [p[1] for p in best_cluster]
        cw = max(c_xs) - min(c_xs)
        ch = max(c_ys) - min(c_ys)
        # Sprite dimensions: width 16..160, height 8..80
        if not (16 <= cw <= 160 and 8 <= ch <= 80):
            return ReelGameState(is_active=False)

        fish_x = sum(c_xs) / len(best_cluster)
        fish_y = sum(c_ys) / len(best_cluster)

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

