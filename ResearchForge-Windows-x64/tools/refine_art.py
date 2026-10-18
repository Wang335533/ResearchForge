#!/usr/bin/env python3
"""Give the procedural illustrations real ink-wash brushwork with SDXL (development tool).

`paint_art.py` composes every scene as seeded, theme-neutral alpha masks. This tool
re-creates those masks in memory, renders each layer as ink on paper, lets Stable
Diffusion XL repaint it (img2img: the procedural layer fixes the composition, the
model supplies brush texture), and converts the result back into an alpha mask that
is clipped to the layer's own footprint. Layers therefore stay separable for parallax
and CSS still colours them per theme. It also paints the small empty-state vignettes.

Needs a CUDA GPU and, in the interpreter that runs it (not the app runtime):
torch, diffusers 0.35, numpy, opencv-python, Pillow, plus the cached weights
stabilityai/stable-diffusion-xl-base-1.0 (fp16) and madebyollin/sdxl-vae-fp16-fix.
    python tools/paint_art.py                       # procedural base (also writes JSON data)
    python tools/refine_art.py [--only dawn,vignettes] [--preview DIR]
Rerunning reproduces the same files: seeds are fixed and the base is regenerated.
"""
import argparse
import os
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paint_art  # noqa: E402
from paint_art import OUT, save_mask, smoothstep  # noqa: E402

PAPER = np.array([238, 233, 220], np.float32)
INK = np.array([38, 38, 36], np.float32)
STYLE = ('traditional Chinese ink wash painting, monochrome black ink on plain xuan rice paper, visible brush strokes, '
         'ink bleeding softly into paper, Song dynasty master, elegant, minimal, generous empty space')
NEGATIVE = ('color, photo, photograph, 3d render, text, letters, calligraphy, watermark, signature, red seal, stamp, chop mark, frame, border, '
            'cartoon, anime, harsh outline, oversaturated, blurry, jpeg artifacts')

# scene -> layer -> (img2img strength, subject[, seed]). Mist, water and wave layers keep their procedural
# softness; islands stay close to the procedural shapes because their reflections are mirrored from them.
LAYERS = {
    'dawn': {
        'far': (0.62, 'distant pale mountain range in morning haze, very light diluted ink, soft wet washes'),
        'mid': (0.72, 'layered misty mountain ridges, hemp-fiber and axe-cut cun strokes on rock faces, layered wet ink washes'),
        'near': (0.68, 'dark foreground rocky ridge with a few small pine trees, bold dry brush, dark ink'),
    },
    'lake': {
        'far': (0.6, 'distant low hills across a lake, pale diluted ink, soft wet washes, misty'),
        'near': (0.68, 'rocky shoreline hills on the right, cun texture strokes, medium ink'),
        'reeds': (0.5, 'reeds and grasses by the water, calligraphic brush strokes, slender leaves'),
    },
    'isles': {
        'islands': (0.56, 'small craggy rocky islets with sharp peaks and a few pines in a calm sea, cun texture strokes, wet ink washes'),
    },
    'plum': {
        'branch': (0.58, 'gnarled old plum tree branch, rough bark in dry brush, flying-white strokes, dark ink, no blossoms, no leaves', 207),
    },
}

# Empty-state vignettes: text-to-image, one small motif per module; seeds picked from candidates.
VIGNETTES = {
    'research': (11, 'a small pavilion on a rocky outcrop with one leaning pine tree, a winding path below'),
    'reading': (31, 'a small scholar seated on a rock under an old pine tree, reading a book, quiet and contemplative'),
    'atlas': (11, 'a tiny fishing boat with a lone fisherman on calm water, a low distant islet'),
    'prompts': (12, 'a single branch of plum blossoms'),
}
VIGNETTE_STYLE = ('minimalist traditional Chinese ink painting, one small subject in the centre, isolated on blank white xuan paper, '
                  'vast empty background, soft wet ink, few confident brush strokes, elegant')
VIGNETTE_NEGATIVE = NEGATIVE + (', chinese characters, inscription, poem, colophon, background wash, dark background, '
                                'landscape background, table, wall, teacup, full frame, crowded')
VIGNETTE_SIZE = (720, 540)


def load_pipelines():
    import torch
    from diffusers import AutoencoderKL, StableDiffusionXLImg2ImgPipeline, StableDiffusionXLPipeline
    os.environ.setdefault('HF_HUB_OFFLINE', '1')
    vae = AutoencoderKL.from_pretrained('madebyollin/sdxl-vae-fp16-fix', torch_dtype=torch.float16)
    img2img = StableDiffusionXLImg2ImgPipeline.from_pretrained(
        'stabilityai/stable-diffusion-xl-base-1.0', vae=vae, torch_dtype=torch.float16, variant='fp16', use_safetensors=True).to('cuda')
    img2img.set_progress_bar_config(disable=True)
    txt2img = StableDiffusionXLPipeline(**img2img.components)
    txt2img.set_progress_bar_config(disable=True)
    return img2img, txt2img, torch


def ink_render(alpha, seed):
    """The layer as ink on paper, roughened so the model has texture to paint into."""
    rng = np.random.default_rng(seed)
    h, w = alpha.shape
    grain = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 2.2)
    streak = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), sigmaX=1.2, sigmaY=9)
    a = np.clip(alpha / max(alpha.max(), 1e-3) * (0.82 + 0.22 * grain + 0.3 * streak), 0, 1)[..., None] * 0.9
    return Image.fromarray((PAPER * (1 - a) + INK * a).astype(np.uint8))


def model_size(w, h, area=1792 * 576):
    """Closest size near SDXL's native pixel budget with the same aspect, in multiples of 64."""
    scale = (area / (w * h)) ** 0.5
    return max(64, round(w * scale / 64) * 64), max(64, round(h * scale / 64) * 64)


