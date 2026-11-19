"""界面主题：浅色/深色双主题、现代化样式表与品牌图标。

设计方向
--------
2026 现代桌面应用审美：柔和的分层背景（冷灰台面 + 纯白/深灰卡片）、
皇家蓝渐变强调色、更大的圆角（卡片 12px / 控件 7px）、聚焦光环、
渐变主按钮 —— 控件反馈以背景微变与描边加深为主，层次靠间距而非重边框。
内容优先，文件队列是视觉中心，参数栏退后但始终可见。

主题切换
--------
通过 theme.set_theme("light" | "dark") 切换当前主题，再用
theme.build_stylesheet(theme.current_theme().name) 重新设置 QApplication 的样式表。
界面代码中需要动态颜色时请使用 theme.current_theme() 返回的 Theme 实例，
而不要直接引用本模块底部的 LIGHT 常量。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from PySide6.QtCore import QByteArray, QEasingCurve, Qt
from PySide6.QtGui import (
    QFont,
    QFontDatabase,
    QFontMetrics,
    QIcon,
    QPixmap,
)

# ----------------------------------------------------------------------
# 间距：4px 基础网格
# ----------------------------------------------------------------------
SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 12
SPACE_LG = 16
SPACE_XL = 24
SPACE_2XL = 32

# ----------------------------------------------------------------------
# 圆角：三档制（见 .claude/skills/desktop-ui-craft/references/style.md INV-05）
# 卡片 8 / 控件 6 / 微型 4 —— 由原 12/7 收敛而来：更小的圆角读起来像仪器，
# 而不是消费级 App。禁止出现第四档；圆形（单选、进度条胶囊）半径取半高，
# 条形端点固定 2px，这两个不算档位。
# ----------------------------------------------------------------------
RADIUS_CARD = 8
RADIUS_CTRL = 6
RADIUS_MICRO = 4

# ----------------------------------------------------------------------
# 动效：时长与曲线全应用只此一份（UI-017）
# ----------------------------------------------------------------------
# 口径（motion 规律库）：进入 150–200ms、退出略短于进入、位置/尺寸变化
# 用 Out 系（先快后慢，落点稳）。任何新的过渡都应引用这里的常量，
# 不要在各控件里写裸数字 —— 否则「减少动效」降级时无法统一收敛。
ANIM_FAST_MS = 120   # 退出、微反馈
ANIM_BASE_MS = 160   # 组件状态切换（胶囊滑动、行显隐）
ANIM_SLOW_MS = 200   # 大块浮层进入（Toast）
ANIM_EASE_OUT = QEasingCurve.Type.OutCubic
ANIM_EASE_IN_OUT = QEasingCurve.Type.InOutCubic

# ----------------------------------------------------------------------
# 字号阶梯
# ----------------------------------------------------------------------
FONT_3XL = 18   # 仅用于规格仪表盘主读数（S1），别处不得使用
FONT_2XL = 16  # 应用标题
FONT_XL = 13   # 规格条次行、区块主标题
FONT_LG = 11   # 卡片/分组标题
FONT_MD = 10   # 正文、标签、输入框、按钮
FONT_SM = 9    # 次要信息：表头、日志、计数、状态、提示

# ----------------------------------------------------------------------
# 字体
# ----------------------------------------------------------------------
UI_FAMILIES = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "PingFang SC",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "Segoe UI",
)
MONO_FAMILIES = (
    "Cascadia Mono",
    "Cascadia Code",
    "Consolas",
    "JetBrains Mono",
    "DejaVu Sans Mono",
    "Courier New",
)


@lru_cache(maxsize=1)
def _installed_families() -> frozenset[str]:
    try:
        return frozenset(QFontDatabase.families())
    except Exception:  # noqa: BLE001 - 无 GUI 环境下退化为默认字体
        return frozenset()


def _pick(candidates: tuple[str, ...], fallback: str) -> str:
    installed = _installed_families()
    for name in candidates:
        if name in installed:
            return name
    return fallback


@lru_cache(maxsize=1)
def ui_family() -> str:
    """界面字体：中文可读性优先。"""
    return _pick(UI_FAMILIES, "sans-serif")


@lru_cache(maxsize=1)
def mono_family() -> str:
    """等宽字体：尺寸、DPI、体积这些数字需要列对齐才有用。"""
    return _pick(MONO_FAMILIES, "monospace")


def ui_font(size: int = FONT_MD, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont(ui_family(), size)
    font.setWeight(weight)
    return font


def mono_font(size: int = FONT_MD, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont(mono_family(), size)
    font.setWeight(weight)
    return font


# ----------------------------------------------------------------------
# 主题数据类
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Theme:
    name: str

    # 背景
    bg_primary: str      # 窗口/台面
    bg_secondary: str    # 卡片、输入框、列表
    bg_tertiary: str     # 工具栏、表头
    bg_hover: str        # 悬停
    bg_sunk: str         # 禁用、进度槽、凹陷区

    # 边框
    border: str
    border_strong: str
    # 输入控件专用边框：输入框与卡片同底色，边框是唯一边界，
    # 必须单独满足 WCAG 1.4.11 的 3:1（浅 3.50 / 深 3.43，2026-W42 实算）
    border_input: str

    # 文字
    text_primary: str
    text_secondary: str
    text_tertiary: str
    text_inverse: str    # 强调色按钮上的文字

    # 强调
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_wash: str
    accent_line: str
    accent_hi: str       # 渐变主按钮的亮端

    # 语义
    success: str
    success_wash: str
    warning: str
    warning_wash: str
    danger: str
    danger_wash: str
    info: str
    info_wash: str

    # 规格条（深色块）
    strip: str
    strip_ink: str
    strip_ink_soft: str


LIGHT = Theme(
    name="light",
    bg_primary="#f3f5f8",
    bg_secondary="#ffffff",
    bg_tertiary="#f6f8fb",
    bg_hover="#edf1f6",
    bg_sunk="#e7ebf0",
    border="#e2e7ee",
    border_strong="#c4cdd9",
    border_input="#7f8a99",
    text_primary="#1b2330",
    text_secondary="#5a6572",
    text_tertiary="#8b95a3",
    text_inverse="#ffffff",
    # 钢蓝：比默认蓝更冷更沉，读起来像仪表刻度而不是网页按钮（style.md 四）
    accent="#2e5c8a",
    accent_hover="#3a6b9c",
    accent_pressed="#1f4265",
    accent_wash="#eaf0f7",
    accent_line="#2e5c8a",
    accent_hi="#3a6b9c",
    success="#15803d",
    success_wash="#e0f5e8",
    warning="#b45309",
    warning_wash="#fdf1d7",
    danger="#dc2626",
    danger_wash="#fde8e8",
    info="#2e5c8a",
    info_wash="#eaf0f7",
    strip="#161c26",
    strip_ink="#f5f7fa",
    strip_ink_soft="#9aa5b1",
)

DARK = Theme(
    name="dark",
    bg_primary="#17191d",
    bg_secondary="#22252b",
    bg_tertiary="#1d2025",
    bg_hover="#2c313a",
    bg_sunk="#121417",
    border="#33383f",
    border_strong="#4a525e",
    border_input="#6f7883",
    text_primary="#eef1f5",
    text_secondary="#b7bfc9",
    text_tertiary="#7d8590",
    text_inverse="#10131a",
    accent="#5f8db8",
    accent_hover="#6f9cc6",
    accent_pressed="#7fadde",
    accent_wash="#1b3049",
    accent_line="#5f8db8",
    accent_hi="#6f9cc6",
    success="#4ade80",
    success_wash="#14331d",
    warning="#f0c451",
    warning_wash="#3a3312",
    danger="#f98a94",
    danger_wash="#3d1d1f",
    info="#5f8db8",
    info_wash="#1b3049",
    strip="#101216",
    strip_ink="#eef1f5",
    strip_ink_soft="#7d8590",
)

_current_theme_name: str = "light"


def set_theme(name: str) -> None:
    """切换当前主题名。"""
    global _current_theme_name
    if name not in ("light", "dark"):
        raise ValueError(f"未知的主题名：{name}")
    _current_theme_name = name


def current_theme() -> Theme:
    """返回当前生效的主题实例。"""
    return DARK if _current_theme_name == "dark" else LIGHT


# ----------------------------------------------------------------------
# 系统级「减少动效」（Reduce Motion）
# ----------------------------------------------------------------------
# Windows 的客户端区动画偏好（设置 > 辅助功能 > 视觉效果）由 app.py 启动时
# 经 ctypes SystemParametersInfo 查询后注入；开启时位移类动效（PillToggle
# 高亮滑动等）降级为瞬时切换。reg.exe 被安全策略禁用，故不走注册表直读。

_reduce_motion: bool = False


def set_reduce_motion(value: bool) -> None:
    """记录系统「减少动效」偏好（app.py 启动时调用一次）。"""
    global _reduce_motion
    _reduce_motion = bool(value)


def reduce_motion() -> bool:
    """当前是否应减少动效。"""
    return _reduce_motion


def strip_palette() -> tuple[str, str, str]:
    """规格条的 (背景, 主文字, 次文字)。

    深色主题下规格条保持深色块（唯一深色锚点）；浅色主题下改用浅色底，
    深色读数 —— 用户反馈浅色界面里一整条黑块太突兀。
    """
    t = current_theme()
    if t.name == "dark":
        return t.strip, t.strip_ink, t.strip_ink_soft
    return t.accent_wash, t.text_primary, t.text_secondary


# ----------------------------------------------------------------------
# 向后兼容：旧代码直接导入的常量（保持浅色主题值）
# ----------------------------------------------------------------------
BENCH = LIGHT.bg_primary
SURFACE = LIGHT.bg_secondary
SURFACE_SUNK = LIGHT.bg_sunk
SURFACE_HOVER = LIGHT.bg_hover
INK = LIGHT.text_primary
INK_SOFT = LIGHT.text_secondary
INK_FAINT = LIGHT.text_tertiary
RULE = LIGHT.border
RULE_STRONG = LIGHT.border_strong
ACCENT = LIGHT.accent
ACCENT_DEEP = LIGHT.accent_hover
ACCENT_WASH = LIGHT.accent_wash
ACCENT_LINE = LIGHT.accent_line
STRIP = LIGHT.strip
STRIP_INK = LIGHT.strip_ink
STRIP_INK_SOFT = LIGHT.strip_ink_soft
CAUTION = LIGHT.warning
CAUTION_WASH = LIGHT.warning_wash
DANGER = LIGHT.danger
DANGER_WASH = LIGHT.danger_wash
OK = LIGHT.success
OK_WASH = LIGHT.success_wash

LOG_COLORS = {
    "ok": OK,
    "info": INK,
    "warn": CAUTION,
    "error": DANGER,
}


# ----------------------------------------------------------------------
# 品牌图标：「IMG / CONV 规格框」位图定稿（2026-10-11 定稿）
# ----------------------------------------------------------------------
# 语义：左上白色面板（内含柱状图）= 待处理的源图；右下青色虚线大框 =
# 输出规格；斜向青色双头箭头 = 拉伸到规格的动作；顶部与左侧青色尺寸标注
# 线 = 量尺寸；双十字星 = 精修；IMG（右上）/ CONV（左侧竖排）= 英文名
# 拆字入标。
#
# 视觉基准是设计定稿位图（image-converter-logo/imgspec-logo-A-fontfix.png，
# 仓库外）：tools/make_brand_master.py 给它加圆角蒙版（底板圆角 180/1024，
# 角外透明，图标惯例）并降采样到 512px 后 base64 内嵌进 gui/brand_master.py
# （生成文件）。运行时只做一次解码 + 平滑缩放——窗口图标、顶栏标记、
# 关于页大图与 exe 文件图标（tools/make_icon.py 生成的 icon.ico）全部
# 同源，与设计稿零差异。品牌标记不随界面主题变化。


@lru_cache(maxsize=1)
def _master_pixmap() -> QPixmap:
    """解码内嵌品牌母版（512px 圆角方，角外透明），进程内只解一次。"""
    from gui import brand_master

    data = QByteArray.fromBase64(brand_master.MASTER_B64.encode("ascii"))
    pixmap = QPixmap()
    if not pixmap.loadFromData(data, "PNG"):
        raise RuntimeError("品牌母版解码失败：gui/brand_master.py 内嵌数据损坏")
    return pixmap


def icon_pixmap(size: int, theme_name: str | None = None) -> QPixmap:
    """应用图标：品牌母版平滑缩放到 size×size。

    theme_name 参数保留以兼容旧调用；品牌标记本身不随主题变化。
    """
    return _master_pixmap().scaled(
        size,
        size,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def app_icon(theme_name: str | None = None) -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(icon_pixmap(size, theme_name))
    return icon


# ----------------------------------------------------------------------
# 样式表
# ----------------------------------------------------------------------
def build_stylesheet(theme_name: str | None = None) -> str:
    """整份 QSS。传入主题名即可切换浅色/深色外观。"""
    t = DARK if theme_name == "dark" else LIGHT
    mono = mono_family()
    ui = ui_family()
    # 规格条配色：浅色主题用浅底深字，深色主题保持深色块
    strip_bg, strip_ink, _strip_soft = (
        (t.strip, t.strip_ink, t.strip_ink_soft)
        if t.name == "dark"
        else (t.accent_wash, t.text_primary, t.text_secondary)
    )

    return f"""
