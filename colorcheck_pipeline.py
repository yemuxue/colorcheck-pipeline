#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
colorcheck_pipeline.py — 24-patch ColorChecker calibration pipeline for a single photo.

Quick start
-----------
    # 1. tell the script where the chart is (once per photo, interactive)
    python colorcheck_pipeline.py photo.jpg --markers
       -> click the centres of the 4 corner patches: TL, TR, BL, BR
       -> writes photo_marked.png and photo.hint.json

    # 2. run the calibration (reuses the saved corners, no interaction)
    python colorcheck_pipeline.py photo.jpg --report

    # or give the corners directly, in pixels or as 0..1 fractions
    python colorcheck_pipeline.py photo.jpg --hint 108,438 246,536 33,530 178,628
    python colorcheck_pipeline.py photo.jpg --hint 0.17,0.51 0.39,0.63 0.05,0.62 0.28,0.74

    # clean studio shot? the best-effort search may work, but verify the grid overlay
    python colorcheck_pipeline.py photo.jpg --auto --report

Why the corners are needed
--------------------------
Geometric detection of the chart was deliberately kept honest: on a casual photo
(uneven light, glare, a hand and other objects in frame) the candidate grids that
minimise colour/uniformity costs are *not* unique - a shifted lattice can score
better than the true one. Automatic search is therefore opt-in via --auto, its
result is checked, and the pipeline warns when the corrected error is too large to
trust. Four clicks remove the entire problem class in one step.

What it does
------------
 1. resolve the 6x4 patch grid (markers/hint, or best-effort search);
 2. refine the grid by minimising within-patch variance;
 3. identify each patch by matching its colour to the ColorChecker reference set;
 4. measure patch RGB, compare with the reference in CIEDE2000 (D65);
 5. fit a photometric model (gain / affine / 3x3 / quadratic), pick the one with the
    best leave-one-out error, and apply it to the whole image;
 6. write <name>_corrected.png, _grid.png, _results.json, _patches.csv and, with
    --report, a before/after card.

Only numpy + Pillow are required (tkinter only for --markers).

Calibration model
-----------------
The reference is the standard 24-patch ColorChecker sRGB table below (BabelColor
averages of the 2005 measurements, D65). Two caveats are unavoidable in a single
photograph and are reported rather than hidden: gloss flare lifts the black patches,
and the print's own colourants differ from the originals (metamerism), so a few
patches keep a residual of several dE00. `chart_consistency` in results.json is the
honest per-patch number after the best photometric fit.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import deque

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------------------
# reference data
# --------------------------------------------------------------------------------------

PATCH_NAMES = [
    "dark skin", "light skin", "blue sky", "foliage", "blue flower", "bluish green",
    "orange", "purplish blue", "moderate red", "purple", "yellow green", "orange yellow",
    "blue", "green", "red", "yellow", "magenta", "cyan",
    "white", "neutral 8", "neutral 6.5", "neutral 5", "neutral 3.5", "black",
]

# sRGB reference values of the 24 patches (row-major, standard ColorChecker order)
REFERENCE_SRGB = np.array([
    (115, 82, 68), (194, 150, 130), (98, 122, 157), (87, 108, 67), (133, 128, 177), (103, 189, 170),
    (214, 126, 44), (80, 91, 166), (193, 90, 99), (94, 60, 108), (157, 188, 64), (224, 163, 46),
    (56, 61, 150), (70, 148, 73), (175, 54, 60), (231, 199, 31), (187, 86, 149), (8, 133, 161),
    (243, 243, 242), (200, 200, 200), (160, 160, 160), (122, 122, 121), (85, 85, 85), (52, 52, 52),
], dtype=float)

NEUTRAL_IDX = list(range(18, 24))          # the 6-step grey ramp
CHROMATIC_IDX = list(range(0, 18))

# --------------------------------------------------------------------------------------
# colour science:  sRGB <-> linear <-> XYZ <-> Lab,  CIEDE2000
# --------------------------------------------------------------------------------------

_M_RGB2XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                       [0.2126729, 0.7151522, 0.0721750],
                       [0.0193339, 0.1191920, 0.9503041]])
_WHITE_D65 = np.array([0.95047, 1.0, 1.08883])


def srgb_to_lin(c):
    c = np.asarray(c, dtype=float)
    c = c / 255.0 if c.max() > 1.0 + 1e-9 else c
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def lin_to_srgb(c):
    c = np.clip(np.asarray(c, dtype=float), 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055) * 255.0


def rgb_to_lab(rgb):
    lin = srgb_to_lin(rgb)
    xyz = lin @ _M_RGB2XYZ.T
    t = xyz / _WHITE_D65
    d = 6.0 / 29.0
    f = np.where(t > d ** 3, np.cbrt(t), t / (3 * d ** 2) + 4.0 / 29.0)
    return np.stack([116 * f[..., 1] - 16,
                     500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], axis=-1)


def de00(lab1, lab2):
    """CIEDE2000 between two Lab arrays (broadcastable)."""
    L1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    L2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]
    C1, C2 = np.hypot(a1, b1), np.hypot(a2, b2)
    Cb = 0.5 * (C1 + C2)
    G = 0.5 * (1 - np.sqrt(Cb ** 7 / (Cb ** 7 + 25.0 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    dLp, dCp = L2 - L1, C2p - C1p
    dhp = h2p - h1p
    dhp = np.where(dhp > 180, dhp - 360, np.where(dhp < -180, dhp + 360, dhp))
    dHp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dhp) / 2)
    Lbp, Cbp = 0.5 * (L1 + L2), 0.5 * (C1p + C2p)
    hbp = np.where(np.abs(h1p - h2p) <= 180, 0.5 * (h1p + h2p),
                   np.where(h1p + h2p < 360, 0.5 * (h1p + h2p + 360),
                            0.5 * (h1p + h2p - 360)))
    T = (1 - 0.17 * np.cos(np.radians(hbp - 30)) + 0.24 * np.cos(np.radians(2 * hbp))
         + 0.32 * np.cos(np.radians(3 * hbp + 6)) - 0.20 * np.cos(np.radians(4 * hbp - 63)))
    dtheta = 30 * np.exp(-(((hbp - 275) / 25) ** 2))
    Rc = 2 * np.sqrt(Cbp ** 7 / (Cbp ** 7 + 25.0 ** 7))
    Sl = 1 + (0.015 * (Lbp - 50) ** 2) / np.sqrt(20 + (Lbp - 50) ** 2)
    Sc, Sh = 1 + 0.045 * Cbp, 1 + 0.015 * Cbp * T
    Rt = -np.sin(np.radians(2 * dtheta)) * Rc
    return np.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2
                   + Rt * (dCp / Sc) * (dHp / Sh))