def density(image):
    """Ink density in [0, 1] from a painted image: paper -> 0, darkest ink -> 1."""
    g = np.asarray(image.convert('L'), np.float32)
    paper, ink = np.percentile(g, 99.5), np.percentile(g, 0.2)
    return smoothstep(0.04, 1.0, np.clip((paper - g) / max(1.0, paper - ink), 0, 1))


def footprint(alpha, grow):
    """Soft region a refined layer may occupy: the procedural layer, slightly grown."""
    solid = (alpha > 0.02).astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1, 2 * grow + 1))
    region = cv2.GaussianBlur(cv2.dilate(solid, kernel).astype(np.float32), (0, 0), grow / 2)
    return np.maximum(np.clip(region, 0, 1), alpha)


def refine_layer(img2img, torch, alpha, strength, subject, seed):
    h, w = alpha.shape
    size = model_size(w, h)
    prompt = subject + ', ' + STYLE
    base = ink_render(alpha, seed).resize(size, Image.LANCZOS)
    painted = img2img(prompt, negative_prompt=NEGATIVE, image=base, strength=strength, num_inference_steps=36,
                      guidance_scale=6.0, generator=torch.Generator('cuda').manual_seed(seed)).images[0]
    # A light full-resolution pass sharpens the brushwork after upscaling.
    full = (w // 8 * 8, -(-h // 8) * 8)
    painted = img2img(prompt, negative_prompt=NEGATIVE, image=painted.resize(full, Image.LANCZOS), strength=0.28,
                      num_inference_steps=30, guidance_scale=5.0, generator=torch.Generator('cuda').manual_seed(seed + 1)).images[0]
    d = cv2.resize(density(painted), (w, h), interpolation=cv2.INTER_AREA)
    d *= footprint(alpha, max(4, w // 400))
    return match_tone(d, alpha), painted


def match_tone(d, alpha):
    """Keep the layer's overall tonal weight (scene balance) with a gamma curve, which,
    unlike a gain, never clips the washes into flat silhouettes."""
    inside = alpha > 0.02
    target, lo, hi = alpha[inside].mean(), 0.25, 4.0
    for _ in range(30):
        gamma = (lo * hi) ** 0.5
        lo, hi = (gamma, hi) if (d[inside] ** gamma).mean() > target else (lo, gamma)
    return np.clip(d, 0, 1) ** gamma


def keep_central(d, frac=0.6):
    """Drop ink islands that do not reach the central area (stray seals, inscriptions, corner marks)."""
    h, w = d.shape
    solid = (cv2.GaussianBlur(d, (0, 0), 6) > 0.06).astype(np.uint8)
    solid = cv2.dilate(solid, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(solid)
    x0, x1, y0, y1 = w * (1 - frac) / 2, w * (1 + frac) / 2, h * (1 - frac) / 2, h * (1 + frac) / 2
    keep = np.zeros((h, w), np.float32)
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area > 0.002 * h * w and x < x1 and x + bw > x0 and y < y1 and y + bh > y0:
            keep[labels == i] = 1
    return d * cv2.GaussianBlur(keep, (0, 0), 8)


def vignette(txt2img, torch, seed, subject):
    image = txt2img(subject + ', ' + VIGNETTE_STYLE, negative_prompt=VIGNETTE_NEGATIVE, width=1024, height=768,
                    num_inference_steps=36, guidance_scale=6.5, generator=torch.Generator('cuda').manual_seed(seed)).images[0]
    d = keep_central(density(image))
    h, w = d.shape
    # Fade towards the edges so the vignette never shows a rectangular boundary.
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    edge = np.minimum.reduce([xx, yy, w - 1 - xx, h - 1 - yy]) / (0.16 * min(w, h))
    d *= smoothstep(0, 1, edge)
    return cv2.resize(d, VIGNETTE_SIZE, interpolation=cv2.INTER_AREA), image


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--only', help='逗号分隔：dawn,lake,isles,plum,vignettes（默认全部）')
    parser.add_argument('--preview', type=Path, help='另存模型原图与彩色合成预览（不随包分发）')
    args = parser.parse_args()
    only = set(args.only.split(',')) if args.only else set(LAYERS) | {'vignettes'}
    img2img, txt2img, torch = load_pipelines()
    if args.preview:
        args.preview.mkdir(parents=True, exist_ok=True)
    painters = {'dawn': paint_art.scene_dawn, 'lake': paint_art.scene_lake, 'isles': paint_art.scene_isles, 'plum': paint_art.scene_plum}
    for index, (name, painter) in enumerate(painters.items()):
        if name not in only:
            continue
        layers, _ = painter(np.random.default_rng(20261013 + index))
        for layer, (strength, subject, *seed) in LAYERS[name].items():
            seed = seed[0] if seed else 100 + index * 10 + len(layer)
            alpha, painted = refine_layer(img2img, torch, layers[layer], strength, subject, seed)
            layers[layer] = alpha
            size = save_mask(alpha, OUT / f'{name}-{layer}.webp')
            print(f'{name}-{layer}: {size // 1024} KB', flush=True)
            if args.preview:
                painted.save(args.preview / f'{name}-{layer}-model.jpg', quality=85)
        if args.preview:
            paint_art.preview(name, layers, args.preview)
    if 'vignettes' in only:
        for name, (seed, subject) in VIGNETTES.items():
            alpha, image = vignette(txt2img, torch, seed, subject)
            size = save_mask(alpha, OUT / f'vignette-{name}.webp', 88)
            print(f'vignette-{name}: {size // 1024} KB', flush=True)
            if args.preview:
                image.save(args.preview / f'vignette-{name}-model.jpg', quality=85)


if __name__ == '__main__':
    main()
