"""Images + voiceover + captions -> mp4 at any aspect ratio."""

from __future__ import annotations

from pathlib import Path

from . import ff

FPS = 30
MIN_SEG = 2.0

# Ken Burns: alternate the direction so consecutive shots do not feel identical.
ZOOM_IN = "min(1+0.00042*on,1.14)"
ZOOM_OUT = "max(1.14-0.00042*on,1.0)"

SEGMENT_FILTER = (
    "[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
    "crop={w}:{h},gblur=sigma={blur},eq=brightness=-0.08[bg];"
    "[0:v]scale={fit}:{fit}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
    "[bg][fg]overlay=(W-w)/2:(H-h)/2[base];"
    "[base]zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    ":d=1:s={w}x{h}:fps={fps},setsar=1,format=yuv420p,{grade}[v]"
)


# A persona shot is already composed around a person, so it fills the frame
# instead of sitting on the blurred bed a product photo needs.
VIDEO_FILTER = (
    "scale={w}:{h}:force_original_aspect_ratio=increase,"
    "crop={w}:{h},setsar=1,fps={fps},format=yuv420p,{grade}"
)

# A still of a person needs more movement than a product photo to stop reading
# as a photo — a wider zoom range plus a slow drift, which zoompan clamps
# inside the frame because the zoom always leaves slack.
PORTRAIT_FILTER = (
    "[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1[base];"
    "[base]zoompan=z='{zoom}'"
    ":x='iw/2-(iw/zoom/2)+sin(on/{fps}*0.7)*(iw*0.012)'"
    ":y='ih/2-(ih/zoom/2)-(on/{fps})*{drift}'"
    ":d=1:s={w}x{h}:fps={fps},format=yuv420p,{grade}[v]"
)
PORTRAIT_IN = "min(1+0.00075*on,1.20)"
PORTRAIT_OUT = "max(1.20-0.00075*on,1.0)"
PORTRAIT_DRIFT = 5  # พิกเซลต่อวินาที เงยขึ้นช้า ๆ ให้เหมือนกล้องมีชีวิต


def _segment_image(image: Path, out: Path, seconds: float, zoom: str,
                   w: int, h: int, crf: int, grade: str) -> None:
    ff.run([
        "-loop", "1", "-framerate", str(FPS), "-t", f"{seconds:.3f}", "-i", str(image),
        "-filter_complex", SEGMENT_FILTER.format(
            w=w, h=h, fps=FPS, zoom=zoom, grade=grade,
            # the product sits inside the safe area, clear of badge and captions
            fit=int(min(w, h) * 0.86),
            blur=max(12, round(w / 42)),
        ),
        "-map", "[v]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        str(out),
    ])


def _segment_video(clip: Path, out: Path, seconds: float,
                   w: int, h: int, crf: int, grade: str) -> None:
    """Cut `seconds` out of a clip. Loops it when the source is shorter, so a
    three-second shot can still fill a five-second slot."""
    ff.run([
        "-stream_loop", "-1", "-t", f"{seconds:.3f}", "-i", str(clip),
        "-an",  # the voiceover is the only audio in the finished clip
        "-vf", VIDEO_FILTER.format(w=w, h=h, fps=FPS, grade=grade),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        str(out),
    ])


def _segment_portrait(image: Path, out: Path, seconds: float, zoom: str,
                      w: int, h: int, crf: int, grade: str) -> None:
    """A still of a person: fill the frame and keep it moving."""
    ff.run([
        "-loop", "1", "-framerate", str(FPS), "-t", f"{seconds:.3f}", "-i", str(image),
        "-filter_complex", PORTRAIT_FILTER.format(
            w=w, h=h, fps=FPS, zoom=zoom, drift=PORTRAIT_DRIFT, grade=grade),
        "-map", "[v]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        str(out),
    ])


