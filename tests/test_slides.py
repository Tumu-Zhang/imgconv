"""幻灯片内容范围：形状并集、扩画幅决策、扩画幅副本。"""

from __future__ import annotations

import pytest
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Emu, Inches

from imgconv import slides


def make_deck(path, boxes, *, slide_in=(10.0, 7.5), pages=1):
    """造一份 PPTX：boxes 是 [(左, 上, 宽, 高)]（英寸），每页都放这批形状。"""
    prs = Presentation()
    prs.slide_width = Inches(slide_in[0])
    prs.slide_height = Inches(slide_in[1])
    layout = prs.slide_layouts[6]  # 全空版式
    for _ in range(pages):
        slide = prs.slides.add_slide(layout)
        for left, top, width, height in boxes:
            slide.shapes.add_shape(
                MSO_SHAPE.RECTANGLE,
                Inches(left), Inches(top), Inches(width), Inches(height),
            )
    prs.save(str(path))
    return path


class TestBounds:
    def test_content_inside_slide(self, tmp_path):
        src = make_deck(tmp_path / "in.pptx", [(1, 1, 3, 2)])
        (page,) = slides.bounds_from_pptx(src)
        assert page.shapes == 1
        assert page.left == pytest.approx(72)
        assert page.right == pytest.approx(4 * 72)
        assert page.overflows is False

    def test_negative_offset_is_overflow(self, tmp_path):
        """形状跑到画幅左边外面：这是"自己没察觉到"的典型情形。"""
        src = make_deck(tmp_path / "out.pptx", [(-1.2, 5.6, 4, 3)])
        (page,) = slides.bounds_from_pptx(src)
        assert page.left < 0
        assert page.overflows is True

    def test_floating_point_noise_is_not_overflow(self, tmp_path):
        """画幅边缘的形状不该因为浮点误差被判成溢出。"""
        src = make_deck(tmp_path / "edge.pptx", [(0, 0, 10, 7.5)])
        (page,) = slides.bounds_from_pptx(src)
        assert page.overflows is False

    def test_empty_slide_has_no_shapes(self, tmp_path):
        prs = Presentation()
        prs.slides.add_slide(prs.slide_layouts[6])
        path = tmp_path / "empty.pptx"
        prs.save(str(path))
        (page,) = slides.bounds_from_pptx(path)
        assert page.shapes == 0


class TestPlanExpansion:
    def test_none_when_content_fits(self, tmp_path):
        src = make_deck(tmp_path / "in.pptx", [(1, 1, 3, 2)])
        assert slides.plan_expansion(slides.bounds_from_pptx(src)) is None

    def test_none_when_no_shapes(self, tmp_path):
        prs = Presentation()
        prs.slides.add_slide(prs.slide_layouts[6])
        path = tmp_path / "empty.pptx"
        prs.save(str(path))
        assert slides.plan_expansion(slides.bounds_from_pptx(path)) is None

    def test_expands_to_cover_overflow_plus_margin(self, tmp_path):
        src = make_deck(tmp_path / "out.pptx", [(-1.2, 5.6, 4, 3)])
        plan = slides.plan_expansion(slides.bounds_from_pptx(src), margin_pt=10)
        assert plan is not None
        # 左边要盖住 -1.2in 再留 10pt 余量
        assert plan.left == pytest.approx(-1.2 * 72 - 10)
        # 下边要盖住 5.6+3=8.6in 再留余量
        assert plan.bottom == pytest.approx(8.6 * 72 + 10)
        # 画幅内的方向不收紧：右边仍是原画幅 + 余量
        assert plan.right == pytest.approx(10 * 72 + 10)
        assert "超出幻灯片边界" in plan.note

    def test_clamped_to_powerpoint_limit(self, tmp_path):
        """内容离谱地大时夹到 56 英寸上限，并在说明里讲清"仍可能被裁"。"""
        src = make_deck(tmp_path / "huge.pptx", [(0, 0, 200, 300)])
        plan = slides.plan_expansion(slides.bounds_from_pptx(src))
        assert plan is not None
        assert plan.width <= slides.MAX_SLIDE_PT + 0.01
        assert plan.height <= slides.MAX_SLIDE_PT + 0.01
        assert "56 英寸" in plan.note

    def test_multi_page_union(self, tmp_path):
        src = make_deck(tmp_path / "two.pptx", [(-2, 0, 3, 2)], pages=2)
        plan = slides.plan_expansion(slides.bounds_from_pptx(src))
        assert plan is not None
        assert plan.left <= -2 * 72


class TestExpandCopy:
    def test_moves_shapes_and_resizes_canvas(self, tmp_path):
        src = make_deck(tmp_path / "out.pptx", [(-1.2, 5.6, 4, 3)])
        plan = slides.plan_expansion(slides.bounds_from_pptx(src))
        assert plan is not None

        dst = tmp_path / "expanded.pptx"
        slides.expand_copy(src, dst, plan)

        prs = Presentation(str(dst))
        assert prs.slide_width == pytest.approx(int(plan.width * slides.EMU_PER_POINT), abs=2)
        assert prs.slide_height == pytest.approx(int(plan.height * slides.EMU_PER_POINT), abs=2)
        shape = prs.slides[0].shapes[0]
        # 平移后左上角 = 原坐标 - plan.left
        assert shape.left == pytest.approx(
            int((-1.2 * 72 - plan.left) * slides.EMU_PER_POINT), abs=2
        )
        assert shape.top == pytest.approx(
            int((5.6 * 72 - plan.top) * slides.EMU_PER_POINT), abs=2
        )

    def test_source_file_is_untouched(self, tmp_path):
        src = make_deck(tmp_path / "out.pptx", [(-1.2, 5.6, 4, 3)])
        before = src.read_bytes()
        plan = slides.plan_expansion(slides.bounds_from_pptx(src))
        slides.expand_copy(src, tmp_path / "expanded.pptx", plan)
        assert src.read_bytes() == before
        assert Presentation(str(src)).slide_width == Emu(int(Inches(10)))


class TestConverterCanvasHook:
    """管线侧：.pptx 走纯 Python 读范围，不需要 PowerPoint 会话。"""

    def test_pptx_plans_expansion_without_office(self, tmp_path):
        from imgconv.model import OutputSpec
        from imgconv.pipeline import Converter

        src = make_deck(tmp_path / "out.pptx", [(-1.2, 5.6, 4, 3)])
        converter = Converter(OutputSpec(output_dir=tmp_path))
        plan = converter._content_canvas(None, src)  # 传 None：.pptx 不该用到它
        assert plan is not None
        assert "超出幻灯片边界" in plan.note

    def test_inside_content_needs_no_expansion(self, tmp_path):
        from imgconv.model import OutputSpec
        from imgconv.pipeline import Converter

        src = make_deck(tmp_path / "in.pptx", [(1, 1, 3, 2)])
        converter = Converter(OutputSpec(output_dir=tmp_path))
        assert converter._content_canvas(None, src) is None

    def test_broken_file_falls_back_silently(self, tmp_path):
        """读不出来就按原画幅走，绝不能因此中断转换。"""
        from imgconv.model import OutputSpec
        from imgconv.pipeline import Converter

        broken = tmp_path / "broken.pptx"
        broken.write_bytes(b"not a pptx")
        converter = Converter(OutputSpec(output_dir=tmp_path))
        assert converter._content_canvas(None, broken) is None
