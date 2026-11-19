"""自动裁白边：探测算法与管线集成。"""

from __future__ import annotations

import fitz
import numpy as np
import pytest
from PIL import Image, ImageDraw

from conftest import read_size
from imgconv import trim
from imgconv.model import OutputFormat, OutputSpec, Unit
from imgconv.pipeline import convert
from imgconv.render import PdfPageRenderer


def _white_band(image: Image.Image, *, axis: str, reverse: bool, threshold=250) -> int:
    """从某一侧数连续"整行/整列都是纯白"的条数 = 白边厚度。"""
    array = np.asarray(image)
    lines = array if axis == "rows" else array.T
    if reverse:
        lines = lines[::-1]
    thickness = 0
    for line in lines:
        if line.min() >= threshold:
            thickness += 1
        else:
            break
    return thickness


def spec(out_dir, **kwargs) -> OutputSpec:
    params = dict(
        fmt=OutputFormat.PNG,
        phys_unit=Unit.CM,
        phys_width=10.0,
        dpi=150,
        output_dir=out_dir,
    )
    params.update(kwargs)
    return OutputSpec(**params)


def boxed_image(
    size=(400, 300),
    box=(100, 60, 300, 240),
    *,
    background=(255, 255, 255),
    ink=(0, 0, 0),
) -> Image.Image:
    image = Image.new("RGB", size, background)
    ImageDraw.Draw(image).rectangle(box, fill=ink)
    return image


def make_page_pdf(path, *, page_pt=(595, 842), box=(100, 200, 400, 400), fill=(0, 0, 0)) -> None:
    doc = fitz.open()
    page = doc.new_page(width=page_pt[0], height=page_pt[1])
    page.draw_rect(fitz.Rect(*box), color=None, fill=fill)
    doc.save(str(path))
    doc.close()


class TestDetect:
    def test_white_margin_is_trimmed(self):
        result = trim.detect(boxed_image())
        assert result is not None
        # 允许几个像素的出入：探测的是"内容存在与否"，不是逐像素对齐
        assert result.box == pytest.approx((100, 60, 300, 240), abs=2)
        assert result.width == result.right - result.left

    def test_full_bleed_needs_no_trim(self):
        assert trim.detect(boxed_image(box=(0, 0, 400, 300))) is None

    def test_blank_image_needs_no_trim(self):
        assert trim.detect(Image.new("RGB", (400, 300), (255, 255, 255))) is None

    def test_dark_background_is_trimmed_too(self):
        """深色模板：判据是"与边缘主色相同"，不是"白色"。"""
        result = trim.detect(boxed_image(background=(20, 20, 26), ink=(240, 240, 240)))
        assert result is not None
        assert result.border == (20, 20, 26)

    def test_lone_speck_is_not_content(self):
        """整页只有几个孤立噪点时不该把输出裁成一个小点。"""
        image = Image.new("RGB", (400, 300), (255, 255, 255))
        ImageDraw.Draw(image).point((200, 150), fill=(0, 0, 0))
        assert trim.detect(image) is None

    def test_thin_edge_artifact_is_ignored(self):
        """满版底色矩形光栅化后会在页边留一条异色缝：那不是内容。

        回归用例：曾经因为这条缝，所有深色满版页面都被判成「内容顶到四边」，
        白边永远裁不掉。
        """
        image = boxed_image(background=(20, 20, 26), ink=(240, 240, 240))
        draw = ImageDraw.Draw(image)
        draw.line([(0, 0), (image.width - 1, 0)], fill=(255, 255, 255))
        draw.line([(image.width - 1, 0), (image.width - 1, image.height - 1)],
                  fill=(255, 255, 255))
        result = trim.detect(image)
        assert result is not None
        assert result.box == pytest.approx((100, 60, 300, 240), abs=3)

    def test_color_banner_on_one_edge_keeps_margin_detection(self):
        """只有一条边有彩色横幅时，边缘中位数仍应认出真正的底色。"""
        image = boxed_image()
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, image.width - 1, 24), fill=(91, 140, 255))
        result = trim.detect(image)
        assert result is not None
        assert result.border == (255, 255, 255)
        assert result.top <= 24

    def test_crop_matches_box(self):
        image = boxed_image()
        result = trim.detect(image)
        assert result is not None
        cropped = trim.crop(image, result)
        assert cropped.size == (result.width, result.height)

    def test_padded_box_never_exceeds_image(self):
        """安全余量向外让，但不能让出画布外。"""
        result = trim.detect(boxed_image(box=(5, 5, 395, 295)))
        assert result is not None
        padded = result.padded(4)
        assert padded.width > result.width and padded.height > result.height
        left, top, right, bottom = padded.box
        assert (left, top) >= (0, 0)
        assert (right, bottom) <= (400, 300)

    def test_one_pixel_wide_rule_is_kept(self):
        """1 像素宽的线也算内容（MIN_CONTENT_RUN=1），不能被"太薄"过滤掉。

        细到这种程度的特征一旦被过滤，就等于把它从输出里切掉了 —— 宁可不裁。
        """
        image = Image.new("RGB", (40, 300), (255, 255, 255))
        ImageDraw.Draw(image).line([(10, 0), (10, 299)], fill=(0, 0, 0), width=1)

        box = trim.detect(image)
        assert box is not None
        assert box.width <= 3, "包围盒应该只包住这条 1px 线"

    def test_extent_keeps_single_row_band(self):
        """_extent 的过滤阈值是 1 行/列：不存在"太薄就丢掉"的行为。"""
        flags = np.array([False, False, True, False, False])
        assert trim._extent(flags) == (2, 3)


