#!/usr/bin/env python3
"""Paint the workspace illustrations as theme-neutral alpha masks (development tool).

Every layer is an ink-density mask: CSS colours it with design tokens through
`mask-image`, so one asset set serves light and dark themes and layers can move
independently for parallax. The painting is procedural and seeded — rerunning the
script reproduces the same files. Point data that the interface animates (island
routes, plum blossoms) is written next to the masks as JSON.

Needs numpy, opencv-python and Pillow in the interpreter that runs it (not part of
the app runtime):
    python tools/paint_art.py [--preview DIR]
"""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'platform/shared_ui/art'


# ---------------------------------------------------------------- noise helpers
def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def noise2(rng, h, w, cell, wrap=False):
    """Smooth value noise in [0, 1] with roughly `cell` pixels between lattice points."""
    gh, gw = max(2, int(np.ceil(h / cell)) + 3), max(2, int(np.ceil(w / cell)) + 3)
    grid = rng.random((gh, gw)).astype(np.float32)
    if wrap:
        gh, gw = max(2, round(h / cell)), max(2, round(w / cell))
        grid = rng.random((gh, gw)).astype(np.float32)
        tiled = np.tile(grid, (3, 3))
        big = cv2.resize(tiled, (w * 3, h * 3), interpolation=cv2.INTER_CUBIC)
        return np.clip(big[h:2 * h, w:2 * w], 0, 1)
    big = cv2.resize(grid, (int(gw * cell), int(gh * cell)), interpolation=cv2.INTER_CUBIC)
    oy, ox = int(rng.integers(0, cell)), int(rng.integers(0, cell))
    return np.clip(big[oy:oy + h, ox:ox + w], 0, 1)


def fbm2(rng, h, w, cell, octaves=5, gain=0.5, wrap=False):
    total, amp, norm = np.zeros((h, w), np.float32), 1.0, 0.0
    for _ in range(octaves):
        total += amp * noise2(rng, h, w, max(2, cell), wrap)
        norm += amp
        amp *= gain
        cell /= 2
    return total / norm


def fbm1(rng, n, cell, octaves=6, gain=0.52):
    total, amp, norm = np.zeros(n, np.float32), 1.0, 0.0
    for _ in range(octaves):
        points = max(4, int(n / cell) + 4)
        values = rng.random(points).astype(np.float32)
        line = cv2.resize(values[None, :], (int(points * cell), 1), interpolation=cv2.INTER_CUBIC)[0]
        offset = int(rng.integers(0, max(1, int(cell))))
        total += amp * np.clip(line[offset:offset + n], 0, 1)
        norm += amp
        amp *= gain
        cell = max(2, cell / 2)
    return total / norm


def streaks(rng, h, w, cell_x, cell_y):
    """Anisotropic noise: thin vertical fibres, like texture strokes (皴) in a wash."""
    gh, gw = int(h / cell_y) + 3, int(w / cell_x) + 3
    grid = rng.random((gh, gw)).astype(np.float32)
    big = cv2.resize(grid, (int(gw * cell_x), int(gh * cell_y)), interpolation=cv2.INTER_CUBIC)
    return np.clip(big[:h, :w], 0, 1)


# ---------------------------------------------------------------- painting
def bumps(w, spec):
    """Composition envelope: a floor plus gaussian rises at (centre, width, height) in 0–1 units."""
    x = np.linspace(0, 1, w, dtype=np.float32)
    floor, rises = spec
    env = np.full(w, floor, np.float32)
    for centre, width, height in rises:
        env += height * np.exp(-((x - centre) / width) ** 2)
    return env


def ridge_line(rng, w, horizon, amp, cell, peaks=0.5, envelope=None, detail=0.18):
    base = fbm1(rng, w, cell, octaves=5, gain=0.5)
    ridged = 1 - np.abs(fbm1(rng, w, cell * 0.7, octaves=4, gain=0.48) * 2 - 1)
    profile = (1 - peaks) * base + peaks * ridged ** 1.8
    profile = (profile - profile.min()) / (profile.max() - profile.min() + 1e-6)
    fine = fbm1(rng, w, cell * 0.12, octaves=3) - 0.5
    env = envelope if envelope is not None else np.ones(w, np.float32)
    return horizon - amp * (profile * env + detail * fine * env)