/* ---------- 基座 ---------- */
QWidget {{
    background: {t.bg_primary};
    color: {t.text_primary};
    font-family: "{ui}";
    font-size: {FONT_MD}pt;
}}
QMainWindow, QDialog {{
    background: {t.bg_primary};
}}

/* 文字类控件不绘制自己的背景。
   上面那条 QWidget 规则会把窗口灰底刷到每个子控件上，卡片（白底）里的
   标签与勾选项于是变成一块块灰斑，卡片失去整体感 —— 这里显式抹掉。 */
QLabel, QCheckBox, QRadioButton {{
    background: transparent;
}}

/* ---------- 工作区卡片（文件队列 / 日志） ---------- */
QFrame#card {{
    background: {t.bg_secondary};
    border: 1px solid {t.border};
    border-radius: {RADIUS_CARD}px;
}}
QLabel#cardTitle {{
    background: transparent;
    color: {t.text_primary};
    font-size: {FONT_LG}pt;
    font-weight: 600;
}}
/* 顶栏 ghost 按钮：QPushButton（无菜单）与 QToolButton（带下拉菜单）同款。
   两条规则必须严格对齐 —— Qt 的 QToolButton 会为菜单指示器额外留出约 3px
   高度，并各自套用不同的默认内边距，不锁死就会让同一行三个按钮高矮不一。
   故：同一 padding + 同一内容高度区间（min/max-height 取同一值）。 */