# --------------------------------------------------------------------------------------
# projective geometry helpers
# --------------------------------------------------------------------------------------

def homography_from_quad(quad):
    """Projective transform mapping the unit square (u,v) to the given 4 points.

    quad order: p00(u=0,v=0), p05(u=1,v=0), p30(u=0,v=1), p35(u=1,v=1).
    """
    src = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
    dst = np.asarray(quad, dtype=float)
    A = []
    for (u, v), (x, y) in zip(src, dst):
        A.append([u, v, 1, 0, 0, 0, -u * x, -v * x, -x])
        A.append([0, 0, 0, u, v, 1, -u * y, -v * y, -y])
    _, _, vt = np.linalg.svd(np.array(A))
    return vt[-1].reshape(3, 3)


def apply_h(H, pts):
    pts = np.atleast_2d(np.asarray(pts, dtype=float))
    q = np.hstack([pts, np.ones((pts.shape[0], 1))]) @ H.T
    return q[:, :2] / q[:, 2:3]


def sample_image(arr, pts):
    """Bilinear sample of an (H,W,3) float array at float pixel coordinates."""
    h, w = arr.shape[:2]
    x = np.clip(pts[:, 0], 0, w - 1.001)
    y = np.clip(pts[:, 1], 0, h - 1.001)
    x0, y0 = x.astype(int), y.astype(int)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    tl, tr = arr[y0, x0], arr[y0, x0 + 1]
    bl, br = arr[y0 + 1, x0], arr[y0 + 1, x0 + 1]
    return (tl * (1 - fx) + tr * fx) * (1 - fy) + (bl * (1 - fx) + br * fx) * fy


def quad_from_points(corr):
    """Least-squares bilinear fit of the 4 grid corners from (row, col) -> (x, y) pairs."""
    rows, cols, xs, ys = [], [], [], []
    for (r, c), (x, y) in corr.items():
        u, v = (c + 0.5) / 6.0, (r + 0.5) / 4.0
        rows.append([(1 - u) * (1 - v), u * (1 - v), (1 - u) * v, u * v])
        xs.append(x)
        ys.append(y)
    A = np.array(rows)
    qx, *_ = np.linalg.lstsq(A, np.array(xs), rcond=None)
    qy, *_ = np.linalg.lstsq(A, np.array(ys), rcond=None)
    return np.stack([qx, qy], axis=1)


# --------------------------------------------------------------------------------------
# step 1+2a : candidate patch blobs (flat colour regions)
# --------------------------------------------------------------------------------------

def _components(mask, min_area=60, max_area=None):
    """Connected components (4-neighbour) of a boolean mask, with per-component stats."""
    H, W = mask.shape
    if max_area is None:
        max_area = max(20000, int(0.03 * H * W))
    lab = np.zeros((H, W), dtype=np.int32)
    blobs = []
    comp = 0
    ys_all, xs_all = np.nonzero(mask)
    for sy, sx in zip(ys_all, xs_all):
        if lab[sy, sx]:
            continue
        comp += 1
        q = deque([(sy, sx)])
        lab[sy, sx] = comp
        pix = []
        while q:
            y, x = q.popleft()
            pix.append((y, x))
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                yy, xx = y + dy, x + dx
                if 0 <= yy < H and 0 <= xx < W and mask[yy, xx] and lab[yy, xx] == 0:
                    lab[yy, xx] = comp
                    q.append((yy, xx))
        n = len(pix)
        if not (min_area <= n <= max_area):
            continue
        p = np.array(pix)
        ys, xs = p[:, 0], p[:, 1]
        blobs.append(dict(
            area=n, cx=float(xs.mean()), cy=float(ys.mean()),
            x0=int(xs.min()), x1=int(xs.max()), y0=int(ys.min()), y1=int(ys.max()),
            w=int(xs.max() - xs.min() + 1), h=int(ys.max() - ys.min() + 1),
        ))
    return blobs, lab


def find_blobs(arr, grid_hint=None, min_area=60, max_area=None, precomputed_mask=None):
    """Split the image into flat-colour connected components and return their statistics.

    Returns a list of dicts with centroid, area, mean colour and pixel count.
    """
    H, W = arr.shape[:2]
    if max_area is None:
        max_area = max(20000, int(0.03 * H * W))
    if precomputed_mask is not None:
        blobs, _ = _components(precomputed_mask, min_area, max_area)
        for b in blobs:
            b["color"] = arr[b["y0"]:b["y1"] + 1, b["x0"]:b["x1"] + 1].reshape(-1, 3).mean(axis=0)
        return blobs, None

    lum = arr.mean(axis=2)
    chroma = arr.max(axis=2) - arr.min(axis=2)
    gy, gx = np.gradient(lum)
    grad = np.hypot(gx, gy)

    flat = grad < 3.0
    # background: very bright AND nearly neutral
    bg = (lum > 175) & (chroma < 18)
    cand = flat & ~bg
    if grid_hint is not None:
        keep = np.zeros((H, W), dtype=bool)
        x0, y0, x1, y1 = grid_hint
        keep[max(0, y0):min(H, y1), max(0, x0):min(W, x1)] = True
        cand &= keep

    blobs, lab = _components(cand, min_area, max_area)
    for b in blobs:
        ys = np.arange(b["y0"], b["y1"] + 1)
        xs = np.arange(b["x0"], b["x1"] + 1)
        sub = arr[b["y0"]:b["y1"] + 1, b["x0"]:b["x1"] + 1].reshape(-1, 3)
        b["color"] = sub.mean(axis=0)
        _ = (ys, xs)
    return blobs, lab


def largest_background_blob(arr):
    """Area fraction of the biggest near-neutral bright component (large => plain backdrop)."""
    H, W = arr.shape[:2]
    lum = arr.mean(axis=2)
    chroma = arr.max(axis=2) - arr.min(axis=2)
    cand = (lum > 170) & (chroma < 20)
    seen = np.zeros((H, W), dtype=bool)
    best = 0
    ys_all, xs_all = np.nonzero(cand)
    for sy, sx in zip(ys_all, xs_all):
        if seen[sy, sx]:
            continue
        q = deque([(sy, sx)])
        seen[sy, sx] = True
        n = 0
        while q:
            y, x = q.popleft()
            n += 1
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                yy, xx = y + dy, x + dx
                if 0 <= yy < H and 0 <= xx < W and cand[yy, xx] and not seen[yy, xx]:
                    seen[yy, xx] = True
                    q.append((yy, xx))
        best = max(best, n)
    return best / float(H * W)


