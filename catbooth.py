#!/usr/bin/env python3
"""
catbooth — wrap a video in a pixel-art Photo Booth window (NDS cat edition)

Usage:
    python3 catbooth.py input.mov
    python3 catbooth.py input.mov -o out.mp4
    python3 catbooth.py input.mov --alpha          # ProRes 4444 .mov w/ transparent rounded corners
    python3 catbooth.py input.mov --fit            # letterbox instead of crop-to-fill
    python3 catbooth.py input.mov --frame-only     # just export the frame PNG

Requires: Pillow, ffmpeg/ffprobe on PATH.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

from PIL import Image

# ---------------------------------------------------------------- palette --
# Base color #4d4775 and friends
P = {
    "base":    (0x4D, 0x47, 0x75, 255),  # window chrome
    "dark":    (0x2A, 0x25, 0x47, 255),  # outlines / shadows
    "mid":     (0x3A, 0x35, 0x5E, 255),  # bar shading
    "light":   (0x6B, 0x63, 0x9C, 255),  # highlights
    "lighter": (0x8D, 0x84, 0xC4, 255),  # brighter highlight
    "pale":    (0xB8, 0xB0, 0xE0, 255),  # text / pale accents
    "white":   (0xE8, 0xE5, 0xF5, 255),  # near-white (cream cat)
    "red":     (0xE0, 0x5A, 0x6D, 255),  # camera button
    "reddk":   (0xA8, 0x3A, 0x4E, 255),
    "tl_r":    (0xE0, 0x5A, 0x6D, 255),  # traffic lights (tinted to fit palette)
    "tl_y":    (0xE8, 0xC1, 0x6E, 255),
    "tl_g":    (0x8F, 0xD0, 0x8A, 255),
    "pink":    (0xE7, 0xA6, 0xC7, 255),  # inner ears / nose
    "hole":    (0, 0, 0, 0),
}

# ------------------------------------------------------------- 3x5 font ----
FONT = {
    "P": ["XX.", "X.X", "XX.", "X..", "X.."],
    "H": ["X.X", "X.X", "XXX", "X.X", "X.X"],
    "O": ["XXX", "X.X", "X.X", "X.X", "XXX"],
    "T": ["XXX", ".X.", ".X.", ".X.", ".X."],
    "B": ["XX.", "X.X", "XX.", "X.X", "XX."],
    "E": ["XXX", "X..", "XX.", "X..", "XXX"],
    "F": ["XXX", "X..", "XX.", "X..", "X.."],
    "C": [".XX", "X..", "X..", "X..", ".XX"],
    "S": ["XXX", "X..", "XXX", "..X", "XXX"],
    "A": [".X.", "X.X", "XXX", "X.X", "X.X"],
    "V": ["X.X", "X.X", "X.X", "X.X", ".X."],
    "-": ["...", "...", "XXX", "...", "..."],
    " ": ["...", "...", "...", "...", "..."],
}

# ------------------------------------------------------------- sprites -----
# chars: . transparent, O outline, C coat, P pink, D dark(eyes), W white glint
SITTING_CAT = [
    ".O.....O.",
    "OCO...OCO",
    "OCPO.OPCO",
    "OCCCCCCCO",
    "OCDCCCDCO",
    "OCCCPCCCO",
    ".OCCCCCO.",
    ".OCCCCCO.",
    "OCCCCCCCO",
    "OCOCCCOCO",
    ".O.OOO.O.",
]

LOAF_CAT = [
    ".O...O....",
    "OCO.OCO...",
    "OCCCCCOOO.",
    "OCDCDCCCCO",
    "OCCPCCCCCO",
    ".OOOOOOOO.",
]

PAW = [
    "X.X",
    ".X.",
    "XXX",
]

HEART = [
    ".X.X.",
    "XXXXX",
    "XXXXX",
    ".XXX.",
    "..X..",
]

CAMERA_GLYPH = [
    "..XXX..",
    ".XXXXX.",
    "XXX.XXX",
    "XX...XX",
    "XXX.XXX",
    ".XXXXX.",
    "..XXX..",
]


def blit_sprite(put, sprite, ox, oy, colormap):
    for y, row in enumerate(sprite):
        for x, ch in enumerate(row):
            if ch in colormap:
                put(ox + x, oy + y, colormap[ch])


def blit_text(put, text, ox, oy, color):
    x = ox
    for ch in text.upper():
        glyph = FONT.get(ch, FONT[" "])
        for gy, row in enumerate(glyph):
            for gx, c in enumerate(row):
                if c == "X":
                    put(x + gx, oy + gy, color)
        x += 4  # 3px glyph + 1px space
    return x - ox - 1  # width drawn


def text_width(text):
    return len(text) * 4 - 1


# ------------------------------------------------------- frame builder -----
TITLE_H = 13      # logical px
BOTTOM_H = 22
BORDER = 2
CORNER = 3        # rounded corner cut


def build_frame(out_w, out_h):
    """Return (frame RGBA Image at out_w x out_h, hole rect in output px)."""
    # logical pixel size: aim for ~180-220 logical rows
    scale = max(2, round(min(out_w, out_h) / 190))
    lw, lh = out_w // scale, out_h // scale

    img = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))
    pix = img.load()

    def put(x, y, color):
        if 0 <= x < lw and 0 <= y < lh:
            pix[x, y] = color

    def rect(x0, y0, x1, y1, color):
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                put(x, y, color)

    # --- window body -------------------------------------------------------
    rect(0, 0, lw - 1, lh - 1, P["base"])
    # outer outline
    rect(0, 0, lw - 1, 0, P["dark"])
    rect(0, lh - 1, lw - 1, lh - 1, P["dark"])
    rect(0, 0, 0, lh - 1, P["dark"])
    rect(lw - 1, 0, lw - 1, lh - 1, P["dark"])
    # inner top highlight
    rect(1, 1, lw - 2, 1, P["light"])

    # rounded corners (transparent cut + dark bevel)
    for cx, cy, dx, dy in [(0, 0, 1, 1), (lw - 1, 0, -1, 1),
                           (0, lh - 1, 1, -1), (lw - 1, lh - 1, -1, -1)]:
        for i in range(CORNER):
            for j in range(CORNER - i):
                put(cx + dx * j, cy + dy * i, (0, 0, 0, 0))
        # bevel the diagonal
        for i in range(CORNER + 1):
            put(cx + dx * (CORNER - i), cy + dy * i, P["dark"])

    # --- title bar ---------------------------------------------------------
    rect(1, 2, lw - 2, TITLE_H - 1, P["base"])
    rect(1, TITLE_H, lw - 2, TITLE_H, P["dark"])          # divider
    rect(1, TITLE_H - 1, lw - 2, TITLE_H - 1, P["mid"])   # divider shadow

    # traffic lights (2x2 chunky with outline)
    for i, key in enumerate(["tl_r", "tl_y", "tl_g"]):
        x0 = 5 + i * 6
        y0 = 5
        rect(x0, y0, x0 + 2, y0 + 2, P["dark"])
        rect(x0, y0, x0 + 1, y0 + 1, P[key])

    # centered title
    title = "CAT-BOOTH"
    tx = (lw - text_width(title)) // 2
    blit_text(put, title, tx + 1, 5, P["dark"])   # drop shadow
    blit_text(put, title, tx, 4, P["white"])

    # loaf cat chilling on the right of the title bar
    blit_sprite(put, LOAF_CAT, lw - 16, TITLE_H - 6, {
        "O": P["dark"], "C": P["lighter"], "D": P["dark"], "P": P["pink"],
    })

    # --- bottom bar --------------------------------------------------------
    by0 = lh - BOTTOM_H
    rect(1, by0, lw - 2, lh - 2, P["base"])
    rect(1, by0, lw - 2, by0, P["dark"])            # divider
    rect(1, by0 + 1, lw - 2, by0 + 1, P["light"])   # highlight under divider

    # mode buttons (bottom-left): 4-grid, single, filmstrip
    bx = 5
    bh0 = by0 + (BOTTOM_H - 9) // 2 + 1
    for icon in range(3):
        # button plate
        rect(bx, bh0, bx + 10, bh0 + 8, P["dark"])
        plate = P["light"] if icon == 1 else P["mid"]
        rect(bx + 1, bh0 + 1, bx + 9, bh0 + 7, plate)
        ic = P["white"] if icon == 1 else P["pale"]
        if icon == 0:      # 4-grid
            rect(bx + 3, bh0 + 2, bx + 4, bh0 + 3, ic)
            rect(bx + 6, bh0 + 2, bx + 7, bh0 + 3, ic)
            rect(bx + 3, bh0 + 5, bx + 4, bh0 + 6, ic)
            rect(bx + 6, bh0 + 5, bx + 7, bh0 + 6, ic)
        elif icon == 1:    # single frame
            rect(bx + 3, bh0 + 2, bx + 7, bh0 + 6, ic)
            rect(bx + 4, bh0 + 3, bx + 6, bh0 + 5, plate)
        else:              # filmstrip
            rect(bx + 2, bh0 + 3, bx + 8, bh0 + 5, ic)
            for fx in (bx + 2, bx + 4, bx + 6, bx + 8):
                put(fx, bh0 + 2, ic)
                put(fx, bh0 + 6, ic)
        bx += 13

    # camera button (center) — chunky red circle
    cx = lw // 2
    cy = by0 + BOTTOM_H // 2 - 1
    r = 6
    for y in range(-r, r + 1):
        for x in range(-r, r + 1):
            d = x * x + y * y
            if d <= r * r:
                put(cx + x, cy + y, P["dark"])
    for y in range(-r + 1, r):
        for x in range(-r + 1, r):
            d = x * x + y * y
            if d <= (r - 1) * (r - 1):
                shade = P["reddk"] if y > r - 4 else P["red"]
                put(cx + x, cy + y, shade)
    blit_sprite(put, CAMERA_GLYPH, cx - 3, cy - 3, {"X": P["white"]})

    # EFFECTS button (bottom-right)
    label = "EFFECTS"
    ew = text_width(label) + 6
    ex1 = lw - 5
    ex0 = ex1 - ew
    ey0 = by0 + (BOTTOM_H - 11) // 2 + 1
    rect(ex0, ey0, ex1, ey0 + 10, P["dark"])
    rect(ex0 + 1, ey0 + 1, ex1 - 1, ey0 + 9, P["mid"])
    rect(ex0 + 1, ey0 + 1, ex1 - 1, ey0 + 1, P["light"])
    blit_text(put, label, ex0 + 3, ey0 + 3, P["white"])

    # sitting cat: peeks over the bottom bar into the video
    cat_x = cx - 30
    cat_y = by0 - 5  # head pokes above divider
    blit_sprite(put, SITTING_CAT, cat_x, cat_y, {
        "O": P["dark"], "C": P["white"], "D": P["dark"], "P": P["pink"],
    })
    # second cat, other side, purple coat
    blit_sprite(put, SITTING_CAT, cx + 22, cat_y, {
        "O": P["dark"], "C": P["lighter"], "D": P["dark"], "P": P["pink"],
    })

    # paw prints wandering along the bottom bar
    for i, px_ in enumerate(range(cx + 40, lw - 45, 9)):
        blit_sprite(put, PAW, px_, by0 + 6 + (i % 2) * 5, {"X": P["mid"]})
    for i, px_ in enumerate(range(45, cx - 45, 9)):
        blit_sprite(put, PAW, px_, by0 + 7 + (i % 2) * 5, {"X": P["mid"]})

    # tiny heart above the cream cat
    blit_sprite(put, HEART, cat_x + 10, cat_y - 7, {"X": P["pink"]})

    # --- content hole ------------------------------------------------------
    hx0, hy0 = BORDER, TITLE_H + 1
    hx1, hy1 = lw - 1 - BORDER, by0 - 1
    rect(hx0, hy0, hx1, hy1, P["hole"])
    # re-draw cat heads over the hole (they peek into the video)
    blit_sprite(put, SITTING_CAT, cat_x, cat_y, {
        "O": P["dark"], "C": P["white"], "D": P["dark"], "P": P["pink"],
    })
    blit_sprite(put, SITTING_CAT, cx + 22, cat_y, {
        "O": P["dark"], "C": P["lighter"], "D": P["dark"], "P": P["pink"],
    })
    blit_sprite(put, HEART, cat_x + 10, cat_y - 7, {"X": P["pink"]})
    # inner shadow line around hole (screen inset look)
    rect(hx0, hy0, hx1, hy0, P["dark"])
    rect(hx0, hy0, hx0, hy1, P["dark"])

    # --- upscale ------------------------------------------------------------
    frame = img.resize((lw * scale, lh * scale), Image.NEAREST)
    # pad to exact output size (right/bottom edge fill)
    if frame.size != (out_w, out_h):
        canvas = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
        canvas.paste(frame, (0, 0))
        # extend last row/col of chrome color
        fw, fh = frame.size
        if fw < out_w:
            edge = frame.crop((fw - scale, 0, fw, fh)).resize((out_w - fw, fh))
            canvas.paste(edge, (fw, 0))
        if fh < out_h:
            edge = canvas.crop((0, fh - scale, out_w, fh)).resize((out_w, out_h - fh))
            canvas.paste(edge, (0, fh))
        frame = canvas

    hole_px = (hx0 * scale + scale, hy0 * scale + scale,
               (hx1 + 1) * scale - scale, (hy1 + 1) * scale)
    return frame, hole_px


# ------------------------------------------------------------- ffmpeg ------
def probe(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", "-select_streams", "v:0", path,
    ])
    s = json.loads(out)["streams"][0]
    return int(s["width"]), int(s["height"])


def has_audio(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", "-select_streams", "a", path,
    ])
    return len(json.loads(out).get("streams", [])) > 0


def run(args):
    in_path = args.input
    if not os.path.exists(in_path):
        sys.exit(f"input not found: {in_path}")

    vw, vh = probe(in_path)
    frame, hole = build_frame(vw, vh)

    if args.frame_only:
        out = args.output or "catbooth_frame.png"
        frame.save(out)
        print(f"frame saved -> {out}")
        return

    hw, hh = hole[2] - hole[0], hole[3] - hole[1]
    hx, hy = hole[0], hole[1]

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    frame.save(tmp.name)

    fit_mode = "decrease" if args.fit else "increase"
    scale_crop = (
        f"scale={hw}:{hh}:force_original_aspect_ratio={fit_mode}:"
        f"flags=lanczos"
    )
    if args.fit:
        inner = f"{scale_crop},pad={hw}:{hh}:(ow-iw)/2:(oh-ih)/2:color=#2A2547"
    else:
        inner = f"{scale_crop},crop={hw}:{hh}"

    if args.alpha:
        out_path = args.output or _default_out(in_path, ".mov")
        bg = f"color=c=black@0.0:s={vw}x{vh},format=rgba"
        vcodec = ["-c:v", "prores_ks", "-profile:v", "4444",
                  "-pix_fmt", "yuva444p10le"]
    else:
        out_path = args.output or _default_out(in_path, ".mp4")
        bg = f"color=c=#2A2547:s={vw}x{vh}"
        vcodec = ["-c:v", "libx264", "-qp", "0", "-preset", "medium",
                  "-pix_fmt", "yuv444p"]
        if args.compat:
            vcodec = ["-c:v", "libx264", "-crf", "0", "-preset", "medium",
                      "-pix_fmt", "yuv420p"]

    cmd = ["ffmpeg", "-y", "-i", in_path, "-loop", "1", "-i", tmp.name]
    fc = (
        f"[0:v]{inner}[vid];"
        f"{bg}[bg];"
        f"[bg][vid]overlay={hx}:{hy}:shortest=1[base];"
        f"[base][1:v]overlay=0:0:format=auto:shortest=1[out]"
    )
    cmd += ["-filter_complex", fc, "-map", "[out]"]
    if has_audio(in_path):
        cmd += ["-map", "0:a", "-c:a", "copy"]
    cmd += vcodec + [out_path]

    print("running ffmpeg...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    os.unlink(tmp.name)
    if res.returncode != 0:
        sys.exit(res.stderr[-2000:])
    print(f"done -> {out_path}")


def _default_out(in_path, ext):
    root, _ = os.path.splitext(os.path.basename(in_path))
    return f"{root}_catbooth{ext}"


def main():
    ap = argparse.ArgumentParser(description="pixel Photo Booth cat frame for videos")
    ap.add_argument("input", help="input video")
    ap.add_argument("-o", "--output", help="output path")
    ap.add_argument("--alpha", action="store_true",
                    help="export ProRes 4444 .mov with transparent rounded corners")
    ap.add_argument("--fit", action="store_true",
                    help="letterbox video inside frame instead of crop-to-fill")
    ap.add_argument("--compat", action="store_true",
                    help="yuv420p H.264 (max compatibility; visually lossless)")
    ap.add_argument("--frame-only", action="store_true",
                    help="just export the frame PNG at the input video's size")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
