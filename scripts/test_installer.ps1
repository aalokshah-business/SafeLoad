$ErrorActionPreference = 'Stop'
$project = if ($PSScriptRoot) { Split-Path $PSScriptRoot -Parent } else { (Get-Location).Path }
$installer = Join-Path $project 'install.ps1'
$tokens = $null
$parseErrors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($installer, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
$runInstaller = [scriptblock]::Create((Get-Content -LiteralPath $installer -Raw))
Add-Type -AssemblyName System.IO.Compression.FileSystem
function Get-TestHash([string]$Path) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($Path)
    try { return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '') }
    finally { $stream.Dispose(); $algorithm.Dispose() }
}
$fixture = Join-Path $project ('tmp\installer-test-' + [guid]::NewGuid().ToString('N'))
$payload = Join-Path $fixture 'payload\SafeLoad'
New-Item -ItemType Directory -Force -Path $payload | Out-Null
Set-Content -LiteralPath (Join-Path $payload 'SafeLoad.exe') -Value 'installer test fixture; never executed'
Set-Content -LiteralPath (Join-Path $payload 'VERSION') -Value '1.0.0'
$archive = Join-Path $fixture 'release.zip'
[IO.Compression.ZipFile]::CreateFromDirectory((Split-Path $payload -Parent), $archive)
$checksum = $archive + '.sha256'
Set-Content -LiteralPath $checksum -Value (Get-TestHash $archive)
$target = Join-Path $fixture 'installed'
& $runInstaller -InstallRoot $target -ArchivePath $archive -ChecksumPath $checksum -NoLaunch -NoShortcuts
$installed = @(Get-ChildItem -LiteralPath (Join-Path $target 'versions') -Recurse -Filter 'SafeLoad.exe')
if ($installed.Count -ne 1) { throw 'Offline installation did not create the executable.' }
# A second install must not overwrite an installed or running version.
& $runInstaller -InstallRoot $target -ArchivePath $archive -ChecksumPath $checksum -NoLaunch -NoShortcuts
$installed = @(Get-ChildItem -LiteralPath (Join-Path $target 'versions') -Recurse -Filter 'SafeLoad.exe')
if ($installed.Count -ne 2) { throw 'Reinstallation did not retain the previous version.' }
$badTarget = Join-Path $fixture 'checksum-must-not-install'
Set-Content -LiteralPath $checksum -Value ('0' * 64)
$rejected = $false
try {
    & $runInstaller -InstallRoot $badTarget -ArchivePath $archive -ChecksumPath $checksum -NoLaunch -NoShortcuts
} catch { $rejected = $_.Exception.Message -like '*checksum verification failed*' }
if (-not $rejected -or (Test-Path -LiteralPath $badTarget)) { throw 'Checksum mismatch was not rejected before installation.' }
$evilArchive = Join-Path $fixture 'traversal.zip'
$zip = [IO.Compression.ZipFile]::Open($evilArchive, [IO.Compression.ZipArchiveMode]::Create)
try { [void]$zip.CreateEntry('../outside.txt') } finally { $zip.Dispose() }
Set-Content -LiteralPath $checksum -Value (Get-TestHash $evilArchive)
$rejected = $false
try {
    & $runInstaller -InstallRoot $badTarget -ArchivePath $evilArchive -ChecksumPath $checksum -NoLaunch -NoShortcuts
} catch { $rejected = $_.Exception.Message -like '*Unsafe path*' }
if (-not $rejected -or (Test-Path -LiteralPath $badTarget)) { throw 'Archive traversal was not rejected before installation.' }
Write-Host 'Installer checks passed: syntax, offline install, reinstall, checksum rejection, traversal rejection.'