# --------------------------------------------------------------------------------------
# step 2b : fit the 6x4 grid
# --------------------------------------------------------------------------------------

def sample_cells(arr, quad, offsets):
    """Sample the 24 cells of the grid; returns (4,6,N,3) float array."""
    H = homography_from_quad(quad)
    ou, ov = offsets
    cells = np.zeros((4, 6, ou.size, 3))
    for r in range(4):
        for c in range(6):
            u = (c + 0.5 + ou) / 6.0
            v = (r + 0.5 + ov) / 4.0
            cells[r, c] = sample_image(arr, apply_h(H, np.stack([u, v], axis=1)))
    return cells


def grid_cost(arr, quad, offsets):
    cells = sample_cells(arr, quad, offsets)
    return float(sum(cells[r, c].std(axis=0).mean() for r in range(4) for c in range(6)))


def refine_quad(arr, quad, offsets, step=6.0, min_step=0.05, max_shift=14.0, grad=None, dist=None):
    """Coordinate descent on the 8 corner coordinates.

    The objective combines "all 24 nodes land on patch blobs" with the grid-alignment
    score when `grad` is given, and falls back to plain within-patch uniformity
    otherwise.
    """
    def objective(q):
        if grad is not None:
            return grid_cost_full(arr, q, grad, dist, offsets)[0]
        return grid_cost(arr, q, offsets)

    base = np.asarray(quad, dtype=float).copy()
    best = base.copy()
    best_c = objective(best)
    while step > min_step:
        improved = False
        for i in range(4):
            for j in range(2):
                for d in (step, -step):
                    if abs(best[i, j] + d - base[i, j]) > max_shift:
                        continue
                    trial = best.copy()
                    trial[i, j] += d
                    c = objective(trial)
                    if c < best_c - 1e-9:
                        best, best_c, improved = trial, c, True
        if not improved:
            step *= 0.5
    return best, best_c


def chart_is_upside_down(meas):
    """True when the grid appears rotated 180 degrees (white patch in the top-right).

    Nearest-colour matching cannot see a 180 degree rotation - the patch *set* is the
    same - so orientation is decided from the grey ramp instead: the white patch is the
    brightest cell in the chart, and it belongs at index 18 (bottom-left).
    """
    if meas.shape[0] != 24:
        return False
    lum = meas.mean(axis=1)
    if float(lum.max()) < 140.0:      # chart in deep shadow - do not guess
        return False
    return int(np.argmax(lum)) == 5   # reference index 5 sits in the top-right corner


def canonical_measurements(cells):
    """Measured patch colours in standard ColorChecker row-major layout.

    The layout of a 24-patch ColorChecker is fixed, so the cell at (row, col) *is*
    reference patch 6*row+col. Identification by nearest colour is deliberately not
    used: it permutes samples when the photo's exposure or the print's materials
    shift colours, which silently corrupts the correction.
    """
    meas = cells.reshape(24, -1, 3).mean(axis=1)
    if chart_is_upside_down(meas):
        flipped = cells[::-1, ::-1]
        return flipped.reshape(24, -1, 3).mean(axis=1), True
    return meas, False


