#!/usr/bin/env python3
"""Render the glmate promo video (1920x1080 landscape) from video/script.md + QQ screenshots.

Pipeline: parse numbered lines from script.md -> edge-tts (zh-CN-XiaoxiaoNeural,
falls back to Windows SAPI) -> one still frame per line (PIL) -> one segment per
line with a slow push-in (ffmpeg zoompan) -> concat.

Requires: Pillow, ffmpeg, edge-tts (pip install edge-tts).
Voice can be changed with the VOICE env var, e.g. VOICE=zh-CN-YunxiNeural.
"""
import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "script.md"
OUT = HERE / "glmate.mp4"
BUILD = HERE / ".build"
ASSETS = HERE

W, H = 1920, 1080
FPS = 30
BG = (244, 242, 238)
INK = (28, 28, 28)
MUTED = (110, 108, 104)
ACCENT = (27, 111, 214)
FONT_BD = "C:/Windows/Fonts/msyhbd.ttc"
FONT_RG = "C:/Windows/Fonts/msyh.ttc"
FONT_MONO = "C:/Windows/Fonts/consolab.ttf"
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "ffprobe")
VOICE = os.environ.get("VOICE", "zh-CN-XiaoxiaoNeural")
RATE = os.environ.get("TTS_RATE", "+18%")

SECTIONS = {
    1: ("01", "这是什么"), 2: ("01", "这是什么"), 3: ("01", "这是什么"),
    4: ("02", "能做什么"), 5: ("02", "能做什么"), 6: ("02", "能做什么"),
    7: ("03", "免费"),
    8: ("04", "怎么部署"), 9: ("04", "怎么部署"), 10: ("04", "怎么部署"),
    11: ("04", "怎么部署"), 12: ("04", "怎么部署"),
    13: ("05", "出问题"),
    14: ("06", "在哪找"), 15: ("06", "在哪找"),
}

# line -> (kind, payload); shot payload = screenshot file, card = (big, sub)
SCENES = {
    1: ("title", ("想把 AI 接进 QQ 群吗？", "glmate · GitHub 开源")),
    2: ("card", ("glmate", "GitHub 上的开源项目")),
    3: ("shot", "展示命令.png"),
    4: ("shot", "展示文字生成 - 副本 (2).png"),
    5: ("shot", "展示图片生成 - 副本.png"),
    6: ("shot", "展示视频生成.png"),
    7: ("card", ("免费", "用的是免费额度")),
    8: ("card", ("不用配环境", "")),
    9: ("card", ("有 AI 编程助手就行", "Qoder · opencode · workbuddy · zcode")),
    10: ("card", ("/m62m61m80bpzm2/glmate", "发给它")),
    11: ("card", ("自己看文档、装依赖、起服务", "")),
    12: ("card", ("只有登录 QQ 要你本人来", "")),
    13: ("card", ("代码是 AI 写的", "报错丢给 AI 修")),
    14: ("card", ("glmate", "github.com/m62m61m80bpzm2/glmate")),
    15: ("end", None),
}

# 右侧有残缺头像、且文字留白足够的截图才裁右边；其余保持原图，避免切断聊天内容。
CROP = {
    "展示视频生成.png": 0.06,
    "展示命令.png": 0.05,
}

# Lines whose caption is skipped: the card itself already shows the text.
NO_CAPTION = {1, 10}

# Spoken overrides: captions keep the written form, TTS gets something pronounceable.
SAY = {
    10: "发一句：读一下这个仓库，m62m61m80bpzm2，斜杠，glmate。",
    14: "项目在 GitHub 上，m62m61m80bpzm2，斜杠，glmate。",
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


def round_shot(path, height, radius=30):
    img = Image.open(ASSETS / path).convert("RGB")
    crop = CROP.get(path, 0.0)
    if crop:
        img = img.crop((0, 0, int(img.width * (1 - crop)), img.height))
    w = int(img.width * height / img.height)
    img = img.resize((w, height), Image.LANCZOS)
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, height - 1], radius, fill=255)
    return img, mask


def paste_shot(base, shot, mask, x, y):
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([x + 8, y + 18, x + shot.width + 8, y + shot.height + 18],
                         34, fill=(0, 0, 0, 55))
    base.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(22)))
    base.paste(shot, (x, y), mask)


def draw_header(d, n):
    num, title = SECTIONS[n]
    f = font(FONT_RG, 32)
    d.text((120, 84), f"{num}   {title}", font=f, fill=MUTED)
    d.rectangle([120, 138, 184, 143], fill=ACCENT)


def draw_watermark(base, n):
    num = SECTIONS[n][0]
    f = font(FONT_BD, 420)
    wm = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    wd = ImageDraw.Draw(wm)
    wd.text((W - 110, H - 40), num, font=f, fill=(28, 28, 28, 12), anchor="rs")
    base.paste(wm, (0, 0), wm)


def draw_caption(d, text):
    f = font(FONT_RG, 34)
    line = "  ".join(wrap(d, text, f, 1500))
    w = d.textlength(line, font=f)
    d.text(((W - w) / 2, H - 74), line, font=f, fill=MUTED)


def frame_title(d, big, sub):
    f = font(FONT_BD, 118)
    lines = wrap(d, big, f, 1620)
    total = len(lines) * 152 + 100
    y = (H - total) // 2 + 10
    top = d.textbbox((120, y), lines[0], font=f)[1]
    d.rectangle([120, top - 50, 184, top - 44], fill=ACCENT)
    for ln in lines:
        d.text((120, y), ln, font=f, fill=INK)
        y += 152
    y += 6
    d.text((126, y), sub, font=font(FONT_RG, 52), fill=MUTED)


