#!/usr/bin/env python3
"""Render the glmate promo video (1080x1920) from video/script.md + QQ screenshots.

Pipeline: parse numbered lines from script.md -> SAPI TTS per line -> one still
frame per line (PIL) -> one segment per line (ffmpeg) -> concat.

Requires: Pillow, ffmpeg on PATH (or FFMPEG env), Windows SAPI voices.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "script.md"
OUT = HERE / "glmate.mp4"
BUILD = HERE / ".build"

W, H = 1080, 1920
FPS = 30
BG = (244, 242, 238)
INK = (28, 28, 28)
MUTED = (110, 108, 104)
ACCENT = (16, 110, 190)
FONT_BD = "C:/Windows/Fonts/msyhbd.ttc"
FONT_RG = "C:/Windows/Fonts/msyh.ttc"
FONT_MONO = "C:/Windows/Fonts/consolab.ttf"
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "ffprobe")

# line number -> ("shot" | "card" | "title" | "end", payload)
# shot payload = screenshot file; card payload = (big text, sub text)
SCENES = {
    1: ("title", None),
    2: ("card", ("把 AI 接进 QQ 群", "")),
    3: ("card", ("群里所有人都能一起用", "不用一人开一个私聊")),
    4: ("shot", "展示文字生成 - 副本 (2).png"),
    5: ("card", ("联网搜索", "先查再回答")),
    6: ("card", ("看图识字", "把图片里的文字读出来")),
    7: ("shot", "展示图片生成 - 副本.png"),
    8: ("shot", "展示视频生成.png"),
    9: ("card", ("直接发到群里", "不是丢给你一个链接")),
    10: ("card", ("免费额度", "不用花钱")),
    11: ("card", ("不用自己配环境", "")),
    12: ("card", ("Qoder · opencode · workbuddy · zcode", "这类 AI 助手都行")),
    13: ("card", ("/m62m61m80bpzm2/glmate", "把这一句发给它")),
    14: ("card", ("自己看文档、装依赖、填配置", "把服务起起来")),
    15: ("card", ("只有登录 QQ 那一步", "需要你本人操作")),
    16: ("card", ("不用读代码", "底层实现不用管")),
    17: ("card", ("代码是 AI 写的", "不保证没有 bug")),
    18: ("card", ("报错丢给它", "让它自己改")),
    19: ("shot", "展示命令.png"),
    20: ("end", None),
}

# Lines whose caption is skipped: the card itself already shows the text.
NO_CAPTION = {13}

# Spoken overrides: captions keep the written form, TTS gets something pronounceable.
SAY = {
    1: "GitHub 上有这么个项目，叫 gl mate。",
    3: "机器人进群之后，群里所有人都能一起找它聊，不用一人开一个私聊。",
    12: "只要电脑上有能自动执行任务的 AI 助手，比如 Qoder、opencode、workbuddy、zcode，这类都算。",
    13: "给它发一句话就行：读一下这个仓库，m62m61m80bpzm2，斜杠，glmate。",
    19: "项目在 GitHub 上，仓库名 m62m61m80bpzm2，斜杠，glmate，MIT 协议开源。",
    20: "建议用一个 QQ 小号来跑。",
}


def load_lines():
    text = SCRIPT.read_text(encoding="utf-8")
    lines = {}
    for m in re.finditer(r"^(\d+)\.\s+(.+)$", text, re.M):
        lines[int(m.group(1))] = m.group(2).strip()
    missing = sorted(set(SCENES) - set(lines))
    if missing:
        sys.exit(f"script.md 缺少行: {missing}")
    return lines


def font(path, size):
    return ImageFont.truetype(path, size)


TOKEN_RE = re.compile(r"[A-Za-z0-9_./:@+\-]+|\s+|.")


def wrap(draw, text, fnt, max_w):
    lines, cur = [], ""
    for tok in TOKEN_RE.findall(text):
        if tok.isspace():
            if cur:
                cur += tok
            continue
        cand = cur + tok
        if draw.textlength(cand, font=fnt) <= max_w:
            cur = cand
        elif draw.textlength(tok, font=fnt) <= max_w:
            lines.append(cur.rstrip())
            cur = tok
        else:
            for ch in tok:
                if draw.textlength(cur + ch, font=fnt) <= max_w:
                    cur += ch
                else:
                    lines.append(cur)
                    cur = ch
    if cur:
        lines.append(cur)
    return [s.strip() for s in lines if s.strip()]


def rounded_shot(img, width, radius=26):
    scale = width / img.width
    img = img.convert("RGB").resize((width, int(img.height * scale)), Image.LANCZOS)
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius, fill=255)
    out = Image.new("RGB", img.size, BG)
    out.paste(img, (0, 0), mask)
    return out, mask


def paste_card(base, shot, mask, cy):
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    x = (W - shot.width) // 2
    y = cy - shot.height // 2
    sd.rounded_rectangle([x + 6, y + 14, x + shot.width + 6, y + shot.height + 14], 30, fill=(0, 0, 0, 60))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    base.alpha_composite(shadow)
    base.paste(shot, (x, y), mask)


def draw_caption(d, text):
    f = font(FONT_RG, 42)
    lines = wrap(d, text, f, W - 180)
    total = len(lines) * 62
    y = H - 210 - total // 2
    for ln in lines:
        w = d.textlength(ln, font=f)
        d.text(((W - w) / 2, y), ln, font=f, fill=MUTED)
        y += 62


def frame_title(d):
    d.text((W / 2, H / 2 - 190), "glmate", font=font(FONT_BD, 150), fill=INK, anchor="mm")
    d.text((W / 2, H / 2 - 40), "把 AI 接进 QQ 群", font=font(FONT_RG, 54), fill=MUTED, anchor="mm")


def frame_card(d, big, sub):
    f_big = font(FONT_MONO, 64) if big.startswith("/") else font(FONT_BD, 68)
    max_w = W - 160
    lines = wrap(d, big, f_big, max_w)
    total = len(lines) * 96 + (80 if sub else 0)
    y = H // 2 - total // 2
    for ln in lines:
        d.text((W / 2, y), ln, font=f_big, fill=INK, anchor="ma")
        y += 96
    if sub:
        y += 14
        for ln in wrap(d, sub, font(FONT_RG, 40), max_w):
            d.text((W / 2, y), ln, font=font(FONT_RG, 40), fill=MUTED, anchor="ma")
            y += 56


def frame_end(d):
    d.text((W / 2, H / 2 - 250), "读", font=font(FONT_BD, 60), fill=MUTED, anchor="mm")
    d.text((W / 2, H / 2 - 120), "/m62m61m80bpzm2/glmate",
           font=font(FONT_MONO, 64), fill=INK, anchor="mm")
    d.text((W / 2, H / 2 + 40), "github.com/m62m61m80bpzm2/glmate",
           font=font(FONT_RG, 38), fill=MUTED, anchor="mm")
    d.text((W / 2, H / 2 + 140), "MIT  ·  用 QQ 小号跑", font=font(FONT_RG, 34), fill=MUTED, anchor="mm")


def make_frame(n, kind, payload, caption):
    base = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(base)
    if kind == "title":
        frame_title(d)
    elif kind == "end":
        frame_end(d)
    elif kind == "card":
        frame_card(d, *payload)
    else:
        img = Image.open(HERE / payload)
        shot, mask = rounded_shot(img, min(760, int(W * 0.72)))
        paste_card(base, shot, mask, int(H * 0.46))
    if n not in NO_CAPTION:
        draw_caption(ImageDraw.Draw(base), caption)
    return base.convert("RGB")


def synth(lines):
    ts = BUILD / "tts"
    ts.mkdir(parents=True, exist_ok=True)
    payload = []
    for n in sorted(SCENES):
        path = ts / f"{n:02d}.wav"
        if path.exists() and path.stat().st_size > 1000:
            continue
        payload.append({"i": n, "text": SAY.get(n, lines[n]), "path": str(path)})
    if not payload:
        return ts
    job = ts / "job.json"
    job.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    ps = ts / "synth.ps1"
    ps.write_text(r"""