def _cross2(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _lattice_hypotheses(cents, min_pitch=8.0):
    """Enumerate plausible 2D lattices spanned by the detected blob centroids."""
    n = len(cents)
    hyps = []
    for i in range(n):
        for j in range(n):
            if j == i:
                continue
            v = cents[j] - cents[i]          # candidate column step
            pv = np.linalg.norm(v)
            if pv < min_pitch:
                continue
            for k in range(n):
                if k in (i, j):
                    continue
                u = cents[k] - cents[i]      # candidate row step (possibly a multiple)
                pu = np.linalg.norm(u)
                if pu < min_pitch:
                    continue
                if abs(_cross2(v, u)) < 0.25 * pv * pu:
                    continue                 # collinear, cannot define the lattice
                if abs(np.dot(v, u)) > 0.35 * pv * pu:
                    continue                 # rows and columns are not that skewed
                if pu < pv:
                    v, u = u, v
                    pv, pu = pu, pv
                ratio = pu / pv
                if not (1.02 < ratio < 6.5):
                    continue
                nb = int(round(ratio))
                row = u / nb
                # cells of a ColorChecker are 30 x 30 px: both pitches stay within 2x
                pr = np.linalg.norm(row)
                if pr < min_pitch or not (0.5 < pv / pr < 2.0):
                    continue
                for r0 in range(4):
                    for c0 in range(6):
                        origin = cents[i] - c0 * v - r0 * row
                        hyps.append(np.array([origin, origin + 5 * v,
                                              origin + 3 * row,
                                              origin + 5 * v + 3 * row]))
    return hyps


_OFFSETS = None


def _default_offsets(n=7, span=0.28):
    global _OFFSETS
    if _OFFSETS is None or _OFFSETS[0].size != n * n:
        g = np.linspace(-span, span, n)
        ou, ov = np.meshgrid(g, g)
        _OFFSETS = (ou.ravel(), ov.ravel())
    return _OFFSETS


def match_cost(meas, uniqueness_penalty=0.25):
    """One-to-one colour-match cost between the 24 measured patches and the reference set.

    Both sets are exposure-normalised, so a global brightness difference does not
    matter. The assignment is forced to be a permutation: every reference colour may
    be used only once, and unassigned patches cost `uniqueness_penalty`. A grid that
    lands on a featureless region therefore cannot win by matching everything to the
    same reference colour, while a correctly placed grid approaches zero.
    """
    m = np.asarray(meas, float)
    scale = max(float(m.max()), 1e-6)
    mn = m / scale
    rn = REFERENCE_SRGB / REFERENCE_SRGB.max()
    d = np.linalg.norm(mn[:, None, :] - rn[None, :, :], axis=2)   # 24 x 24
    total = 0.0
    used = set()
    flat = np.dstack(np.unravel_index(np.argsort(d, axis=None), d.shape))[0]
    for i, j in flat:
        if i in used or j in used:
            continue
        used.add(i)
        used.add(j)
        total += d[i, j]
        if len(used) == 48:
            break
    total += uniqueness_penalty * (24 - len(used) // 2)
    # punish degenerate grids that see almost no colour variation
    spread = float(mn.std(axis=0).sum())
    if spread < 0.25:
        total += (0.25 - spread) * 8.0
    return float(total)


def _probe_geometry():
    """Sampling patterns used to score a candidate grid."""
    t = np.linspace(0.06, 0.94, 5)
    # internal separators of the 4x6 patch grid
    lines = []
    for k in range(1, 6):          # vertical separators
        lines.append(np.stack([np.full_like(t, k / 6.0), t], axis=1))
    for k in range(1, 4):          # horizontal separators
        lines.append(np.stack([t, np.full_like(t, k / 4.0)], axis=1))
    lines = np.vstack(lines)
    # patch centres
    g = np.linspace(-0.10, 0.10, 3)
    ou, ov = np.meshgrid(g, g)
    centres = []
    for r in range(4):
        for c in range(6):
            centres.append(np.stack([(c + 0.5) / 6.0 + ou.ravel() / 6.0,
                                     (r + 0.5) / 4.0 + ov.ravel() / 4.0], axis=1))
    return lines, np.vstack(centres)


_LINES, _CENTRES = _probe_geometry()


def grid_alignment_score(grad, quad):
    """High where the cell borders sit on patch separators, low inside the patches."""
    Hm = homography_from_quad(quad)
    line_g = sample_image(grad, apply_h(Hm, _LINES)).ravel()
    cent_g = sample_image(grad, apply_h(Hm, _CENTRES)).ravel()
    return float(cent_g.mean() - line_g.mean())


def _distance_to_mask(mask):
    """Approximate Euclidean distance (8-connected chamfer) from every pixel to `mask`."""
    H, W = mask.shape
    INF = 1e9
    d = np.where(mask, 0.0, INF)
    # two-pass chamfer: forward then backward
    a, b = 1.0, 1.41421356
    for y in range(H):
        row = d[y]
        if y > 0:
            prev = d[y - 1]
            np.minimum(row, prev + a, out=row)
            np.minimum(row[:-1], prev[1:] + b, out=row[:-1])
            np.minimum(row[1:], prev[:-1] + b, out=row[1:])
        for x in range(1, W):
            if row[x - 1] + a < row[x]:
                row[x] = row[x - 1] + a
    for y in range(H - 1, -1, -1):
        row = d[y]
        if y < H - 1:
            nxt = d[y + 1]
            np.minimum(row, nxt + a, out=row)
            np.minimum(row[:-1], nxt[1:] + b, out=row[:-1])
            np.minimum(row[1:], nxt[:-1] + b, out=row[1:])
        for x in range(W - 2, -1, -1):
            if row[x + 1] + a < row[x]:
                row[x] = row[x + 1] + a
    return d


def build_patch_distance(arr):
    """Distance field to the nearest "looks like a chart patch" pixel.

    Patch pixels are locally flat and either colourful or clearly darker than the
    paper/backdrop, which excludes the plain background (bright and neutral) and the
    dark separators (strong gradient). A correctly placed grid puts all 24 nodes in
    such regions, so the mean distance at the nodes is near zero for the right grid
    and large for a shifted one.
    """
    lum = arr.mean(axis=2)
    chroma = arr.max(axis=2) - arr.min(axis=2)
    gy, gx = np.gradient(lum)
    grad = np.hypot(gx, gy)
    patchish = (grad < 2.5) & ((chroma > 22) | (lum < 205))
    if patchish.sum() < 200:
        return None
    return _distance_to_mask(patchish)


def node_distance_cost(dist, quad):
    """Mean distance from the 24 patch-node centres to the nearest patch blob."""
    Hm = homography_from_quad(quad)
    nodes = np.array([[(c + 0.5) / 6.0, (r + 0.5) / 4.0]
                      for r in range(4) for c in range(6)])
    pts = apply_h(Hm, nodes)
    vals = sample_image(dist, pts)
    return float(np.asarray(vals).mean())


def grid_cost_full(arr, quad, grad, dist, offsets, w_dist=0.05, w_align=0.02):
    """Combined objective: nodes on patches + borders on separators + flat interiors."""
    cells = sample_cells(arr, quad, offsets)
    uni = float(sum(cells[r, c].std(axis=0).mean() for r in range(4) for c in range(6)))
    align = grid_alignment_score(grad, quad)
    nd = node_distance_cost(dist, quad) if dist is not None else 0.0
    return w_dist * nd + w_align * align + 0.0007 * uni, align, uni, nd


def _cluster_centres(points, k=4, iters=40):
    """Tiny k-means; returns k cluster centres (deterministic seeding)."""
    pts = np.asarray(points, float)
    order = np.argsort(pts[:, 0] + pts[:, 1])
    seeds = [pts[order[int(round(t))]] for t in np.linspace(0, len(pts) - 1, k)]
    cent = np.array(seeds, float)
    for _ in range(iters):
        d = np.linalg.norm(pts[:, None, :] - cent[None, :, :], axis=2)
        lab = d.argmin(axis=1)
        new = np.array([pts[lab == i].mean(axis=0) if np.any(lab == i) else cent[i]
                        for i in range(k)])
        if np.allclose(new, cent):
            break
        cent = new
    cent = np.asarray(cent, dtype=float)
    return cent


def find_marker_quad(arr, verbose=False):
    """Locate the 4 markers drawn by --markers and return the grid quad, or None.

    The marker is a saturated green cross whose centre sits exactly on a grid corner.
    Green objects in the scene (e.g. a green swatch card) are rejected by size and
    shape, so the scene does not have to be free of green.
    """
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    mask = (g > 140) & (r < 120) & (b < 120) & (g - r > 60) & (g - b > 60)
    if int(mask.sum()) < 20:
        return None
    comps, _ = find_blobs(arr, precomputed_mask=mask, min_area=25, max_area=4000)
    # marker crosses are compact, nearly square and all the same size; a green swatch or
    # its anti-aliased edge is either elongated or a different size
    cands = [c for c in comps
             if c["w"] > 0 and c["h"] > 0 and 0.45 < c["w"] / c["h"] < 2.2]
    if len(cands) >= 4:
        # the four markers are drawn identically, so their detected areas agree closely;
        # pick the largest set of 4 whose areas are mutually consistent
        best = None
        for c in cands:
            ref = c["area"]
            group = [d for d in cands if abs(d["area"] - ref) <= max(3.0, 0.06 * ref)]
            if best is None or len(group) > len(best):
                best = group
        if best is not None and len(best) == 4:
            cands = best
    if verbose:
        print(f"    green blobs: {len(comps)}, marker-like: {len(cands)}")
    if len(cands) != 4:
        return None
    quad = np.array([[(c["x0"] + c["x1"]) / 2.0, (c["y0"] + c["y1"]) / 2.0] for c in cands])
    # order into TL, TR, BL, BR: two corners share the smaller y (the top edge)
    order = np.argsort(quad[:, 0])
    quad = quad[order]
    left = quad[:2]
    right = quad[2:]
    tl, bl = left[np.argsort(left[:, 1])]
    tr, br = right[np.argsort(right[:, 1])]
    quad = np.array([tl, tr, bl, br])
    if verbose:
        print(f"    marker quad: {np.round(quad, 1).tolist()}")
    return quad


def _lattice_fallback(arr, verbose=False):
    """Best-effort automatic search used by --auto (works on clean studio shots)."""
    offsets = _default_offsets()
    H, W = arr.shape[:2]
    lum = arr.mean(axis=2)
    gy, gx = np.gradient(lum)
    grad = np.hypot(gx, gy)[..., None].astype(np.float32)
    dist = build_patch_distance(arr)

    blobs, _ = find_blobs(arr)
    if verbose:
        print(f"    candidate flat blobs: {len(blobs)}")
    if len(blobs) < 8:
        raise RuntimeError("not enough flat colour regions found")
    cents = np.array([[(b["x0"] + b["x1"]) / 2.0, (b["y0"] + b["y1"]) / 2.0] for b in blobs])
    areas = np.array([b["area"] for b in blobs], dtype=float)
    med = float(np.median(areas))
    keep = (areas >= max(60.0, 0.30 * med)) & (areas <= 4.0 * med)
    cents = cents[keep]
    if len(cents) < 8:
        raise RuntimeError("too few usable flat regions")
    hyps = _lattice_hypotheses(cents, min_pitch=0.03 * max(H, W))
    bounds = [q for q in hyps
              if q[:, 0].min() > -20 and q[:, 0].max() < W + 20
              and q[:, 1].min() > -20 and q[:, 1].max() < H + 20]
    if not bounds:
        raise RuntimeError("no lattice candidate fits inside the image")
    if dist is not None:
        scores = np.array([node_distance_cost(dist, q) for q in bounds])
        order = np.argsort(scores)[:12]
    else:
        scores = np.array([grid_alignment_score(grad, q) for q in bounds])
        order = np.argsort(-scores)[:12]
    best = None
    for idx in order:
        q2, _ = refine_quad(arr, bounds[idx], offsets, step=4.0, max_shift=8.0, grad=grad, dist=dist)
        cost = grid_cost_full(arr, q2, grad, dist, offsets)[0]
        if best is None or cost < best[0]:
            best = (cost, q2)
    cost, quad = best
    quad, _ = refine_quad(arr, quad, offsets, step=2.0, max_shift=5.0, grad=grad, dist=dist)
    if verbose:
        print(f"    fallback grid cost {grid_cost_full(arr, quad, grad, dist, offsets)[0]:.3f}")
    return quad


def detect_chart(arr, hint=None, verbose=False, allow_auto=False):
    """Resolve the 6x4 patch grid: explicit corners, marker pixels, or --auto search."""
    offsets = _default_offsets()
    if hint is not None:
        quad = np.asarray(hint, dtype=float)
        quad, cost = refine_quad(arr, quad, offsets)
        return dict(quad=quad, cells=sample_cells(arr, quad, offsets),
                    cost=cost, method="manual grid corners")

    quad = find_marker_quad(arr, verbose=verbose)
    if quad is not None:
        quad, cost = refine_quad(arr, quad, offsets)
        return dict(quad=quad, cells=sample_cells(arr, quad, offsets),
                    cost=cost, method="green markers")

    if not allow_auto:
        raise RuntimeError(
            "could not locate the chart: run with --markers to place 4 corner markers, "
            "or pass --hint 'x,y x,y x,y x,y', or try --auto")
    quad = _lattice_fallback(arr, verbose=verbose)
    quad, cost = refine_quad(arr, quad, offsets)
    return dict(quad=quad, cells=sample_cells(arr, quad, offsets),
                cost=cost, method="automatic lattice search (best effort)")


# --------------------------------------------------------------------------------------
# step 3+4 : photometric models and selection
# --------------------------------------------------------------------------------------

def _fit_linear(design, target):
    coef, *_ = np.linalg.lstsq(design, target, rcond=None)
    return coef


def build_models(meas_srgb, ref_srgb=None, indices=None):
    """Fit several photometric transfer models: measured sRGB -> reference sRGB.

    `indices` restricts which patches are used for fitting (used by leave-one-out).
    Every model is returned as dict(name, predict(rgb) -> rgb, params).
    """
    if ref_srgb is None:
        ref_srgb = REFERENCE_SRGB
    if indices is not None:
        meas_srgb = meas_srgb[indices]
        ref_srgb = ref_srgb[indices]
    lin_m = srgb_to_lin(meas_srgb)
    lin_r = srgb_to_lin(ref_srgb)
    n = meas_srgb.shape[0]
    models = []

    # 1. identity
    models.append(dict(name="none", predict=lambda x: np.asarray(x, float), params={}))

    # 2. per-channel gain in linear light
    g = (lin_m * lin_r).sum(axis=0) / np.maximum((lin_m ** 2).sum(axis=0), 1e-12)
    models.append(dict(name="linear gain",
                       predict=lambda x, g=g: lin_to_srgb(srgb_to_lin(x) * g),
                       params=dict(gain=g.tolist())))

    # 3. per-channel affine in linear light
    A1 = np.hstack([lin_m, np.ones((n, 1))])
    c1 = _fit_linear(A1, lin_r)
    diag = np.diag(c1[:3, :])
    bias = c1[3, :]
    models.append(dict(name="linear affine",
                       predict=lambda x, diag=diag, bias=bias: lin_to_srgb(srgb_to_lin(x) * diag + bias),
                       params=dict(slope=diag.tolist(), bias=bias.tolist())))

    # 4. full 3x3 matrix + bias in linear light
    K = c1[:3, :].T
    t = c1[3, :]
    models.append(dict(name="3x3 matrix",
                       predict=lambda x, K=K, t=t: lin_to_srgb(srgb_to_lin(x) @ K.T + t),
                       params=dict(matrix=K.tolist(), bias=t.tolist())))

    # 5. quadratic per channel in linear light
    Aq = np.hstack([lin_m, lin_m ** 2, np.ones((n, 1))])
    cq = _fit_linear(Aq, lin_r)
    models.append(dict(name="quadratic",
                       predict=lambda x, cq=cq: lin_to_srgb(
                           np.hstack([srgb_to_lin(x), srgb_to_lin(x) ** 2,
                                      np.ones((np.asarray(x).shape[0], 1))]) @ cq),
                       params=dict(coef=cq.tolist())))
    return models


def model_error(model, meas_srgb, ref_srgb=None):
    if ref_srgb is None:
        ref_srgb = REFERENCE_SRGB
    pred = np.clip(model["predict"](meas_srgb), 0, 255)
    return de00(rgb_to_lab(pred), rgb_to_lab(ref_srgb))


def leave_one_out(model_name, meas_srgb):
    """Mean cross-validation error; guards against overfitting the model choice."""
    if model_name == "none":
        return None
    n = meas_srgb.shape[0]
    errs = []
    for hold in range(n):
        train = [i for i in range(n) if i != hold]
        m = [mm for mm in build_models(meas_srgb, indices=train) if mm["name"] == model_name][0]
        errs.append(float(model_error(m, meas_srgb[hold:hold + 1],
                                      REFERENCE_SRGB[hold:hold + 1])[0]))
    return float(np.mean(errs))


def _gamut_probe():
    g = np.linspace(0, 255, 9)
    r, gg, b = np.meshgrid(g, g, g, indexing="ij")
    return np.stack([r.ravel(), gg.ravel(), b.ravel()], axis=1)


_GAMUT_PROBE = _gamut_probe()


def extrapolation_error(model):
    """How badly the model distorts colours outside the chart (mean dE00 over sRGB).

    A model fitted only on 24 patches can look excellent on them while destroying
    saturated colours elsewhere in the frame; this catches that.
    """
    pred = np.asarray(model["predict"](_GAMUT_PROBE), dtype=float)
    if not np.all(np.isfinite(pred)):
        return float("inf")
    clipped = np.clip(pred, 0, 255)
    de = de00(rgb_to_lab(clipped), rgb_to_lab(_GAMUT_PROBE))
    return float(np.mean(de))


# --------------------------------------------------------------------------------------
# main pipeline
# --------------------------------------------------------------------------------------

def load_fonts():
    try:
        return (ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 15),
                ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 12),
                ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 17))
    except Exception:
        f = ImageFont.load_default()
        return f, f, f