QPushButton#ghostBtn,
QToolButton#ghostBtn {{
    background: transparent;
    /* 预留透明焦点环：focus 只改颜色，不改变按钮尺寸或让顶栏跳动。
       原 padding 5px 12px + 无边框；现在 3px 10px + 四边 2px，外框相等。 */
    border: 2px solid transparent;
    border-radius: {RADIUS_CTRL}px;
    padding: 3px 10px;
    min-height: 20px;
    max-height: 20px;
    color: {t.text_secondary};
}}
QPushButton#ghostBtn:focus,
QToolButton#ghostBtn:focus {{
    border: 2px solid {t.accent};
}}
QPushButton#ghostBtn:hover,
QToolButton#ghostBtn:hover {{
    background: {t.bg_hover};
    color: {t.text_primary};
}}
QPushButton#ghostBtn:pressed,
QToolButton#ghostBtn:pressed {{
    background: {t.bg_sunk};
}}
QPushButton#ghostBtn:disabled {{
    color: {t.text_tertiary};
    background: transparent;
}}
/* 下拉箭头显式定位：subcontrol-origin/padding 同源，保证两个
   QToolButton 的箭头垂直居中、距文字间距一致（不靠各 popupMode
   的默认值渲染）。 */
QToolButton#ghostBtn::menu-indicator {{
    subcontrol-origin: padding;
    subcontrol-position: right center;
    width: 9px;
    right: 3px;
}}

