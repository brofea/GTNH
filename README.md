# ad astra per aspera

brofea 的 GTNH 配置仓库

## 模组下载脚本

直接运行：

```bash
curl -fsSL https://raw.githubusercontent.com/brofea/GTNH/main/gtnh-mod-downloader.py | python3
```

脚本会下载模组至系统默认下载目录中 `mods` 文件夹，并在文件名添加中文名作为附加模组的标识。模组列表来自 [GTNH 中文维基——可添加 MOD](https://gtnh.huijiwiki.com/wiki/%E5%8F%AF%E6%B7%BB%E5%8A%A0MOD)，2026/8/30 抓取，GTNH 版本 2.9.0-Beta2

额外功能：

- 若下载模组过多触发 GitHub API 限流，可以 [创建 Token](https://github.com/settings/personal-access-tokens/new)（填名字直接创建即可）并在开始下载前填入

- 在 TUI 中按 `l` 可以加载同目录下的 `gtnh-mods.conf` 配置文件

## 模组配置

先记在这里，以后做成脚本好了

- `/%GTNH_INSTANCE%/config/aroma1997/AromaBackup.cfg`
  - `I:keep=5`，备份保留五个

- `/%GTNH_INSTANCE%/config/lwjgl3ify.cfg`
  - `B:rawMouseInput=true`，启用原生鼠标输入

- `/%GTNH_INSTANCE%/config/GregTech/Pollution.cfg`
  - `B:"Activate Pollution"=false`，关闭污染

- `/%GTNH_INSTANCE%/config/GregTech/GregTech.cfg`
  - 将所有 `Explosions` 相关的配置改为 `false`，关闭爆炸

- `/%GTNH_INSTANCE%/config/Thaumcraft.cfg`
  - `I:biome_taint_spread=0`, 关闭腐化蔓延

- `/%GTNH_INSTANCE%/config/OmniOcular`
  - 下载 [CCYF78/GTNH_OmniOcular](https://github.com/CCYF78/GTNH_OmniOcular) 仓库 zip 后解压到此目录