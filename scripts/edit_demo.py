"""Cut the recorded demo into one video: trimmed pauses, a voiced call scene with live captions, ElevenLabs SFX and a music bed.

    cd demo_videos && python ../scripts/edit_demo.py

Needs (in demo_videos/): desk_a.mp4 (mission control), desk_b.mp4 (/flow), swipe_phone.mp4, title.png, end.png, cap1-4.png,
audio/call.json + audio/lineNN.mp3 (the call, voiced with ElevenLabs), audio/sfx_*.mp3, audio/music_loop.mp3.
"""

import json
import subprocess

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FF = imageio_ffmpeg.get_ffmpeg_exe()
W, H = 1440, 900
GAP = 0.25
TITLE, END = 2.6, 3.6
HELD_AT = 162  # seconds into desk_a.mp4 where the hold lands


def font(size, bold=False):
    return ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf", size)


def dur(path):
    return float(subprocess.run([FF, "-i", path], capture_output=True, text=True).stderr.split("Duration: ")[1].split(",")[0].split(":")[-1])


def wrap(d, text, f, width):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if d.textlength(t, font=f) > width:
            lines.append(cur)
            cur = w
        else:
            cur = t
    return lines + [cur]


def call_card(who, text, path, ringing=False):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    x0, y0, x1, y1 = 300, 170, 1140, 730
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([x0, y0 + 14, x1, y1 + 14], 36, fill=(0, 0, 0, 160))
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(24)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([x0, y0, x1, y1], 36, fill=(11, 14, 20, 238), outline=(61, 255, 160, 90), width=2)
    d.text((x0 + 36, y0 + 34), "◌ RINGING…" if ringing else "● LIVE CALL", font=font(20, True), fill=(255, 181, 71) if ringing else (61, 255, 160))
    d.text((x1 - 36, y0 + 34), "ElevenLabs voice · Twilio", font=font(18), fill=(120, 132, 150), anchor="ra")
    for cx, label, sub, active, homie in ((x0 + 220, "Homie Calls", "AI assistant", who == "homie", True),
                                          (x1 - 220, "Chris", "Leasing manager · The Durant", who == "office", False)):
        if active and not ringing:
            for r, a in ((112, 40), (98, 70)):
                d.ellipse([cx - r, y0 + 175 - r, cx + r, y0 + 175 + r], outline=(61, 255, 160, a), width=4)
        if homie:
            av = Image.open("../relay_app/avatars/calls.png").convert("RGBA").resize((150, 150))
            m = Image.new("L", (150, 150), 0)
            ImageDraw.Draw(m).ellipse([0, 0, 150, 150], fill=255)
            img.paste(av, (cx - 75, y0 + 100), m)
        else:
            d.ellipse([cx - 75, y0 + 100, cx + 75, y0 + 250], fill=(36, 42, 58))
            d.text((cx, y0 + 175), "C", font=font(70, True), fill=(220, 226, 236), anchor="mm")
        d.text((cx, y0 + 285), label, font=font(26, True), fill=(242, 245, 249), anchor="mm")
        d.text((cx, y0 + 318), sub, font=font(18), fill=(130, 140, 158), anchor="mm")
    d.text((W / 2, y0 + 175), "…" if ringing else "⟷", font=font(44, True), fill=(90, 100, 120), anchor="mm")
    if text:
        f = font(28, who == "homie")
        color = (106, 184, 255) if who == "homie" else (255, 213, 140)
        d.text((x0 + 60, y0 + 372), "HOMIE" if who == "homie" else "CHRIS · THE DURANT", font=font(16, True), fill=color)
        for i, ln in enumerate(wrap(d, "“" + text + "”", f, x1 - x0 - 120)[:3]):
            d.text((x0 + 60, y0 + 402 + i * 38), ln, font=f, fill=(236, 240, 246))
    img.save(path)


