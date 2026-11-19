"""通过本机 PowerPoint 把 PPT/PPTX 导出为 PDF（矢量保真路径）。

为什么绕道 PDF 而不是直接 `Slide.Export` 出 PNG：
    1. PDF 保留矢量文字与图形，之后由 PyMuPDF 按任意目标 DPI 光栅化，
       600 dpi、1200 dpi 都依然锐利；
    2. `Slide.Export` 的像素尺寸有上限，做大尺寸高 DPI 输出会受限；
    3. PPT 与 PDF 两种输入共用同一条渲染后端，行为一致、可复现。

本模块是唯一依赖 Microsoft Office 的地方。没有装 PowerPoint 时不做静默降级
（python-pptx 无法渲染页面），而是抛出可读的错误提示用户改用 PDF。

关于关闭 PowerPoint 的实测结论（Office 16 / Windows，本机反复验证）
------------------------------------------------------------------
用 COM 优雅退出非常慢，而且慢得没有道理：
    app.Quit()                        约 16 秒
    紧接着 Release 掉 Application 代理  约 60 秒
    合计                              ~76 秒全部阻塞在调用线程里
把这段挪到后台线程也不行：从非创建线程调用未 marshal 的 COM 接口，Quit 实际上
根本没送达，PowerPoint 会一直留在后台（实测 180 秒仍在，占 600 MB 内存）。

所以这里改为直接结束我们自己启动的那个进程：
    DispatchEx 保证它是独立实例，和用户正在编辑的 PowerPoint 互不影响；
    我们只让它「只读打开 + 另存为临时 PDF」，PDF 写完并关闭后没有任何未保存内容，
    此时终止它不会造成数据丢失，也不会触发「上次异常退出」的文档恢复提示。
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path

PP_SAVE_AS_PDF = 32
PP_ALERTS_NONE = 1
PP_FIXED_FORMAT_PDF = 2
PP_WINDOW_MINIMIZED = 2

# 旧版 .ppt 读形状坐标要走 COM：单次调用很便宜，但数量多了会明显拖慢，
# 设个上限（超了只影响"内容范围"的精度，不影响转换本身）。
_MAX_COM_SHAPES = 3000

_POWERPOINT_IMAGE = "POWERPNT.EXE"


class OfficeUnavailableError(RuntimeError):
    """本机缺少可用的 PowerPoint，或 COM 调用失败。"""


def _list_powerpoint_pids() -> set[int]:
    """列出当前所有 POWERPNT.EXE 的进程号。"""
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {_POWERPOINT_IMAGE}", "/FO", "CSV", "/NH"],
            capture_output=True,
            timeout=20,
        )
    except Exception:  # noqa: BLE001 - 取不到就退化成温和关闭
        return set()

    # 中文 Windows 上 tasklist 输出是 GBK；我们只关心 "POWERPNT.EXE","<pid>"
    # 这段纯 ASCII，所以按 ASCII 解码并把其余内容替换掉即可，不依赖系统代码页。
    pids: set[int] = set()
    for line in completed.stdout.decode("ascii", errors="replace").splitlines():
        fields = line.split('","')
        if len(fields) >= 2 and fields[0].strip('"').upper().startswith("POWERPNT"):
            with contextlib.suppress(ValueError):
                pids.add(int(fields[1].strip('"')))
    return pids


def _kill_processes(pids: Iterable[int]) -> None:
    for pid in pids:
        with contextlib.suppress(Exception):
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True,
                timeout=20,
            )


def _import_win32():
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except ImportError as exc:  # pragma: no cover - 非 Windows 或未装 pywin32
        raise OfficeUnavailableError(
            "未安装 pywin32，无法驱动 PowerPoint。请执行 pip install pywin32，"
            "或把幻灯片另存为 PDF 后再输入。"
        ) from exc
    return pythoncom, win32com.client


def is_available() -> tuple[bool, str]:
    """检测本机是否可以驱动 PowerPoint。返回 (是否可用, 说明)。"""
    if os.name != "nt":
        return False, "当前系统非 Windows，无法调用 PowerPoint"
    try:
        pythoncom, win32 = _import_win32()
    except OfficeUnavailableError as exc:
        return False, str(exc)

    pythoncom.CoInitialize()
    before = _list_powerpoint_pids()
    app = None
    try:
        app = win32.DispatchEx("PowerPoint.Application")
        version = ""
        with contextlib.suppress(Exception):
            version = str(app.Version)
        return True, f"PowerPoint {version}".strip()
    except Exception as exc:  # noqa: BLE001
        return False, f"未检测到可用的 PowerPoint：{exc}"
    finally:
        # 探测用的实例立刻回收，不走那套 76 秒的优雅退出
        spawned = _list_powerpoint_pids() - before
        if spawned:
            _kill_processes(spawned)
        elif app is not None:
            with contextlib.suppress(Exception):
                app.Quit()


class PowerPointSession:
    """复用同一个 PowerPoint 进程批量导出，避免每个文件启停一次。

    必须在创建它的线程内使用（COM 单元模型）。转换工作线程里用 with 语句即可。
    """

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = Path(
            cache_dir or Path(tempfile.mkdtemp(prefix="imgconv_ppt_"))
        )
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._owns_cache = cache_dir is None
        self._app = None
        self._own_pids: set[int] = set()

    # -- 生命周期 -------------------------------------------------------
    def __enter__(self) -> "PowerPointSession":
        pythoncom, win32 = _import_win32()
        # COM 单元必须在使用它的线程里初始化。这里刻意不配对调用 CoUninitialize：
        # 反初始化会同步等待 PowerPoint 代理释放，而那正是我们要规避的几十秒阻塞。
        # 单元随线程/进程结束由系统回收。
        pythoncom.CoInitialize()

        before = _list_powerpoint_pids()
        try:
            self._app = win32.DispatchEx("PowerPoint.Application")
            # PowerPoint 不允许把主窗口设为不可见，只能接受它出现；
            # 用 DispatchEx 保证是独立实例，不干扰用户正在编辑的文稿。
            with contextlib.suppress(Exception):
                self._app.Visible = True
            # 出现就够了，别霸占前台 —— 一个整屏的 PowerPoint 窗口盖上来，
            # 用户第一反应是"这软件卡死了"（BUG-002 的观感来源之一）。
            with contextlib.suppress(Exception):
                self._app.WindowState = PP_WINDOW_MINIMIZED
            with contextlib.suppress(Exception):
                self._app.DisplayAlerts = PP_ALERTS_NONE
            with contextlib.suppress(Exception):
                self._app.AutomationSecurity = 1  # 保守值，禁用宏相关提示
        except Exception as exc:  # noqa: BLE001
            self.__exit__(None, None, None)
            raise OfficeUnavailableError(
                f"无法启动 PowerPoint：{exc}。请确认已安装 Microsoft PowerPoint，"
                "或把幻灯片另存为 PDF 后再输入。"
            ) from exc

        # 进程刚起来时 tasklist 可能还没列出它，重试几次
        for _ in range(10):
            self._own_pids = _list_powerpoint_pids() - before
            if self._own_pids:
                break
            time.sleep(0.3)
        return self

    def __exit__(self, *exc_info) -> None:
        pids = self._own_pids
        self._own_pids = set()
        app = self._app
        self._app = None

        if pids:
            # 结束进程（瞬时）。进程消失后释放 COM 代理不会再等它做退出握手。
            _kill_processes(pids)
        elif app is not None:
            # 极端情况下没识别出自己的进程号，只能走可能很慢的优雅退出
            with contextlib.suppress(Exception):
                app.Quit()

        if app is not None:
            with contextlib.suppress(Exception):
                del app

        if self._owns_cache:
            shutil.rmtree(self._cache_dir, ignore_errors=True)

    # -- 能力 -----------------------------------------------------------
    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    def abort(self) -> None:
        """结束本会话自己启动的 PowerPoint 进程（任意线程可调用）。

        只做 taskkill、不碰 COM，所以从界面线程调用来打断一段卡在 SaveAs 里的
        转换是安全的：阻塞中的调用方会立刻收到 COM 错误，转换线程随即退出。
        """
        _kill_processes(self._own_pids)

    # -- 导出 -----------------------------------------------------------
    def export_pdf(
        self,
        src: Path | str,
        dst: Path | None = None,
        *,
        expand: tuple[float, float, float, float] | None = None,
    ) -> Path:
        """把演示文稿导出为 PDF，返回 PDF 路径。

        同一源文件重复调用会命中缓存，方便「一次导出、多档 DPI 输出」。

        expand 给定时（幻灯片坐标系里的 (left, top, right, bottom)，单位点），
        先把**副本**的画幅改成这块区域、再把所有形状整体平移，然后才导出 ——
        这样溢出画幅的内容也会出现在 PDF 里。原文一个字都不改：改的是缓存目录
        里的副本，导出完随缓存一起清掉。
        """
        if self._app is None:
            raise OfficeUnavailableError("PowerPoint 会话尚未启动")

        src = Path(src).resolve()
        if not src.exists():
            raise FileNotFoundError(f"找不到文件：{src}")

        target = Path(dst).resolve() if dst else self._cache_for(src, expand)
        if target.exists() and target.stat().st_size > 0:
            return target

        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            with contextlib.suppress(OSError):
                target.unlink()

        work_src = src
        writable = False
        if expand is not None:
            work_src = self._staged_copy(src)
            if src.suffix.lower() in (".pptx", ".pptm"):
                # .pptx 直接改 XML（确定性，且不受 PowerPoint 自动居中影响），
                # 副本仍然是只读打开 —— 少一次写操作就少一次出错机会。
                from imgconv import slides as slides_module

                plan = slides_module.ExpandPlan(*expand, note="")
                slides_module.expand_copy(src, work_src, plan)
            else:
                writable = True  # 旧版 .ppt 只能让 PowerPoint 自己改
        presentation = None
        try:
            # WithWindow=False 能显著加快导出，且必须与 ReadOnly=True 同时使用；
            # 要改画幅/挪形状（旧版 .ppt）时就只能打开可写句柄。
            presentation = self._app.Presentations.Open(
                str(work_src), not writable, False, False
            )
            if writable:
                self._resize_canvas(presentation, expand)
            presentation.SaveAs(str(target), PP_SAVE_AS_PDF)
        except Exception as exc:  # noqa: BLE001
            raise OfficeUnavailableError(
                f"PowerPoint 导出 PDF 失败：{src.name} —— {exc}"
            ) from exc
        finally:
            if presentation is not None:
                with contextlib.suppress(Exception):
                    presentation.Close()

        if not target.exists() or target.stat().st_size == 0:
            raise OfficeUnavailableError(
                f"PowerPoint 未生成有效 PDF：{src.name}。"
                "文件可能已损坏，或包含被禁用宏的受限内容。"
            )
        return target

    # -- 画幅调整（为"内容溢出画幅"服务）--------------------------------
    def _staged_copy(self, src: Path) -> Path:
        """把源文件复制到缓存目录：所有改动只发生在副本上。"""
        digest = hashlib.sha1(
            f"{src}:{src.stat().st_mtime_ns}:{src.stat().st_size}".encode("utf-8")
        ).hexdigest()[:16]
        copy = self._cache_dir / f"{digest}{src.suffix or '.pptx'}"
        if not copy.exists() or copy.stat().st_size == 0:
            shutil.copy2(src, copy)
        return copy

    @staticmethod
    def _resize_canvas(presentation, rect: tuple[float, float, float, float]) -> None:
        """把画幅改成 rect，并把每页的顶层形状整体平移，让内容落在画幅里。

        **必须先记坐标、再改画幅、最后写绝对位置**：PowerPoint 在改画幅时会
        自动把内容居中（实测整体位移正好是画幅差的一半），按增量 `Left -= left`
        改会被它带偏 —— 当初就是这么把图外的内容推得更外面的。

        只动顶层形状：组合形状的 .Left/.Top 一挪，组内子形状跟着走。
        任何单个形状挪不动（只读占位符等）都跳过 —— 宁可它留在原地，
        也不要因为一个形状失败而放弃整份文稿。
        """
        left, top, right, bottom = rect

        captured = []
        for slide in presentation.Slides:
            items = []
            for shape in slide.Shapes:
                position: list[float | None] = []
                for attr in ("Left", "Top"):
                    try:
                        position.append(float(getattr(shape, attr)))
                    except Exception:  # noqa: BLE001
                        position.append(None)
                items.append((shape, position))
            captured.append(items)

        setup = presentation.PageSetup
        setup.SlideWidth = max(72.0, float(right - left))
        setup.SlideHeight = max(72.0, float(bottom - top))

        for items in captured:
            for shape, (old_left, old_top) in items:
                if old_left is not None:
                    with contextlib.suppress(Exception):
                        shape.Left = old_left - left
                if old_top is not None:
                    with contextlib.suppress(Exception):
                        shape.Top = old_top - top

    def describe(self, src: Path | str) -> tuple[int, float | None]:
        """读取幻灯片页数与宽高比（旧版 .ppt 无法用 python-pptx 探测时使用）。"""
        if self._app is None:
            raise OfficeUnavailableError("PowerPoint 会话尚未启动")
        src = Path(src).resolve()
        presentation = None
        try:
            presentation = self._app.Presentations.Open(str(src), True, False, False)
            count = int(presentation.Slides.Count)
            width = float(presentation.PageSetup.SlideWidth)
            height = float(presentation.PageSetup.SlideHeight)
            aspect = width / height if height else None
            return count, aspect
        except Exception as exc:  # noqa: BLE001
            raise OfficeUnavailableError(f"PowerPoint 读取失败：{src.name} —— {exc}") from exc
        finally:
            if presentation is not None:
                with contextlib.suppress(Exception):
                    presentation.Close()

    # -- 内容范围（旧版 .ppt 专用；.pptx 走 python-pptx 更快）------------
    def shape_bounds(self, src: Path | str) -> list:
        """读每页顶层形状的并集（点）。旧版 .ppt 没有纯 Python 解析器，只能问 PowerPoint。

        形状数量设了上限：畸形文件里几千个形状逐个 COM 调用会拖成分钟级，
        到了上限就不再继续读（返回的范围会偏小，由调用方提示"可能仍被裁"）。
        """
        if self._app is None:
            raise OfficeUnavailableError("PowerPoint 会话尚未启动")
        from imgconv.slides import ContentBounds

        src = Path(src).resolve()
        presentation = None
        pages = []
        try:
            presentation = self._app.Presentations.Open(str(src), True, False, False)
            slide_w = float(presentation.PageSetup.SlideWidth)
            slide_h = float(presentation.PageSetup.SlideHeight)
            count = int(presentation.Slides.Count)
            budget = _MAX_COM_SHAPES
            for index in range(1, count + 1):
                shapes = presentation.Slides(index).Shapes
                total = int(shapes.Count)
                lefts: list[float] = []
                tops: list[float] = []
                rights: list[float] = []
                bottoms: list[float] = []
                truncated = False
                for position in range(1, total + 1):
                    if budget <= 0:
                        truncated = True
                        break
                    budget -= 1
                    shape = shapes(position)
                    with contextlib.suppress(Exception):
                        left = float(shape.Left)
                        top = float(shape.Top)
                        lefts.append(left)
                        tops.append(top)
                        rights.append(left + float(shape.Width))
                        bottoms.append(top + float(shape.Height))
                pages.append(
                    ContentBounds(
                        left=min(lefts) if lefts else 0.0,
                        top=min(tops) if tops else 0.0,
                        right=max(rights) if rights else slide_w,
                        bottom=max(bottoms) if bottoms else slide_h,
                        slide_width=slide_w,
                        slide_height=slide_h,
                        shapes=len(lefts),
                        incomplete=truncated or len(lefts) < total,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            raise OfficeUnavailableError(
                f"PowerPoint 读取形状范围失败：{src.name} —— {exc}"
            ) from exc
        finally:
            if presentation is not None:
                with contextlib.suppress(Exception):
                    presentation.Close()
        return pages

    # -- 内部 -----------------------------------------------------------
    def _cache_for(
        self, src: Path, expand: tuple[float, float, float, float] | None = None
    ) -> Path:
        key = f"{src}:{src.stat().st_mtime_ns}:{src.stat().st_size}"
        if expand is not None:
            # 展开画幅后的 PDF 与原画幅的不是同一份，缓存键必须分开
            key += "|expand:" + ",".join(f"{value:.2f}" for value in expand)
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        return self._cache_dir / f"{digest}.pdf"


__all__ = [
    "OfficeUnavailableError",
    "PowerPointSession",
    "is_available",
]
