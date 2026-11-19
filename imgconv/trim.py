"""自动裁边：只保留主体内容，把四周的空白背景裁掉。

为什么需要它
------------
投稿图最怕"一张画布里内容只占一半"：空白边跟着画布一起被算进物理尺寸，
真正的内容被压小；等到后面有人手工裁剪再放大，等于先丢分辨率再插值放大，
既费人工又降画质。这里在渲染阶段就把包围盒算出来，让目标 DPI 全部落在
内容上。

判据
----
不是"白色"而是"与边缘主色相同"：边缘取样（四周各 EDGE_BAND 像素宽的边带）
取中位数作为背景色，因此深色 PPT 模板、米色扫描件这些非白底色一样能裁。
中位数对"只有一条边有彩色横幅"这种版面是稳健的 —— 横幅占不满周长的一半。

为什么用低分辨率探测、再到矢量层裁剪
------------------------------------
先按 PROBE_DPI 光栅化一页找包围盒，换算回 PDF 坐标（点）后用 MuPDF 的
clip 只渲染那一块 —— 于是 1200 dpi 的像素全部落在内容上，而不是先按整页
渲染再裁掉（那样白边也白白吃掉了内存与时间）。

安全的取向
----------
失败模式不对称：切掉一根笔画是**不可挽回**的（只能回源文件重做），而多留
0.085 mm 白边没人看得出来。所以这里一律"向外让"：探测框本身包含所有有差异
的像素，最终还会再向外让 SAFETY_PAD 个探测像素，渲染时裁切框也对齐到设备
像素网格的**外侧**。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

# 单通道允许的背景色抖动（JPEG 压缩、渐变、抗锯齿都会让"同一种白"略有差异）
DEFAULT_TOLERANCE = 10
# 取边缘色用的边带宽度（像素）
EDGE_BAND = 2
# 探测分辨率上限：再高只是徒增耗时，包围盒精度到 1/300 英寸（约 0.085 mm）足够
PROBE_DPI = 300
# 裁完剩下的面积不足原图这个比例，视为"整页只有孤立噪点"，放弃裁剪
MIN_KEEP_RATIO = 0.02
# 一段"有内容"的连续像素至少要这么厚才算数（探测像素）。
# 定成 1 = 只要有一行/列与底色不同就算内容：宁可少裁，也不能把一条细线裁掉。
# 页边的假边由 EDGE_IGNORE 负责，不靠这里过滤。
MIN_CONTENT_RUN = 1
# 裁完再向外让出的安全余量（探测像素）。1 个探测像素在 300 dpi 下约 0.085 mm：
# 肉眼看不见，但足以吸收阈值边界上的抗锯齿像素 —— 用户明确要求
# 「宁可留一点点白边，也不要裁到线条」。
SAFETY_PAD = 1
# 探测时先忽略最外面这几像素。光栅化把「铺满整页的底色矩形」压在页面边界上时，
# 最后一行/列只会被覆盖一部分，于是留下一条颜色不同的缝（不是内容）。不忽略
# 它，任何深色满版模板都会被判成「内容顶到四边」，永远裁不掉白边。
EDGE_IGNORE = 2
# 逐块处理的块高，控制峰值内存（一行 3 通道 int16 的块，而不是整图）
_CHUNK_ROWS = 512


@dataclass(frozen=True)
class TrimBox:
    """内容包围盒（像素，右/下为开区间）。"""

    left: int
    top: int
    right: int
    bottom: int
    size: tuple[int, int]
    border: tuple[int, int, int]

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.right, self.bottom)

    @property
    def area_ratio(self) -> float:
        total = self.size[0] * self.size[1]
        if total <= 0:
            return 0.0
        return (self.width * self.height) / total

    def describe(self) -> str:
        return (
            f"裁去四周空白：{self.size[0]} x {self.size[1]} px → "
            f"{self.width} x {self.height} px"
        )

    def padded(self, pad: int = SAFETY_PAD) -> "TrimBox":
        """向外让出 pad 像素（安全余量），不超出原图边界。

        「宁可留一点点白边，也不要裁到线条」是用户的明确取舍，所以默认
        由调用方再向外让出一个探测像素 —— 那个宽度肉眼看不见（300 dpi 下
        0.085 mm），却能把阈值边界上的抗锯齿像素留在框内。
        """
        if pad <= 0:
            return self
        width, height = self.size
        return TrimBox(
            left=max(0, self.left - pad),
            top=max(0, self.top - pad),
            right=min(width, self.right + pad),
            bottom=min(height, self.bottom + pad),
            size=self.size,
            border=self.border,
        )


def border_color(array: np.ndarray) -> np.ndarray:
    """四周边缘像素的中位数颜色（浮点，形状 (3,)）。"""
    height, width = array.shape[:2]
    band = max(1, min(EDGE_BAND, height // 2, width // 2))
    edges = np.concatenate(
        [
            array[:band].reshape(-1, array.shape[2]),
            array[-band:].reshape(-1, array.shape[2]),
            array[:, :band].reshape(-1, array.shape[2]),
            array[:, -band:].reshape(-1, array.shape[2]),
        ]
    )
    return np.median(edges.astype(np.float32), axis=0)


def _runs(flags: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """把布尔数组切成连续 True 段，返回（起点, 终点[开区间]）。"""
    padded = np.concatenate(([False], flags, [False]))
    diff = np.diff(padded.astype(np.int8))
    return np.flatnonzero(diff == 1), np.flatnonzero(diff == -1)


def _extent(flags: np.ndarray, min_run: int = MIN_CONTENT_RUN) -> tuple[int, int] | None:
    """内容在某个方向上的范围 [first, last+1)；没有够厚的连续段返回 None。

    只认「连续 min_run 像素以上」的段，是为了把光栅化取整留下的 1px 边缘缝
    滤掉 —— 否则一张"满页底色 + 居中内容"的深色幻灯片会被那条缝判成
    "内容顶到边"，白边一辈子裁不掉。
    """
    starts, ends = _runs(flags)
    if starts.size == 0:
        return None
    keep = (ends - starts) >= min_run
    if not keep.any():
        return None
    return int(starts[keep][0]), int(ends[keep][-1])


def detect(image: Image.Image, *, tolerance: int = DEFAULT_TOLERANCE) -> TrimBox | None:
    """找出内容包围盒。没有可裁的边（或结果可疑）时返回 None。

    返回的框已经保证「不裁掉任何内容」：边界像素只要与背景色有明显差异就会被
    算进内容；调用方可以按需再向外留一点安全余量。
    """
    if image.width < 2 * EDGE_IGNORE + 4 or image.height < 2 * EDGE_IGNORE + 4:
        return None

    array = np.asarray(image.convert("RGB"))
    height, width = array.shape[:2]
    # 只看内圈：外圈几像素可能是光栅化取整留下的假边（见 EDGE_IGNORE）
    inner = array[EDGE_IGNORE : height - EDGE_IGNORE, EDGE_IGNORE : width - EDGE_IGNORE]
    inner_h, inner_w = inner.shape[:2]
    background = border_color(inner).astype(np.int16)

    row_flags = np.zeros(inner_h, dtype=bool)
    col_flags = np.zeros(inner_w, dtype=bool)
    for start in range(0, inner_h, _CHUNK_ROWS):
        block = inner[start : start + _CHUNK_ROWS]
        diff = np.abs(block.astype(np.int16) - background).max(axis=2) > tolerance
        row_flags[start : start + block.shape[0]] = diff.any(axis=1)
        # 列标记按位累加：任意一行有内容，这一列就算有内容
        col_flags |= diff.any(axis=0)

    vertical = _extent(row_flags)
    horizontal = _extent(col_flags)
    if vertical is None or horizontal is None:
        return None

    top, bottom = vertical[0] + EDGE_IGNORE, vertical[1] + EDGE_IGNORE
    left, right = horizontal[0] + EDGE_IGNORE, horizontal[1] + EDGE_IGNORE
    # 内容贴着内圈起点 = 可能一直延伸到页边：这一边视为不可裁，避免切进笔画
    left = 0 if left <= EDGE_IGNORE else left
    top = 0 if top <= EDGE_IGNORE else top
    right = width if right >= width - EDGE_IGNORE else right
    bottom = height if bottom >= height - EDGE_IGNORE else bottom

    box = TrimBox(
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        size=(width, height),
        border=tuple(int(round(v)) for v in background),
    )
    if (box.width, box.height) == (width, height):
        return None
    if box.area_ratio < MIN_KEEP_RATIO:
        return None
    return box


def crop(image: Image.Image, box: TrimBox) -> Image.Image:
    """按包围盒裁剪（PIL 的 crop 是左闭右开，与 TrimBox 一致）。"""
    return image.crop(box.box)


__all__ = [
    "DEFAULT_TOLERANCE",
    "EDGE_BAND",
    "MIN_KEEP_RATIO",
    "PROBE_DPI",
    "SAFETY_PAD",
    "TrimBox",
    "border_color",
    "crop",
    "detect",
]
