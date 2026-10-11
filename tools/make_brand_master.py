"""生成品牌图标母版模块 ``gui/brand_master.py``。

以设计定稿位图为唯一视觉基准（image-converter-logo/
imgspec-logo-A-fontfix.png，1024×1024 满幅圆角方）：加圆角 alpha
蒙版（底板圆角 180/1024，角外透明，符合图标惯例），降采样到 512px，
再以 base64 内嵌进生成文件。运行时 ``gui/theme.icon_pixmap`` 只做
一次解码 + 平滑缩放，窗口图标、顶栏标记、关于页大图与 exe 文件
图标（tools/make_icon.py）全部同源，保证与设计稿零差异。

与 ``make_brand_paths.py`` 同一生成模式：产出是生成文件，勿手改；
重设计时换定稿图重跑本脚本即可。

用法：
    python tools/make_brand_master.py [定稿PNG路径]
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT.parent / "image-converter-logo" / "imgspec-logo-A-fontfix.png"
TARGET = ROOT / "gui" / "brand_master.py"

CANVAS = 1024   # 定稿视箱边长
TILE_RX = 180   # 底板圆角半径（与矢量复刻稿一致）
MASTER_SIZE = 512  # 内嵌尺寸：运行时最大用到 256px，2x 超采样保证缩小质量

HEADER = '"""品牌图标母版（生成文件，勿手改）。\n\n由 tools/make_brand_master.py 从设计定稿位图生成：圆角蒙版 +\n{size}px 内嵌。重设计时重跑该脚本，不要直接编辑本文件。\n"""\n\nMASTER_SIZE = {size}\n'


def build_master(source: Path) -> Image.Image:
    """定稿位图 → 圆角蒙版 → 512px 母版。"""
    image = Image.open(source).convert("RGBA")
    if image.size != (CANVAS, CANVAS):
        raise SystemExit(f"定稿应为 {CANVAS}×{CANVAS}，实际 {image.size}：{source}")

    mask = Image.new("L", image.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, CANVAS - 1, CANVAS - 1), radius=TILE_RX, fill=255)

    master = Image.new("RGBA", image.size, (0, 0, 0, 0))
    master.paste(image, (0, 0), mask)
    return master.resize((MASTER_SIZE, MASTER_SIZE), Image.LANCZOS)


def main() -> int:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    if not source.exists():
        raise SystemExit(f"找不到定稿位图：{source}")

    master = build_master(source)
    import io

    buffer = io.BytesIO()
    master.save(buffer, "PNG", optimize=True)
    png = buffer.getvalue()

    b64 = base64.b64encode(png).decode("ascii")
    lines = [b64[i : i + 100] for i in range(0, len(b64), 100)]

    TARGET.write_text(
        HEADER.format(size=MASTER_SIZE)
        + "MASTER_B64 = (\n" + "".join(f'    "{line}"\n' for line in lines) + ")\n",
        encoding="utf-8",
    )
    print(
        f"已生成 {TARGET}（源 {source.name} → {MASTER_SIZE}px，"
        f"内嵌 PNG {len(png)} bytes）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