def main():
    call = json.load(open("audio/call.json"))
    ring = dur("audio/sfx_ring.mp3")
    call_card("", "", "call_ring.png", ringing=True)
    for i, line in enumerate(call):
        call_card(line["who"], line["text"], f"call_{i:02d}.png")
    call_len = ring + sum(line["dur"] + GAP for line in call)

    segs = [("s1", "desk_a.mp4", 0, 31, 6.0), ("s2", "desk_a.mp4", 31, 47, 1.0), ("s3a", "desk_b.mp4", 47, 55, 2.0),
            ("call", "desk_b.mp4", 55, 55 + call_len, 1.0), ("s3c", "desk_b.mp4", 55 + call_len, 125, 4.0), ("s4", "desk_a.mp4", 125, 171, 3.0)]
    t, starts = TITLE, {}
    for name, _, a, b, sp in segs:
        starts[name] = t
        t += (b - a) / sp
    total = t + END
    call_t0 = starts["call"]

    inputs = ["-loop", "1", "-t", str(TITLE), "-i", "title.png", "-loop", "1", "-t", str(END), "-i", "end.png",
              "-i", "desk_a.mp4", "-i", "desk_b.mp4", "-i", "swipe_phone.mp4",
              "-i", "cap1.png", "-i", "cap2.png", "-i", "cap3.png", "-i", "cap4.png", "-i", "call_ring.png"]
    idx = {"desk_a.mp4": 2, "desk_b.mp4": 3, "phone": 4, "cap1": 5, "cap2": 6, "cap3": 7, "cap4": 8, "ring": 9}
    for i in range(len(call)):
        inputs += ["-i", f"call_{i:02d}.png"]
        idx[f"c{i}"] = 10 + i

    fc = [f"[{idx[src]}:v]trim={a}:{b},setpts=(PTS-STARTPTS)/{sp},fps=30[{name}]" for name, src, a, b, sp in segs]
    fc += [f"[{idx['phone']}:v]scale=-2:740,fps=30,setpts=PTS-STARTPTS[ph]", "[s2][ph]overlay=x=W-w-60:y=60:eof_action=pass[s2p]",
           f"[s1][{idx['cap1']}:v]overlay[v1]", f"[s2p][{idx['cap2']}:v]overlay[v2]", f"[s3a][{idx['cap3']}:v]overlay[v3a]",
           "[call]eq=brightness=-0.18:saturation=0.7[cdim]", f"[cdim][{idx['ring']}:v]overlay=enable='between(t,0,{ring:.2f})'[cr]"]
    cur, t0 = "cr", ring
    for i, line in enumerate(call):
        a, b = t0, (t0 + line["dur"] + GAP) if i < len(call) - 1 else call_len + 1
        fc.append(f"[{cur}][{idx[f'c{i}']}:v]overlay=enable='between(t,{a:.2f},{b:.2f})'[cc{i}]")
        cur, t0 = f"cc{i}", t0 + line["dur"] + GAP
    fc += [f"[{cur}][{idx['cap3']}:v]overlay[vcall]", f"[s3c][{idx['cap3']}:v]overlay[v3c]", f"[s4][{idx['cap4']}:v]overlay[v4]"]
    order = ["0:v", "v1", "v2", "v3a", "vcall", "v3c", "v4", "1:v"]
    fc += [f"[{o}]fps=30,format=yuv420p,setsar=1[o{k}]" for k, o in enumerate(order)]
    fc.append("".join(f"[o{k}]" for k in range(len(order))) + f"concat=n={len(order)}:v=1:a=0[vout]")

    n_video = sum(1 for x in inputs if x == "-i")
    audio = [("music", "audio/music_loop.mp3"), ("whoosh", "audio/sfx_whoosh.mp3"), ("swipe", "audio/sfx_swipe.mp3"),
             ("pop", "audio/sfx_pop.mp3"), ("success", "audio/sfx_success.mp3"), ("ring", "audio/sfx_ring.mp3")] + \
            [(f"l{i}", line["file"]) for i, line in enumerate(call)]
    amap = {}
    for k, (key, path) in enumerate(audio):
        amap[key] = n_video + k
        inputs += (["-stream_loop", "-1"] if key == "music" else []) + ["-i", path]

    events = [("whoosh", starts[n] - 0.15, 0.55) for n in ("s1", "s2", "s3a", "s3c", "s4")]
    events += [("whoosh", total - END - 0.15, 0.55), ("pop", starts["s2"] + 0.4, 0.7)]
    events += [("swipe", starts["s2"] + 2.5 + 1.6 * (n + 1) - 0.05, 0.8) for n in range(6)]
    events.append(("ring", call_t0, 0.9))
    tt = call_t0 + ring
    for i, line in enumerate(call):
        events.append((f"l{i}", tt, 1.0))
        tt += line["dur"] + GAP
    events.append(("success", starts["s4"] + (HELD_AT - 125) / 3.0, 0.9))

    uses = {}
    for key, _, _ in events:
        uses[amap[key]] = uses.get(amap[key], 0) + 1
    af = [f"[{src}:a]asplit={n}" + "".join(f"[sp{src}_{c}]" for c in range(n)) for src, n in uses.items() if n > 1]
    labels, seen = [], {}
    for j, (key, at, gain) in enumerate(events):
        src = amap[key]
        c = seen.get(src, 0)
        seen[src] = c + 1
        lab = f"[sp{src}_{c}]" if uses[src] > 1 else f"[{src}:a]"
        ms = max(0, int(at * 1000))
        af.append(f"{lab}aresample=44100,aformat=channel_layouts=stereo,volume={gain},adelay={ms}|{ms}[e{j}]")
        labels.append(f"[e{j}]")
    call_end = call_t0 + call_len
    af.append(f"[{amap['music']}:a]aresample=44100,aformat=channel_layouts=stereo,atrim=0:{total:.2f},"
              f"volume='if(between(t,{call_t0 - 0.5:.2f},{call_end + 0.5:.2f}),0.05,0.20)':eval=frame,"
              f"afade=t=in:st=0:d=1.5,afade=t=out:st={total - 2.5:.2f}:d=2.5[mus]")
    labels.append("[mus]")
    af.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=longest,atrim=0:{total:.2f},alimiter=limit=0.95[aout]")

    subprocess.run([FF, "-loglevel", "error", "-y", *inputs, "-filter_complex", ";".join(fc + af), "-map", "[vout]", "-map", "[aout]",
                    "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart", "-t", f"{total:.2f}", "homie_demo_final.mp4"], check=True)
    print(f"homie_demo_final.mp4 · {dur('homie_demo_final.mp4'):.1f}s · call scene at {call_t0:.1f}s for {call_len:.1f}s")


if __name__ == "__main__":
    main()