def draw_before_after(im, out_img, meas, corrected, de_raw, de_corr, path):
    font, fsm, fbold = load_fonts()
    cell, label_w, num_w = 52, 150, 210
    gap = 24
    tab_w = cell * 3 + label_w + num_w
    fig_w = im.width * 2 + gap + tab_w + 40
    fig_h = max(im.height + 40, 24 * 30 + 110)
    fig = Image.new("RGB", (fig_w, fig_h), (252, 252, 252))
    fig.paste(im, (10, 10))
    fig.paste(out_img, (im.width + gap, 10))
    d = ImageDraw.Draw(fig)
    d.text((10, im.height + 16), "original (as shot)", fill=(20, 20, 20), font=fbold)
    d.text((im.width + gap + 6, im.height + 16), "after photometric correction",
           fill=(20, 20, 20), font=fbold)

    tx, ty = im.width * 2 + gap + 20, 14
    d.text((tx, ty), "24-patch check: measured / corrected / reference", fill=(0, 0, 0), font=fbold)
    ty += 26
    heads = ["measured", "corrected", "reference"]
    for i, h in enumerate(heads):
        d.rectangle([tx + label_w + i * cell, ty, tx + label_w + (i + 1) * cell, ty + 18],
                    fill=(230, 230, 230), outline=(150, 150, 150))
        d.text((tx + label_w + i * cell + 4, ty + 2), h, fill=(60, 60, 60), font=fsm)
    d.text((tx + label_w + 3 * cell + 12, ty + 2), "raw dE00", fill=(60, 60, 60), font=fsm)
    d.text((tx + label_w + 3 * cell + 92, ty + 2), "corr dE00", fill=(60, 60, 60), font=fsm)
    ty += 22
    for i, name in enumerate(PATCH_NAMES):
        y = ty + i * 30
        d.text((tx, y + 8), name, fill=(10, 10, 10), font=font)
        for k, col in enumerate((meas[i], corrected[i], REFERENCE_SRGB[i])):
            d.rectangle([tx + label_w + k * cell, y + 2, tx + label_w + (k + 1) * cell, y + 26],
                        fill=tuple(int(v) for v in np.clip(col, 0, 255)), outline=(180, 180, 180))
        d.text((tx + label_w + 3 * cell + 14, y + 4), f"{de_raw[i]:5.2f}", fill=(160, 30, 30), font=font)
        d.text((tx + label_w + 3 * cell + 94, y + 4), f"{de_corr[i]:5.2f}", fill=(20, 110, 40), font=font)
    ty += 24 * 30 + 8
    d.text((tx, ty), f"mean dE00   raw {de_raw.mean():.2f}   ->   corrected {de_corr.mean():.2f}"
                     f"   (max {de_corr.max():.2f})", fill=(0, 0, 0), font=font)
    d.text((tx, ty + 22), f"within dE00 <= 2.0: {int((de_corr <= 2).sum())}/24  |  "
                          f"chromatic 18: {de_corr[:18].mean():.2f}  neutral 6: {de_corr[18:].mean():.2f}",
           fill=(70, 70, 70), font=fsm)
    fig.save(path)


