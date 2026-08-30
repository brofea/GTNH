#!/usr/bin/env python3
"""
从 GTNH 中文维基的静态快照中选择并下载模组。

运行时不会请求维基页面。模组列表和每行的第一个 GitHub、CurseForge 或
Modrinth 地址都在本文件中；下载时只访问对应平台的公开元数据接口。
"""

from __future__ import annotations

import argparse
import getpass
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
ALLOWED_MOD_EXTENSIONS = (".jar", ".litemod")
CONFIG_FILENAME = "gtnh-mods.conf"

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

    english_name: str
    chinese_name: str
    category: str
    source_url: Optional[str]

    @property
    def name(self) -> str:
        """兼容显示和日志使用的组合名称。"""
        if self.english_name == self.chinese_name:
            return self.english_name
        return "{} {}".format(self.english_name, self.chinese_name)

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
    existing: bool = False


# 这是 2026-08-30 对 SOURCE_PAGE 的一次性静态快照。
# 只保留了表格中的模组名、分类和相关地址列中第一个支持的平台链接。
MODS: Tuple[Mod, ...] = (
    Mod("Inputfix", "中文输入修复补丁", "星门规则模组/功能增强", "https://www.curseforge.com/minecraft/mc-mods/inputfix"),
    Mod("NeverEnoughCharacters-Rework", "NEI拼音搜索－重制版", "星门规则模组/功能增强", "https://github.com/asdflj/NeverEnoughCharacters-Rework"),
    Mod("AE2 Auto Pattern Upload", "AE2样板自动上传", "星门规则模组/功能增强", "https://www.curseforge.com/minecraft/mc-mods/ae2-auto-pattern-upload"),
    Mod("Untranslator", "未转译者", "星门规则模组/功能增强", "https://github.com/RealSilverMoon/Untranslator"),
    Mod("MouseSideButtonFix", "侧键修复", "星门规则模组/功能增强", "https://github.com/asdflj/MouseSideButtonFix"),
    Mod("WorldEdit", "创世神", "星门规则模组/功能增强", "https://github.com/GTNewHorizons/worldedit-gtnh"),
    Mod("OmniOcular", "OmniOcular", "星门规则模组/功能增强", "https://www.curseforge.com/minecraft/mc-mods/omni-ocular"),
    Mod("Damage Indicators", "伤害显示", "星门规则模组/功能增强", "https://www.curseforge.com/minecraft/mc-mods/damage-indicators-mod"),
    Mod("NEI-RecipeTree", "NEI配方树", "星门规则模组/功能增强", "https://github.com/XSana/NEI-RecipeTree"),
    Mod("Applied Cooking", "应用厨房GTNH版", "星门规则模组/功能增强", "https://github.com/asdflj/AppliedCooking"),
    Mod("InputMethodBlocker-GTNH", "输入法冲突修复", "星门规则模组/功能增强", "https://github.com/HOMEFTW/InputMethodBlocker-GTNH/releases"),
    Mod("JdkJarVersion21Enforcer", "Java25启动修复", "星门规则模组/功能增强", "https://github.com/HOMEFTW/JdkJarVersion21Enforcer"),
    Mod("ModernKeyBinding", "现代化按键绑定", "星门规则模组/功能增强", "https://github.com/Nova-Committee/ModernKeyBinding"),
    Mod("Smooth Font", "平滑字体", "星门规则模组/性能优化", "https://www.curseforge.com/minecraft/mc-mods/smooth-font"),
    Mod("Qz-UILib", "Qz-UI库", "星门规则模组/性能优化", "https://github.com/QuanhuZeYu/Qz-UILib"),
    Mod("FPS Reducer", "FPS减速器", "星门规则模组/性能优化", "https://www.curseforge.com/minecraft/mc-mods/fps-reducer"),
    Mod("Raw Mouse Input", "鼠标原始输入修复", "星门规则模组/性能优化", "https://www.curseforge.com/minecraft/mc-mods/raw-input-1-12-2"),
    Mod("NoFog", "没有雾", "星门规则模组/性能优化", "https://www.curseforge.com/minecraft/mc-mods/nofog"),
    Mod("Lag Goggles: Legacy", "延迟监视：移植版", "星门规则模组/性能优化", "https://github.com/FalsePattern/LagGogglesLegacy"),
    Mod("SmallPhone", "小手机", "星门规则模组/视听增强", "https://github.com/RealSilverMoon/SmallPhone/releases"),
    Mod("FMusic", "FMusic", "星门规则模组/视听增强", "https://github.com/czqwq/FMusic"),
    Mod("Mineshot", "高清截图", "星门规则模组/视听增强", "https://github.com/ABKQPO/mineshot"),
    Mod("MyCTMLib", "连接纹理库支持", "星门规则模组/视听增强", "https://github.com/ABKQPO/MyCTMLib"),
    Mod("DarkTextFix", "暗色文本修复", "星门规则模组/视听增强", "https://github.com/eudesjuniorr/DarkTextFix"),
    Mod("World Tooltips", "掉落物信息显示", "星门规则模组/视听增强", "https://www.curseforge.com/minecraft/mc-mods/world-tooltips"),
    Mod("BakaDanmaku", "直播弹幕模组", "星门规则模组/视听增强", None),
    Mod("Better Foliage", "更好的树叶", "星门规则模组/视听增强", "https://www.curseforge.com/minecraft/mc-mods/better-foliage"),
    Mod("Better Rain", "更好的降雨", "星门规则模组/视听增强", None),
    Mod("BetterTooltipBox", "更好的提示框", "星门规则模组/视听增强", "https://github.com/xiaoxing2005/BetterTooltipBox/releases"),
    Mod("Chat Bubbles", "聊天泡泡", "星门规则模组/视听增强", "https://www.curseforge.com/minecraft/mc-mods/chat-bubbles"),
    Mod("Dynamic Surroundings", "动态环境", "星门规则模组/视听增强", "https://www.curseforge.com/minecraft/mc-mods/dynamic-surroundings"),
    Mod("Extra Player Renderer", "额外玩家渲染", "星门规则模组/视听增强", "https://www.curseforge.com/minecraft/mc-mods/extra-player-render"),
    Mod("Fancy Block Particles", "梦幻方块效果", "星门规则模组/视听增强", "https://www.curseforge.com/minecraft/mc-mods/fancy-block-particles"),
    Mod("ItemPhysic Lite", "物品掉落物理-轻量版", "星门规则模组/视听增强", "https://www.curseforge.com/minecraft/mc-mods/itemphysic-lite"),
    Mod("MAtmos", "真实环境音效", "星门规则模组/视听增强", "https://www.curseforge.com/minecraft/mc-mods/matmos"),
    Mod("SkinPort", "SkinPort", "星门规则模组/视听增强", "https://www.curseforge.com/minecraft/mc-mods/skinport"),
    Mod("Cosmetic Armor Reworked", "时装盔甲重置版", "星门规则模组/视听增强", "https://www.curseforge.com/minecraft/mc-mods/cosmetic-armor-reworked"),
    Mod("Distant Horizons Standalone", "Distant Horizons GTNH版", "星门规则模组/视听增强", "https://github.com/DarkShadow44/DistantHorizonsStandalone"),
    Mod("Better Line Break", "更好的换行", "星门规则模组/视听增强", "https://github.com/KatatsumuriPan/BetterLineBreak"),
    Mod("Angelica", "安洁莉卡/天使优化", "星门规则模组/旧版限定", "https://github.com/GTNewHorizons/Angelica/releases"),
    Mod("Server Utilities", "服务器实用工具", "星门规则模组/旧版限定", "https://github.com/GTNewHorizons/ServerUtilities"),
    Mod("Advanced Backups", "高级备份", "星门规则模组/旧版限定", "https://www.curseforge.com/minecraft/mc-mods/aromabackup"),
    Mod("AromaBackup", "存档备份", "星门规则模组/旧版限定", "https://www.curseforge.com/minecraft/mc-mods/aromabackup"),
    Mod("Backhand Unofficial", "副手 非官方版", "星门规则模组/旧版限定", "https://github.com/GTNewHorizons/Backhand"),
    Mod("ZeroPointBugfix", "零级BUG修复", "星门规则模组/旧版限定", "https://github.com/wohaopa/ZeroPointServerBugfix"),
    Mod("Crafting Tweaks", "合成辅助", "星门规则模组/旧版限定", "https://www.curseforge.com/minecraft/mc-mods/crafting-tweaks"),
    Mod("Dynamic Lights", "动态光源", "星门规则模组/旧版限定", "https://www.curseforge.com/minecraft/mc-mods/dynamic-lights/"),
    Mod("Big Chat History", "更多聊天记录", "星门规则模组/旧版限定", None),
    Mod("Not Enough Characters", "NEI 拼音搜索", "星门规则模组/旧版限定", "https://www.curseforge.com/minecraft/mc-mods/not-enough-characters"),
    Mod("Link Info", "链接信息", "星门规则模组/旧版限定", None),
    Mod("Twist Space Technology Mod", "扭曲空间科技", "违反门规", "https://github.com/Nxer/Twist-Space-Technology-Mod/releases"),
    Mod("Programmable Hatch Mod", "可编程仓室", "违反门规", "https://github.com/reobf/Programmable-Hatches-Mod/releases"),
    Mod("GT Not Leisure", "格雷不休闲 & 科技不休闲", "违反门规", "https://github.com/ABKQPO/GT-Not-Leisure"),
    Mod("Box Plus Plus", "盒", "违反门规", "https://github.com/RealSilverMoon/BoxPlusPlus"),
    Mod("AE2 Thing", "AE2 Thing", "违反门规", "https://github.com/asdflj/AE2Things"),
    Mod("EZNuclear", "核电轻而易举", "违反门规", "https://github.com/czqwq/EZNuclear"),
    Mod("EZMiner", "轻松连锁", "违反门规", "https://github.com/czqwq/EZMiner"),
    Mod("Bandit-Legacy", "Bandit-Legacy", "违反门规", "https://github.com/ElytraServers/Bandit-Legacy"),
    Mod("Qz-Miner", "爆破连锁", "违反门规", "https://github.com/QuanhuZeYu/Qz-Miner"),
    Mod("123Technology", "123科技", "违反门规", "https://github.com/CallmeSHaobe/123Technology"),
    Mod("MessTech", "混乱科技", "违反门规", "https://github.com/czqwq/MessTech"),
    Mod("EyeOfHarmonyBuffer", "万宁鸿蒙", "违反门规", "https://github.com/SafariXiu/EyeOfHarmonyBuffer/tree/master"),
    Mod("GTNN", "格雷新视野：憋削了！", "违反门规", "https://github.com/ElytraServers/gtnh-no-nerf"),
    Mod("NHU", "格雷新视野：实用工具", "违反门规", "https://github.com/Keriils/NH-Utilities"),
    Mod("TakoTech", "塔可科技", "违反门规", "https://github.com/XSana/TakoTech"),
    Mod("Think Tech", "俺寻思科技", "违反门规", "https://github.com/Ol925/ThinkTech"),
    Mod("CheaperVoidMiners", "更前期的虚空矿机", "违反门规", "https://github.com/Jonodonozym/CheaperVoidMiners"),
    Mod("GT-Not-Hard", "格雷不难", "违反门规", "https://github.com/z1564058782/GT-Not-Hard"),
    Mod("Nyx", "Nyx", "违反门规", "https://github.com/RhyVis/GTNH-Nyx"),
    Mod("Void-Miner-Tweak-Mod", "Void-Miner-Tweak-Mod", "违反门规", "https://github.com/reobf/Void-Miner-Tweak-Mod"),
    Mod("Torcherino-GTNH", "Torcherino-GTNH", "违反门规", "https://github.com/czqwq/Torcherino-GTNH"),
    Mod("Time Crystal", "Time Crystal", "违反门规", "https://github.com/kuolemax/time-crystal"),
    Mod("AE2PatternGen", "AE2PatternGen", "违反门规", "https://github.com/Ch4oooooooLL/AE2PatternGen"),
    Mod("WildcardPattern", "通配样板", "违反门规", "https://github.com/clfpwp/WildcardPatternforGTNH-1.7.10"),
    Mod("GTNH Modify", "万宁NH", "违反门规", "https://github.com/ElytraServers/GTNH-CutCorners"),
    Mod("GT-Simple-Wireless-Network", "简单无线网络", "违反门规", "https://github.com/MIAOKATZE/GT-Simple-Wireless-Network"),
    Mod("GT-Interesting-Thing", "有趣事物", "违反门规", "https://github.com/MIAOKATZE/GT-Interesting-Thing"),
    Mod("GT-Steam-Reborn", "蒸汽重生", "违反门规", "https://github.com/MIAOKATZE/GT-Steam-Reborn"),
    Mod("AE2InfinityCell", "AE2无限存储元件", "违反门规", "https://github.com/DancingSnow0517/AE2InfinityCell/releases"),
    Mod("Applied Energistics: EU Network", "AE EU 网络", "违反门规", "https://github.com/DancingSnow0517/Applied-Energistics-EU-Network/releases"),
    Mod("Advanced Memory Card", "高级内存卡", "违反门规", "https://github.com/suntide-20210418/AdvancedMemoryCard/releases"),
    Mod("Fission-Evolved", "裂变进化", "违反门规", "https://github.com/shenFNX/Fission-Evolved/releases"),
    Mod("AE2 Stuff", "AE2 Stuff", "自定添加", "https://www.curseforge.com/minecraft/mc-mods/ae2-stuff"),
    Mod("Brandons Core", "Brandons Core", "自定添加", "https://www.curseforge.com/minecraft/mc-mods/brandons-core"),
    Mod("WorldEdit CUI", "WorldEdit CUI", "自定添加", "https://www.curseforge.com/minecraft/mc-mods/worldeditcui-forge-edition")

)


