# brofea 的 GTNH 配置仓库

## 模组下载脚本

模组列表来自 [GTNH 中文维基——可添加 MOD](https://gtnh.huijiwiki.com/wiki/%E5%8F%AF%E6%B7%BB%E5%8A%A0MOD)，2026/8/30 抓取，GTNH 版本 2.9.0-Beta2

在终端运行如下命令，按照提示操作，模组会下载至系统默认下载目录下的 `mods` 文件夹，文件名添加中文名开头作为附加模组的标识。

```bash
curl -fsSL https://raw.githubusercontent.com/brofea/GTNH/main/gtnh-mod-downloader.py | python3
```

若下载模组过多触发 GitHub API 限流，可以 [创建 Token](https://github.com/settings/personal-access-tokens/new)（填名字直接创建即可）并重新运行脚本
