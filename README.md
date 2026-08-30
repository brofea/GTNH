# brofea 的 GTNH 配置仓库

## 模组下载脚本

直接运行：

```bash
curl -fsSL https://raw.githubusercontent.com/brofea/GTNH/main/gtnh-mod-downloader.py | python3
```

脚本会下载模组至系统默认下载目录中 `mods` 文件夹，并在文件名添加中文名作为附加模组的标识

若下载模组过多触发 GitHub API 限流，可以 [创建 Token](https://github.com/settings/personal-access-tokens/new)（填名字直接创建即可）并重新运行脚本

在 TUI 中按 `l` 可以加载同目录下的 `gtnh-mods.conf`，配置文件每行填写一个模组英文名，默认配置包含常用的 22 个模组。直接运行脚本时从脚本所在目录读取；使用 `curl | python3` 时从当前工作目录读取。

模组列表来自 [GTNH 中文维基——可添加 MOD](https://gtnh.huijiwiki.com/wiki/%E5%8F%AF%E6%B7%BB%E5%8A%A0MOD)，2026/8/30 抓取，GTNH 版本 2.9.0-Beta2
