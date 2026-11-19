"""幻灯片「画了多少东西」的真实范围：比画幅大，还是小。

为什么需要它
------------
PowerPoint 导出 PDF 时**只导出幻灯片画幅**：画幅之外的部分直接被裁掉，
一个字都不留。所以「先整页导出成图，再裁白边」这条路永远救不回溢出画幅的
内容 —— 那部分在 PDF 里根本不存在。

要拿到真实范围只能回到矢量层：读每个形状的坐标（EMU / 点）。这里取
**顶层形状的并集**（组合形状自身的 xfrm 已覆盖其子形状，不必递归）。

只扩不缩
--------
内容比画幅小时**什么都不做**：母版/版式上的装饰元素（页码、Logo、页脚线）
不在 `slide.shapes` 里，缩画幅会把它们无声地裁掉。这类"画幅内的白边"交给
视觉裁边（render.page_content_rect）处理，那里看得到所有被画出来的东西。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

EMU_PER_POINT = 12700

# 展开画幅时向外多留的余量（点）。阴影、发光、粗描边都会超出形状框，多留
# 一点由后续的视觉裁边收回来，不会浪费输出 —— 但能避免把阴影切掉半截。
EXPAND_MARGIN_PT = 12.0

# PowerPoint 的画幅限制：1 英寸 ~ 56 英寸
MIN_SLIDE_PT = 72.0
MAX_SLIDE_PT = 56 * 72.0

# 判定"算不算超出画幅"的容差（点）。浮点换算误差不该被当成溢出。
OVERFLOW_TOLERANCE_PT = 0.5


@dataclass(frozen=True)
class ContentBounds:
    """一页幻灯片上所有（有坐标的）形状的并集，单位：点。"""

    left: float
    top: float
    right: float
    bottom: float
    slide_width: float
    slide_height: float
    shapes: int = 0
    # 有形状缺显式坐标（占位符继承版式位置等），范围可能偏小
    incomplete: bool = False

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def overflows(self) -> bool:
        tol = OVERFLOW_TOLERANCE_PT
        return (
            self.left < -tol
            or self.top < -tol
            or self.right > self.slide_width + tol
            or self.bottom > self.slide_height + tol
        )

    def describe(self) -> str:
        return (
            f"{self.width / 72:.2f} x {self.height / 72:.2f} in"
            f"（{self.shapes} 个形状）"
        )


@dataclass(frozen=True)
class ExpandPlan:
    """把画幅扩到这块区域（幻灯片坐标系，点）。"""

    left: float
    top: float
    right: float
    bottom: float
    note: str

    @property
    def rect(self) -> tuple[float, float, float, float]:
        return (self.left, self.top, self.right, self.bottom)

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top


def bounds_from_pptx(path: Path | str) -> list[ContentBounds]:
    """用 python-pptx 读出每页形状并集（不需要 PowerPoint，快）。"""
    from pptx import Presentation

    prs = Presentation(str(path))
    slide_w = float(prs.slide_width) / EMU_PER_POINT
    slide_h = float(prs.slide_height) / EMU_PER_POINT

    pages: list[ContentBounds] = []
    for slide in prs.slides:
        lefts: list[float] = []
        tops: list[float] = []
        rights: list[float] = []
        bottoms: list[float] = []
        skipped = 0
        for shape in slide.shapes:
            try:
                left, top, width, height = (
                    shape.left, shape.top, shape.width, shape.height
                )
            except Exception:  # noqa: BLE001 - 个别形状没有几何信息
                skipped += 1
                continue
            if None in (left, top, width, height):
                skipped += 1
                continue
            lefts.append(float(left) / EMU_PER_POINT)
            tops.append(float(top) / EMU_PER_POINT)
            rights.append((float(left) + float(width)) / EMU_PER_POINT)
            bottoms.append((float(top) + float(height)) / EMU_PER_POINT)

        pages.append(
            ContentBounds(
                left=min(lefts) if lefts else 0.0,
                top=min(tops) if tops else 0.0,
                right=max(rights) if rights else slide_w,
                bottom=max(bottoms) if bottoms else slide_h,
                slide_width=slide_w,
                slide_height=slide_h,
                shapes=len(lefts),
                incomplete=skipped > 0,
            )
        )
    return pages


def plan_expansion(
    pages: list[ContentBounds],
    *,
    margin_pt: float = EXPAND_MARGIN_PT,
    limits: tuple[float, float] = (MIN_SLIDE_PT, MAX_SLIDE_PT),
) -> ExpandPlan | None:
    """决定要不要扩画幅。

    返回 None 表示"内容都在画幅内，什么都不用做"（最安全的分支）。
    任何一页溢出就扩：画幅 = 所有页内容并集 + margin，尺寸夹在 PowerPoint
    允许的范围内；夹到上限时会说明"仍可能被裁"。
    """
    pages = [page for page in pages if page.shapes]
    if not pages:
        return None
    if not any(page.overflows for page in pages):
        return None

    left = min(page.left for page in pages)
    top = min(page.top for page in pages)
    right = max(page.right for page in pages)
    bottom = max(page.bottom for page in pages)
    # 溢出即扩：向画幅外延伸的那几边要包住内容，画幅内的边不再收紧
    left = min(left, 0.0) - margin_pt
    top = min(top, 0.0) - margin_pt
    right = max(right, pages[0].slide_width) + margin_pt
    bottom = max(bottom, pages[0].slide_height) + margin_pt

    low, high = limits
    clipped: list[str] = []
    if right - left > high:
        right = left + high
        clipped.append("宽")
    if bottom - top > high:
        bottom = top + high
        clipped.append("高")
    if right - left < low:
        right = left + low
    if bottom - top < low:
        bottom = top + low

    note = (
        f"有图形超出幻灯片边界，已把画幅扩到 "
        f"{(right - left) / 72:.2f} x {(bottom - top) / 72:.2f} in 后导出"
    )
    if clipped:
        note += (
            f"（超出 PowerPoint 的 56 英寸上限，{'/'.join(clipped)}方向已被夹住，"
            "这方向的内容仍可能被裁）"
        )
    if any(page.incomplete for page in pages):
        note += "；另有形状没有显式坐标，其范围只能按已知形状估计"
    return ExpandPlan(left=left, top=top, right=right, bottom=bottom, note=note)


def expand_copy(
    src: Path | str, dst: Path | str, plan: ExpandPlan
) -> tuple[Path, int, int]:
    """按 plan 扩画幅 + 平移所有形状，另存为 dst。返回（目标路径, 移动数, 跳过数）。

    为什么改 XML 而不是用 COM 改
    ---------------------------
    PowerPoint 在改画幅时会**自动把内容居中**（实测整体位移正好是画幅差的一半），
    于是"把每个形状左移多少"这类增量改法会被它带偏，原本在图外的内容反而被推到
    更外面。直接改 XML 是确定性的：新画幅 + 绝对坐标，一步到位、不依赖 Office。

    只动顶层形状：组合形状挪动后组内子形状自然跟随。
    """
    from pptx import Presentation
    from pptx.util import Emu

    src, dst = Path(src), Path(dst)
    prs = Presentation(str(src))

    dx = -int(round(plan.left * EMU_PER_POINT))
    dy = -int(round(plan.top * EMU_PER_POINT))
    prs.slide_width = Emu(int(round(plan.width * EMU_PER_POINT)))
    prs.slide_height = Emu(int(round(plan.height * EMU_PER_POINT)))

    moved = 0
    skipped = 0
    for slide in prs.slides:
        for shape in slide.shapes:
            left, top = shape.left, shape.top
            if left is None or top is None:  # 位置继承自版式，无权改动
                skipped += 1
                continue
            try:
                shape.left = Emu(int(left) + dx)
                shape.top = Emu(int(top) + dy)
                moved += 1
            except Exception:  # noqa: BLE001 - 个别形状的 xfrm 不可写
                skipped += 1

    dst.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(dst))
    return dst, moved, skipped


__all__ = [
    "EMU_PER_POINT",
    "EXPAND_MARGIN_PT",
    "MAX_SLIDE_PT",
    "MIN_SLIDE_PT",
    "ContentBounds",
    "ExpandPlan",
    "OVERFLOW_TOLERANCE_PT",
    "bounds_from_pptx",
    "expand_copy",
    "plan_expansion",
]
