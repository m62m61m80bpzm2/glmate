#!/usr/bin/env python3
"""Generate the horizontal (1920x1080) and vertical (1080x1920) covers.

Reuses the drawing helpers and assets from render.py so covers stay visually
consistent with the video.
"""
from pathlib import Path
import os

from PIL import Image, ImageDraw

import render as R

OUT_H = R.HERE / "cover-horizontal.png"
OUT_V = R.HERE / "cover-vertical.png"


def label(d, x, y):
    d.text((x, y), "glmate", font=R.font(R.FONT_BD, 44), fill=R.INK)
    d.text((x + 160, y + 8), "GitHub 开源 · 免费额度", font=R.font(R.FONT_RG, 34), fill=R.MUTED)


def headline_lines(d, x, y, lines, size, gap):
    f = R.font(R.FONT_BD, size)
    for ln in lines:
        d.text((x, y), ln, font=f, fill=R.INK)
        y += gap
    return y


def shot_on(base, path, height, x, y):
    shot, mask = R.round_shot(path, height)
    R.paste_shot(base, shot, mask, x, y)
    return shot


def save(base, path):
    tmp = path.with_suffix(".tmp.png")
    base.convert("RGB").save(tmp)
    os.replace(tmp, path)
    print("saved", path)


def horizontal():
    W, H = 1920, 1080
    base = Image.new("RGBA", (W, H), R.BG)
    d = ImageDraw.Draw(base)

    label(d, 120, 96)
    d.rectangle([120, 176, 200, 182], fill=R.ACCENT)

    y = headline_lines(d, 120, 300, ["把 AI 接进", "QQ 群"], 196, 232)
    d.text((126, y + 18), "能聊天、联网、看图、画图、生成视频",
           font=R.font(R.FONT_RG, 44), fill=R.MUTED)
    d.text((126, y + 96), "把这句话发给 AI：/m62m61m80bpzm2/glmate",
           font=R.font(R.FONT_RG, 40), fill=R.MUTED)

    shot_on(base, "展示视频生成.png", 760, W - 760 * 333 // 398 - 110, 150)
    save(base, OUT_H)


def vertical():
    W, H = 1080, 1920
    base = Image.new("RGBA", (W, H), R.BG)
    d = ImageDraw.Draw(base)

    label(d, 100, 140)
    d.rectangle([100, 222, 180, 228], fill=R.ACCENT)

    y = headline_lines(d, 100, 340, ["把 AI", "接进 QQ 群"], 150, 180)
    d.text((106, y + 14), "能聊天、联网、看图、画图、生成视频",
           font=R.font(R.FONT_RG, 40), fill=R.MUTED)
    d.text((106, y + 72), "免费额度 · 一句话部署",
           font=R.font(R.FONT_RG, 40), fill=R.MUTED)

    shot, mask = R.round_shot("展示视频生成.png", 690)
    R.paste_shot(base, shot, mask, (W - shot.width) // 2, 860)

    d.rectangle([100, 1630, 180, 1636], fill=R.ACCENT)
    d.text((100, 1656), "读", font=R.font(R.FONT_RG, 44), fill=R.MUTED)
    d.text((152, 1652), "/m62m61m80bpzm2/glmate",
           font=R.font(R.FONT_MONO, 46), fill=R.INK)
    d.text((100, 1730), "发给你的 AI 编程助手", font=R.font(R.FONT_RG, 38), fill=R.MUTED)

    save(base, OUT_V)


if __name__ == "__main__":
    horizontal()
    vertical()