def _segment(source: Path, out: Path, seconds: float, zoom: str,
             w: int, h: int, crf: int, portrait: bool = False,
             grade: str = "null") -> None:
    from . import persona
    if persona.is_video(source):
        _segment_video(source, out, seconds, w, h, crf, grade)
    elif portrait:
        _segment_portrait(source, out, seconds, zoom, w, h, crf, grade)
    else:
        _segment_image(source, out, seconds, zoom, w, h, crf, grade)


# ----------------------------------------------------------------- สายพาน

# การ์ดสินค้ากว้างเท่าไรเทียบกับความกว้างเฟรม — เหลือขอบให้เห็นพื้นหลังเบลอ
BELT_CARD = 0.78
# วินาทีที่การ์ดหนึ่งใบใช้เลื่อนพ้นตัวเอง ยิ่งน้อยยิ่งไหลเร็ว
BELT_PER_CARD = 2.4

BELT_FILTER = (
    "[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
    "gblur=sigma={blur},eq=brightness=-0.14,setsar=1[bg];"
    "{cards}"
    "{strip}hstack=inputs={n2}[belt];"
    # พื้นหลังนิ่ง สายพานเลื่อนซ้ายอย่างเดียว พอเลื่อนครบหนึ่งรอบ ภาพที่เห็น
    # เหมือนตอนเริ่มพอดี เพราะการ์ดถูกต่อไว้สองชุด การวนจึงไม่มีรอยต่อ
    "[bg][belt]overlay=x='-mod(t*{speed}\\,{period})':y=(H-h)/2:shortest=1,"
    "format=yuv420p,{grade}[v]"
)


def build_belt(shots: list[Path], total: float, workdir: Path,
               w: int = 1080, h: int = 1920, crf: int = 20,
               grade: str = "null") -> Path:
    """สายพาน — ภาพสินค้าไหลต่อเนื่องแนวนอน ไม่มีรอยตัดเลยทั้งคลิป

    ต่อการ์ดไว้สองชุดแล้วเลื่อนทับระยะหนึ่งชุด พอครบรอบภาพจึงซ้ำกับตอนเริ่ม
    ได้ลูปที่ไม่มีรอยต่อโดยไม่ต้องรู้ว่าคลิปจะยาวเท่าไร
    """
    if not shots:
        raise ValueError("ไม่มีช็อตให้เรนเดอร์")

    card = int(w * BELT_CARD) // 2 * 2  # เลขคู่ ไม่งั้น yuv420p บ่น
    period = card * len(shots)
    speed = card / BELT_PER_CARD

    cards = "".join(
        f"[{i}:v]scale={card}:{card}:force_original_aspect_ratio=increase,"
        f"crop={card}:{card},setsar=1,split=2[c{i}a][c{i}b];"
        for i in range(len(shots))
    )
    strip = ("".join(f"[c{i}a]" for i in range(len(shots)))
             + "".join(f"[c{i}b]" for i in range(len(shots))))

    args: list[str] = []
    for shot in shots:
        args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{total:.3f}", "-i", str(shot)]

    visual = workdir / "visual.mp4"
    ff.run([
        *args,
        "-filter_complex", BELT_FILTER.format(
            w=w, h=h, blur=max(12, round(w / 42)), cards=cards, strip=strip,
            n2=len(shots) * 2, speed=f"{speed:.2f}", period=period, grade=grade,
        ),
        "-map", "[v]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-r", str(FPS), "-t", f"{total:.3f}",
        str(visual.resolve()),
    ], cwd=workdir)
    return visual


# ------------------------------------------------------- ต่อช็อตเข้าด้วยกัน

def _join_cut(parts: list[Path], workdir: Path) -> Path:
    """ตัดแข็ง — ต่อไฟล์ตรง ๆ ไม่ต้องเข้ารหัสใหม่"""
    listing = workdir / "segments.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    visual = workdir / "visual.mp4"
    ff.run(
        ["-f", "concat", "-safe", "0", "-i", listing.name, "-c", "copy", visual.name],
        cwd=workdir,
    )
    return visual