/* 描边强调按钮：空状态 CTA 等次级主动作 */
QPushButton#accentOutline {{
    background: {t.accent_wash};
    border: 1px solid {t.accent};
    border-radius: {RADIUS_CTRL}px;
    padding: 7px 18px;
    color: {t.accent};
    font-weight: 600;
}}
QPushButton#accentOutline:focus {{
    /* ID 基态会压过通用 QPushButton:focus，必须同等级显式补齐。
       1px→2px 的同时内收 padding，聚焦前后几何不变。 */
    border: 2px solid {t.accent_pressed};
    padding: 6px 17px;
}}
QPushButton#accentOutline:hover {{
    background: {t.accent};
    color: {t.text_inverse};
}}
QPushButton#accentOutline:pressed {{
    background: {t.accent_pressed};
    border-color: {t.accent_pressed};
    color: {t.text_inverse};
}}

/* ---------- 参数面板：一张卡 + 分区（style.md 面板语法） ---------- */
/* 从「三张彼此独立的卡片」改为「一张连续面板 + 内部分区」。理由是卡片式
   分组会让每个参数域看起来都是一等公民，而参数域之间其实是同一件事的
   三个步骤；连续面板读起来像一台仪器的控制面板，而不是三张并排的表单。
   分区之间用 1px 分隔线（见 QFrame#panelDivider）而不是留白 + 边框。 */