def texture_field(rng, h, w, strength):
    """Wash granulation plus patchy vertical fibre strokes (皴), never uniform stripes."""
    grain = fbm2(rng, h, w, 70, 5)
    fibres = streaks(rng, h, w, 4.5, 70) * 0.6 + streaks(rng, h, w, 9, 140) * 0.4
    patches = smoothstep(0.42, 0.78, fbm2(rng, h, w, 260, 4))
    return 1 - strength * (0.55 * (1 - grain) + 0.45 * patches * smoothstep(0.35, 0.85, 1 - fibres))


def ridge_layer(rng, w, h, line, falloff, floor=0.08, rim=0.4, rim_width=10, texture=0.22, soft=0.0, envelope=None):
    y = np.arange(h, dtype=np.float32)[:, None]
    d = y - line[None, :]
    inside = smoothstep(-1.1, 1.1, d)
    below = np.clip(d, 0, None)
    density = floor + (1 - floor) * np.exp(-below / falloff)
    density *= 1 + rim * np.exp(-(below / rim_width) ** 2)
    alpha = inside * density * texture_field(rng, h, w, texture)
    if envelope is not None:
        alpha *= envelope[None, :]
    if soft:
        alpha = cv2.GaussianBlur(alpha, (0, 0), soft)
    return np.clip(alpha, 0, 1)


def mist_band(rng, w, h, center, width, strength=1.0):
    y = np.arange(h, dtype=np.float32)[:, None]
    band = np.exp(-((y - center) / width) ** 2)
    flow = fbm2(rng, h, w, 300, 5)
    wisps = smoothstep(0.3, 0.75, flow * 0.8 + 0.2 * streaks(rng, h, w, 220, 18))
    return np.clip(band * wisps * strength, 0, 1)


def bristle_stroke(canvas, points, widths, scale, rng, dryness=0.3, wetness=1.0):
    """One brush stroke as many bristles: dry bristles break first at the edges and the tail (飞白)."""
    pts = np.asarray(points, np.float32)
    if len(pts) < 2:
        return
    tangent = np.gradient(pts, axis=0)
    normal = np.stack([-tangent[:, 1], tangent[:, 0]], 1)
    normal /= np.linalg.norm(normal, axis=1, keepdims=True) + 1e-6
    width = np.asarray(list(widths) + [widths[-1]], np.float32)[:len(pts)]
    count = int(np.clip(width.max() * 0.9, 3, 36))
    t = np.linspace(0, 1, len(pts))
    for b in range(count):
        offset = (b / (count - 1) - 0.5) if count > 1 else 0.0
        offset += rng.normal(0, 0.035)
        ink = rng.uniform(0.84, 1.0) * wetness
        flow = fbm1(rng, len(pts), max(3, len(pts) / 5), octaves=3)
        threshold = dryness * (0.25 + 0.75 * t ** 1.3) * (0.45 + 1.6 * abs(offset))
        line = pts + normal * (offset * width)[:, None]
        thickness = np.maximum(1, width / count * 2.6 * scale).astype(int)
        for i in range(len(line) - 1):
            if flow[i] < threshold[i]:
                continue
            a, c = line[i] * scale, line[i + 1] * scale
            value = float(ink * (0.72 + 0.28 * min(1.0, flow[i] * 1.4)))
            cv2.line(canvas, (int(a[0]), int(a[1])), (int(c[0]), int(c[1])), value, int(thickness[i]), cv2.LINE_AA)


