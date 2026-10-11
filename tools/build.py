"""一键打包。

把之前手工敲的那长串 PyInstaller 参数固化下来，并在打包后做三件收尾：
把使用说明放到 dist 根目录（用户不会进 _internal 翻）、**精简掉用不上的运行库**，
以及打印产物概况。

精简（见 PRUNE）是必需的：PySide6 / pywin32 的官方 hook 会把整个包照单全收，
其中 opengl32sw、QML 全套、96 个 Qt 翻译文件、Pythonwin、QtNetwork 的 OpenSSL
等等对本程序毫无用处，加起来约 70MB（占总体的三分之一）。每一条都先做过依赖
核对，见 PRUNE 上方的注释。

用法：
    python tools/build.py
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    # 保证「python 生产部/tools/build.py」无论从哪个目录启动，
    # 都能 import tools.make_icon（icon 缺失时的兜底生成）
    sys.path.insert(0, str(ROOT))
APP_NAME = "图片转换器"

# 这些包本身不是本工具的依赖，但环境里装了它们，PyInstaller 会顺着某些
# 可选导入把它们整个拖进来 —— 光是 torch 就有 364MB。全部排除。
EXCLUDES = (
    "tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide2", "IPython",
    "pytest", "torch", "torchvision", "torchaudio", "pyarrow", "scipy",
    "onnxruntime", "pandas", "cryptography", "sklearn", "cv2", "numba",
    "sympy", "h5py", "sqlalchemy", "lxml", "wx", "notebook", "jupyter",
    "setuptools",
    # 本程序用不到的 Qt 模块（详见 PRUNE）；一并排除可省下分析时间与 PYZ 体积
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf", "PySide6.QtNetwork",
    "PySide6.QtOpenGL", "PySide6.QtSvg", "PySide6.QtVirtualKeyboard",
    # Pythonwin（win32ui）与 pywin 的 GUI 工具链：COM 自动化只用 pythoncom
    "win32ui", "pywin", "Pythonwin",
    # AVIF 编解码：支持格式清单（imgconv/ingest.py）里没有 AVIF
    "PIL._avif",
)

HIDDEN_IMPORTS = ("pythoncom", "win32com", "win32com.client")

# ----------------------------------------------------------------------
# 打包后精简：删掉对纯 Widgets 桌面应用完全无用的运行库
# ----------------------------------------------------------------------
# 支持通配符（相对 _internal/）。删除前逐项做过依赖核对：
#   · PE 导入表全盘扫描 —— 确认 _internal 里没有任何**在用**模块静态引用它；
#   · 反例留档：libcrypto-1_1 / libssl-1_1 **必须保留**（CPython 的 _ssl.pyd 与
#     _hashlib.pyd 直接依赖），而 libcrypto-3 / libssl-3 只有 QtNetwork 的
#     TLS 后端要用，跟着 QtNetwork 一起删；
#   · Qt6OpenGL 只被 Qt6Quick 引用；Qt6Pdf 只被 qpdf 图片插件引用；
#     Qt6Network 只被 QtPdf/Qml/Quick、QtNetwork.pyd 与触摸/网络信息插件引用。
# 删完必须跑 --selftest（管线：MuPDF + Pillow + numpy）与 --smoke（界面全量构建）。
PRUNE = (
    # Mesa 软件 OpenGL 兜底：本程序全程 QPainter 光栅绘制，从不建 GL 上下文
    "PySide6/opengl32sw.dll",
    # QML / Quick / 虚拟键盘：纯 Widgets 应用，全仓无一处 QML
    "PySide6/Qt6Quick.dll",
    "PySide6/Qt6Qml.dll",
    "PySide6/Qt6QmlModels.dll",
    "PySide6/Qt6QmlMeta.dll",
    "PySide6/Qt6QmlWorkerScript.dll",
    "PySide6/Qt6VirtualKeyboard.dll",
    "PySide6/Qt6OpenGL.dll",
    "PySide6/plugins/platforminputcontexts",
    # Qt PDF：PDF 一律走 MuPDF 矢量重渲染
    "PySide6/Qt6Pdf.dll",
    "PySide6/plugins/imageformats/qpdf.dll",
    # Qt Network：检查更新走 urllib
    "PySide6/Qt6Network.dll",
    "PySide6/QtNetwork.pyd",
    "PySide6/plugins/tls",
    "PySide6/plugins/networkinformation",
    "PySide6/plugins/generic/qtuiotouchplugin.dll",
    # Qt 自带翻译：没装 QTranslator，96 个 .qm 一个都不会被加载
    "PySide6/translations",
    # Qt Svg：只被图标/图片格式插件引用，而图片 I/O 全走 Pillow
    "PySide6/Qt6Svg.dll",
    "PySide6/plugins/iconengines/qsvgicon.dll",
    "PySide6/plugins/imageformats/qsvg.dll",
    # Pythonwin：mfc140u.dll + win32ui.pyd，COM 自动化用不到
    "Pythonwin",
    # OpenSSL 3：QtNetwork 的 TLS 后端专用
    "libcrypto-3-x64.dll",
    "libssl-3-x64.dll",
    # AVIF 编解码扩展（约 7.5MB）
    "PIL/_avif.*.pyd",
    # --add-data 的副产物，仓库根已经有一份 README
    "README.md",
)


def _measure(root: Path) -> tuple[int, int]:
    """统计目录里的文件数与总字节。"""
    files = [f for f in root.rglob("*") if f.is_file()]
    return len(files), sum(f.stat().st_size for f in files)


def _prune(internal: Path) -> tuple[int, list[str], list[str]]:
    """按 PRUNE 删掉用不上的运行库，返回（实际省下的字节, 已处理项, 缺失项）。

    省下的字节按**删除前后实测差值**报告，而不是把命中项的预期体积加起来 ——
    这样万一某个目录被占用/被拦截而没删掉，报告里立刻看得出来（不会假装成功）。
    缺失项不算错：--exclude-module 已经把大部分挡在收集阶段，本来就不存在。
    """
    before = _measure(internal)[1]
    handled: list[str] = []
    missing: list[str] = []
    for pattern in PRUNE:
        hits = sorted(internal.glob(pattern))
        if not hits:
            missing.append(pattern)
            continue
        for target in hits:
            if target.is_dir():
                size = _measure(target)[1]
                shutil.rmtree(target, ignore_errors=True)
            else:
                size = target.stat().st_size
                target.unlink(missing_ok=True)
            if target.exists():
                print(f"  ! 删除失败（被占用？）：{target.relative_to(internal)}")
            handled.append(f"{target.relative_to(internal)}  {size / 1048576:.1f} MB")
    after = _measure(internal)[1]
    return max(0, before - after), handled, missing


def main() -> int:
    try:
        import PyInstaller.__main__
    except ImportError:
        print("没有安装 PyInstaller。请执行： pip install pyinstaller", file=sys.stderr)
        return 1

    icon = ROOT / "icon.ico"
    if not icon.exists():
        print("缺少 icon.ico，正在生成…")
        from tools.make_icon import main as make_icon

        if make_icon() != 0:
            return 1

    # README 跟仓库走：优先项目根（生产部/），没有就找仓库根
    readme = ROOT / "README.md"
    if not readme.exists():
        readme = ROOT.parent / "README.md"

    args = [
        "--noconfirm",
        "--windowed",
        "--name", APP_NAME,
        "--icon", str(icon),
        # 产物路径显式锚定到 ROOT（生产部/），与启动时的工作目录无关 ——
        # PyInstaller 默认用「当前目录」，从仓库根启动会把 dist/build 撒在根目录
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT),
        # 使用说明随包带上；PyInstaller 6 会把它放进 _internal，
        # 下面再复制一份到 dist 根目录，让用户一眼能看到
        "--add-data", f"{readme}{';.'}",
    ]
    for name in HIDDEN_IMPORTS:
        args += ["--hidden-import", name]
    for name in EXCLUDES:
        args += ["--exclude-module", name]
    args.append(str(ROOT / "main.py"))

    print("开始打包…", flush=True)
    started = time.perf_counter()
    PyInstaller.__main__.run(args)
    elapsed = time.perf_counter() - started

    dist_dir = ROOT / "dist" / APP_NAME
    if not (dist_dir / f"{APP_NAME}.exe").exists():
        print("打包似乎失败了：找不到 exe", file=sys.stderr)
        return 1

    # 把说明复制到产物根目录 —— 用户打开文件夹第一眼就能看到
    if readme.exists():
        shutil.copy2(readme, dist_dir / "README.md")

    # 用户要求「双击就能找到，别藏太深」：把 exe 和运行库从 dist/ 提到项目根，
    # 这样打开项目文件夹就直接看到 图片转换器.exe，不用再点进两层目录。
    for name in (f"{APP_NAME}.exe", "_internal", "README.md"):
        source = dist_dir / name
        if not source.exists():
            continue
        target = ROOT / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink()
        shutil.move(str(source), str(target))
    shutil.rmtree(ROOT / "dist", ignore_errors=True)

    exe = ROOT / f"{APP_NAME}.exe"
    internal = ROOT / "_internal"

    saved, removed, missing = _prune(internal)
    print(f"\n精简：删掉 {len(removed)} 项，省下 {saved / 1048576:.1f} MB")
    for line in removed:
        print(f"  - {line}")
    if missing:
        print(f"  （以下 {len(missing)} 项本来就没被打进来，无需删：{', '.join(missing)}）")

    size_mb = sum(
        f.stat().st_size
        for base in (exe, internal)
        if base.exists()
        for f in ([base] if base.is_file() else base.rglob("*"))
        if f.is_file()
    ) / 1048576

    print(f"\n打包完成，用时 {elapsed:.0f}s")
    print(f"  体积：{size_mb:.0f} MB（精简后；未精简约 {size_mb + saved / 1048576:.0f} MB）")
    print(f"  双击：{exe}")
    print("\n提示：exe 必须和同级的 _internal 文件夹一起拷走才能用。")
    print("     精简后务必跑一遍：图片转换器.exe --selftest 与 --smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