QFrame#sidePanelBody {{
    background: {t.bg_secondary};
    border: 1px solid {t.border};
    border-radius: {RADIUS_CARD}px;
}}
QFrame#panelDivider {{
    background: {t.border};
    border: none;
}}
QGroupBox {{
    background: transparent;
    border: none;
    margin-top: 0px;
    padding-top: 20px;
    padding-bottom: 0px;
    padding-left: 0px;
    padding-right: 0px;
    /* 标题字号/字重必须写在这里：Qt 对 ::title 子控件的 font 支持
       不完整，font-weight 写在 ::title 上会被静默忽略（6.11 实测，
       探针对照确认）；写在分组本体上标题即加粗，组内子控件仍走
       各自的基础规则，不会被连带加粗 */
    font-size: {FONT_MD}pt;
    font-weight: 700;
}}
QGroupBox::title {{
    subcontrol-origin: padding;
    subcontrol-position: top left;
    left: 0px;
    top: 3px;
    background: transparent;
    color: {t.text_primary};
    /* 字号/字重由 QGroupBox 本体负责（见上），这里只管颜色与定位 */
}}

/* 字段标签：9pt 二级灰 —— 原三级灰浅色仅 3.03:1、深色 4.12:1，
   双主题都过不了 WCAG 1.4.11 的 4.5:1（2026-W42 实算）；
   换二级灰后浅 5.93:1 / 深 8.27:1 达标 */
QLabel#fieldLabel {{
    background: transparent;
    color: {t.text_secondary};
    font-size: {FONT_SM}pt;
}}

/* 字段下的常显小字（命名模板变量说明等）：任务必需的说明不能只藏在 tooltip */
QLabel#fieldHint {{
    background: transparent;
    color: {t.text_secondary};
    font-size: {FONT_SM}pt;
}}

/* ---------- 规格条（跟随主题：浅色下浅底深字，深色下保持深色块） ---------- */
QFrame#specStrip {{
    background: {strip_bg};
    border: none;
    border-radius: {RADIUS_CARD}px;
}}
QFrame#specStrip QLabel {{
    background: transparent;
    color: {strip_ink};
    font-family: "{mono}";
    font-size: {FONT_XL}pt;
}}
QFrame#specAccent {{
    background: {t.accent_line};
    border: none;
    border-radius: 2px;
}}

/* ---------- 参数栏容器 ---------- */
QScrollArea#sidePanel {{
    background: transparent;
    border: none;
}}
QScrollArea#sidePanel > QWidget > QWidget {{
    background: transparent;
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: {t.bg_secondary};
    border: 1px solid {t.border};
    border-radius: {RADIUS_CTRL}px;
    padding: 7px 16px;
    color: {t.text_primary};
}}
QPushButton:hover {{
    background: {t.bg_hover};
    border-color: {t.border_strong};
}}
QPushButton:pressed {{
    background: {t.bg_sunk};
    color: {t.text_secondary};
}}
QPushButton:focus {{
    border-color: {t.accent};
}}
QPushButton:disabled {{
    background: {t.bg_sunk};
    border-color: {t.border};
    color: {t.text_tertiary};
}}
QPushButton#primary {{
    background: {t.accent};
    border: 1px solid {t.accent};
    border-radius: {RADIUS_CARD}px;
    color: {t.text_inverse};
    font-size: {FONT_LG}pt;
    font-weight: 600;
    padding: 9px 24px;
}}
QPushButton#primary:focus {{
    border: 2px solid {t.accent_pressed};
    padding: 8px 23px;
}}
QPushButton#primary:hover {{
    background: {t.accent_hover};
    border-color: {t.accent_hover};
}}
QPushButton#primary:pressed {{
    background: {t.accent_pressed};
    border-color: {t.accent_pressed};
}}
QPushButton#primary:disabled {{
    background: {t.bg_sunk};
    border-color: {t.border};
    color: {t.text_tertiary};
}}
QPushButton#danger {{
    background: {t.danger_wash};
    border-color: {t.danger};
    color: {t.danger};
}}
QPushButton#danger:hover {{
    background: {t.danger};
    border-color: {t.danger};
    color: {t.text_inverse};
}}