Add-Type -AssemblyName System.Speech
$items = Get-Content -Raw -Encoding UTF8 '__JOB__' | ConvertFrom-Json
foreach ($it in $items) {
  $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
  try { $s.SelectVoice('Microsoft Huihui Desktop') } catch {}
  $s.Rate = 2
  $s.SetOutputToWaveFile($it.path)
  $s.Speak($it.text)
  $s.Dispose()
}
""".replace("__JOB__", str(job).replace("'", "''")), encoding="utf-8")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps)], check=True)
    return ts


def duration(path):
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def main():
    BUILD.mkdir(exist_ok=True)
    lines = load_lines()
    tts = synth(lines)
    segs = []
    for n in sorted(SCENES):
        kind, payload = SCENES[n]
        frame = make_frame(n, kind, payload, lines[n])
        fp = BUILD / f"{n:02d}.png"
        frame.save(fp)
        audio = tts / f"{n:02d}.wav"
        dur = duration(audio) + 0.4
        seg = BUILD / f"{n:02d}.mp4"
        subprocess.run([FFMPEG, "-y", "-loglevel", "error",
                        "-loop", "1", "-framerate", str(FPS), "-i", str(fp),
                        "-i", str(audio),
                        "-c:v", "libx264", "-preset", "medium", "-tune", "stillimage",
                        "-pix_fmt", "yuv420p", "-r", str(FPS),
                        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                        "-t", f"{dur:.3f}", "-shortest", str(seg)], check=True)
        segs.append(seg)
        print(f"  {n:02d} {dur:5.2f}s {kind:5s} {lines[n][:34]}")
    lst = BUILD / "concat.txt"
    lst.write_text("".join(f"file '{s.as_posix()}'\n" for s in segs), encoding="utf-8")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(OUT)], check=True)
    print(f"done -> {OUT}  ({duration(OUT):.1f}s)")


if __name__ == "__main__":
    main()