def draw_grid_overlay(im, quad, meas, de_corr, path, scale=3, box=None):
    font, fsm, _ = load_fonts()
    H = homography_from_quad(quad)
    out = im.copy()
    d = ImageDraw.Draw(out)
    offsets = np.meshgrid(np.linspace(-0.28, 0.28, 5), np.linspace(-0.28, 0.28, 5))
    ou, ov = offsets[0].ravel(), offsets[1].ravel()
    centers = []
    for r in range(4):
        for c in range(6):
            pts = apply_h(H, np.stack([(c + 0.5 + ou) / 6.0, (r + 0.5 + ov) / 4.0], axis=1))
            for x, y in pts:
                d.point((x, y), fill=(0, 255, 255))
            centers.append(apply_h(H, np.array([[(c + 0.5) / 6.0, (r + 0.5) / 4.0]]))[0])
    for i, (x, y) in enumerate(centers):
        d.text((x - 12, y - 26), f"{de_corr[i]:.1f}", fill=(255, 255, 255),
               font=fsm, stroke_width=2, stroke_fill=(0, 0, 0))
    if box is None:
        xs = [p[0] for p in centers]
        ys = [p[1] for p in centers]
        box = (max(0, int(min(xs)) - 60), max(0, int(min(ys)) - 60),
               min(im.width, int(max(xs)) + 60), min(im.height, int(max(ys)) + 60))
    crop = out.crop(box)
    crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS).save(path)