/* ---------- 输入控件：聚焦光环（整圈描边加粗）是现代表单的标准反馈 ----------
   边框用专用 border_input（双主题对输入框底色 ≥3:1）；hover 提到二级文字色，
   比 base 更深一档，反馈方向正确 */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {t.bg_secondary};
    border: 1px solid {t.border_input};
    border-radius: {RADIUS_CTRL}px;
    padding: 6px 10px;
    color: {t.text_primary};
    selection-background-color: {t.accent_wash};
    selection-color: {t.text_primary};
    min-height: 20px;
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
    border-color: {t.text_secondary};
    background: {t.bg_tertiary};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 2px solid {t.accent};
    padding: 5px 9px;
    background: {t.bg_secondary};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background: {t.bg_sunk};
    border-color: {t.border};
    color: {t.text_tertiary};
}}
QComboBox::drop-down {{
    border: none;
    width: 28px;
}}
/* 下拉指示器由 ChevronComboBox 自绘（gui/widgets.py）：QSS 的 border
   三角做不出圆头笔触的 chevron，硬边与全局圆角语言不符（UI-018）。
   这里只把原生箭头尺寸归零，drop-down 的 28px 点击区仍然保留，
   位置必须与 ChevronComboBox.DROP_ZONE 一致。 */
QComboBox::down-arrow {{
    width: 0px;
    height: 0px;
    border: none;
    image: none;
}}
QComboBox QAbstractItemView {{
    background: {t.bg_secondary};
    border: 1px solid {t.border_strong};
    border-radius: {RADIUS_CARD}px;
    selection-background-color: {t.accent_wash};
    selection-color: {t.text_primary};
    outline: none;
    /* 内边距就是条目高亮与弹层圆角边框之间的「安全距离」：
       高亮落在 padding 内，自然不会顶出直角 */
    padding: 6px;
}}
QComboBox QAbstractItemView::item {{
    min-height: 30px;
    padding: 4px 12px;
    border-radius: 6px;
}}
QComboBox QAbstractItemView::item:selected {{
    background: {t.accent_wash};
    color: {t.text_primary};
}}
QComboBox QAbstractItemView::item:hover {{
    background: {t.bg_hover};
}}

/* 页码就地编辑器：与 chip 同底色同圆角（1px accent 描边表示编辑态），
   没有浮起的白底蓝框 —— 单元格是原地变成输入态 */
QTableWidget QLineEdit#pagesEditor {{
    background: {t.bg_tertiary};
    border: 1px solid {t.accent};
    border-radius: 6px;
    padding: 2px 8px;
    color: {t.accent};
    selection-background-color: {t.accent_wash};
    selection-color: {t.text_primary};
    min-height: 0px;
}}

/* ---------- 勾选与单选 ---------- */
/* min-height 取 20px（+ 上下 padding 2px = 24px 总高）：
   16px 指示器 + 4px 内边距只有 22px，低于 WCAG 2.5.8 的最小点击目标 24×24
   （tools/ui_audit.py 的最小热区断言）。 */
QCheckBox, QRadioButton {{
    spacing: {SPACE_SM}px;
    color: {t.text_primary};
    padding: 2px 0px;
    min-height: 20px;
}}
QCheckBox:disabled, QRadioButton:disabled {{ color: {t.text_tertiary}; }}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: {RADIUS_MICRO}px;
}}
QCheckBox::indicator:unchecked,
QRadioButton::indicator:unchecked {{
    border: 1px solid {t.border_strong};
    background: {t.bg_secondary};
}}
QCheckBox::indicator:hover,
QRadioButton::indicator:hover {{
    border-color: {t.accent};
}}
QCheckBox::indicator:checked {{
    border: 1px solid {t.accent};
    background: {t.accent};
}}
QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 8px;
}}
QRadioButton::indicator:checked {{
    border: 1px solid {t.accent};
    background: {t.accent};
}}

