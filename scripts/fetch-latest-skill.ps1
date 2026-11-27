[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$SkillFolderName)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$RepositorySlug = 'bmwz217/1688-sourcing-skills'
$BranchName = 'main'

if ([string]::IsNullOrWhiteSpace($SkillFolderName) -or
    [System.IO.Path]::GetFileName($SkillFolderName) -ne $SkillFolderName -or
    $SkillFolderName -match '[\\/:*?"<>|]' -or
    $SkillFolderName -match '(^|[\\/])\.\.([\\/]|$)' -or
    $SkillFolderName.StartsWith('.')) {
    throw '请输入一个技能文件夹名，不要输入路径。'
}

function Get-ChildFullPath {
    param([string]$Path, [string]$Parent)
    $separators = [char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    $parentFull = [System.IO.Path]::GetFullPath($Parent).TrimEnd($separators) + [System.IO.Path]::DirectorySeparatorChar
    $pathFull = [System.IO.Path]::GetFullPath($Path)
    if (-not $pathFull.StartsWith($parentFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "路径超出允许目录：$Path"
    }
    return $pathFull
}
function Remove-ChildPath {
    param([string]$Path, [string]$Parent)
    $safePath = Get-ChildFullPath -Path $Path -Parent $Parent
    if (Test-Path -LiteralPath $safePath) { Remove-Item -LiteralPath $safePath -Recurse -Force }
}

$localAppData = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::LocalApplicationData)
if ([string]::IsNullOrWhiteSpace($localAppData)) { throw '无法找到本机 LocalAppData 缓存目录。' }
$cacheRoot = Join-Path $localAppData 'AmazonSkills\skills'
$null = New-Item -ItemType Directory -Path $cacheRoot -Force
$targetDir = Get-ChildFullPath -Path (Join-Path $cacheRoot $SkillFolderName) -Parent $cacheRoot

$tempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$workspace = Join-Path $tempBase ('amazon-skills-refresh-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $workspace -Force
$zipPath = Join-Path $workspace 'repository.zip'
$extractRoot = Join-Path $workspace 'extract'
$stageDir = Join-Path $cacheRoot ('.refresh-' + [guid]::NewGuid().ToString('N'))
$backupDir = $null

try {
    $cacheToken = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds().ToString() + '-' + [guid]::NewGuid().ToString('N')
    $zipUrl = 'https://codeload.github.com/' + $RepositorySlug + '/zip/refs/heads/' + $BranchName + '?t=' + $cacheToken
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing -MaximumRedirection 10 -TimeoutSec 120 -Headers @{
        'Cache-Control' = 'no-cache'
        'Pragma' = 'no-cache'
    }
    if ((Get-Item -LiteralPath $zipPath).Length -gt 100MB) { throw '技能仓库压缩包超过 100 MB，已停止解压。' }

    $null = Add-Type -AssemblyName System.IO.Compression.FileSystem
    $null = New-Item -ItemType Directory -Path $extractRoot -Force
    $archive = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        if ($archive.Entries.Count -gt 20000) { throw '压缩包文件数量异常，已停止解压。' }
        $totalBytes = [long]0
        $separators = [char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
        $extractPrefix = [System.IO.Path]::GetFullPath($extractRoot).TrimEnd($separators) + [System.IO.Path]::DirectorySeparatorChar
        foreach ($entry in $archive.Entries) {
            $name = $entry.FullName.Replace('\', '/')
            if ($name.StartsWith('/') -or $name -match '^[A-Za-z]:' -or $name -match '(^|/)\.\.(/|$)' -or $name -match '[\x00-\x1f]') {
                throw '压缩包中存在不安全的路径，已停止解压。'
            }
            $relative = $name.Replace('/', [System.IO.Path]::DirectorySeparatorChar)
            $destination = [System.IO.Path]::GetFullPath((Join-Path $extractRoot $relative))
            if (-not $destination.StartsWith($extractPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                throw '压缩包文件超出临时解压目录，已停止解压。'
            }
            $unixMode = [int](($entry.ExternalAttributes -shr 16) -band 0xFFFF)
            if (($unixMode -band 0xF000) -eq 0xA000) { throw '压缩包包含符号链接，已停止解压。' }
            $totalBytes += $entry.Length
            if ($totalBytes -gt 250MB) { throw '解压内容超过 250 MB，已停止解压。' }
        }
    }
    finally { $archive.Dispose() }

    [System.IO.Compression.ZipFile]::ExtractToDirectory($zipPath, $extractRoot)
    $roots = @(Get-ChildItem -LiteralPath $extractRoot -Directory -Force)
    if ($roots.Count -ne 1) { throw '压缩包目录结构异常。' }

    $skillsRoot = Join-Path $roots[0].FullName 'skills'
    if (-not (Test-Path -LiteralPath $skillsRoot -PathType Container)) { throw '仓库中没有 skills 目录。' }
    $separators = [char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    $skillsPrefix = [System.IO.Path]::GetFullPath($skillsRoot).TrimEnd($separators) + [System.IO.Path]::DirectorySeparatorChar
    $sourceDir = [System.IO.Path]::GetFullPath((Join-Path $skillsRoot $SkillFolderName))
    if (-not $sourceDir.StartsWith($skillsPrefix, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not (Test-Path -LiteralPath $sourceDir -PathType Container)) { throw "仓库中不存在技能目录：$SkillFolderName" }

    $null = New-Item -ItemType Directory -Path $stageDir -Force
    Get-ChildItem -LiteralPath $sourceDir -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $stageDir -Recurse -Force | Out-Null
    }

    if (Test-Path -LiteralPath $targetDir) {
        $backupDir = Join-Path $cacheRoot ('.backup-' + [guid]::NewGuid().ToString('N'))
        $null = Get-ChildFullPath -Path $backupDir -Parent $cacheRoot
        Move-Item -LiteralPath $targetDir -Destination $backupDir | Out-Null
    }
    try { Move-Item -LiteralPath $stageDir -Destination $targetDir | Out-Null }
    catch {
        if ($backupDir -and (Test-Path -LiteralPath $backupDir) -and -not (Test-Path -LiteralPath $targetDir)) {
            Move-Item -LiteralPath $backupDir -Destination $targetDir | Out-Null
            $backupDir = $null
        }
        throw
    }
    if ($backupDir -and (Test-Path -LiteralPath $backupDir)) {
        try { Remove-ChildPath -Path $backupDir -Parent $cacheRoot } catch { }
    }
}
finally {
    if (Test-Path -LiteralPath $workspace) { Remove-ChildPath -Path $workspace -Parent $tempBase }
    if (Test-Path -LiteralPath $stageDir) { Remove-ChildPath -Path $stageDir -Parent $cacheRoot }
}
Write-Output $targetDir
