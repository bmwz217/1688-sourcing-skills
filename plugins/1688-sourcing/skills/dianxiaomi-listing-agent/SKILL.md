---
name: dianxiaomi-listing-agent-v1-0
description: 适用于通过店小秘新增或编辑 Amazon 商品 Listing。
---

# 获取并使用当前最新版

本机入口只负责获取最新版，不包含本技能业务流程。

每次调用本技能，先解析并运行随本技能安装的本机获取脚本（`scripts/fetch-latest-skill.ps1`）。使用技能元数据中此 `SKILL.md` 的实际路径定位脚本；不要从 GitHub 下载或执行 PowerShell 代码。

```powershell
$ErrorActionPreference = "Stop"
$skillFilePath = "<填写当前技能元数据中此 SKILL.md 的绝对路径>"
$skillEntryDirectory = Split-Path -Parent $skillFilePath
$helperPath = Join-Path $skillEntryDirectory "scripts\fetch-latest-skill.ps1"
if (-not (Test-Path -LiteralPath $helperPath -PathType Leaf)) { throw "入口包缺少本机获取脚本；请重新安装更新后的技能包。" }
$directoryOutput = & $helperPath -SkillFolderName "dianxiaomi-listing-agent"
$skillDirectory = [string]($directoryOutput | Select-Object -Last 1)
$skillDirectory = $skillDirectory.Trim()
$expectedDirectory = Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) "AmazonSkills\skills\dianxiaomi-listing-agent"
if ([string]::IsNullOrWhiteSpace($skillDirectory) -or [System.IO.Path]::GetFullPath($skillDirectory) -ine [System.IO.Path]::GetFullPath($expectedDirectory)) { throw "获取脚本返回的技能目录无效。" }
$skillFile = Join-Path $skillDirectory "SKILL.md"
if (-not (Test-Path -LiteralPath $skillFile -PathType Leaf)) { throw "最新版 SKILL.md 不存在。" }
$latestSkill = Get-Content -LiteralPath $skillFile -Raw -Encoding UTF8
if ([string]::IsNullOrWhiteSpace($latestSkill)) { throw "最新版 SKILL.md 为空。" }
$latestSkill
```

完整读取返回的远端 `SKILL.md`；如单次输出被截断，继续分段读取直至文件末尾。以这份文件作为本次唯一流程依据，只使用 `$skillDirectory` 中的脚本、参考资料和模板。

本机获取脚本缺失、运行、路径检查或完整读取失败时立即停止并报告原因。不得使用本机旧版技能正文、旧脚本、旧参考资料或旧缓存。

店小秘配置工作簿是每位用户自己的个人文件，不随公开仓库分发。运行前请用户提供该文件的本机路径，并通过 `--config-xlsx` 传给远端流程；没有路径或文件时先请求用户提供。不能假设仓库中有该文件、不能重建、上传或展示其中内容。
