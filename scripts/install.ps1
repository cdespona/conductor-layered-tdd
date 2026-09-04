param(
    [ValidateSet("binary", "go")]
    [string]$Method = "binary",
    [string]$Dir = "$HOME\bin"
)

$ErrorActionPreference = "Stop"
$Owner = "cdespona"
$Repository = "conductor-layered-tdd"
$Binary = "ltdd.exe"

if ($Method -eq "go") {
    if (-not (Get-Command go -ErrorAction SilentlyContinue)) {
        throw "go is required for -Method go"
    }
    go install "github.com/$Owner/$Repository/cmd/ltdd@latest"
    Write-Host "installed ltdd with go install"
    exit 0
}

$Architecture = switch ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture) {
    "X64" { "amd64" }
    "Arm64" { "arm64" }
    default { throw "unsupported architecture: $($_)" }
}

$Release = Invoke-RestMethod "https://api.github.com/repos/$Owner/$Repository/releases/latest"
$Tag = $Release.tag_name
$Version = $Tag.TrimStart("v")
$Archive = "${Repository}_${Version}_windows_${Architecture}.zip"
$TemporaryDir = Join-Path ([System.IO.Path]::GetTempPath()) ("ltdd-install-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $TemporaryDir | Out-Null

try {
    $ArchivePath = Join-Path $TemporaryDir $Archive
    $ChecksumsPath = Join-Path $TemporaryDir "checksums.txt"
    $BaseUrl = "https://github.com/$Owner/$Repository/releases/download/$Tag"
    Invoke-WebRequest "$BaseUrl/$Archive" -OutFile $ArchivePath
    Invoke-WebRequest "$BaseUrl/checksums.txt" -OutFile $ChecksumsPath

    $Expected = (Get-Content $ChecksumsPath | Where-Object { $_ -match "\s$([regex]::Escape($Archive))$" } | Select-Object -First 1) -split "\s+" | Select-Object -First 1
    $Actual = (Get-FileHash -Algorithm SHA256 $ArchivePath).Hash.ToLowerInvariant()
    if (-not $Expected -or $Expected.ToLowerInvariant() -ne $Actual) {
        throw "checksum verification failed for $Archive"
    }

    Expand-Archive -Path $ArchivePath -DestinationPath $TemporaryDir -Force
    New-Item -ItemType Directory -Path $Dir -Force | Out-Null
    Copy-Item (Join-Path $TemporaryDir $Binary) (Join-Path $Dir $Binary) -Force
    Write-Host "installed ltdd $Tag to $(Join-Path $Dir $Binary)"
}
finally {
    Remove-Item -Recurse -Force $TemporaryDir -ErrorAction SilentlyContinue
}
