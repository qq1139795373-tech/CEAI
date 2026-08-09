"""Generate CEAI.ico using only the standard library (no Pillow).

Builds a 256x256 PNG (RGBA) and wraps it in an ICO container.
Windows Vista+ supports PNG-compressed images inside .ico files.
"""
import struct
import zlib

W = H = 256


def rounded_rect_mask(x, y, x0, y0, x1, y1, r):
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    if x < x0 + r and y < y0 + r:
        return (x - (x0 + r)) ** 2 + (y - (y0 + r)) ** 2 <= r * r
    if x > x1 - r and y < y0 + r:
        return (x - (x1 - r)) ** 2 + (y - (y0 + r)) ** 2 <= r * r
    if x < x0 + r and y > y1 - r:
        return (x - (x0 + r)) ** 2 + (y - (y1 - r)) ** 2 <= r * r
    if x > x1 - r and y > y1 - r:
        return (x - (x1 - r)) ** 2 + (y - (y1 - r)) ** 2 <= r * r
    return True


def ring(x, y, cx, cy, outer, inner):
    d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
    if inner <= d <= outer:
        return 1.0
    if outer < d < outer + 2:
        return max(0.0, 1 - (d - outer) / 2)
    if inner - 2 < d < inner:
        return max(0.0, 1 - (inner - d) / 2)
    return 0.0


def handle(x, y):
    # 放大镜手柄:从镜头右下边缘延伸到角落
    hx0, hy0, hx1, hy1 = 172, 172, 224, 224
    # 到线段的最短距离
    vx, vy = hx1 - hx0, hy1 - hy0
    t = max(0.0, min(1.0, ((x - hx0) * vx + (y - hy0) * vy) / (vx * vx + vy * vy)))
    px, py = hx0 + t * vx, hy0 + t * vy
    d = ((x - px) ** 2 + (y - py) ** 2) ** 0.5
    if d <= 12:
        return 1.0
    if d <= 14:
        return max(0.0, 1 - (d - 12) / 2)
    return 0.0


def pixel(x, y):
    # 蓝色圆角背景
    inside = rounded_rect_mask(x + 0.5, y + 0.5, 8, 8, 247, 247, 40)
    top = (36, 160, 255)   # #24a0ff
    bottom = (0, 80, 190)  # #0050be
    t = (y + 0.5) / H
    bg = tuple(int(a * (1 - t) + b * t) for a, b in zip(top, bottom))

    # 放大镜
    lens = ring(x + 0.5, y + 0.5, 116, 108, 82, 62)
    hdl = handle(x + 0.5, y + 0.5)

    white = (255, 255, 255)
    lens_rgb = tuple(int(c + (w - c) * lens) for c, w in zip(bg, white))
    out = tuple(int(c + (w - c) * hdl) for c, w in zip(lens_rgb, white))

    alpha = 255 if inside else 0
    return (out[0], out[1], out[2], alpha)


def build_png(size):
    rows = []
    for y in range(size):
        row = bytearray([0])  # filter: None
        for x in range(size):
            # 采样放大到 256x256 的设计图
            sx = x * W // size
            sy = y * H // size
            r, g, b, a = pixel(sx, sy)
            row += bytes((r, g, b, a))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    idat = zlib.compress(raw, 9)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def build_ico(png_bytes):
    # ICONDIR
    ico = struct.pack("<HHH", 0, 1, 1)
    # ICONDIRENTRY (width/height 0 == 256)
    ico += struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png_bytes), 22)
    ico += png_bytes
    return ico


def main():
    png = build_png(256)
    ico = build_ico(png)
    with open("CEAI.ico", "wb") as f:
        f.write(ico)
    print("CEAI.ico written, %d bytes" % len(ico))


if __name__ == "__main__":
    main()