def pine(canvas, x, y, height, rng, scale):
    lean = rng.uniform(-0.16, 0.16)
    trunk = [(x + lean * height * (i / 10) ** 1.4, y - height * i / 10) for i in range(11)]
    bristle_stroke(canvas, trunk, [height * 0.06 * (1 - 0.5 * i / 10) for i in range(10)], scale, rng, 0.15)
    tiers = int(rng.integers(4, 7))
    for i in range(tiers):
        f = 0.3 + 0.7 * i / max(1, tiers - 1)
        cx, cy = x + lean * height * f ** 1.4, y - height * f
        span = height * (0.46 - 0.32 * f) * rng.uniform(0.8, 1.15)
        for _ in range(3):
            ex = cx + rng.uniform(-span * 0.45, span * 0.45)
            axes = (max(1, int(span * rng.uniform(0.4, 0.7) * scale)), max(1, int(height * 0.05 * rng.uniform(0.8, 1.3) * scale)))
            cv2.ellipse(canvas, (int(ex * scale), int((cy + rng.uniform(-3, 3)) * scale)), axes, rng.uniform(-10, 10), 0, 360, 1.0, -1, cv2.LINE_AA)


def pines_layer(rng, w, h, line, xs, height_range):
    scale = 3
    canvas = np.zeros((h * scale, w * scale), np.float32)
    for x in xs:
        for g in range(int(rng.integers(1, 4))):
            gx = float(np.clip(x + rng.uniform(-34, 34), 1, w - 2))
            pine(canvas, gx, line[int(gx)] + rng.uniform(3, 9), rng.uniform(*height_range) * (1 - 0.2 * g), rng, scale)
    small = cv2.resize(canvas, (w, h), interpolation=cv2.INTER_AREA)
    return np.clip(small * (0.82 + 0.18 * fbm2(rng, h, w, 18, 3)), 0, 1)