class DownloadError(RuntimeError):
    """下载元数据或文件失败。"""


class GitHubRateLimitError(DownloadError):
    """GitHub API 或文件下载返回了疑似限流响应。"""


def _request(
    url: str,
    *,
    accept: str = "*/*",
    github_token: Optional[str] = None,
) -> Request:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": accept,
    }
    if github_token and urlparse(url).netloc.lower() == "api.github.com":
        headers["Authorization"] = "Bearer {}".format(github_token)
    return Request(url, headers=headers)


def _get_json(url: str, github_token: Optional[str] = None) -> Any:
    try:
        with urlopen(
            _request(url, accept="application/json", github_token=github_token),
            timeout=HTTP_TIMEOUT,
            context=_ssl_context(),
        ) as response:
            body = response.read()
    except HTTPError as exc:
        if exc.code in (403, 429) and urlparse(url).netloc.lower() == "api.github.com":
            raise GitHubRateLimitError(
                "GitHub API 可能触发了限流，请求过快，请稍后再试"
            ) from exc
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


def _is_supported_mod_filename(filename: str) -> bool:
    """只允许 Minecraft 模组常用的 JAR 和 Litemod 文件。"""
    return Path(filename).suffix.casefold() in ALLOWED_MOD_EXTENSIONS


def _release_asset(release: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    assets = [
        asset
        for asset in release.get("assets", [])
        if isinstance(asset, dict)
        and asset.get("name")
        and asset.get("browser_download_url")
        and _is_supported_mod_filename(str(asset.get("name")))
    ]
    if not assets:
        return None

    def score(asset: Dict[str, Any]) -> Tuple[int, int]:
        name = str(asset.get("name", "")).lower()
        auxiliary = ("source", "sources", "javadoc", "api", "dev")
        return (1 if any(word in name for word in auxiliary) else 0, len(name))

    return sorted(assets, key=score)[0]


def _github_download(mod: Mod, github_token: Optional[str] = None) -> DownloadInfo:
    owner, repo = _github_repo(mod.source_url or "")
    api_base = "https://api.github.com/repos/{}/{}".format(quote(owner), quote(repo))
    releases: List[Dict[str, Any]] = []
    latest_error: Optional[Exception] = None

    try:
        latest = _get_json(api_base + "/releases/latest", github_token)
        if isinstance(latest, dict):
            releases.append(latest)
    except GitHubRateLimitError:
        # 限流时不要继续请求 fallback endpoint，避免把限流延长。
        raise
    except DownloadError as exc:
        latest_error = exc

    # 某些仓库没有“Latest”标记，但仍有普通 release。
    need_fallback = not releases or _release_asset(releases[0]) is None
    if need_fallback:
        try:
            older = _get_json(api_base + "/releases?per_page=30", github_token)
            if isinstance(older, list):
                releases.extend(item for item in older if isinstance(item, dict))
        except GitHubRateLimitError:
            # 即使 latest 请求成功，fallback 被限流也必须立刻暂停。
            raise
        except DownloadError as exc:
            if not releases:
                raise latest_error or exc

    for release in releases:
        asset = _release_asset(release)
        if asset:
            filename = str(asset.get("name", "mod.jar"))
            download_url = str(asset.get("browser_download_url", ""))
            if download_url:
                return DownloadInfo("GitHub", filename, download_url, mod.source_url or "")

    raise DownloadError("GitHub 最新 release 没有 .jar 或 .litemod 文件")


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
        files = [
            item
            for item in version.get("files", [])
            if isinstance(item, dict)
            and item.get("filename")
            and item.get("url")
            and _is_supported_mod_filename(str(item.get("filename")))
        ]
        if files:
            primary = next((item for item in files if item.get("primary")), None)
            return primary or files[0]
    return None


def _modrinth_compatible_versions(versions: Any) -> List[Dict[str, Any]]:
    """只保留明确声明支持 Minecraft 1.7.10 的 Modrinth 版本。"""
    if not isinstance(versions, list):
        return []
    return [
        item
        for item in versions
        if isinstance(item, dict)
        and isinstance(item.get("game_versions"), list)
        and MINECRAFT_VERSION in item["game_versions"]
    ]


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
    file_info = _modrinth_file(_modrinth_compatible_versions(versions))

    if not file_info:
        # 接口的筛选参数在旧项目上偶尔不完整，退一步读取项目版本，
        # 但仍只接受明确声明支持 1.7.10 的版本，避免误下现代版本。
        all_versions = _get_json(api)
        file_info = _modrinth_file(_modrinth_compatible_versions(all_versions))

    if not file_info or not file_info.get("url"):
        raise DownloadError("Modrinth 没有找到支持 {} 的文件".format(MINECRAFT_VERSION))
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
    files = [
        item
        for item in files
        if isinstance(item, dict)
        and _is_supported_mod_filename(str(item.get("name") or item.get("display") or ""))
    ]
    if not files:
        raise DownloadError(
            "CurseForge 没有找到支持 {} 的 .jar 或 .litemod 文件".format(MINECRAFT_VERSION)
        )

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
    return DownloadInfo(
        "CurseForge",
        filename,
        download_url,
        mod.source_url or "",
    )


def resolve_download(mod: Mod, github_token: Optional[str] = None) -> DownloadInfo:
    """把维基上的项目页解析为实际的最新文件下载信息。"""
    if not mod.source_url:
        raise DownloadError("该表格行没有 GitHub、CurseForge 或 Modrinth 链接")
    host = urlparse(mod.source_url).netloc.lower()
    if "github.com" in host:
        return _github_download(mod, github_token)
    if "curseforge.com" in host:
        return _curseforge_download(mod)
    if "modrinth.com" in host:
        return _modrinth_download(mod)
    raise DownloadError("不支持的下载平台")


def _safe_filename(name: str, fallback: str) -> str:
    """清理文件名并保留下载源提供的扩展名。"""
    name = Path(name.replace("\\", "/")).name.strip()
    if not name or name in {".", ".."}:
        name = Path(fallback.replace("\\", "/")).name.strip()
    name = _safe_filename_component(name, "mod")
    return name


def _safe_filename_component(name: str, fallback: str) -> str:
    """清理文件名片段，同时保留中文和【】前缀。"""
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = re.sub(r"[/\\]", "_", name)
    name = re.sub(r"[^\w .()\[\]【】-]", "_", name, flags=re.UNICODE)
    name = name.strip(" .")
    return name if name and name not in {".", ".."} else fallback


def _prefixed_download_filename(info_filename: str, mod: Mod) -> str:
    """生成保留原扩展名、带中文模组名前缀的下载文件名。"""
    base = _safe_filename(info_filename, mod.english_name + ".jar")
    base_path = Path(base)
    chinese_name = _safe_filename_component(mod.chinese_name, mod.english_name)
    return "【{}】{}{}".format(chinese_name, base_path.stem, base_path.suffix)


def _without_localized_prefix(filename: str) -> str:
    """去掉下载文件名开头的【中文名】前缀。"""
    if filename.startswith("【"):
        end = filename.find("】", 1)
        if end != -1:
            return filename[end + 1 :]
    return filename


def _find_existing_download(directory: Path, info_filename: str) -> Optional[Path]:
    """查找已经下载过的同名版本，兼容带中文名前缀和不带前缀的文件。"""
    expected = _safe_filename(info_filename, "mod.jar").casefold()
    try:
        candidates = directory.iterdir()
    except OSError:
        return None
    for candidate in candidates:
        try:
            if not candidate.is_file():
                continue
        except OSError:
            continue
        actual = _without_localized_prefix(candidate.name).casefold()
        if actual == expected:
            return candidate
    return None


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
    """返回系统下载目录下的 mods 子目录，并兼容 Linux 的 XDG 目录设置。"""
    base_directory = Path.home() / "Downloads"
    if os.name != "nt":
        config = Path.home() / ".config/user-dirs.dirs"
        if config.is_file():
            try:
                for line in config.read_text(encoding="utf-8").splitlines():
                    if line.startswith("XDG_DOWNLOAD_DIR="):
                        value = line.split("=", 1)[1].strip().strip('"')
                        value = value.replace("$HOME", str(Path.home()))
                        if value:
                            base_directory = Path(value).expanduser()
                            break
            except OSError:
                pass
    return base_directory / "mods"


def _ensure_download_directory(directory: Path) -> None:
    """确保下载目录存在且确实是目录。"""
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DownloadError("无法创建下载目录 {}：{}".format(directory, exc)) from exc
    if not directory.is_dir():
        raise DownloadError("下载路径不是文件夹：{}".format(directory))


def download_file(
    info: DownloadInfo,
    directory: Path,
    fallback_name: str,
    target_name: Optional[str] = None,
) -> Path:
    _ensure_download_directory(directory)
    filename = target_name or info.filename
    fallback = fallback_name if Path(fallback_name).suffix else fallback_name + ".jar"
    safe_filename = _safe_filename(filename, fallback)
    if not _is_supported_mod_filename(safe_filename):
        raise DownloadError("不支持的模组文件格式，仅允许 .jar 或 .litemod")
    target = _unique_path(directory, safe_filename)
    partial = target.with_name(target.name + ".part")
    try:
        with urlopen(
            _request(info.download_url),
            timeout=HTTP_TIMEOUT,
            context=_ssl_context(),
        ) as response:
            with partial.open("wb") as output:
                first = response.read(4)
                if not first:
                    raise DownloadError("下载内容为空，可能被网站拦截")
                output.write(first)
                while True:
                    chunk = response.read(1024 * 128)
                    if not chunk:
                        break
                    output.write(chunk)
        os.replace(str(partial), str(target))
    except HTTPError as exc:
        _remove_if_exists(partial)
        if exc.code in (403, 429) and "github" in urlparse(info.download_url).netloc.lower():
            raise GitHubRateLimitError("GitHub 下载可能触发了限流，请求过快，请稍后再试") from exc
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


def _wait_for_github_rate_limit() -> bool:
    """暂停到用户按 Enter；没有控制终端时返回 False。"""
    device = "CONIN$" if os.name == "nt" else "/dev/tty"
    try:
        fd = os.open(device, os.O_RDWR)
    except OSError:
        return False
    try:
        sys.stdout.write("  GitHub 限流，脚本已暂停；请求过快，请稍后再试。按 Enter 重试当前模组：")
        sys.stdout.flush()
        while True:
            data = os.read(fd, 4096)
            if not data:
                return False
            if os.name == "nt" or b"\n" in data or b"\r" in data:
                return True
    except (KeyboardInterrupt, OSError):
        return False
    finally:
        os.close(fd)


def _prompt_github_token() -> Optional[str]:
    """在本次进程中读取可选 GitHub Token，不写入文件或环境变量。"""
    try:
        token = getpass.getpass(
            "GitHub Token（可选，直接回车跳过；本次运行结束后不会保存）："
        ).strip()
    except (EOFError, KeyboardInterrupt):
        print("\n未输入 GitHub Token，继续使用未认证请求。")
        return None
    if not token:
        print("未使用 GitHub Token。")
        return None
    print("已启用本次运行的 GitHub Token（不会保存）。")
    return token


def download_selected(
    selected: Sequence[Mod],
    directory: Path,
    github_token: Optional[str] = None,
) -> List[DownloadResult]:
    results: List[DownloadResult] = []
    print("\n下载目录：{}".format(directory))
    try:
        _ensure_download_directory(directory)
    except DownloadError as exc:
        error = str(exc)
        print("自动下载停止：{}".format(error))
        return [DownloadResult(mod, None, error) for mod in selected]
    print("下载目录已准备。")
    for number, mod in enumerate(selected, 1):
        print("\n[{}/{}] {}".format(number, len(selected), mod.name))
        while True:
            try:
                info = resolve_download(mod, github_token)
                print("  {} 最新文件：{}".format(info.provider, info.filename))
                existing = _find_existing_download(directory, info.filename)
                if existing:
                    print("  已存在，跳过：{}".format(existing))
                    results.append(DownloadResult(mod, existing, None, True))
                    break
                target_name = _prefixed_download_filename(info.filename, mod)
                path = download_file(info, directory, mod.english_name, target_name)
                print("  已保存：{}".format(path))
                results.append(DownloadResult(mod, path, None))
                break
            except GitHubRateLimitError as exc:
                print("  {}".format(exc))
                if _wait_for_github_rate_limit():
                    continue
                results.append(DownloadResult(mod, None, str(exc)))
                return results
            except DownloadError as exc:
                print("  自动下载失败：{}".format(exc))
                results.append(DownloadResult(mod, None, str(exc)))
                break
            except Exception as exc:
                print("  自动下载失败：{}".format(exc))
                results.append(DownloadResult(mod, None, str(exc)))
                break
    return results


# ---------- 无依赖终端界面 ----------


KEY_UP = "up"
KEY_DOWN = "down"
KEY_PAGE_UP = "page_up"
KEY_PAGE_DOWN = "page_down"
KEY_ENTER = "enter"
KEY_ESCAPE = "escape"


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


def _configuration_path() -> Path:
    """返回配置文件路径；管道运行时使用当前工作目录。"""
    script_path = globals().get("__file__")
    if script_path and script_path not in {"<stdin>", "-"}:
        return Path(str(script_path)).resolve().parent / CONFIG_FILENAME
    return Path.cwd() / CONFIG_FILENAME


def _load_config_selection(
    config_path: Optional[Path] = None,
) -> Tuple[Optional[Set[int]], str]:
    """读取每行一个英文模组名的配置，返回选择索引和提示信息。"""
    path = config_path or _configuration_path()
    try:
        content = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None, "找不到配置文件：{}".format(path)
    except OSError as exc:
        return None, "读取配置文件失败：{}：{}".format(path, exc)

    names = {mod.english_name.casefold(): index for index, mod in enumerate(MODS)}
    selected: Set[int] = set()
    unknown_count = 0
    for raw_line in content.splitlines():
        name = raw_line.split("#", 1)[0].strip()
        if not name:
            continue
        index = names.get(name.casefold())
        if index is None:
            unknown_count += 1
        else:
            selected.add(index)

    message = "已加载配置：{} 个模组。".format(len(selected))
    if unknown_count:
        message += " 已忽略 {} 个未知模组名。".format(unknown_count)
    return selected, message


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


def _toggle_indices(selected: Set[int], indices: Iterable[int]) -> bool:
    """切换一组条目的全选状态，返回切换后是否为全选。"""
    index_set = set(indices)
    if index_set.issubset(selected):
        selected.difference_update(index_set)
        return False
    selected.update(index_set)
    return True


def _draw(
    visible: Sequence[int],
    cursor: int,
    selected: Set[int],
    message: str,
) -> None:
    columns, rows = shutil.get_terminal_size((80, 24))
    color = bool(sys.stdout.isatty() and os.environ.get("TERM") != "dumb")
    # 保持普通 CLI 的单栏布局，不使用边框、横线或多栏表格。
    # 主动留出 6 列安全边距，避免终端列数报告略有偏差时发生折返。
    content_width = max(24, columns - 6)
    margin = "  "
    list_height = max(1, rows - 8)
    start = max(0, min(cursor - list_height // 2, max(0, len(visible) - list_height)))
    end = min(len(visible), start + list_height)

    def line(content: str, style: Optional[str] = None, pad: bool = False) -> str:
        body = _clip(content, content_width)
        if pad:
            body = _pad(body, content_width)
        if style:
            body = _color(style, body, color)
        return margin + body

    output = ["\033[2J\033[H\033[?25l"]
    output.append(line("GTNH 可添加 MOD 下载器", "\033[1;36m"))
    output.append(line("快照 {}  ·  {} 个条目  ·  已选 {} 个".format(SNAPSHOT_DATE, len(MODS), len(selected))))
    output.append(line("↑↓ 移动   Space 选择   c 全选当前分类   a 全选   n 清空   l 加载配置   Enter 下载   q 退出"))
    output.append("")

    for view_index in range(start, end):
        mod_index = visible[view_index]
        mod = MODS[mod_index]
        marker = "[x]" if mod_index in selected else "[ ]"
        pointer = ">" if view_index == cursor else " "
        group = _clip(mod.category.split("/")[-1], 10)
        source = _clip(_source_name(mod), 10)
        prefix = "{} {} {:>2}  {}  ".format(pointer, marker, mod_index + 1, group)
        suffix = "  {}".format(source)
        name_width = max(8, content_width - _display_width(prefix) - _display_width(suffix))
        content = prefix + _clip(mod.name, name_width) + suffix
        if view_index == cursor:
            output.append(line(content, "\033[7m", pad=True))
        elif mod_index in selected:
            output.append(line(content, "\033[32m"))
        else:
            output.append(line(content))

    if not visible:
        output.append(line("没有匹配的模组。"))
    else:
        output.append(line("显示 {}-{} / {}".format(start + 1, end, len(visible))))
    if message:
        output.append(line(message))
    else:
        output.append(line("下载失败会在结束页显示可手动打开的项目链接。"))
    # 显式发送 CRLF。部分 macOS 终端不会把单独的 LF 解释为回到行首，
    # 否则每次重绘都会沿着上一行的列位置继续输出，造成斜向错位。
    sys.stdout.write("\r\n".join(output) + "\033[0m\033[?25h")
    sys.stdout.flush()


def _selector() -> List[Mod]:
    terminal = Terminal()
    if not terminal.interactive or terminal.fd is None:
        terminal.close()
        return _line_selector()

    selected: Set[int] = set()
    message = ""
    cursor = 0
    try:
        with _raw_terminal(terminal.fd):
            while True:
                visible = list(range(len(MODS)))
                if visible:
                    cursor = max(0, min(cursor, len(visible) - 1))
                else:
                    cursor = 0
                _draw(visible, cursor, selected, message)
                key = _read_key(terminal.fd)

                if key == "q":
                    return []
                if key == KEY_ESCAPE:
                    return []
                if key == KEY_UP:
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
                elif key == "c":
                    if visible:
                        current = MODS[visible[cursor]]
                        category_indices = [
                            index for index, mod in enumerate(MODS) if mod.category == current.category
                        ]
                        category_name = current.category.split("/")[-1]
                        if _toggle_indices(selected, category_indices):
                            message = "已选中分类“{}”的 {} 个模组。".format(
                                category_name, len(category_indices)
                            )
                        else:
                            message = "已取消分类“{}”的选择。".format(category_name)
                elif key == "a":
                    all_indices = set(range(len(MODS)))
                    if _toggle_indices(selected, all_indices):
                        message = "已选中全部 {} 个模组。".format(len(MODS))
                    else:
                        message = "已取消全部模组的选择。"
                elif key == "n":
                    selected.clear()
                    message = "已清空选择。"
                elif key == "l":
                    loaded, message = _load_config_selection()
                    if loaded is not None:
                        selected.clear()
                        selected.update(loaded)
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
    succeeded = [item for item in results if item.path and not item.existing]
    failed = [item for item in results if not item.path]
    existing = [item for item in results if item.existing]
    print("\n完成：成功 {} 个，失败 {} 个，已有 {} 个".format(len(succeeded), len(failed), len(existing)))
    if not failed:
        return
    print("\n以下项目请手动打开链接下载（页面会显示对应版本）：")
    for item in failed:
        print("- {}".format(item.mod.name))
        if item.mod.source_url:
            print("  {}".format(_link(item.mod.source_url)))
        else:
            print("  该行快照没有 GitHub、CurseForge 或 Modrinth 直链。")
            print("  原始维基页面：{}".format(_link(SOURCE_PAGE)))


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
    github_token = (
        _prompt_github_token()
        if any(mod.source == "GitHub" for mod in selected)
        else None
    )
    results = download_selected(selected, downloads_directory(), github_token)
    _report(results)
    return 0 if all(item.path for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
