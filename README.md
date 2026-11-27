# 亚马逊技能仓库

公开的 Codex 技能源仓库。完整技能说明、脚本、引用资料和模板统一保存在 `skills/<本机技能文件夹名>/`。

## 每次使用获取最新版

每个技能入口目录都随包附带一份经过检查的本机获取脚本：`plugins/1688-sourcing/skills/<技能名>/scripts/fetch-latest-skill.ps1`。调用时，入口运行这份本机脚本；它只把公开仓库最新 `main` 压缩包作为数据下载、校验并解压到稳定缓存，不会在每次使用时从 GitHub 下载并执行新的 PowerShell 脚本：

`%LOCALAPPDATA%\AmazonSkills\skills\<技能名>\`

无需 GitHub 账号或 GitHub 连接，只需能访问公开网页。浏览器登录资料保存在单独的本机目录，不放入仓库或可被替换的技能缓存。下载器的商品输出必须使用用户提供的绝对 `--folder` 路径。

## 更新技能

修改 `skills/<技能名>/` 下的流程或配套文件并提交到默认分支 `main`。下一次调用时会下载该技能的新目录，不需要重新打包或逐人发送文件。只有获取器本身变化或新增技能时，才需要更新并重新分发一次入口包。新增全新技能时，仍需为本机安装一次对应入口。

维护者可用以下命令在本地检查指定技能的最新文件：

`powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\fetch-latest-skill.ps1 -SkillFolderName "amazon-competitor-listing"`

脚本输出缓存目录路径。若下载或安全检查失败，脚本会报错；入口必须停止，不得退回使用旧缓存。

## 技能目录

- `skills/1688-image-downloader-v1.2/`
- `skills/amazon-competitor-listing/`
- `skills/backpack-product-image-design-v1-6/`
- `skills/christmas-home-decor-sourcing-v1-2/`
- `skills/dianxiaomi-listing-agent/`
- `skills/product-image-light-edit/`
- `skills/店小秘助手/`

每个目录包含对应的 `SKILL.md` 和该技能所需的配套脚本、引用资料或模板。

## 维护范围

仓库不包含浏览器登录资料、缓存文件或店小秘账户配置。第三方许可证未确认的 `ui-ux-pro-max` 和 Codex 自带的系统技能、浏览器技能不在此仓库中。