def reflect(land, water, rng, strength=0.5, reach=0.45):
    h, w = land.shape
    out = np.zeros_like(land)
    span = min(water, h - water)
    if span <= 0:
        return out
    flipped = land[water - span:water][::-1]
    yy, xx = np.mgrid[0:span, 0:w].astype(np.float32)
    ripple = 3.2 * np.sin(yy * 0.85 + 6 * fbm2(rng, span, w, 120, 3)) * (0.25 + yy / span)
    out[water:water + span] = cv2.remap(flipped, xx + ripple, yy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    fade = np.exp(-np.arange(span, dtype=np.float32) / (span * reach))[:, None]
    breaks = 0.5 + 0.5 * smoothstep(0.3, 0.72, streaks(rng, span, w, 260, 2.6))
    out[water:water + span] *= fade * breaks * strength
    return out


def wave_strokes(rng, w, h, rows, count, length=(60, 180), width=(1.6, 3.2), scale=3):
    """Short horizontal ink strokes on water (水纹), scattered along given waterlines."""
    canvas = np.zeros((h * scale, w * scale), np.float32)
    for _ in range(count):
        y0, x_lo, x_hi = rows[int(rng.integers(len(rows)))]
        x = rng.uniform(x_lo, x_hi) * w
        y = y0 + rng.uniform(4, 60)
        n, length_px = 12, rng.uniform(*length)
        pts = [(x + length_px * i / n, y + 2.2 * np.sin(i / n * np.pi * rng.uniform(1, 2))) for i in range(n + 1)]
        wd = rng.uniform(*width)
        bristle_stroke(canvas, pts, [wd * np.sin(np.pi * (i + 0.5) / n) + 0.4 for i in range(n)], scale, rng, 0.5, 0.8)
    return cv2.resize(canvas, (w, h), interpolation=cv2.INTER_AREA)


# ---------------------------------------------------------------- scenes
def scene_dawn(rng):
    w, h = 2800, 900
    far_env = bumps(w, (0.28, [(0.66, 0.16, 0.75), (0.86, 0.1, 0.45), (0.38, 0.12, 0.25)]))
    far = ridge_layer(rng, w, h, ridge_line(rng, w, 520, 330, 600, 0.4, far_env, 0.08), 240, floor=0.05, rim=0.16, texture=0.12, soft=1.4)
    mid_env = bumps(w, (0.32, [(0.52, 0.14, 0.55), (0.9, 0.12, 0.6), (0.18, 0.1, 0.22)]))
    mid = ridge_layer(rng, w, h, ridge_line(rng, w, 650, 260, 420, 0.55, mid_env, 0.12), 160, floor=0.1, rim=0.32, texture=0.22, soft=0.5)
    near_env = bumps(w, (0.35, [(0.78, 0.16, 0.6), (0.05, 0.08, 0.35), (0.42, 0.1, 0.2)]))
    near_line = ridge_line(rng, w, 830, 200, 340, 0.45, near_env, 0.14)
    near = ridge_layer(rng, w, h, near_line, 110, floor=0.22, rim=0.42, texture=0.26)
    near = np.maximum(near, pines_layer(rng, w, h, near_line, [w * 0.71, w * 0.79, w * 0.86, w * 0.06], (52, 98)))
    mist = np.maximum(mist_band(rng, w, h, 585, 62, 0.9), mist_band(rng, w, h, 735, 50, 0.8))
    return {'far': far, 'mid': mid, 'near': near, 'mist': mist}, {}


def scene_lake(rng):
    w, h = 2800, 900
    water = 560
    far_env = bumps(w, (0.35, [(0.72, 0.18, 0.65), (0.3, 0.14, 0.3)]))
    far = ridge_layer(rng, w, h, ridge_line(rng, w, water - 30, 170, 640, 0.35, far_env, 0.08), 150, floor=0.22, rim=0.18, texture=0.1, soft=1.2)
    far[water:] = 0
    near_env = bumps(w, (0.0, [(0.94, 0.12, 0.95), (0.03, 0.07, 0.6)]))
    near_line = ridge_line(rng, w, water + 4, 250, 360, 0.5, np.clip(near_env, 0, 1), 0.1)
    near = ridge_layer(rng, w, h, near_line, 110, floor=0.35, rim=0.34, texture=0.22, envelope=np.clip(near_env * 1.6, 0, 1))
    near[water:] = 0
    reflection = reflect(np.maximum(far * 0.7, near), water, rng, 0.5, 0.42)
    scale = 3
    canvas = np.zeros((h * scale, w * scale), np.float32)
    # Left reeds stay low so the page title can sit above them; the right bank grows taller.
    for side, (lo, hi), count, (h_lo, h_hi) in ((0, (0.0, 0.15), 16, (140, 300)), (1, (0.83, 1.0), 26, (210, 490))):
        for _ in range(count):
            base_x = rng.uniform(lo, hi) * w
            height = rng.uniform(h_lo, h_hi)
            bend = rng.uniform(-0.3, 0.3) + (0.14 if side == 0 else -0.14)
            n = 26
            stem = [(base_x + bend * height * (i / n) ** 2, h + 12 - height * i / n) for i in range(n + 1)]
            bristle_stroke(canvas, stem, [rng.uniform(3.0, 4.6) * (1 - 0.8 * i / n) + 0.5 for i in range(n)], scale, rng, 0.25)
            if rng.random() < 0.5:
                tip = stem[-1]
                head = [(tip[0] + bend * 30 * i / 8, tip[1] + 34 - 40 * i / 8) for i in range(9)]
                bristle_stroke(canvas, head, [9 * np.sin(np.pi * (i + 0.6) / 8) + 1 for i in range(8)], scale, rng, 0.35, 0.9)
            for _ in range(int(rng.integers(1, 3))):
                k = int(rng.integers(5, 15))
                leaf_len, direction = rng.uniform(90, 190), rng.choice([-1, 1])
                origin = stem[k]
                leaf = [(origin[0] + direction * leaf_len * i / 14, origin[1] - leaf_len * 0.55 * i / 14 + leaf_len * 0.7 * (i / 14) ** 2) for i in range(15)]
                bristle_stroke(canvas, leaf, [7 * np.sin(np.pi * (i + 0.5) / 14) ** 0.8 + 0.4 for i in range(14)], scale, rng, 0.4)
    reeds = np.clip(cv2.resize(canvas, (w, h), interpolation=cv2.INTER_AREA), 0, 1)
    waves = wave_strokes(rng, w, h, [(water, 0.25, 0.7), (water + 80, 0.3, 0.62)], 16, (80, 220), (1.4, 2.6))
    mist = mist_band(rng, w, h, water - 28, 38, 0.9)
    return {'far': far, 'near': near, 'reflection': np.clip(reflection, 0, 1), 'waves': np.clip(waves * 0.7, 0, 1),
            'reeds': reeds, 'mist': mist}, {'water': round(water / h, 4)}


def scene_isles(rng):
    w, h = 2800, 900
    islands = np.zeros((h, w), np.float32)
    reflections = np.zeros((h, w), np.float32)
    # (centre x, waterline y, width, height): far islets sit higher and smaller.
    specs = [(0.08, 0.44, 0.16, 120), (0.27, 0.36, 0.13, 95), (0.45, 0.47, 0.2, 165), (0.63, 0.33, 0.11, 80),
             (0.8, 0.43, 0.18, 150), (0.95, 0.31, 0.09, 70), (0.2, 0.74, 0.22, 210), (0.58, 0.8, 0.26, 230), (0.9, 0.72, 0.17, 160)]
    anchors = []
    canopy = np.zeros((h, w), np.float32)
    x = np.linspace(0, 1, w, dtype=np.float32)
    for cx, wy, span, height in specs:
        water = int(wy * h)
        env = np.clip(1 - ((x - cx) / (span / 2)) ** 2, 0, 1) ** 0.7
        line = ridge_line(rng, w, water, height, 160, 0.5, env, 0.1)
        line = np.where(env > 0, np.minimum(line, water - 1), water + 400)
        depth = 0.55 + 0.45 * (wy - 0.3) / 0.5
        isle = ridge_layer(rng, w, h, line, 70 + height * 0.35, floor=0.25, rim=0.4, texture=0.24) * smoothstep(0.0, 0.08, env)
        isle[water:] = 0
        isle *= depth
        islands = np.maximum(islands, isle)
        reflections = np.maximum(reflections, reflect(isle, water, rng, 0.45, 0.5))
        peak = int(np.argmin(np.where(env > 0, line, 1e9)))
        anchors.append({'x': round(peak / w, 4), 'y': round((water - 6) / h, 4), 'top': round(float(line[peak]) / h, 4), 'size': round(span, 3)})
        if height >= 150:
            trees = pines_layer(rng, w, h, line, [peak + rng.uniform(-30, 30)], (height * 0.22, height * 0.36))
            trees[water:] = 0
            canopy = np.maximum(canopy, trees * depth)
    rows = [(int(s[1] * h), s[0] - s[2] * 0.6, s[0] + s[2] * 0.6) for s in specs]
    waves = wave_strokes(rng, w, h, rows, 70, (50, 150), (1.2, 2.4))
    mist = np.maximum(mist_band(rng, w, h, h * 0.4, 46, 0.85), mist_band(rng, w, h, h * 0.66, 60, 0.7))
    routes = [[0, 1], [1, 2], [2, 3], [3, 4], [4, 5], [2, 7], [0, 6], [6, 7], [7, 8], [4, 8]]
    islands = np.maximum(islands, canopy)
    return ({'islands': np.clip(islands, 0, 1), 'reflection': np.clip(reflections, 0, 1), 'waves': np.clip(waves * 0.8, 0, 1), 'mist': mist},
            {'isles': anchors, 'routes': routes, 'width': w, 'height': h})


def scene_plum(rng):
    w, h = 1700, 1150
    scale = 3
    canvas = np.zeros((h * scale, w * scale), np.float32)
    blossoms = []

    def limb(x, y, heading, length, width, depth):
        """Plum limbs grow in straight, angular segments (如铁) rather than curves."""
        pts, widths = [(x, y)], []
        travelled = 0.0
        while travelled < length:
            seg = rng.uniform(55, 120) * (0.7 if depth else 1.0)
            turn = rng.uniform(-0.42, 0.42)
            direction = heading + turn
            for i in range(1, 5):
                pts.append((x + np.cos(direction) * seg * i / 4, y + np.sin(direction) * seg * i / 4))
                widths.append(width * max(0.06, 1 - 0.94 * min(1.0, (travelled + seg * i / 4) / length)) ** 0.8)
            x, y = pts[-1]
            travelled += seg
            if depth < 2 and rng.random() < (0.38 if depth == 0 else 0.22):
                side = rng.choice([-1, 1])
                limb(x, y, heading + side * rng.uniform(0.5, 1.0) - 0.25, length * rng.uniform(0.32, 0.55), widths[-1] * 0.6, depth + 1)
            if depth >= 1 and rng.random() < 0.3:
                twig_heading = -np.pi / 2 + rng.uniform(-0.55, 0.55)
                tl = rng.uniform(45, 140)
                twig = [(x + np.cos(twig_heading) * tl * i / 6, y + np.sin(twig_heading) * tl * i / 6) for i in range(7)]
                bristle_stroke(canvas, twig, [max(1.2, widths[-1] * 0.35) * (1 - 0.6 * i / 6) for i in range(6)], scale, rng, 0.25)
                blossoms.append((twig[-1], 'tip'))
                blossoms.append((twig[3], 'side'))
            if depth >= 1 and rng.random() < 0.3:
                blossoms.append(((x, y), 'node'))
        bristle_stroke(canvas, pts, widths, scale, rng, dryness=0.85 if depth == 0 else 0.55)
        blossoms.append((pts[-1], 'tip'))

    limb(w * 1.03, h * 0.8, np.pi + 0.36, 1250, 48, 0)
    limb(w * 0.97, h * 1.04, -np.pi / 2 - 0.5, 520, 26, 1)
    branch = cv2.resize(canvas, (w, h), interpolation=cv2.INTER_AREA)
    branch = np.clip(branch * (0.8 + 0.2 * fbm2(rng, h, w, 26, 3)), 0, 1)
    flowers = []
    for (bx, by), kind in blossoms:
        if not (20 < bx < w - 10 and 20 < by < h - 10):
            continue
        for _ in range(1 if kind == 'side' else int(rng.integers(1, 3))):
            fx, fy = bx + rng.normal(0, 10), by + rng.normal(0, 9)
            state = 'bud' if (kind == 'tip' and rng.random() < 0.5) or rng.random() < 0.15 else 'open'
            flowers.append({'x': round(float(fx) / w, 4), 'y': round(float(fy) / h, 4), 's': round(rng.uniform(16, 27) / w, 4),
                            'r': int(rng.uniform(0, 72)), 'state': state})
    flowers.sort(key=lambda f: -f['x'])
    return {'branch': branch}, {'blossoms': flowers, 'width': w, 'height': h}


def paper_tile(rng):
    size = 512
    grain = fbm2(rng, size, size, 3, 3, wrap=True)
    speck = smoothstep(0.8, 0.98, noise2(rng, size, size, 2, wrap=True)) * 0.5
    fibres = np.zeros((size * 3, size * 3), np.float32)
    for _ in range(700):
        x, y = rng.uniform(0, size * 3), rng.uniform(0, size * 3)
        ang, length = rng.uniform(0, np.pi), rng.uniform(12, 70)
        pts = []
        for i in range(9):
            ang += rng.normal(0, 0.22)
            pts.append((int(x), int(y)))
            x += np.cos(ang) * length / 9
            y += np.sin(ang) * length / 9
        cv2.polylines(fibres, [np.array(pts, np.int32)], False, float(rng.uniform(0.15, 0.5)), 1, cv2.LINE_AA)
    fibres = sum(fibres[i * size:(i + 1) * size, j * size:(j + 1) * size] for i in range(3) for j in range(3))
    return np.clip(0.3 * np.abs(grain - 0.5) * 2 + 0.5 * np.clip(fibres, 0, 1) + speck, 0, 1)


def enso(rng):
    size = 640
    scale = 2
    canvas = np.zeros((size * scale, size * scale), np.float32)
    cx = cy = size / 2
    start, sweep, n = 2.0, np.pi * 1.84, 140
    pts, widths = [], []
    for i in range(n + 1):
        t = i / n
        a = start + sweep * t
        r = size * 0.35 * (1 + 0.03 * np.sin(t * 5.5 + 0.4))
        pts.append((cx + np.cos(a) * r, cy + np.sin(a) * r))
        widths.append(size * (0.085 * (1 - t) ** 0.65 + 0.014))
    bristle_stroke(canvas, pts, widths[:-1], scale, rng, dryness=0.75)
    return np.clip(cv2.resize(canvas, (size, size), interpolation=cv2.INTER_AREA), 0, 1)


def seal(rng):
    size = 256
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    inset = 12
    edge = fbm2(rng, size, size, 9, 3)
    d = np.minimum.reduce([xx - inset, yy - inset, size - inset - xx, size - inset - yy])
    body = smoothstep(-1.5, 2.5, d + (edge - 0.5) * 8)
    wear = smoothstep(0.15, 0.5, fbm2(rng, size, size, 16, 4)) * 0.3 + 0.7
    holes = 1 - smoothstep(0.86, 0.95, noise2(rng, size, size, 5)) * 0.85
    return np.clip(body * wear * holes, 0, 1)


# ---------------------------------------------------------------- output
def save_mask(alpha, path, quality=86):
    a = (np.clip(alpha, 0, 1) * 255).astype(np.uint8)
    rgba = np.zeros((*a.shape, 4), np.uint8)
    rgba[..., 3] = a
    Image.fromarray(rgba, 'RGBA').save(path, 'WEBP', quality=quality, alpha_quality=92, method=6)
    return path.stat().st_size


PREVIEW = {
    'dawn': (('#f1ece2', '#f8f3ea'), [('far', '#b9c9c0'), ('mist', '#f6f1e7'), ('mid', '#8ea69a'), ('near', '#5d776b')]),
    'lake': (('#f2ede3', '#f7f2e9'), [('far', '#bccbc5'), ('mist', '#f6f1e7'), ('near', '#7f988c'), ('reflection', '#8fa59a'), ('waves', '#9fb2a8'), ('reeds', '#4f6559')]),
    'isles': (('#f3eee4', '#f6f1e8'), [('waves', '#a9b9b0'), ('reflection', '#9fb2a8'), ('islands', '#6f897c'), ('mist', '#f6f1e7')]),
    'plum': (('#f6f1e8', '#f6f1e8'), [('branch', '#3b3a35')]),
}

def preview(scene, layers, folder):
    sky, order = PREVIEW[scene]
    h, w = next(iter(layers.values())).shape
    top, bottom = (np.array([int(c[i:i + 2], 16) for i in (1, 3, 5)], np.float32) for c in sky)
    t = np.linspace(0, 1, h)[:, None, None]
    image = np.repeat(top * (1 - t) + bottom * t, w, axis=1)
    for name, colour in order:
        if name not in layers:
            continue
        rgb = np.array([int(colour[i:i + 2], 16) for i in (1, 3, 5)], np.float32)
        a = layers[name][..., None]
        image = image * (1 - a) + rgb * a
    Image.fromarray(np.clip(image, 0, 255).astype(np.uint8)).save(folder / f'{scene}-preview.jpg', quality=88)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--preview', type=Path, help='另存彩色合成预览（不随包分发）')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {}
    scenes = {'dawn': scene_dawn, 'lake': scene_lake, 'isles': scene_isles, 'plum': scene_plum}
    for index, (name, painter) in enumerate(scenes.items()):
        layers, data = painter(np.random.default_rng(20261013 + index))
        sizes = {}
        for layer, alpha in layers.items():
            sizes[layer] = save_mask(alpha, OUT / f'{name}-{layer}.webp')
        h, w = next(iter(layers.values())).shape
        manifest[name] = {'width': w, 'height': h, 'layers': list(layers)}
        if data:
            (OUT / f'{name}.json').write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        print(name, {k: f'{v // 1024} KB' for k, v in sizes.items()})
        if args.preview:
            args.preview.mkdir(parents=True, exist_ok=True)
            preview(name, layers, args.preview)
    rng = np.random.default_rng(7)
    for name, alpha in (('paper', paper_tile(rng)), ('enso', enso(rng)), ('seal', seal(rng))):
        print(name, f'{save_mask(alpha, OUT / f"{name}.webp", 90) // 1024} KB')
    (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
