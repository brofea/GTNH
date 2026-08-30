#!/usr/bin/env python3
"""从 GTNH 中文维基的静态快照中选择并下载模组。

运行时不会请求维基页面。模组列表和每行的第一个 GitHub、CurseForge 或
Modrinth 地址都在本文件中；下载时只访问对应平台的公开元数据接口。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import select
import shutil
import ssl
import sys
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

if os.name == "posix":
    import termios
    import tty
else:  # pragma: no cover - Windows 使用 msvcrt，不需要 POSIX 终端模块。
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]


SOURCE_PAGE = "https://gtnh.huijiwiki.com/wiki/%E5%8F%AF%E6%B7%BB%E5%8A%A0MOD"
SNAPSHOT_DATE = "2026-08-30"
MINECRAFT_VERSION = "1.7.10"
HTTP_TIMEOUT = 30
USER_AGENT = "gtnh-mod-downloader/1.0 (+https://github.com/)"

try:
    import certifi  # type: ignore
except ImportError:  # 标准 Python 没有 certifi 时仍使用系统证书库。
    certifi = None  # type: ignore[assignment]


def _ssl_context() -> ssl.SSLContext:
    """使用系统证书；若 Python 自带证书库缺失，优先使用已存在的 certifi。"""
    if certifi is not None:
        try:
            return ssl.create_default_context(cafile=certifi.where())
        except (OSError, AttributeError):
            pass
    return ssl.create_default_context()


@dataclass(frozen=True)
class Mod:
    """从维基表格保留下来的最小模组信息。"""

    name: str
    category: str
    source_url: Optional[str]

    @property
    def source(self) -> str:
        if not self.source_url:
            return "无直链"
        host = urlparse(self.source_url).netloc.lower()
        if "github.com" in host:
            return "GitHub"
        if "curseforge.com" in host:
            return "CurseForge"
        if "modrinth.com" in host:
            return "Modrinth"
        return "其他"


@dataclass(frozen=True)
class DownloadInfo:
    """某个平台解析出的实际文件地址。"""

    provider: str
    filename: str
    download_url: str
    manual_url: str


@dataclass(frozen=True)
class DownloadResult:
    mod: Mod
    path: Optional[Path]
    error: Optional[str]


# 这是 2026-08-30 对 SOURCE_PAGE 的一次性静态快照。
# 只保留了表格中的模组名、分类和相关地址列中第一个支持的平台链接。
MODS: Tuple[Mod, ...] = (
    Mod("Inputfix 中文输入修复补丁", "星门规则模组/功能增强", "https://www.curseforge.com/minecraft/mc-mods/inputfix"),
    Mod("NeverEnoughCharacters-Rework NEI拼音搜索－重制版", "星门规则模组/功能增强", "https://github.com/asdflj/NeverEnoughCharacters-Rework"),
    Mod("AE2 Auto Pattern Upload AE2样板自动上传", "星门规则模组/功能增强", "https://github.com/GaLicn/AE2-Auto-Pattern-Upload/"),
    Mod("Untranslator 未转译者", "星门规则模组/功能增强", "https://github.com/RealSilverMoon/Untranslator"),
    Mod("MouseSideButtonFix 侧键修复", "星门规则模组/功能增强", "https://github.com/asdflj/MouseSideButtonFix"),
    Mod("WorldEdit 创世神", "星门规则模组/功能增强", "https://github.com/GTNewHorizons/worldedit-gtnh"),
    Mod("OmniOcular", "星门规则模组/功能增强", "https://www.curseforge.com/minecraft/mc-mods/omni-ocular"),
    Mod("Damage Indicators 伤害显示", "星门规则模组/功能增强", "https://www.curseforge.com/minecraft/mc-mods/damage-indicators-mod"),
    Mod("NEI-RecipeTree NEI配方树", "星门规则模组/功能增强", "https://github.com/XSana/NEI-RecipeTree"),
    Mod("Applied Cooking 应用厨房GTNH版", "星门规则模组/功能增强", "https://github.com/asdflj/AppliedCooking"),
    Mod("InputMethodBlocker-GTNH 输入法冲突修复", "星门规则模组/功能增强", "https://github.com/HOMEFTW/InputMethodBlocker-GTNH/releases"),
    Mod("JdkJarVersion21Enforcer Java25启动修复", "星门规则模组/功能增强", "https://github.com/HOMEFTW/JdkJarVersion21Enforcer"),
    Mod("ModernKeyBinding 现代化按键绑定", "星门规则模组/功能增强", "https://github.com/Nova-Committee/ModernKeyBinding"),
    Mod("Smooth Font 平滑字体", "星门规则模组/性能优化", "https://www.curseforge.com/minecraft/mc-mods/smooth-font"),
    Mod("Qz-UILib Qz-UI库", "星门规则模组/性能优化", "https://github.com/QuanhuZeYu/Qz-UILib"),
    Mod("FPS Reducer FPS减速器", "星门规则模组/性能优化", "https://www.curseforge.com/minecraft/mc-mods/fps-reducer"),
    Mod("Raw Mouse Input 鼠标原始输入修复", "星门规则模组/性能优化", "https://www.curseforge.com/minecraft/mc-mods/raw-input-1-12-2"),
    Mod("NoFog 没有雾", "星门规则模组/性能优化", "https://www.curseforge.com/minecraft/mc-mods/nofog"),
    Mod("Lag Goggles: Legacy 延迟监视：移植版", "星门规则模组/性能优化", "https://github.com/FalsePattern/LagGogglesLegacy"),
    Mod("SmallPhone 小手机", "星门规则模组/视听增强", "https://github.com/RealSilverMoon/SmallPhone/releases"),
    Mod("FMusic", "星门规则模组/视听增强", "https://github.com/czqwq/FMusic"),
    Mod("Mineshot 高清截图", "星门规则模组/视听增强", "https://github.com/ABKQPO/mineshot"),
    Mod("MyCTMLib 连接纹理库支持", "星门规则模组/视听增强", "https://github.com/ABKQPO/MyCTMLib"),
    Mod("DarkTextFix 暗色文本修复", "星门规则模组/视听增强", "https://github.com/eudesjuniorr/DarkTextFix"),
    Mod("World Tooltips 掉落物信息显示", "星门规则模组/视听增强", "https://www.curseforge.com/minecraft/mc-mods/world-tooltips"),
    Mod("BakaDanmaku 直播弹幕模组", "星门规则模组/视听增强", None),
    Mod("Better Foliage 更好的树叶", "星门规则模组/视听增强", "https://www.curseforge.com/minecraft/mc-mods/better-foliage"),
    Mod("Better Rain 更好的降雨", "星门规则模组/视听增强", None),
    Mod("BetterTooltipBox 更好的提示框", "星门规则模组/视听增强", "https://github.com/xiaoxing2005/BetterTooltipBox/releases"),
    Mod("Chat Bubbles 聊天泡泡", "星门规则模组/视听增强", "https://www.curseforge.com/minecraft/mc-mods/chat-bubbles"),
    Mod("Dynamic Surroundings 动态环境", "星门规则模组/视听增强", "https://www.curseforge.com/minecraft/mc-mods/dynamic-surroundings"),
    Mod("Extra Player Renderer 额外玩家渲染", "星门规则模组/视听增强", "https://www.curseforge.com/minecraft/mc-mods/extra-player-render"),
    Mod("Fancy Block Particles 梦幻方块效果", "星门规则模组/视听增强", "https://www.curseforge.com/minecraft/mc-mods/fancy-block-particles"),
    Mod("ItemPhysic Lite 物品掉落物理-轻量版", "星门规则模组/视听增强", "https://www.curseforge.com/minecraft/mc-mods/itemphysic-lite"),
    Mod("MAtmos 真实环境音效", "星门规则模组/视听增强", "https://www.curseforge.com/minecraft/mc-mods/matmos"),
    Mod("SkinPort", "星门规则模组/视听增强", "https://www.curseforge.com/minecraft/mc-mods/skinport"),
    Mod("Cosmetic Armor Reworked 时装盔甲重置版", "星门规则模组/视听增强", "https://www.curseforge.com/minecraft/mc-mods/cosmetic-armor-reworked"),
    Mod("Distant Horizons Standalone Distant Horizons GTNH版", "星门规则模组/视听增强", "https://github.com/DarkShadow44/DistantHorizonsStandalone"),
    Mod("Better Line Break 更好的换行", "星门规则模组/视听增强", "https://github.com/KatatsumuriPan/BetterLineBreak"),
    Mod("Angelica 安洁莉卡/天使优化", "星门规则模组/旧版本限定", "https://github.com/GTNewHorizons/Angelica/releases"),
    Mod("Server Utilities 服务器实用工具", "星门规则模组/旧版本限定", "https://github.com/GTNewHorizons/ServerUtilities"),
    Mod("Advanced Backups 高级备份", "星门规则模组/旧版本限定", "https://www.curseforge.com/minecraft/mc-mods/aromabackup"),
    Mod("AromaBackup 存档备份", "星门规则模组/旧版本限定", "https://www.curseforge.com/minecraft/mc-mods/aromabackup"),
    Mod("Backhand Unofficial 副手 非官方版", "星门规则模组/旧版本限定", "https://github.com/GTNewHorizons/Backhand"),
    Mod("ZeroPointBugfix 零级BUG修复", "星门规则模组/旧版本限定", "https://github.com/wohaopa/ZeroPointServerBugfix"),
    Mod("Crafting Tweaks 合成辅助", "星门规则模组/旧版本限定", "https://www.curseforge.com/minecraft/mc-mods/crafting-tweaks"),
    Mod("Dynamic Lights 动态光源", "星门规则模组/旧版本限定", "https://www.curseforge.com/minecraft/mc-mods/dynamic-lights/"),
    Mod("Big Chat History 更多聊天记录", "星门规则模组/旧版本限定", None),
    Mod("Not Enough Characters NEI 拼音搜索", "星门规则模组/旧版本限定", "https://www.curseforge.com/minecraft/mc-mods/not-enough-characters"),
    Mod("Link Info 链接信息", "星门规则模组/旧版本限定", None),
    Mod("Twist Space Technology Mod 扭曲空间科技", "非星门规则", "https://github.com/Nxer/Twist-Space-Technology-Mod/releases"),
    Mod("Programmable Hatch Mod 可编程仓室", "非星门规则", "https://github.com/reobf/Programmable-Hatches-Mod/releases"),
    Mod("GT Not Leisure 格雷不休闲 & 科技不休闲", "非星门规则", "https://github.com/ABKQPO/GT-Not-Leisure"),
    Mod("Box Plus Plus 盒", "非星门规则", "https://github.com/RealSilverMoon/BoxPlusPlus"),
    Mod("AE2 Thing AE2 Thing", "非星门规则", "https://github.com/asdflj/AE2Things"),
    Mod("EZNuclear 核电轻而易举", "非星门规则", "https://github.com/czqwq/EZNuclear"),
    Mod("EZMiner 轻松连锁", "非星门规则", "https://github.com/czqwq/EZMiner"),
    Mod("Bandit-Legacy", "非星门规则", "https://github.com/ElytraServers/Bandit-Legacy"),
    Mod("Qz-Miner 爆破连锁", "非星门规则", "https://github.com/QuanhuZeYu/Qz-Miner"),
    Mod("123Technology 123科技", "非星门规则", "https://github.com/CallmeSHaobe/123Technology"),
    Mod("MessTech 混乱科技", "非星门规则", "https://github.com/czqwq/MessTech"),
    Mod("EyeOfHarmonyBuffer 万宁鸿蒙", "非星门规则", "https://github.com/SafariXiu/EyeOfHarmonyBuffer/tree/master"),
    Mod("GTNN 格雷新视野：憋削了！", "非星门规则", "https://github.com/ElytraServers/gtnh-no-nerf"),
    Mod("NHU 格雷新视野：实用工具", "非星门规则", "https://github.com/Keriils/NH-Utilities"),
    Mod("TakoTech 塔可科技", "非星门规则", "https://github.com/XSana/TakoTech"),
    Mod("Think Tech 俺寻思科技", "非星门规则", "https://github.com/Ol925/ThinkTech"),
    Mod("CheaperVoidMiners 更前期的虚空矿机", "非星门规则", "https://github.com/Jonodonozym/CheaperVoidMiners"),
    Mod("GT-Not-Hard 格雷不难", "非星门规则", "https://github.com/z1564058782/GT-Not-Hard"),
    Mod("Nyx", "非星门规则", "https://github.com/RhyVis/GTNH-Nyx"),
    Mod("Void-Miner-Tweak-Mod", "非星门规则", "https://github.com/reobf/Void-Miner-Tweak-Mod"),
    Mod("Torcherino-GTNH", "非星门规则", "https://github.com/czqwq/Torcherino-GTNH"),
    Mod("Time Crystal", "非星门规则", "https://github.com/kuolemax/time-crystal"),
    Mod("AE2PatternGen AE2PatternGen", "非星门规则", "https://github.com/Ch4oooooooLL/AE2PatternGen"),
    Mod("WildcardPattern 通配样板", "非星门规则", "https://github.com/clfpwp/WildcardPatternforGTNH-1.7.10"),
    Mod("GTNH Modify 万宁NH", "非星门规则", "https://github.com/ElytraServers/GTNH-CutCorners"),
    Mod("GT-Simple-Wireless-Network 简单无线网络", "非星门规则", "https://github.com/MIAOKATZE/GT-Simple-Wireless-Network"),
    Mod("GT-Interesting-Thing 有趣事物", "非星门规则", "https://github.com/MIAOKATZE/GT-Interesting-Thing"),
    Mod("GT-Steam-Reborn 蒸汽重生", "非星门规则", "https://github.com/MIAOKATZE/GT-Steam-Reborn"),
    Mod("AE2InfinityCell AE2无限存储元件", "非星门规则", "https://github.com/DancingSnow0517/AE2InfinityCell/releases"),
    Mod("Applied Energistics: EU Network AE EU 网络", "非星门规则", "https://github.com/DancingSnow0517/Applied-Energistics-EU-Network/releases"),
    Mod("Advanced Memory Card 高级内存卡", "非星门规则", "https://github.com/suntide-20210418/AdvancedMemoryCard/releases"),
    Mod("Fission-Evolved 裂变进化", "非星门规则", "https://github.com/shenFNX/Fission-Evolved/releases"),
)


class DownloadError(RuntimeError):
    """下载元数据或文件失败。"""


def _request(url: str, *, accept: str = "*/*") -> Request:
    return Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": accept,
        },
    )


def _get_json(url: str) -> Any:
    try:
        with urlopen(
            _request(url, accept="application/json"),
            timeout=HTTP_TIMEOUT,
            context=_ssl_context(),
        ) as response:
            body = response.read()
    except HTTPError as exc:
        raise DownloadError("HTTP {}：{}".format(exc.code, url)) from exc
    except URLError as exc:
        raise DownloadError("网络错误：{}".format(exc.reason)) from exc
    except OSError as exc:
        raise DownloadError("网络错误：{}".format(exc)) from exc

    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DownloadError("服务器返回的不是有效 JSON：{}".format(url)) from exc


def _github_repo(url: str) -> Tuple[str, str]:
    parts = [part for part in urlparse(url).path.split("/") if part]
    if len(parts) < 2:
        raise DownloadError("无法识别 GitHub 仓库地址")
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


def _jar_asset(release: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    assets = [asset for asset in release.get("assets", []) if isinstance(asset, dict)]
    jars = [asset for asset in assets if str(asset.get("name", "")).lower().endswith(".jar")]
    if not jars:
        return None

    def score(asset: Dict[str, Any]) -> Tuple[int, int]:
        name = str(asset.get("name", "")).lower()
        auxiliary = ("source", "sources", "javadoc", "api", "dev")
        return (1 if any(word in name for word in auxiliary) else 0, len(name))

    return sorted(jars, key=score)[0]


def _github_download(mod: Mod) -> DownloadInfo:
    owner, repo = _github_repo(mod.source_url or "")
    api_base = "https://api.github.com/repos/{}/{}".format(quote(owner), quote(repo))
    releases: List[Dict[str, Any]] = []
    latest_error: Optional[Exception] = None

    try:
        latest = _get_json(api_base + "/releases/latest")
        if isinstance(latest, dict):
            releases.append(latest)
    except DownloadError as exc:
        latest_error = exc

    # 某些仓库没有“Latest”标记，但仍有普通 release。
    need_fallback = not releases or _jar_asset(releases[0]) is None
    if need_fallback:
        try:
            older = _get_json(api_base + "/releases?per_page=30")
            if isinstance(older, list):
                releases.extend(item for item in older if isinstance(item, dict))
        except DownloadError as exc:
            if not releases:
                raise latest_error or exc

    for release in releases:
        asset = _jar_asset(release)
        if asset:
            filename = str(asset.get("name", "mod.jar"))
            download_url = str(asset.get("browser_download_url", ""))
            if download_url:
                return DownloadInfo("GitHub", filename, download_url, mod.source_url or "")

    raise DownloadError("GitHub 最新 release 没有 JAR 资产")


def _modrinth_slug(url: str) -> str:
    parts = [part for part in urlparse(url).path.split("/") if part]
    try:
        index = [part.lower() for part in parts].index("mod")
        return parts[index + 1]
    except (ValueError, IndexError) as exc:
        raise DownloadError("无法识别 Modrinth 项目地址") from exc


def _modrinth_file(versions: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(versions, list):
        return None
    for version in versions:
        if not isinstance(version, dict):
            continue
        files = [item for item in version.get("files", []) if isinstance(item, dict)]
        jars = [item for item in files if str(item.get("filename", "")).lower().endswith(".jar")]
        if jars:
            primary = next((item for item in jars if item.get("primary")), None)
            return primary or jars[0]
    return None


def _modrinth_download(mod: Mod) -> DownloadInfo:
    slug = _modrinth_slug(mod.source_url or "")
    api = "https://api.modrinth.com/v2/project/{}/version".format(quote(slug, safe=""))
    query = urlencode(
        {
            "loaders": json.dumps(["forge"]),
            "game_versions": json.dumps([MINECRAFT_VERSION]),
        }
    )
    versions = _get_json(api + "?" + query)
    file_info = _modrinth_file(versions)

    if not file_info:
        # 接口的筛选参数在旧项目上偶尔不完整，退一步读取项目版本，
        # 但仍只接受明确声明支持 1.7.10 的版本，避免误下现代版本。
        all_versions = _get_json(api)
        compatible = [
            item
            for item in all_versions
            if isinstance(item, dict) and MINECRAFT_VERSION in item.get("game_versions", [])
        ] if isinstance(all_versions, list) else []
        file_info = _modrinth_file(compatible)

    if not file_info or not file_info.get("url"):
        raise DownloadError("Modrinth 没有找到支持 {} 的 JAR".format(MINECRAFT_VERSION))
    return DownloadInfo(
        "Modrinth",
        str(file_info.get("filename", "mod.jar")),
        str(file_info["url"]),
        mod.source_url or "",
    )


def _curseforge_slug(url: str) -> str:
    parts = [part for part in urlparse(url).path.split("/") if part]
    lowered = [part.lower() for part in parts]
    try:
        index = lowered.index("mc-mods")
        return parts[index + 1]
    except (ValueError, IndexError) as exc:
        raise DownloadError("无法识别 CurseForge 项目地址") from exc


def _curseforge_download(mod: Mod) -> DownloadInfo:
    slug = _curseforge_slug(mod.source_url or "")
    data = _get_json("https://api.cfwidget.com/minecraft/mc-mods/{}".format(quote(slug, safe="")))
    versions = data.get("versions", {}) if isinstance(data, dict) else {}
    files = versions.get(MINECRAFT_VERSION, []) if isinstance(versions, dict) else []
    if not files and isinstance(data, dict):
        files = [
            item
            for item in data.get("files", [])
            if isinstance(item, dict) and MINECRAFT_VERSION in item.get("versions", [])
        ]
    files = [item for item in files if isinstance(item, dict) and str(item.get("name", "")).lower().endswith(".jar")]
    if not files:
        raise DownloadError("CurseForge 没有找到支持 {} 的 JAR".format(MINECRAFT_VERSION))

    file_info = max(files, key=lambda item: str(item.get("uploaded_at", "")))
    file_id = file_info.get("id")
    filename = str(file_info.get("name") or file_info.get("display") or "mod.jar")
    if not file_id:
        raise DownloadError("CurseForge 文件信息缺少文件 ID")

    # CurseForge 页面本身可能被反爬拦截，Forge CDN 直链不需要 API key。
    try:
        first = int(file_id) // 1000
        second = int(file_id) % 1000
    except (TypeError, ValueError) as exc:
        raise DownloadError("CurseForge 文件 ID 无效") from exc
    download_url = "https://edge.forgecdn.net/files/{}/{}/{}".format(
        first, second, quote(filename, safe="")
    )
    return DownloadInfo("CurseForge", filename, download_url, mod.source_url or "")


def resolve_download(mod: Mod) -> DownloadInfo:
    """把维基上的项目页解析为实际的最新 JAR 下载信息。"""
    if not mod.source_url:
        raise DownloadError("该表格行没有 GitHub、CurseForge 或 Modrinth 链接")
    host = urlparse(mod.source_url).netloc.lower()
    if "github.com" in host:
        return _github_download(mod)
    if "curseforge.com" in host:
        return _curseforge_download(mod)
    if "modrinth.com" in host:
        return _modrinth_download(mod)
    raise DownloadError("不支持的下载平台")


def _safe_filename(name: str, fallback: str) -> str:
    name = Path(name.replace("\\", "/")).name.strip()
    if not name or name in {".", ".."}:
        name = Path(fallback.replace("\\", "/")).name.strip()
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = re.sub(r"[^\w .()\[\]-]", "_", name, flags=re.UNICODE)
    if not name or name in {".", ".."}:
        name = "mod"
    if not name.lower().endswith(".jar"):
        name += ".jar"
    return name


def _unique_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    counter = 1
    while True:
        candidate = directory / "{} ({}){}".format(stem, counter, suffix)
        if not candidate.exists():
            return candidate
        counter += 1


def downloads_directory() -> Path:
    """返回系统下载目录，并兼容 Linux 的 XDG 目录设置。"""
    if os.name != "nt":
        config = Path.home() / ".config/user-dirs.dirs"
        if config.is_file():
            try:
                for line in config.read_text(encoding="utf-8").splitlines():
                    if line.startswith("XDG_DOWNLOAD_DIR="):
                        value = line.split("=", 1)[1].strip().strip('"')
                        value = value.replace("$HOME", str(Path.home()))
                        if value:
                            return Path(value).expanduser()
            except OSError:
                pass
    return Path.home() / "Downloads"


def download_file(info: DownloadInfo, directory: Path, fallback_name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = _unique_path(directory, _safe_filename(info.filename, fallback_name))
    partial = target.with_name(target.name + ".part")
    try:
        with urlopen(
            _request(info.download_url),
            timeout=HTTP_TIMEOUT,
            context=_ssl_context(),
        ) as response:
            with partial.open("wb") as output:
                first = response.read(4)
                if not first.startswith(b"PK"):
                    raise DownloadError("下载内容不是 JAR 文件，可能被网站拦截")
                output.write(first)
                while True:
                    chunk = response.read(1024 * 128)
                    if not chunk:
                        break
                    output.write(chunk)
        os.replace(str(partial), str(target))
    except HTTPError as exc:
        _remove_if_exists(partial)
        raise DownloadError("下载 HTTP {}：{}".format(exc.code, info.download_url)) from exc
    except (URLError, OSError) as exc:
        _remove_if_exists(partial)
        raise DownloadError("下载失败：{}".format(exc)) from exc
    except Exception:
        _remove_if_exists(partial)
        raise
    return target


def _remove_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def download_selected(selected: Sequence[Mod], directory: Path) -> List[DownloadResult]:
    results: List[DownloadResult] = []
    print("\n下载目录：{}".format(directory))
    for number, mod in enumerate(selected, 1):
        print("\n[{}/{}] {}".format(number, len(selected), mod.name))
        try:
            info = resolve_download(mod)
            print("  {} 最新文件：{}".format(info.provider, info.filename))
            path = download_file(info, directory, mod.name)
            print("  已保存：{}".format(path))
            results.append(DownloadResult(mod, path, None))
        except DownloadError as exc:
            print("  自动下载失败：{}".format(exc))
            results.append(DownloadResult(mod, None, str(exc)))
        except Exception as exc:
            print("  自动下载失败：{}".format(exc))
            results.append(DownloadResult(mod, None, str(exc)))
    return results


# ---------- 无依赖终端界面 ----------


KEY_UP = "up"
KEY_DOWN = "down"
KEY_PAGE_UP = "page_up"
KEY_PAGE_DOWN = "page_down"
KEY_ENTER = "enter"
KEY_ESCAPE = "escape"
KEY_BACKSPACE = "backspace"


def _display_width(text: str) -> int:
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in "WFA" else 1
    return width


def _clip(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if _display_width(text) <= width:
        return text
    result: List[str] = []
    used = 0
    for char in text:
        char_width = 0 if unicodedata.combining(char) else (2 if unicodedata.east_asian_width(char) in "WFA" else 1)
        if used + char_width > width - 1:
            break
        result.append(char)
        used += char_width
    return "".join(result) + "…"


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def _source_name(mod: Mod) -> str:
    return mod.source


class Terminal:
    def __init__(self) -> None:
        self.fd: Optional[int] = None
        self.owned = False
        for path in ("/dev/tty", "CONIN$"):
            try:
                self.fd = os.open(path, os.O_RDWR)
                self.owned = True
                break
            except OSError:
                continue
        if self.fd is None:
            try:
                self.fd = sys.stdin.fileno()
            except (AttributeError, OSError):
                self.fd = None

    @property
    def interactive(self) -> bool:
        return self.fd is not None and os.isatty(self.fd)

    def close(self) -> None:
        if self.owned and self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass


@contextmanager
def _raw_terminal(fd: int) -> Iterable[None]:
    original = None
    if os.name == "posix" and os.isatty(fd):
        original = termios.tcgetattr(fd)
        tty.setraw(fd)
    try:
        yield
    finally:
        if original is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, original)


def _read_key(fd: int) -> str:
    if os.name == "nt":
        import msvcrt

        char = msvcrt.getwch()
        if char in ("\x00", "\xe0"):
            arrows = {"H": KEY_UP, "P": KEY_DOWN, "I": KEY_PAGE_UP, "Q": KEY_PAGE_DOWN}
            return arrows.get(msvcrt.getwch(), "")
        if char in ("\r", "\n"):
            return KEY_ENTER
        if char == "\x08":
            return KEY_BACKSPACE
        if char == "\x1b":
            return KEY_ESCAPE
        if char == "\x03":
            return "q"
        return char

    raw = os.read(fd, 1)
    if not raw:
        return "q"
    if raw != b"\x1b":
        if raw in (b"\r", b"\n"):
            return KEY_ENTER
        if raw in (b"\x7f", b"\x08"):
            return KEY_BACKSPACE
        if raw == b"\x03":
            return "q"
        return raw.decode("utf-8", errors="ignore")

    sequence = bytearray()
    while select.select([fd], [], [], 0.04)[0]:
        sequence.extend(os.read(fd, 1))
    values = {
        b"[A": KEY_UP,
        b"[B": KEY_DOWN,
        b"[5~": KEY_PAGE_UP,
        b"[6~": KEY_PAGE_DOWN,
        b"OA": KEY_UP,
        b"OB": KEY_DOWN,
    }
    return values.get(bytes(sequence), KEY_ESCAPE)


def _color(code: str, text: str, enabled: bool) -> str:
    return "{}{}\033[0m".format(code, text) if enabled else text


def _visible_indices(query: str) -> List[int]:
    normalized = query.casefold().strip()
    if not normalized:
        return list(range(len(MODS)))
    return [
        index
        for index, mod in enumerate(MODS)
        if normalized in "{} {} {}".format(mod.name, mod.category, mod.source).casefold()
    ]


def _draw(
    visible: Sequence[int],
    cursor: int,
    selected: Set[int],
    query: str,
    search_mode: bool,
    message: str,
) -> None:
    columns, rows = shutil.get_terminal_size((100, 24))
    color = bool(sys.stdout.isatty() and os.environ.get("TERM") != "dumb")
    list_height = max(1, rows - 7)
    start = max(0, min(cursor - list_height // 2, max(0, len(visible) - list_height)))
    end = min(len(visible), start + list_height)
    group_width = 14
    source_width = 10
    fixed_width = 3 + 4 + group_width + source_width + 10
    name_width = max(12, columns - fixed_width)

    output = ["\033[2J\033[H\033[?25l"]
    title = "GTNH 可添加 MOD 下载器"
    output.append(_color("\033[1;36m", title, color))
    output.append("  快照：{}  |  共 {} 项，已选 {} 项".format(SNAPSHOT_DATE, len(MODS), len(selected)))
    if query:
        output.append("  筛选：{}  （按 / 修改，Esc 清除）".format(query))
    else:
        output.append("  ↑↓移动  Space选择  Enter下载  /筛选  a全选  n清空  q退出")
    output.append("")
    header = "  {} {} {} {}".format("编号", _pad("组别", group_width), _pad("模组名", name_width), "来源")
    output.append(_color("\033[1;33m", header, color))

    for view_index in range(start, end):
        mod_index = visible[view_index]
        mod = MODS[mod_index]
        marker = "✓" if mod_index in selected else " "
        number = "{:>3}".format(mod_index + 1)
        group = mod.category.split("/")[-1]
        line = "{} {} {} {} {}".format(
            marker,
            number,
            _pad(_clip(group, group_width), group_width),
            _pad(_clip(mod.name, name_width), name_width),
            _clip(_source_name(mod), source_width),
        )
        if view_index == cursor:
            line = _color("\033[7m", line, color)
        elif mod_index in selected:
            line = _color("\033[32m", line, color)
        output.append(line)

    if not visible:
        output.append("  没有匹配的模组。")
    output.append("")
    if search_mode:
        output.append("  搜索：{}_  （Enter确定，Esc取消，Backspace删除）".format(query))
    elif message:
        output.append("  {}".format(message))
    else:
        output.append("  支持多选；下载失败时会在结束页显示可手动打开的项目链接。")
    sys.stdout.write("\n".join(output) + "\033[?25h")
    sys.stdout.flush()


def _selector() -> List[Mod]:
    terminal = Terminal()
    if not terminal.interactive or terminal.fd is None:
        terminal.close()
        return _line_selector()

    selected: Set[int] = set()
    query = ""
    search_mode = False
    message = ""
    cursor = 0
    try:
        with _raw_terminal(terminal.fd):
            while True:
                visible = _visible_indices(query)
                if visible:
                    cursor = max(0, min(cursor, len(visible) - 1))
                else:
                    cursor = 0
                _draw(visible, cursor, selected, query, search_mode, message)
                key = _read_key(terminal.fd)

                if search_mode:
                    if key == KEY_ENTER:
                        query = query.strip()
                        search_mode = False
                        cursor = 0
                    elif key == KEY_ESCAPE:
                        query = ""
                        search_mode = False
                        cursor = 0
                    elif key == KEY_BACKSPACE:
                        query = query[:-1]
                    elif len(key) == 1 and key.isprintable():
                        query += key
                    continue

                if key == "q":
                    return []
                if key == KEY_ESCAPE:
                    if query:
                        query = ""
                        cursor = 0
                        message = ""
                    else:
                        return []
                if key == "/":
                    query = ""
                    search_mode = True
                    message = ""
                elif key == KEY_UP:
                    cursor = max(0, cursor - 1)
                elif key == KEY_DOWN:
                    cursor = min(max(0, len(visible) - 1), cursor + 1)
                elif key == KEY_PAGE_UP:
                    cursor = max(0, cursor - max(1, shutil.get_terminal_size((100, 24)).lines - 7))
                elif key == KEY_PAGE_DOWN:
                    cursor = min(max(0, len(visible) - 1), cursor + max(1, shutil.get_terminal_size((100, 24)).lines - 7))
                elif key in (" ", "space"):
                    if visible:
                        mod_index = visible[cursor]
                        if mod_index in selected:
                            selected.remove(mod_index)
                        else:
                            selected.add(mod_index)
                elif key == "a":
                    selected.update(visible)
                    message = "已选中当前筛选结果。"
                elif key == "n":
                    selected.clear()
                    message = "已清空选择。"
                elif key == "?":
                    message = "无直链的条目仍可选择，但只能手动下载。"
                elif key in (KEY_ENTER, "d"):
                    if selected:
                        return [MODS[index] for index in sorted(selected)]
                    message = "请先按 Space 选择至少一个模组。"
    finally:
        sys.stdout.write("\033[0m\033[?25h\033[2J\033[H")
        sys.stdout.flush()
        terminal.close()


def _line_selector() -> List[Mod]:
    """没有可用 TTY 时的标准输入后备模式。"""
    print("GTNH 可添加 MOD 下载器（列表模式）")
    for index, mod in enumerate(MODS, 1):
        print("{:>3}. [{}] {}".format(index, mod.source, mod.name))
    try:
        raw = input("请输入要下载的编号（可用空格分隔，直接回车退出）：").strip()
    except (EOFError, KeyboardInterrupt):
        return []
    indices: List[int] = []
    for token in raw.replace(",", " ").split():
        try:
            index = int(token) - 1
        except ValueError:
            continue
        if 0 <= index < len(MODS) and index not in indices:
            indices.append(index)
    return [MODS[index] for index in indices]


def _link(url: str) -> str:
    """在支持 OSC 8 的终端中提供可点击链接，同时保留纯文本 URL。"""
    if not sys.stdout.isatty() or os.environ.get("TERM") == "dumb":
        return url
    return "\033]8;;{}\033\\{}\033]8;;\033\\".format(url, url)


def _report(results: Sequence[DownloadResult]) -> None:
    succeeded = [item for item in results if item.path]
    failed = [item for item in results if not item.path]
    print("\n完成：成功 {} 个，失败 {} 个。".format(len(succeeded), len(failed)))
    if not failed:
        return
    print("\n以下项目请手动打开链接下载（页面会显示对应版本）：")
    for item in failed:
        print("- {}".format(item.mod.name))
        if item.mod.source_url:
            print("  {}".format(_link(item.mod.source_url)))
        else:
            print("  该行快照没有 GitHub、CurseForge 或 Modrinth 链接，请在原始页面查找。")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="选择并下载 GTNH 可添加 MOD")
    parser.add_argument("--list", action="store_true", help="只打印静态模组列表，不进入 TUI")
    args = parser.parse_args(argv)
    if args.list:
        for index, mod in enumerate(MODS, 1):
            print("{:>3}. [{}] {}".format(index, mod.source, mod.name))
        return 0

    selected = _selector()
    if not selected:
        print("未选择模组，已退出。")
        return 0
    results = download_selected(selected, downloads_directory())
    _report(results)
    return 0 if all(item.path for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