def frame_shot(base, d, n, shot_file, caption):
    shot, mask = round_shot(shot_file, 700)
    paste_shot(base, shot, mask, 120, (H - 640) // 2 + 30)
    x = 120 + shot.width + 110
    max_w = W - x - 110
    f_big = font(FONT_BD, 92)
    y = 450
    for ln in wrap(d, caption, f_big, max_w):
        if y == 450:
            top = d.textbbox((x, y), ln, font=f_big)[1]
            d.rectangle([x, top - 46, x + 64, top - 40], fill=ACCENT)
        d.text((x, y), ln, font=f_big, fill=INK)
        y += 120
    return y


def frame_card(d, n, big, sub):
    f_big = font(FONT_MONO, 96) if big.startswith("/") else font(FONT_BD, 96)
    max_w = 1600
    lines = wrap(d, big, f_big, max_w)
    total = len(lines) * 132 + (86 if sub else 0)
    y = (H - total) // 2 + 10
    top = d.textbbox((160, y), lines[0], font=f_big)[1]
    d.rectangle([160, top - 46, 224, top - 40], fill=ACCENT)
    for ln in lines:
        d.text((160, y), ln, font=f_big, fill=INK)
        y += 132
    if sub:
        y += 16
        for ln in wrap(d, sub, font(FONT_RG, 46), max_w):
            d.text((160, y), ln, font=font(FONT_RG, 46), fill=MUTED)
            y += 62


def frame_end(d, n):
    d.text((160, 330), "读", font=font(FONT_RG, 56), fill=MUTED)
    d.text((160, 410), "/m62m61m80bpzm2/glmate", font=font(FONT_MONO, 92), fill=INK)
    d.rectangle([160, 560, 260, 566], fill=ACCENT)
    d.text((160, 610), "github.com/m62m61m80bpzm2/glmate",
           font=font(FONT_RG, 44), fill=MUTED)
    d.text((160, 690), "MIT  ·  用 QQ 小号跑", font=font(FONT_RG, 38), fill=MUTED)


def make_frame(n, kind, payload, caption):
    base = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(base)
    draw_header(d, n)
    draw_watermark(base, n)
    if kind == "title":
        frame_title(d, *payload)
    elif kind == "end":
        frame_end(d, n)
    elif kind == "shot":
        frame_shot(base, d, n, payload, caption)
    else:
        frame_card(d, n, *payload)
    if n not in NO_CAPTION:
        draw_caption(d, caption)
    return base.convert("RGB")


def synth_edge(lines):
    import edge_tts

    async def run():
        for n in sorted(SCENES):
            out = BUILD / "tts" / f"{n:02d}.mp3"
            if out.exists() and out.stat().st_size > 2000:
                continue
            text = SAY.get(n, lines[n])
            for attempt in range(3):
                try:
                    await edge_tts.Communicate(text, VOICE, rate=RATE).save(str(out))
                    break
                except Exception as e:
                    if attempt == 2:
                        raise
                    print(f"  tts retry {n}: {e}")

    asyncio.run(run())


def synth_sapi(lines):
    ts = BUILD / "tts"
    payload = []
    for n in sorted(SCENES):
        out = ts / f"{n:02d}.wav"
        if out.exists() and out.stat().st_size > 1000:
            continue
        payload.append({"i": n, "text": SAY.get(n, lines[n]).replace("glmate", "gl mate"),
                        "path": str(out)})
    if not payload:
        return
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
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(ps)], check=True)


def synth(lines):
    (BUILD / "tts").mkdir(parents=True, exist_ok=True)
    try:
        synth_edge(lines)
        return ".mp3"
    except Exception as e:
        print(f"edge-tts 不可用（{e}），回退到 Windows SAPI")
        synth_sapi(lines)
        return ".wav"


def duration(path):
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def segment(n, frame, audio, dur, seg):
    frames = max(2, int(dur * FPS))
    vf = (
        f"zoompan=z='min(1+0.035*on/{frames},1.035)'"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},"
        f"fade=t=in:st=0:d=0.22:color=0xF4F2EE,"
        f"fade=t=out:st={max(0, dur - 0.22):.3f}:d=0.22:color=0xF4F2EE,"
        f"format=yuv420p"
    )
    subprocess.run([FFMPEG, "-y", "-loglevel", "error",
                    "-loop", "1", "-framerate", str(FPS), "-i", str(frame),
                    "-i", str(audio),
                    "-vf", vf,
                    "-af", "apad=pad_dur=0.28",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                    "-r", str(FPS),
                    "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
                    "-t", f"{dur:.3f}", str(seg)], check=True)


def main():
    BUILD.mkdir(exist_ok=True)
    lines = load_lines()
    ext = synth(lines)
    segs = []
    for n in sorted(SCENES):
        kind, payload = SCENES[n]
        frame = make_frame(n, kind, payload, lines[n])
        fp = BUILD / f"{n:02d}.png"
        frame.save(fp)
        audio = BUILD / "tts" / f"{n:02d}{ext}"
        dur = duration(audio) + 0.28
        seg = BUILD / f"{n:02d}.mp4"
        segment(n, fp, audio, dur, seg)
        segs.append(seg)
        print(f"  {n:02d} {dur:5.2f}s {kind:5s} {lines[n][:30]}")
    lst = BUILD / "concat.txt"
    lst.write_text("".join(f"file '{s.as_posix()}'\n" for s in segs), encoding="utf-8")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(OUT)], check=True)
    print(f"done -> {OUT}  ({duration(OUT):.1f}s, voice={VOICE})")


if __name__ == "__main__":
    main()
