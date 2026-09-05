"""Small cached Pillow overlay shared by the Metal and OpenGL renderers."""
from functools import lru_cache
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

IVORY = (244, 238, 219, 255)
MUTED = (192, 199, 190, 255)
ORANGE = (239, 153, 91, 255)
PANEL = (15, 25, 28, 185)


@lru_cache(maxsize=40)
def _font(size, bold=False):
    name = "Arial Bold.ttf" if bold else "Arial.ttf"
    candidates = [Path("/System/Library/Fonts/Supplemental") / name,
                  Path("/usr/share/fonts/truetype/dejavu") /
                  ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def _number(value, default=0.0):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (ValueError, TypeError):
        return default


@lru_cache(maxsize=384)
def _label(message, font, fill, anchor, stroke_width, stroke_fill):
    bounds = font.getbbox(message, anchor=anchor, stroke_width=stroke_width)
    x0, y0, x1, y1 = bounds
    stamp = Image.new("RGBA", (max(1, x1-x0), max(1, y1-y0)))
    ImageDraw.Draw(stamp).text((-x0, -y0), message, font=font, fill=fill,
                               anchor=anchor, stroke_width=stroke_width, stroke_fill=stroke_fill)
    return stamp, x0, y0


@lru_cache(maxsize=8)
def _base(width, height, help_visible, controller):
    image = Image.new("RGBA", (width, height))
    d = ImageDraw.Draw(image)
    scale = max(.45, min(width / 1280, height / 720))
    p = lambda v: round(v * scale)
    font = lambda n, bold=False: _font(max(8, p(n)), bold)
    margin = p(28)
    def panel(rect):
        d.rounded_rectangle(rect, radius=p(9), fill=PANEL)
    panel((margin, margin, margin+p(285), margin+p(79)))
    d.rectangle((margin, margin+p(13), margin+p(3), margin+p(37)), fill=ORANGE)
    d.text((margin+p(17), margin+p(12)), "R O A D S", font=font(22, True), fill=IVORY)
    d.text((margin+p(159), margin+p(18)), "NEW WAVE", font=font(10, True), fill=ORANGE)
    right = width-margin
    panel((right-p(180), height-margin-p(118), right, height-margin))
    d.text((right-p(19), height-margin-p(35)), "KM/H", font=font(11), fill=MUTED, anchor="ra")
    if help_visible:
        panel((margin, height-margin-p(88), margin+p(391), height-margin))
        if controller:
            first, second = "CONTROLLER CONNECTED", "WASD also available  /  H hide help"
        else:
            first, second = "W / S   throttle & brake / reverse", "A / D   steer     SPACE   handbrake"
        d.text((margin+p(15), height-margin-p(74)), first, font=font(12), fill=IVORY)
        d.text((margin+p(15), height-margin-p(51)), second, font=font(12), fill=IVORY)
        d.text((margin+p(15), height-margin-p(27)),
               "R recover   C camera   H help   P pause   ESC quit", font=font(11), fill=MUTED)
    else:
        d.text((margin, height-margin-p(17)), "H  CONTROLS   /   P  PAUSE   /   ESC  QUIT", font=font(10), fill=IVORY,
               stroke_width=1, stroke_fill=(15,25,28,170))
    return image


def render_hud(width, height, state, info):
    """Return an RGBA overlay; speed is signed m/s and distance is meters.

    Cache only static pixels/fonts, keeping dynamic values and caller objects out
    of caches. The caller may upload this image at 10 Hz independently of physics.
    """
    width, height = max(1, int(width)), max(1, int(height))
    image = _base(width, height, bool(info.get("help", True)),
                  bool(info.get("controller", False))).copy()
    d = ImageDraw.Draw(image)
    def text(position, message, font, fill, anchor=None, stroke_width=0, stroke_fill=None):
        stamp, x0, y0 = _label(message, font, fill, anchor, stroke_width, stroke_fill)
        image.alpha_composite(stamp, (round(position[0]+x0), round(position[1]+y0)))
    scale = max(.45, min(width / 1280, height / 720))
    p = lambda v: round(v * scale)
    font = lambda n, bold=False: _font(max(8, p(n)), bold)
    margin = p(28)
    right, bottom = width-margin, height-margin
    speed = _number(getattr(state, "speed", 0))
    heading = -_number(getattr(state, "yaw", 0)) * 180 / math.pi % 360
    direction = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")[int((heading+22.5)//45)%8]
    biome = str(info.get("biome", "Open country")).replace("_", " ").upper()[:26]
    text((margin+p(17), margin+p(49)), biome, font=font(11), fill=MUTED)
    text((right-p(17), bottom-p(108)), f"{abs(speed)*3.6:03.0f}",
           font=font(54, True), fill=IVORY, anchor="ra")
    gear = "R" if speed < -.3 else "D" if speed > .3 else "N"
    text((right-p(160), bottom-p(34)), gear, font=font(19, True), fill=ORANGE)
    d.line((right-p(122), bottom-p(21), right-p(74), bottom-p(21)), fill=(*MUTED[:3],90), width=1)
    text((right-p(117), bottom-p(38)), direction, font=font(10, True), fill=MUTED)
    trip = max(0, _number(getattr(state, "distance", 0))) / 1000
    trip_text = f"TRIP  {trip:06.2f} KM"
    text((width//2, bottom-p(20)), trip_text, anchor="ma", font=font(11), fill=IVORY,
           stroke_width=1, stroke_fill=(15,25,28,180))
    hour = _number(info.get("time", 16)) % 24
    minutes = int(hour*60)
    status = f"{minutes//60:02d}:{minutes%60:02d}   /   {str(info.get('quality', 'Balanced')).upper()}"
    text((right, margin+p(4)), status, anchor="ra", font=font(11), fill=IVORY,
           stroke_width=1, stroke_fill=(15,25,28,160))
    backend = str(info.get("backend", "GPU"))[:24]
    fps = max(0, _number(info.get("fps", 0)))
    text((right, margin+p(24)), f"{backend}  /  {fps:.0f} FPS", anchor="ra", font=font(10),
           fill=MUTED, stroke_width=1, stroke_fill=(15,25,28,160))
    notice = str(info.get("notice", "") or "")[:90]
    if "loading" in info and info["loading"] is not None:
        progress = min(100, max(0, _number(info["loading"])))
        notice = f"Preparing the road ahead  {progress:.0f}%"
    if notice:
        box_width = min(width-p(40), int(d.textlength(notice,font=font(13)))+p(36))
        d.rounded_rectangle(((width-box_width)//2, margin+p(18),
                             (width+box_width)//2, margin+p(58)), radius=p(8), fill=PANEL)
        text((width//2, margin+p(29)), notice, anchor="ma", font=font(13), fill=IVORY)
    if info.get("paused", False):
        cx, cy = width//2, height//2
        d.rounded_rectangle((cx-p(230),cy-p(135),cx+p(230),cy+p(135)),radius=p(14),fill=(12,23,27,241))
        text((cx,cy-p(106)), "A MOMENT OFF THE ROAD",font=font(11,True),fill=ORANGE,anchor="ma")
        text((cx,cy-p(73)), "Paused",font=font(37,True),fill=IVORY,anchor="ma")
        text((cx,cy-p(13)), "Explore freely. Your next road is yours.",font=font(13),fill=MUTED,anchor="ma")
        d.line((cx-p(165),cy+p(21),cx+p(165),cy+p(21)),fill=(81,91,91,160))
        text((cx,cy+p(41)), "P  RESUME     /     ESC  QUIT",font=font(13,True),fill=IVORY,anchor="ma")
        text((cx,cy+p(86)), f"WORLD SEED  {info.get('seed', 0)}",font=font(10),fill=MUTED,anchor="ma")
    return image
