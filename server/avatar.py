"""Avatar renderer: crops the face (8x8 px) from a Minecraft skin PNG,
upscales nearest-neighbour and overlays the helmet layer when present.
Placeholder faces are generated procedurally (no bundled binary needed)."""
import io
import struct
from typing import Optional, Tuple

FACE_BOX = (8, 8, 16, 16)     # classic layout: face occupies 8..16
HELMET_BOX = (40, 8, 48, 16)  # overlay layer, same band


def decode_png_dimensions(data: bytes) -> Optional[Tuple[int, int]]:
    """Minimal IHDR parse: returns (width,height) or None."""
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    if data[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def is_skin_shape(w: int, h: int) -> bool:
    return (w, h) in ((64, 64), (64, 32))


def render_face_png(skin_bytes: bytes, size_px: int = 64,
                    include_hat_layer: bool = True) -> Optional[bytes]:
    """Crop the face rectangle and upscale nearest-neighbour to size_px.
    Returns PNG bytes or None when the skin is unusable."""
    try:
        from PIL import Image
    except ImportError:
        raise RuntimeError("Pillow missing in production image")
    img = Image.open(io.BytesIO(skin_bytes)).convert("RGBA")
    w, h = img.size
    if not is_skin_shape(w, h):
        return None
    sx = w / 64.0
    fx0, fy0, fx1, fy1 = FACE_BOX
    face = img.crop((int(fx0 * sx), int(fy0 * sx),
                     int(fx1 * sx), int(fy1 * sx)))
    if include_hat_layer and h >= 64:
        hx0, hy0, hx1, hy1 = HELMET_BOX
        hat = img.crop((int(hx0 * sx), int(hy0 * sx),
                        int(hx1 * sx), int(hy1 * sx)))
        face.alpha_composite(hat)
    face = face.resize((size_px, size_px), Image.Resampling.NEAREST)
    buf = io.BytesIO()
    face.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def placeholder_png(size_px: int = 64) -> bytes:
    """Neutral grey face with darker eyes — deterministic, no network."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size_px, size_px), (90, 96, 102, 255))
    draw = ImageDraw.Draw(img)
    q = max(1, size_px // 8)
    eye_y = 3 * q
    for ex in (2 * q, 5 * q):
        draw.rectangle([ex, eye_y, ex + q - 1, eye_y + q - 1],
                       fill=(38, 42, 46, 255))
    mouth_y = 5 * q
    draw.rectangle([3 * q, mouth_y, 4 * q + 1, mouth_y + q - 1],
                   fill=(64, 68, 74, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
