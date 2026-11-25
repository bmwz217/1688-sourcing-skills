# 1688 选品技能

这是内部技能源仓库。插件中的选品技能每次运行时，都会通过已连接的 GitHub 工具读取 `workflow/christmas-home-decor-sourcing-v1-2.md`；该文件是完整流程的唯一来源。

## 更新规则

修改 `workflow/christmas-home-decor-sourcing-v1-2.md` 并提交到默认分支即可更新流程。日常规则变更不需要重新打包插件；下次调用技能时会读取新版本。若无法访问 GitHub，技能会停止，不会回退到旧规则。

## 包含内容

- 圣诞与家居装饰选品技能及其配套脚本、利润表模板
- 1688 下载器技能及其配套脚本
- Codex 插件和 GitHub marketplace 配置

## 运行依赖

- 选品流程需要已连接的 GitHub 工具，用于每次读取最新版规则。
- 选品流程调用 `$ego-browser`，使用环境需提供该浏览器工具。
- 1688 下载器在本机运行，需要 Windows、Python 和 Edge；首次运行会在下载器脚本旁创建“浏览器用户数据”目录，供使用者自行登录 1688。
- 登录数据和下载结果不属于仓库内容，已通过 `.gitignore` 排除。请勿提交这些目录。
- 下载器的 Python 依赖见其 `scripts/requirements.txt` 和 `scripts/详情图清理依赖.txt`。

本仓库不包含任何浏览器登录资料。