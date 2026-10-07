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
    ":d=1:s={w}x{h}:fps={fps},setsar=1,format=yuv420p[v]"
)


# A persona shot is already composed around a person, so it fills the frame
# instead of sitting on the blurred bed a product photo needs.
VIDEO_FILTER = (
    "scale={w}:{h}:force_original_aspect_ratio=increase,"
    "crop={w}:{h},setsar=1,fps={fps},format=yuv420p"
)

# A still of a person needs more movement than a product photo to stop reading
# as a photo — a wider zoom range plus a slow drift, which zoompan clamps
# inside the frame because the zoom always leaves slack.
PORTRAIT_FILTER = (
    "[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1[base];"
    "[base]zoompan=z='{zoom}'"
    ":x='iw/2-(iw/zoom/2)+sin(on/{fps}*0.7)*(iw*0.012)'"
    ":y='ih/2-(ih/zoom/2)-(on/{fps})*{drift}'"
    ":d=1:s={w}x{h}:fps={fps},format=yuv420p[v]"
)
PORTRAIT_IN = "min(1+0.00075*on,1.20)"
PORTRAIT_OUT = "max(1.20-0.00075*on,1.0)"
PORTRAIT_DRIFT = 5  # พิกเซลต่อวินาที เงยขึ้นช้า ๆ ให้เหมือนกล้องมีชีวิต


def _segment_image(image: Path, out: Path, seconds: float, zoom: str,
                   w: int, h: int, crf: int) -> None:
    ff.run([
        "-loop", "1", "-framerate", str(FPS), "-t", f"{seconds:.3f}", "-i", str(image),
        "-filter_complex", SEGMENT_FILTER.format(
            w=w, h=h, fps=FPS, zoom=zoom,
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
                   w: int, h: int, crf: int) -> None:
    """Cut `seconds` out of a clip. Loops it when the source is shorter, so a
    three-second shot can still fill a five-second slot."""
    ff.run([
        "-stream_loop", "-1", "-t", f"{seconds:.3f}", "-i", str(clip),
        "-an",  # the voiceover is the only audio in the finished clip
        "-vf", VIDEO_FILTER.format(w=w, h=h, fps=FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        str(out),
    ])


def _segment_portrait(image: Path, out: Path, seconds: float, zoom: str,
                      w: int, h: int, crf: int) -> None:
    """A still of a person: fill the frame and keep it moving."""
    ff.run([
        "-loop", "1", "-framerate", str(FPS), "-t", f"{seconds:.3f}", "-i", str(image),
        "-filter_complex", PORTRAIT_FILTER.format(
            w=w, h=h, fps=FPS, zoom=zoom, drift=PORTRAIT_DRIFT),
        "-map", "[v]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        str(out),
    ])


def _segment(source: Path, out: Path, seconds: float, zoom: str,
             w: int, h: int, crf: int, portrait: bool = False) -> None:
    from . import persona
    if persona.is_video(source):
        _segment_video(source, out, seconds, w, h, crf)
    elif portrait:
        _segment_portrait(source, out, seconds, zoom, w, h, crf)
    else:
        _segment_image(source, out, seconds, zoom, w, h, crf)


def build_visual(shots: list[Path], total: float, workdir: Path,
                 w: int = 1080, h: int = 1920, crf: int = 20,
                 people: set[Path] | None = None) -> Path:
    """shots เป็นได้ทั้งรูปนิ่งและคลิปสั้น ปนกันในลำดับเดียวได้

    people บอกว่าช็อตไหนเป็นคน เพื่อให้จัดเฟรมและการเคลื่อนไหวคนละแบบกับสินค้า
    """
    if not shots:
        raise ValueError("ไม่มีช็อตให้เรนเดอร์")

    people = people or set()
    seconds = max(MIN_SEG, total / len(shots))
    parts = []
    for i, shot in enumerate(shots):
        part = workdir / f"seg_{i:02d}.mp4"
        portrait = shot in people
        zoom = (PORTRAIT_IN if i % 2 == 0 else PORTRAIT_OUT) if portrait else \
               (ZOOM_IN if i % 2 == 0 else ZOOM_OUT)
        _segment(shot, part, seconds, zoom, w, h, crf, portrait)
        parts.append(part)

    listing = workdir / "segments.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")

    visual = workdir / "visual.mp4"
    ff.run(
        ["-f", "concat", "-safe", "0", "-i", listing.name, "-c", "copy", visual.name],
        cwd=workdir,
    )
    return visual


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