/* ---------- 表格 ---------- */
QTableWidget {{
    background: {t.bg_secondary};
    alternate-background-color: {t.bg_tertiary};
    border: 1px solid {t.border};
    border-radius: {RADIUS_CARD}px;
    gridline-color: transparent;
    selection-background-color: {t.accent_wash};
    selection-color: {t.text_primary};
    outline: none;
}}
QTableWidget::item {{
    padding: 7px 10px;
    border: none;
}}
QTableWidget::item:selected {{
    background: {t.accent_wash};
    color: {t.text_primary};
}}
QTableWidget::item:hover {{
    background: {t.bg_hover};
}}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {t.bg_tertiary};
    border: none;
    border-bottom: 1px solid {t.border};
    padding: 9px 10px;
    color: {t.text_secondary};
    font-size: {FONT_SM}pt;
    font-weight: 600;
}}
/* 表头首末段补圆角：QTableWidget 的圆角不会裁剪子控件，实底表头段
   会盖掉表格上缘两个圆角变直角 —— 半径取 RADIUS_CARD-1，正好贴着
   表格 1px 描边内嵌曲线（表头顶边与表格内缘齐平）。 */
QHeaderView::section:first {{
    border-top-left-radius: {RADIUS_CARD - 1}px;
}}
QHeaderView::section:last {{
    border-top-right-radius: {RADIUS_CARD - 1}px;
}}
QTableCornerButton::section {{
    background: {t.bg_tertiary};
    border: none;
}}

/* ---------- 日志 ---------- */
QPlainTextEdit {{
    background: {t.bg_secondary};
    border: 1px solid {t.border};
    border-radius: {RADIUS_CARD}px;
    padding: 10px 12px;
    font-family: "{mono}";
    font-size: {FONT_MD}pt;
    color: {t.text_primary};
    selection-background-color: {t.accent_wash};
    selection-color: {t.text_primary};
}}

/* ---------- 进度：带百分比文字 ---------- */
QProgressBar {{
    background: {t.bg_sunk};
    border: none;
    border-radius: 8px;
    height: 16px;
    text-align: center;
    color: {t.text_secondary};
    font-size: {FONT_SM}pt;
}}
QProgressBar::chunk {{
    background: {t.accent};
    border-radius: 8px;
}}

/* ---------- 滚动条 ---------- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0px;
}}
QScrollBar::handle:vertical {{
    background: {t.border_strong};
    border-radius: {RADIUS_MICRO}px;
    min-height: 36px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover {{ background: {t.text_tertiary}; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0px;
}}
QScrollBar::handle:horizontal {{
    background: {t.border_strong};
    border-radius: {RADIUS_MICRO}px;
    min-width: 36px;
    margin: 2px;
}}
QScrollBar::handle:horizontal:hover {{ background: {t.text_tertiary}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---------- 分割条：细而干净 ---------- */
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:vertical {{
    height: 6px;
}}
QSplitter::handle:horizontal {{
    width: 6px;
}}
QSplitter::handle:hover {{
    background: {t.accent_wash};
}}

/* ---------- 空状态 / 拖拽提示 ---------- */
QFrame#dropHint {{
    background: {t.bg_secondary};
    border: 2px dashed {t.border_strong};
    border-radius: {RADIUS_CARD}px;
}}
QFrame#dropHint QLabel {{
    background: transparent;
    color: {t.text_secondary};
    font-size: {FONT_SM}pt;
}}
QFrame#dropHint QLabel#dropHintTitle {{
    color: {t.text_primary};
    font-size: {FONT_LG}pt;
    font-weight: 600;
}}
QFrame#dropHint QLabel#dropHintSub {{
    color: {t.text_tertiary};
    font-size: {FONT_SM}pt;
}}
QFrame#dropHintActive {{
    background: {t.accent_wash};
    border: 2px dashed {t.accent};
    border-radius: {RADIUS_CARD}px;
}}
QFrame#dropHintActive QLabel {{
    background: transparent;
    color: {t.accent};
}}
QFrame#dropHintActive QLabel#dropHintTitle,
QFrame#dropHintActive QLabel#dropHintSub {{
    color: {t.accent};
}}