def hint_path_for(image_path, hint_path=None):
    if hint_path:
        return hint_path
    base = os.path.splitext(image_path)[0]
    return base + ".hint.json"


def load_hint(image_path, hint_path=None):
    """Saved grid corners for this image, if any (written by --markers)."""
    p = hint_path_for(image_path, hint_path)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            h, w = data["image_size"]
            quad = np.array(data["grid_quad"], dtype=float)
            if data.get("normalised"):
                quad = quad * np.array([w, h], dtype=float)
            return quad, p
        except Exception:
            return None, p
    return None, p


def save_hint(image_path, quad, image_size, hint_path=None):
    p = hint_path_for(image_path, hint_path)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(dict(image_size=list(image_size), normalised=False,
                       grid_quad=[[float(v) for v in pt] for pt in quad],
                       note="grid corners = centres of the 4 corner patches, order TL,TR,BL,BR"),
                  f, indent=2)
    return p


def run(input_path, out_dir="out", hint=None, rotate_180=False, want_report=False,
        allow_auto=False, hint_file=None, model_name=None):
    os.makedirs(out_dir, exist_ok=True)
    im = Image.open(input_path).convert("RGB")
    if rotate_180:
        im = im.rotate(180)
    arr = np.asarray(im, dtype=np.float32)
    print(f"[1/6] loaded {input_path}  {im.width}x{im.height}")

    quad_hint = None
    if hint is not None:
        quad_hint = np.array(hint, dtype=float).reshape(4, 2)
        if np.abs(quad_hint).max() <= 1.0 + 1e-9:      # given as normalised fractions
            quad_hint = quad_hint * np.array([im.width, im.height], dtype=float)
    else:
        quad_hint, hp = load_hint(input_path, hint_file)
        if quad_hint is not None:
            print(f"      using saved grid corners from {os.path.basename(hp)}")

    print("[2/6] locating the colour chart ...")
    det = detect_chart(arr, hint=quad_hint, verbose=True, allow_auto=allow_auto)
    print(f"      method: {det['method']}")
    print(f"      grid corners: {np.round(det['quad'], 1).tolist()}")

    meas_ordered, flipped = canonical_measurements(det["cells"])
    if flipped:
        print("      note: chart read as rotated 180 degrees - grid flipped to the standard layout")
    print("[3/6] measured 24 patches")

    lab_m = rgb_to_lab(meas_ordered)
    lab_r = rgb_to_lab(REFERENCE_SRGB)
    de_raw = de00(lab_m, lab_r)

    print("[4/6] fitting photometric models ...")
    models = build_models(meas_ordered)
    scored = []
    for m in models:
        de = model_error(m, meas_ordered)
        cv = leave_one_out(m["name"], meas_ordered)
        ext = extrapolation_error(m)
        scored.append((m, de, cv, ext))
        print(f"      {m['name']:<14} mean dE00 = {de.mean():5.2f}  max = {de.max():5.2f}"
              + (f"  LOO-CV = {cv:5.2f}" if cv is not None else "")
              + f"  out-of-chart = {ext:5.2f}")
    # choose by leave-one-out when available, else by in-sample error, but never accept
    # a model that mangles saturated colours elsewhere in the frame
    usable = [s for s in scored if s[3] < 12.0] or scored
    if model_name:
        forced = [s for s in scored if s[0]["name"] == model_name]
        if not forced:
            raise RuntimeError(f"unknown model '{model_name}'; available: "
                               + ", ".join(s[0]["name"] for s in scored))
        best_model, best_de, _, best_ext = forced[0]
    else:
        best_model, best_de, _, best_ext = min(
            usable, key=lambda s: (s[2] if s[2] is not None else s[1].mean()))
    de_corr = best_de
    print(f"      selected: {best_model['name']} (out-of-chart {best_ext:.2f})")

    # a photo whose patches cannot be brought anywhere near the reference means the grid
    # is probably misplaced - say so instead of shipping a silently wrong correction
    reliable = True
    if de_corr.mean() > 5.0:
        reliable = False
        print(f"      WARNING: mean dE00 after correction is {de_corr.mean():.2f}; the grid is "
              f"probably not aligned with the chart. Re-check the corners (--markers or --hint).")

    corrected = np.clip(best_model["predict"](meas_ordered), 0, 255)

    print("[5/6] applying the correction to the whole image ...")
    h, w = arr.shape[:2]
    flat = arr.reshape(-1, 3)
    out = np.empty_like(flat)
    step = 400000
    for i in range(0, flat.shape[0], step):
        out[i:i + step] = np.clip(best_model["predict"](flat[i:i + step]), 0, 255)
    out_img = Image.fromarray(out.reshape(h, w, 3).astype(np.uint8))
    base = os.path.splitext(os.path.basename(input_path))[0]
    corr_png = os.path.join(out_dir, f"{base}_corrected.png")
    out_img.save(corr_png)
    try:
        out_img.save(os.path.join(out_dir, f"{base}_corrected.webp"), quality=95)
    except Exception:
        pass

    overlay_png = os.path.join(out_dir, f"{base}_grid.png")
    draw_grid_overlay(im, det["quad"], meas_ordered, de_corr, overlay_png)
    report_png = None
    if want_report:
        report_png = os.path.join(out_dir, f"{base}_report.png")
        draw_before_after(im, out_img, meas_ordered, corrected, de_raw, de_corr, report_png)

    print("[6/6] writing results ...")
    consistency = de_corr
    results = dict(
        input=os.path.abspath(input_path),
        image_size=[im.width, im.height],
        method=det["method"],
        grid_quad=[[round(float(v), 2) for v in p] for p in det["quad"]],
        model=best_model["name"],
        model_params=best_model["params"],
        model_selection=dict(selected=best_model["name"],
                             out_of_chart_dE00=round(float(best_ext), 3),
                             candidates=[dict(name=s[0]["name"],
                                              in_sample_mean=round(float(s[1].mean()), 3),
                                              loo_cv=None if s[2] is None else round(float(s[2]), 3),
                                              out_of_chart=round(float(s[3]), 3))
                                         for s in scored]),
        raw=dict(mean=round(float(de_raw.mean()), 3),
                 median=round(float(np.median(de_raw)), 3),
                 max=round(float(de_raw.max()), 3)),
        corrected=dict(mean=round(float(de_corr.mean()), 3),
                       median=round(float(np.median(de_corr)), 3),
                       max=round(float(de_corr.max()), 3),
                       within_2=int((de_corr <= 2).sum())),
        chart_consistency=dict(chromatic_mean=round(float(de_corr[:18].mean()), 3),
                               neutral_mean=round(float(de_corr[18:].mean()), 3)),
        patches=[dict(index=i, name=PATCH_NAMES[i],
                      measured=[round(float(v), 1) for v in meas_ordered[i]],
                      corrected=[round(float(v), 1) for v in corrected[i]],
                      reference=[int(v) for v in REFERENCE_SRGB[i]],
                      dE00_raw=round(float(de_raw[i]), 2),
                      dE00_corrected=round(float(de_corr[i]), 2)) for i in range(24)],
        outputs=dict(corrected=os.path.abspath(corr_png),
                     grid_overlay=os.path.abspath(overlay_png),
                     report=os.path.abspath(report_png) if report_png else None),
        reliable=reliable,
    )
    json_path = os.path.join(out_dir, f"{base}_results.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    csv_path = os.path.join(out_dir, f"{base}_patches.csv")
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("index,name,meas_R,meas_G,meas_B,corr_R,corr_G,corr_B,ref_R,ref_G,ref_B,dE00_raw,dE00_corrected\n")
        for i in range(24):
            m, c, r = meas_ordered[i], corrected[i], REFERENCE_SRGB[i]
            f.write(f"{i},{PATCH_NAMES[i]},{m[0]:.0f},{m[1]:.0f},{m[2]:.0f},"
                    f"{c[0]:.0f},{c[1]:.0f},{c[2]:.0f},{r[0]:.0f},{r[1]:.0f},{r[2]:.0f},"
                    f"{de_raw[i]:.2f},{de_corr[i]:.2f}\n")

    print(f"      corrected image : {corr_png}")
    print(f"      grid overlay    : {overlay_png}")
    print(f"      results json    : {json_path}")
    print(f"      patch csv       : {csv_path}")
    if report_png:
        print(f"      report card     : {report_png}")
    print(f"\nmean dE00: raw {de_raw.mean():.2f} -> corrected {de_corr.mean():.2f} "
          f"(max {de_corr.max():.2f}, within 2.0: {int((de_corr <= 2).sum())}/24)")
    return results


def pick_markers(input_path, out_path=None, hint_path=None, rotate_180=False, display_max=1200):
    """Click the 4 corner patches in a window, draw green crosses, save the image and hint."""
    try:
        import tkinter as tk
        from PIL import ImageTk
    except Exception as exc:  # noqa: BLE001
        print(f"error: the marker tool needs tkinter: {exc}", file=sys.stderr)
        return 2
    im = Image.open(input_path).convert("RGB")
    if rotate_180:
        im = im.rotate(180)
    scale = min(1.0, display_max / max(im.size))
    shown = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
    state = dict(points=[])

    root = tk.Tk()
    root.title("click the CENTRES of the 4 corner patches: TL, TR, BL, BR")
    canvas = tk.Canvas(root, width=shown.width, height=shown.height, highlightthickness=0)
    canvas.pack()
    photo = ImageTk.PhotoImage(shown)
    canvas.create_image(0, 0, anchor="nw", image=photo)

    def on_click(event):
        x, y = event.x / scale, event.y / scale
        state["points"].append((x, y))
        r = 9
        canvas.create_line(event.x - r, event.y, event.x + r, event.y, fill="#00ff00", width=3)
        canvas.create_line(event.x, event.y - r, event.x, event.y + r, fill="#00ff00", width=3)
        print(f"  corner {len(state['points'])}: ({x:.1f}, {y:.1f})")
        if len(state["points"]) == 4:
            root.after(120, root.destroy)

    canvas.bind("<Button-1>", on_click)
    print("click the 4 corner-patch centres in order TL, TR, BL, BR (close the window to abort)")
    root.mainloop()
    if len(state["points"]) != 4:
        print("aborted: expected 4 clicks")
        return 1

    quad = np.array(state["points"], dtype=float)
    marked = im.copy()
    d = ImageDraw.Draw(marked)
    for x, y in quad:
        d.line([(x - 14, y), (x + 14, y)], fill=(0, 255, 0), width=3)
        d.line([(x, y - 14), (x, y + 14)], fill=(0, 255, 0), width=3)
    marked_path = out_path or (os.path.splitext(input_path)[0] + "_marked.png")
    marked.save(marked_path)
    hp = save_hint(input_path, quad, im.size, hint_path)
    print(f"wrote {marked_path}")
    print(f"wrote {hp}  ->  the pipeline will reuse these corners automatically")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="ColorChecker photometric correction pipeline")
    ap.add_argument("input", help="input image")
    ap.add_argument("--out", default="out", help="output directory (default: out)")
    ap.add_argument("--hint", nargs=4, default=None, metavar=("P00", "P05", "P30", "P35"),
                    help="grid corners 'x,y' (or 'u,v' fractions): centres of the top-left, "
                         "top-right, bottom-left and bottom-right patches")
    ap.add_argument("--hint-file", default=None, help="json file with saved grid corners")
    ap.add_argument("--markers", action="store_true",
                    help="open a window to click the 4 corner patches, then exit "
                         "(writes <image>_marked.png and <image>.hint.json)")
    ap.add_argument("--auto", action="store_true",
                    help="try to find the chart automatically (best effort, clean studio "
                         "shots only - verify the grid overlay it writes)")
    ap.add_argument("--rotate-180", action="store_true",
                    help="rotate the image first (use when the chart in the photo is upside down)")
    ap.add_argument("--report", action="store_true", help="also write the before/after card")
    ap.add_argument("--model", default=None,
                    help="force a transfer model: none | 'linear gain' | 'linear affine' | "
                         "'3x3 matrix' | quadratic (default: best leave-one-out error)")
    args = ap.parse_args(argv)

    if args.markers:
        return pick_markers(args.input, hint_path=args.hint_file, rotate_180=args.rotate_180)

    hint = None
    if args.hint:
        hint = []
        for token in args.hint:
            x, y = token.split(",")
            hint.append((float(x), float(y)))

    try:
        run(args.input, out_dir=args.out, hint=hint,
            rotate_180=args.rotate_180, want_report=args.report,
            allow_auto=args.auto, hint_file=args.hint_file, model_name=args.model)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
