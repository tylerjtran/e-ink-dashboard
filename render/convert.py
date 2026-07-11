"""Convert the rendered PNG into the raw 1-bit buffer the Pico's e-paper
driver expects.

The Waveshare Pico-ePaper-7.5 MicroPython driver (EPD_7in5) builds its frame
buffer with:
    framebuf.FrameBuffer(buf, 800, 480, framebuf.MONO_HLSB)
where a filled-white buffer is 0xFF and black pixels are written as 0x00 --
i.e. bit 1 = white, bit 0 = black, packed MSB-first, row-major, 800/8 = 100
bytes per row (no padding, since 800 is byte-aligned).

Pillow's Image.convert("1").tobytes() produces exactly this layout (verified
empirically: a run of white pixels followed by black packs as 1s then 0s,
MSB-first). So the Pico can fetch this file's bytes and copy them straight
into `epd.buffer_1Gray` with no on-device image decoding.

Usage:
    python convert.py [--in output/dashboard.png] [--out output/dashboard.bin]
"""
import argparse
from pathlib import Path

from PIL import Image

HERE = Path(__file__).parent
WIDTH, HEIGHT = 800, 480
EXPECTED_SIZE = WIDTH * HEIGHT // 8


def convert(png_path: Path, out_path: Path) -> None:
    img = Image.open(png_path).convert("L")
    if img.size != (WIDTH, HEIGHT):
        raise ValueError(f"expected {WIDTH}x{HEIGHT}, got {img.size}")

    # dither=Image.NONE: simple thresholding, not error-diffusion dithering --
    # the dashboard is high-contrast text/lines, so dithering just adds noise.
    bw = img.convert("1", dither=Image.NONE)
    data = bw.tobytes()

    if len(data) != EXPECTED_SIZE:
        raise ValueError(f"expected {EXPECTED_SIZE} bytes, got {len(data)}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="in_path", default=str(HERE / "output" / "dashboard.png"))
    parser.add_argument("--out", default=str(HERE / "output" / "dashboard.bin"))
    args = parser.parse_args()

    convert(Path(args.in_path), Path(args.out))
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