/* ---------- 顶栏 / 状态与计数 ---------- */
QLabel#appTitle {{
    background: transparent;
    color: {t.text_primary};
    font-size: {FONT_2XL}pt;
    font-weight: 700;
}}
QLabel#appSubtitle {{
    background: transparent;
    color: {t.text_tertiary};
    font-size: {FONT_SM}pt;
}}
QLabel#topBarLabel {{
    background: transparent;
    color: {t.text_tertiary};
    font-size: {FONT_SM}pt;
}}
QLabel#statusLabel {{
    color: {t.text_secondary};
    font-size: {FONT_SM}pt;
}}
QLabel#countLabel {{
    color: {t.text_tertiary};
    font-size: {FONT_SM}pt;
}}
QLabel#outputPath {{
    font-family: "{mono}";
    font-size: {FONT_SM}pt;
    color: {t.text_primary};
}}
QLabel#hintLabel {{
    color: {t.text_tertiary};
    font-size: {FONT_SM}pt;
    font-style: italic;
}}

/* ---------- Toast 完成通知 ---------- */
/* 外层只负责定位与淡入淡出（透明底），底色/描边/投影在 #toast 面板上 —
   一个控件只能挂一个 QGraphicsEffect，两层拆分才能同时有投影与透明度 */
QWidget#toastHost {{
    background: transparent;
}}
QFrame#toast {{
    background: {t.bg_secondary};
    border: 1px solid {t.border};
    border-radius: {RADIUS_CARD}px;
}}
QFrame#toast QLabel {{
    background: transparent;
    color: {t.text_primary};
}}
QFrame#toast QLabel#toastTitle {{
    font-weight: 600;
}}
QFrame#toast QLabel#toastBody {{
    color: {t.text_secondary};
    font-size: {FONT_SM}pt;
}}
QPushButton#toastClose {{
    background: transparent;
    border: none;
    border-radius: {RADIUS_MICRO}px;
    padding: 2px 6px;
    color: {t.text_tertiary};
    font-weight: 700;
}}
QPushButton#toastClose:hover {{
    background: {t.bg_hover};
    color: {t.text_primary};
}}
QPushButton#toastAction {{
    background: transparent;
    border: none;
    border-radius: {RADIUS_MICRO}px;
    padding: 4px 8px;
    color: {t.accent};
    font-weight: 600;
}}
QPushButton#toastAction:hover {{
    background: {t.accent_wash};
}}

/* ---------- 提示气泡 ---------- */
QToolTip {{
    background: {t.strip};
    color: {t.strip_ink};
    border: none;
    border-radius: 8px;
    padding: 7px 10px;
    font-size: {FONT_SM}pt;
}}

/* ---------- 右键菜单 ---------- */
QMenu {{
    background: {t.bg_secondary};
    border: 1px solid {t.border};
    border-radius: {RADIUS_CARD}px;
    padding: 5px;
}}
QMenu::item {{
    padding: 8px 26px 8px 14px;
    border-radius: 6px;
    color: {t.text_primary};
}}
QMenu::item:selected {{
    background: {t.accent_wash};
    color: {t.text_primary};
}}
QMenu::item:disabled {{
    color: {t.text_tertiary};
}}
QMenu::separator {{
    height: 1px;
    background: {t.border};
    margin: 5px 10px;
}}

/* ---------- 对话框：按钮规格与主界面一致 ---------- */
QMessageBox {{
    background: {t.bg_primary};
}}
QMessageBox QLabel {{
    color: {t.text_primary};
    font-size: {FONT_MD}pt;
}}
QMessageBox QPushButton {{
    min-width: 92px;
    min-height: 30px;
}}
"""


__all__ = [
    "ANIM_BASE_MS",
    "ANIM_EASE_IN_OUT",
    "ANIM_EASE_OUT",
    "ANIM_FAST_MS",
    "ANIM_SLOW_MS",
    "DARK",
    "LIGHT",
    "FONT_2XL",
    "FONT_LG",
    "RADIUS_CARD",
    "RADIUS_CTRL",
    "FONT_MD",
    "FONT_SM",
    "FONT_XL",
    "SPACE_2XL",
    "SPACE_LG",
    "SPACE_MD",
    "SPACE_SM",
    "SPACE_XL",
    "SPACE_XS",
    "Theme",
    "app_icon",
    "build_stylesheet",
    "current_theme",
    "icon_pixmap",
    "mono_family",
    "mono_font",
    "set_theme",
    "strip_palette",
    "ui_family",
    "ui_font",
]
