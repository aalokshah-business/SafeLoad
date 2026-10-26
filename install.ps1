<#
.SYNOPSIS
Installs the latest SafeLoad Windows x64 release for the current user.
.DESCRIPTION
Downloads a ZIP and SHA-256 checksum from GitHub Releases, verifies before extraction,
and creates Desktop and Start Menu shortcuts. No administrator rights or Python needed.
Use ArchivePath and ChecksumPath together to install an already downloaded release.
#>
[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\SafeLoad'),
    [string]$ArchivePath,
    [string]$ChecksumPath,
    [switch]$NoShortcuts,
    [switch]$NoLaunch
)

$ErrorActionPreference = 'Stop'
function Get-SafeLoadHash([string]$Path) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead((Resolve-Path -LiteralPath $Path).Path)
    try { return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '') }
    finally { $stream.Dispose(); $algorithm.Dispose() }
}
if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem) {
    throw 'SafeLoad requires 64-bit Windows 10 or newer.'
}
if ([bool]$ArchivePath -ne [bool]$ChecksumPath) {
    throw 'Supply both ArchivePath and ChecksumPath for an offline installation.'
}
$releaseBase = 'https://github.com/aalokshah-business/SafeLoad/releases/latest/download'
$asset = 'SafeLoad-Windows-x64.zip'
$work = Join-Path ([IO.Path]::GetTempPath()) ('SafeLoad-install-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    if (-not $ArchivePath) {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $ArchivePath = Join-Path $work $asset
        $ChecksumPath = Join-Path $work ($asset + '.sha256')
        Write-Host 'Downloading SafeLoad...'
        Invoke-WebRequest -UseBasicParsing -Uri ($releaseBase + '/' + $asset) -OutFile $ArchivePath
        Invoke-WebRequest -UseBasicParsing -Uri ($releaseBase + '/' + $asset + '.sha256') -OutFile $ChecksumPath
    }
    $expected = (Get-Content -LiteralPath $ChecksumPath -Raw).Trim()
    if ($expected -notmatch '^[a-fA-F0-9]{64}$' -or
        (Get-SafeLoadHash $ArchivePath) -ne $expected) {
        throw 'SafeLoad checksum verification failed. Nothing was installed.'
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $extract = Join-Path $work 'extracted'
    $extractPrefix = [IO.Path]::GetFullPath($extract) + [IO.Path]::DirectorySeparatorChar
    $zip = [IO.Compression.ZipFile]::OpenRead((Resolve-Path -LiteralPath $ArchivePath).Path)
    try {
        $total = [long]0
        foreach ($entry in $zip.Entries) {
            $candidate = [IO.Path]::GetFullPath((Join-Path $extract $entry.FullName))
            if (-not $candidate.StartsWith($extractPrefix, [StringComparison]::OrdinalIgnoreCase) -or
                $entry.FullName.Contains(':') -or
                (($entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000) {
                throw 'Unsafe path or link in SafeLoad archive.'
            }
            $total += $entry.Length
            if ($total -gt 1GB) { throw 'SafeLoad archive exceeds extraction limits.' }
        }
    }
    finally { $zip.Dispose() }
    [IO.Compression.ZipFile]::ExtractToDirectory((Resolve-Path -LiteralPath $ArchivePath).Path, $extract)
    $bundle = Join-Path $extract 'SafeLoad'
    if (-not (Test-Path -LiteralPath (Join-Path $bundle 'SafeLoad.exe') -PathType Leaf)) {
        throw 'The downloaded archive does not contain SafeLoad.exe.'
    }
    $version = (Get-Content -LiteralPath (Join-Path $bundle 'VERSION') -Raw).Trim()
    if ($version -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid SafeLoad release version.' }
    $InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
    $versions = Join-Path $InstallRoot 'versions'
    New-Item -ItemType Directory -Force -Path $versions | Out-Null
    $destination = Join-Path $versions ($version + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    # New version directory avoids overwriting an app that is currently running.
    Copy-Item -LiteralPath $bundle -Destination $destination -Recurse
    $exe = Join-Path $destination 'SafeLoad.exe'
    if (-not $NoShortcuts) {
        $shell = New-Object -ComObject WScript.Shell
        $locations = @([Environment]::GetFolderPath('Desktop'),
                       [Environment]::GetFolderPath('Programs'))
        foreach ($location in $locations) {
            if ($location) {
                $shortcut = $shell.CreateShortcut((Join-Path $location 'SafeLoad.lnk'))
                $shortcut.TargetPath = $exe
                $shortcut.WorkingDirectory = $destination
                $shortcut.Description = 'SafeLoad - Offline Keyword Scanner'
                $shortcut.Save()
            }
        }
    }
    Write-Host "SafeLoad $version installed at $destination"
    if (-not $NoLaunch) { Start-Process -FilePath $exe }
}
finally {
    # Delete only this invocation's unique, verified temporary directory.
    $tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    $resolvedWork = [IO.Path]::GetFullPath($work)
    if ($resolvedWork.StartsWith($tempBase, [StringComparison]::OrdinalIgnoreCase) -and
        [IO.Path]::GetFileName($resolvedWork) -match '^SafeLoad-install-[a-f0-9]{32}$') {
        Remove-Item -LiteralPath $resolvedWork -Recurse -Force -ErrorAction SilentlyContinue
    }
}
