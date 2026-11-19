"""转换编排：把一批输入文件按同一份规格输出成期刊可用的图片。

每个输入文件可能产生多个输出项（多页 PDF / 多页幻灯片 -> 每页一张图），
所以任务粒度是"源文件 + 页号"。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from imgconv import encode, ingest, office, render, slides, trim
from imgconv.encode import EncodeResult, VolumeLimitUnreachable
from imgconv.ingest import SourceInfo, SourceKind
from imgconv.model import (
    ConflictPolicy,
    OutputSpec,
    SpecError,
    TargetGeometry,
    fit_within,
    parse_page_range,
)

ProgressCallback = Callable[["ProgressEvent"], None]
CancelCheck = Callable[[], bool]
StageCallback = Callable[[str], None]


@dataclass
class ProgressEvent:
    completed_files: int
    total_files: int
    current: str
    page: str = ""


@dataclass
class TaskResult:
    """一个输出项的结果（成功或失败）。"""

    source: Path
    page: int | None
    out_path: Path | None
    ok: bool
    geometry: TargetGeometry | None = None
    num_bytes: int = 0
    encode: EncodeResult | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    elapsed: float = 0.0
    source_kind: SourceKind | None = None

    @property
    def summary(self) -> str:
        if not self.ok:
            return f"失败：{self.error}"
        parts = []
        if self.geometry:
            parts.append(self.geometry.describe())
        if self.encode:
            parts.append(self.encode.describe())
        return " / ".join(parts)


@dataclass
class ConversionReport:
    results: list[TaskResult] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.results if not r.ok)

    @property
    def warning_count(self) -> int:
        return sum(1 for r in self.results if r.warnings)

    @property
    def success(self) -> bool:
        return self.fail_count == 0 and self.ok_count > 0


# ----------------------------------------------------------------------
# 输出路径
# ----------------------------------------------------------------------
def default_output_dir(source_dir: Path) -> Path:
    """不指定输出目录时，落在源文件同级的 converted 子目录，避免污染原始素材。"""
    return Path(source_dir) / "converted"


def _resolve_output_dir(spec: OutputSpec, source: Path) -> Path:
    return Path(spec.output_dir) if spec.output_dir else default_output_dir(source.parent)


def build_output_name(spec: OutputSpec, source: Path, page: int, multi_page: bool) -> str:
    template = spec.name_template or "{stem}"
    if multi_page and "{page" not in template and "{index" not in template:
        template = template + "_p{page:02d}"
    try:
        base = template.format(stem=source.stem, page=page, index=page)
    except (KeyError, IndexError, ValueError) as exc:
        raise SpecError(f"命名模板不合法：{template} —— {exc}") from exc
    return f"{base}{spec.fmt.extension}"


def resolve_conflict(path: Path, policy: ConflictPolicy) -> Path | None:
    """按冲突策略决定最终路径；SKIP 时返回 None。"""
    if not path.exists():
        return path
    if policy is ConflictPolicy.OVERWRITE:
        return path
    if policy is ConflictPolicy.SKIP:
        return None
    counter = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


# ----------------------------------------------------------------------
# 转换器
# ----------------------------------------------------------------------
class Converter:
    """按一份 OutputSpec 批量转换。

    可复用的长生命周期对象：PowerPoint 会话按需惰性开启，并在所有幻灯片
    处理完之前保持存活，避免每个文件都重启一次 Office。
    """

    def __init__(
        self,
        spec: OutputSpec,
        *,
        progress: ProgressCallback | None = None,
        should_cancel: CancelCheck | None = None,
        on_stage: StageCallback | None = None,
    ) -> None:
        self.spec = spec
        self.progress = progress
        self.should_cancel = should_cancel or (lambda: False)
        # 阶段回调：把「现在到底在干什么」写进界面日志。
        # 没有它，一次慢转换（PowerPoint 导出几十秒）在日志里完全静默，
        # 用户只能看到一个不动的进度条，误以为是卡死（BUG-002）。
        self.on_stage = on_stage or (lambda _message: None)
        self._ppt: office.PowerPointSession | None = None

    # -- 公共入口 -------------------------------------------------------
    def run(
        self,
        paths: Iterable[Path | str],
        page_overrides: Mapping[str, str | None] | None = None,
        spec_overrides: Mapping[str, OutputSpec] | None = None,
    ) -> ConversionReport:
        """批量转换。

        page_overrides: 以 ``str(源文件路径)`` 为键的页码范围覆盖
        （如 ``"1,3-5"``，None 表示全部页）。用于让界面里每个 PDF/PPT
        文件带自己的页码设置；未命中的文件用 spec.page_range。

        spec_overrides: 以 ``str(源文件路径)`` 为键的整份规格覆盖。
        用于让界面里每个文件带自己的尺寸参数（同批文件可各自设置
        不同尺寸）；未命中的文件用构造时的 spec。
        """
        sources = [Path(p) for p in paths]
        report = ConversionReport()
        total = len(sources)

        try:
            for index, source in enumerate(sources):
                if self.should_cancel():
                    break
                self._emit(index, total, source.name)
                key = str(source)
                override = page_overrides.get(key) if page_overrides else None
                per_file_spec = spec_overrides.get(key) if spec_overrides else None
                saved_spec = self.spec
                if override is not None or per_file_spec is not None:
                    base = per_file_spec if per_file_spec is not None else self.spec
                    self.spec = replace(base, page_range=override or None)
                try:
                    report.results.extend(self.convert_one(source))
                finally:
                    self.spec = saved_spec
        finally:
            self._close_ppt()

        return report

    def convert_one(self, source: Path) -> list[TaskResult]:
        started = time.perf_counter()
        info = ingest.probe(source)

        if info.error:
            return [
                TaskResult(
                    source=source, page=None, out_path=None, ok=False,
                    error=info.error, source_kind=info.kind,
                    elapsed=time.perf_counter() - started,
                )
            ]

        try:
            self.spec.validate()
        except SpecError as exc:
            return [
                TaskResult(
                    source=source, page=None, out_path=None, ok=False,
                    error=f"输出规格有误：{exc}", source_kind=info.kind,
                )
            ]

        try:
            if info.kind is SourceKind.RASTER:
                results = self._convert_raster(source, info)
            elif info.kind in (SourceKind.PDF, SourceKind.SVG):
                # SVG 与 PDF 走同一条矢量渲染路径（MuPDF 两者都能直接解析）
                results = self._convert_pdf(source, info, None)
            elif info.kind is SourceKind.SLIDES:
                results = self._convert_slides(source, info)
            else:  # pragma: no cover - probe 已拦截
                raise RuntimeError(f"不支持的文件类型：{source.suffix}")
        except (VolumeLimitUnreachable, office.OfficeUnavailableError, SpecError) as exc:
            # 用户点了取消时，COM 被强行打断的报错没有诊断价值，换成可读的说明
            message = (
                "转换已取消（PowerPoint 会话被主动结束）"
                if self.should_cancel()
                else str(exc)
            )
            return [
                TaskResult(
                    source=source, page=None, out_path=None, ok=False,
                    error=message, source_kind=info.kind,
                    elapsed=time.perf_counter() - started,
                )
            ]
        except Exception as exc:  # noqa: BLE001 - 单个文件失败不应中断整批
            return [
                TaskResult(
                    source=source, page=None, out_path=None, ok=False,
                    error=f"{type(exc).__name__}: {exc}", source_kind=info.kind,
                    elapsed=time.perf_counter() - started,
                )
            ]

        elapsed = time.perf_counter() - started
        for result in results:
            result.elapsed = elapsed
            result.source_kind = info.kind
        return results

    # -- 分支实现 -------------------------------------------------------
    def _convert_raster(self, source: Path, info: SourceInfo) -> list[TaskResult]:
        loaded = render.load_raster(source)
        image = loaded.image
        warnings = list(loaded.warnings)

        if self.spec.trim_margin:
            box = trim.detect(image)
            if box is None:
                warnings.append("未检测到可裁剪的空白边（内容已铺满或底色过于接近）")
            else:
                # 向外让出一个像素：宁可留一丝白边，也不切掉阈值边界上的边缘像素
                box = box.padded()
                image = trim.crop(image, box)
                warnings.append(box.describe())

        aspect = image.size[0] / image.size[1]
        geometry = self.spec.resolve(aspect)
        geometry, scale_warnings = fit_within(
            geometry, image.size, allow_upscale=self.spec.allow_upscale
        )

        target_px = geometry.pixel_size
        self._stage(f"正在重采样到 {target_px[0]} x {target_px[1]} px…")
        image, resize_warnings = render.resize_raster(image, target_px)

        out_path = self._plan_output(source, page=1, multi_page=False)
        if out_path is None:
            return [TaskResult(
                source=source, page=1, out_path=None, ok=False,
                error="同名文件已存在，按「跳过」策略未输出", warnings=warnings,
            )]

        self._stage(f"正在编码 {out_path.suffix or self.spec.fmt.extension}…")
        result = encode.encode_to_file(image, out_path, self.spec)
        return [TaskResult(
            source=source, page=1, out_path=out_path, ok=True,
            geometry=geometry, num_bytes=result.num_bytes, encode=result,
            warnings=warnings + scale_warnings + resize_warnings + result.warnings,
        )]

    def _convert_pdf(
        self, source: Path, info: SourceInfo, pdf_path: Path | None
    ) -> list[TaskResult]:
        actual_pdf = pdf_path or source
        results: list[TaskResult] = []

        with render.PdfPageRenderer(actual_pdf) as doc:
            pages = parse_page_range(self.spec.page_range, doc.page_count)
            # 页号后缀取决于「源文档是不是多页」，而不是「这次选中了几页」：
            # 从 10 页 PDF 里只取第 3 页，输出叫 doc_p03.tif 才不会被误认成第 1 页。
            multi = doc.page_count > 1

            for page_no in pages:
                if self.should_cancel():
                    break
                index = page_no - 1
                warnings: list[str] = []

                # 自动裁白边：先在低分辨率探测图上找主体包围盒，再把目标像素
                # 全部投到这块区域上 —— 白边既不占画布，也不占渲染内存。
                clip = None
                if self.spec.trim_margin:
                    self._stage(f"第 {page_no} 页：正在探测空白边…")
                    clip, ratio = doc.page_content_rect(index)
                    if clip is None:
                        warnings.append(
                            f"第 {page_no} 页未检测到可裁剪的空白边（内容已铺满页面）"
                        )
                    else:
                        warnings.append(
                            f"第 {page_no} 页已裁去四周空白：内容占页面 {ratio * 100:.0f}%"
                        )

                aspect = (
                    clip.width / clip.height if clip is not None
                    else doc.page_aspect(index)
                )
                geometry = self.spec.resolve(aspect)

                if self.spec.check_embedded_dpi:
                    embedded = doc.embedded_image_dpi(index)
                    if embedded is not None and embedded < geometry.dpi * 0.95:
                        warnings.append(
                            f"第 {page_no} 页内嵌位图的有效分辨率仅约 {embedded:.0f} dpi，"
                            f"低于目标 {geometry.dpi} dpi。"
                            "放大不会产生新细节，建议改为用矢量源或提高原始截屏分辨率。"
                        )

                size_px = geometry.pixel_size
                self._stage(
                    f"第 {page_no}/{doc.page_count} 页：正在渲染 "
                    f"{size_px[0]} x {size_px[1]} px @ {geometry.dpi} dpi…"
                )
                image, render_warnings = doc.render_page(index, geometry, clip=clip)
                warnings.extend(render_warnings)

                out_path = self._plan_output(source, page=page_no, multi_page=multi)
                if out_path is None:
                    results.append(TaskResult(
                        source=source, page=page_no, out_path=None, ok=False,
                        error="同名文件已存在，按「跳过」策略未输出", warnings=warnings,
                    ))
                    continue

                self._stage(f"第 {page_no} 页：正在编码 {out_path.suffix}…")
                encoded = encode.encode_to_file(image, out_path, self.spec)
                results.append(TaskResult(
                    source=source, page=page_no, out_path=out_path, ok=True,
                    geometry=geometry, num_bytes=encoded.num_bytes, encode=encoded,
                    warnings=warnings + encoded.warnings,
                ))
        return results

    def _convert_slides(self, source: Path, info: SourceInfo) -> list[TaskResult]:
        session = self._ppt_session()

        # 先问"到底画了多少东西"：PowerPoint 导出 PDF 只导出画幅内的内容，
        # 溢出画幅的部分在 PDF 里根本不存在 —— 事后再裁白边也救不回来。
        # 所以开启裁边时先量形状范围，需要的话把画幅扩到内容那么大再导出。
        plan = None
        if self.spec.trim_margin:
            plan = self._content_canvas(session, source)

        if plan is None:
            self._stage("正在用 PowerPoint 打开并导出 PDF（首次启动 Office 会慢一些）…")
            pdf_path = session.export_pdf(source)
        else:
            self._stage("正在按绘制范围扩展画幅并导出 PDF…")
            pdf_path = session.export_pdf(source, expand=plan.rect)
        self._stage("PowerPoint 已导出 PDF，开始逐页渲染…")

        results = self._convert_pdf(source, info, pdf_path)
        if plan is not None and plan.note:
            # 画幅说明放在每条结果的最前面：它比"裁掉多少白"更值得先看到
            for result in results:
                result.warnings.insert(0, plan.note)
        return results

    def _content_canvas(self, session, source: Path):
        """量出形状范围，决定是否需要扩画幅。失败一律返回 None（按原画幅走）。"""
        try:
            if source.suffix.lower() in (".pptx", ".pptm"):
                pages = slides.bounds_from_pptx(source)
            else:
                # 旧版 .ppt 没有纯 Python 解析器，只能问 PowerPoint 本体
                self._stage("正在读取幻灯片里各形状的位置…")
                pages = session.shape_bounds(source)
            plan = slides.plan_expansion(pages)
        except Exception as exc:  # noqa: BLE001 - 量不出来不算致命，退回原画幅
            self._stage(f"提示：无法读取形状范围（{type(exc).__name__}: {exc}），按原画幅导出")
            return None
        if plan is not None:
            self._stage(plan.note)
        return plan

    def abort(self) -> None:
        """请求中止（可从界面线程调用）。

        COM 调用（PowerPoint 导出）不响应取消标志，光置位 `should_cancel` 得等
        它自己跑完 —— 结束我们自己启动的那个 PowerPoint 进程，阻塞中的调用会
        立刻抛错返回，转换线程随即退出。
        """
        session = self._ppt
        if session is not None:
            session.abort()

    # -- 内部工具 -------------------------------------------------------
    def _stage(self, message: str) -> None:
        """上报当前阶段；回调出问题绝不能连带拖垮转换本身。"""
        try:
            self.on_stage(message)
        except Exception:  # noqa: BLE001
            pass

    def _ppt_session(self) -> office.PowerPointSession:
        if self._ppt is None:
            self._ppt = office.PowerPointSession()
            self._ppt.__enter__()
        return self._ppt

    def _close_ppt(self) -> None:
        if self._ppt is not None:
            self._ppt.__exit__(None, None, None)
            self._ppt = None

    def _plan_output(self, source: Path, *, page: int, multi_page: bool) -> Path | None:
        out_dir = _resolve_output_dir(self.spec, source)
        name = build_output_name(self.spec, source, page, multi_page)
        return resolve_conflict(out_dir / name, self.spec.on_conflict)

    def _emit(self, index: int, total: int, current: str, page: str = "") -> None:
        if self.progress:
            self.progress(ProgressEvent(index, total, current, page))


def convert(
    paths: Iterable[Path | str],
    spec: OutputSpec,
    *,
    progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
    page_overrides: Mapping[str, str | None] | None = None,
    spec_overrides: Mapping[str, OutputSpec] | None = None,
) -> ConversionReport:
    """一次性转换的便捷入口。"""
    converter = Converter(spec, progress=progress, should_cancel=should_cancel)
    return converter.run(paths, page_overrides, spec_overrides)


__all__ = [
    "ConversionReport",
    "Converter",
    "ProgressEvent",
    "TaskResult",
    "build_output_name",
    "convert",
    "default_output_dir",
    "resolve_conflict",
]