def _join_xfade(parts: list[Path], seconds: float, fade: float,
                workdir: Path, crf: int) -> Path:
    """ข้ามภาพนุ่ม ๆ — ต้องเข้ารหัสใหม่ เพราะภาพสองช็อตซ้อนกันจริง

    ช็อตที่ n เริ่มซ้อนตอน n*(seconds-fade) เพราะทุกครั้งที่ข้าม ความยาวรวม
    หายไปเท่ากับ fade — ตัวเรียกเผื่อความยาวช็อตมาให้แล้ว ผลรวมจึงยังตรงเป้า
    """
    args: list[str] = []
    for part in parts:
        args += ["-i", part.name]

    chains = []
    label = "0:v"
    for i in range(1, len(parts)):
        out = f"x{i}"
        chains.append(
            f"[{label}][{i}:v]xfade=transition=fade:duration={fade:.3f}"
            f":offset={i * (seconds - fade):.3f}[{out}]"
        )
        label = out

    visual = workdir / "visual.mp4"
    ff.run([
        *args,
        "-filter_complex", ";".join(chains),
        "-map", f"[{label}]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        visual.name,
    ], cwd=workdir)
    return visual


def build_visual(shots: list[Path], total: float, workdir: Path,
                 w: int = 1080, h: int = 1920, crf: int = 20,
                 people: set[Path] | None = None, style=None) -> Path:
    """shots เป็นได้ทั้งรูปนิ่งและคลิปสั้น ปนกันในลำดับเดียวได้

    people บอกว่าช็อตไหนเป็นคน เพื่อให้จัดเฟรมและการเคลื่อนไหวคนละแบบกับสินค้า
    style คุมเกรดสี จังหวะข้ามภาพ และว่าจะตัดเป็นช็อตหรือไหลเป็นสายพาน
    """
    if not shots:
        raise ValueError("ไม่มีช็อตให้เรนเดอร์")

    from . import style as styles
    chosen = style if hasattr(style, "grade") else styles.get(style)

    if chosen.belt:
        return build_belt(shots, total, workdir, w, h, crf, chosen.grade)

    people = people or set()
    # ทุกครั้งที่ข้ามภาพ ความยาวรวมหายไปเท่ากับ fade จึงยืดช็อตชดเชยไว้ก่อน
    fade = chosen.xfade if len(shots) > 1 else 0.0
    seconds = max(MIN_SEG, (total + (len(shots) - 1) * fade) / len(shots))

    parts = []
    for i, shot in enumerate(shots):
        part = workdir / f"seg_{i:02d}.mp4"
        portrait = shot in people
        zoom = (PORTRAIT_IN if i % 2 == 0 else PORTRAIT_OUT) if portrait else \
               (ZOOM_IN if i % 2 == 0 else ZOOM_OUT)
        _segment(shot, part, seconds, zoom, w, h, crf, portrait, chosen.grade)
        parts.append(part)

    if fade > 0 and len(parts) > 1:
        return _join_xfade(parts, seconds, fade, workdir, crf)
    return _join_cut(parts, workdir)


def mux(visual: Path, voice: Path, ass: Path, out: Path,
        bgm: Path | None = None, crf: int = 20) -> Path:
    # Run from the work dir so the ass filter gets a bare filename: a Windows
    # absolute path inside a filter argument needs its drive colon escaped.
    workdir = visual.parent
    args = ["-i", visual.name, "-i", str(voice)]
    chains = [f"[0:v]ass={ass.name}[v]"]
    maps = ["-map", "[v]"]

    if bgm and bgm.exists():
        args += ["-stream_loop", "-1", "-i", str(bgm)]
        chains.append(
            "[1:a]volume=1.0,alimiter=limit=0.95[vo];"
            "[2:a]volume=0.09[bed];"
            "[vo][bed]amix=inputs=2:duration=first:normalize=0[a]"
        )
        maps += ["-map", "[a]"]
    else:
        maps += ["-map", "1:a"]

    ff.run([
        *args,
        "-filter_complex", ";".join(chains),
        *maps,
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest", "-movflags", "+faststart",
        str(out.resolve()),
    ], cwd=workdir)
    return out