class TestPageContentRect:
    def test_rect_follows_content_box(self, tmp_path):
        pdf = tmp_path / "page.pdf"
        make_page_pdf(pdf, box=(100, 200, 400, 400))
        with PdfPageRenderer(pdf) as doc:
            rect, ratio = doc.page_content_rect(0)
        assert rect is not None
        assert rect.width == pytest.approx(300, abs=3)
        assert rect.height == pytest.approx(200, abs=3)
        assert 0 < ratio < 0.2

    def test_rect_is_none_for_full_page_content(self, tmp_path):
        pdf = tmp_path / "full.pdf"
        make_page_pdf(pdf, box=(0, 0, 595, 842))
        with PdfPageRenderer(pdf) as doc:
            rect, ratio = doc.page_content_rect(0)
        assert rect is None
        assert ratio == 1.0

    def test_rule_only_page_is_left_uncropped(self, tmp_path):
        """整页只有一根细线：面积占比过小 → 放弃裁剪。

        放弃裁剪是**安全方向**（输出整页，线一定还在）；只有"裁了却裁掉内容"
        才是事故。
        """
        pdf = tmp_path / "rule.pdf"
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.draw_line(fitz.Point(50, 400), fitz.Point(545, 400),
                       color=(0, 0, 0), width=0.5)
        doc.save(str(pdf))
        doc.close()

        with PdfPageRenderer(pdf) as renderer:
            rect, ratio = renderer.page_content_rect(0)
        assert rect is None
        assert ratio == 1.0


class TestPipelineIntegration:
    def test_pdf_output_keeps_content_aspect(self, tmp_path, workdir):
        """裁边后输出的宽高比 = 内容的宽高比，而不是纸张的宽高比。"""
        pdf = tmp_path / "page.pdf"
        make_page_pdf(pdf, box=(100, 200, 400, 400))  # 300 x 200 pt -> 1.5

        report = convert([pdf], spec(workdir, trim_margin=True))
        assert report.success, [r.error for r in report.results]
        width, height = read_size(report.results[0].out_path)
        assert width == pytest.approx(591, abs=2)
        assert height / width == pytest.approx(1 / 1.5, abs=0.02)
        assert any("已裁去四周空白" in w for w in report.results[0].warnings)

    def test_without_trim_keeps_page_aspect(self, tmp_path, workdir):
        pdf = tmp_path / "page.pdf"
        make_page_pdf(pdf, box=(100, 200, 400, 400))

        report = convert([pdf], spec(workdir, trim_margin=False))
        width, height = read_size(report.results[0].out_path)
        assert height / width == pytest.approx(842 / 595, abs=0.02)
        assert not report.results[0].warnings

    def test_trimmed_output_keeps_only_a_hairline_border(self, tmp_path, workdir):
        """四周只能留一丝白边（安全余量），不能是整片留白。

        取向是「宁可留一点点白边，也不要裁到线条」：包围盒向外让一个探测像素
        （300 dpi 下约 0.085 mm），换算到 150 dpi 输出不到 1 px；渲染时裁剪框
        还对到设备像素网格外侧，实际最多 2~3 px。判据取"白边厚度"而不是
        "边界像素必须不是白的" —— 后者与这条取舍直接冲突。
        """
        pdf = tmp_path / "page.pdf"
        make_page_pdf(pdf, box=(100, 200, 400, 400))

        report = convert([pdf], spec(workdir, trim_margin=True))
        out = report.results[0].out_path
        with Image.open(out) as image:
            grey = image.convert("L")
            width, height = grey.size
            band = {
                "top": _white_band(grey, axis="rows", reverse=False),
                "bottom": _white_band(grey, axis="rows", reverse=True),
                "left": _white_band(grey, axis="cols", reverse=False),
                "right": _white_band(grey, axis="cols", reverse=True),
            }
        assert width > 0 and height > 0
        for side, thickness in band.items():
            assert thickness <= 3, f"{side} 白边 {thickness}px，超出安全余量"

    def test_raster_trim_changes_output_aspect(self, tmp_path, workdir):
        from conftest import make_raster

        src = make_raster(tmp_path / "fig.png", (400, 300))  # 纯色 = 没有可裁的边
        report = convert([src], spec(workdir, trim_margin=True))
        assert report.success
        assert any("未检测到可裁剪的空白边" in w for w in report.results[0].warnings)

    def test_raster_with_margin_is_trimmed(self, tmp_path, workdir):
        src = tmp_path / "fig.png"
        boxed_image(size=(400, 300), box=(100, 60, 300, 240)).save(src)

        report = convert([src], spec(workdir, trim_margin=True))
        width, height = read_size(report.results[0].out_path)
        # 内容 200 x 180 -> 比例 1.111；宽度 591 -> 高约 532
        assert width == pytest.approx(591, abs=2)
        assert height / width == pytest.approx(180 / 200, abs=0.02)
