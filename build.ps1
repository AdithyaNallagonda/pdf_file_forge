# FileForge build — run on Windows 10/11 x64 with Python 3.11 or 3.12 installed.
#   powershell -ExecutionPolicy Bypass -File build.ps1
# Output: dist\FileForge\FileForge.exe  and (if Inno Setup present) dist\FileForge-Setup.exe
param(
    [switch]$SkipInstaller,      # stop after the exe
    [switch]$NoTesseract,        # don't embed OCR even if it's on this machine
    [switch]$SkipTests,          # bypass the regression suite (not advised)
    [string]$SignThumbprint,     # SHA1 thumbprint of the code-signing certificate
    [string]$TimestampUrl = "http://timestamp.sectigo.com"
    [string]$IsccPath,           # explicit path to ISCC.exe if auto-detection fails
    # OCR language packs to embed. Defaults cover an all-India arbitration practice.
    [string[]]$Languages = @("eng","osd","hin","tel","tam","kan","mal","mar",
                             "guj","ben","pan","ori","urd","san")
)
$ErrorActionPreference = "Stop"
$Root = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
Set-Location $Root

Write-Host "== FileForge build ==" -ForegroundColor Cyan
Write-Host "Project folder: $Root"

$missing = @()
foreach ($need in @("main.py", "requirements.txt", "FileForge.spec",
                    "app\ui.py", "app\config.py", "app\ops\compress.py")) {
    if (-not (Test-Path (Join-Path $Root $need))) { $missing += $need }
}
if ($missing.Count) {
    Write-Host "`nMissing from $Root :" -ForegroundColor Red
    $missing | ForEach-Object { Write-Host "   $_" -ForegroundColor Red }
    Write-Host "`nWhat is actually here:" -ForegroundColor Yellow
    Get-ChildItem $Root | Select-Object -First 25 |
        ForEach-Object { Write-Host ("   " + $(if ($_.PSIsContainer) {"[dir] "} else {"      "}) + $_.Name) }
    Write-Host "`nExpected layout:" -ForegroundColor Yellow
    Write-Host "   FileForge\build.ps1"
    Write-Host "   FileForge\main.py"
    Write-Host "   FileForge\app\ui.py  (and 5 more .py files)"
    Write-Host "   FileForge\app\ops\compress.py  (and 4 more .py files)"
    throw "Incomplete project folder. Extract FileForge-source.zip and run the copy inside it."
}

$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
if (-not $py) { throw "Python 3.11+ not found." }

# --- build venv -------------------------------------------------------------
$Venv = Join-Path $Root ".buildenv"
if (-not (Test-Path $Venv)) { & $py.Source -m venv $Venv }
$VPy = Join-Path $Venv "Scripts\python.exe"
& $VPy -m pip install --upgrade pip wheel --quiet
& $VPy -m pip install -r requirements.txt pyinstaller pywin32 --quiet
Write-Host "Dependencies installed." -ForegroundColor Green

# --- stage OCR engine (Apache-2.0, redistributable) -------------------------
$Tools = Join-Path $Root "tools"
Remove-Item $Tools -Recurse -Force -ErrorAction SilentlyContinue
if (-not $NoTesseract) {
    $tessExe = (Get-Command tesseract -EA SilentlyContinue).Source
    if (-not $tessExe -and (Test-Path "C:\Program Files\Tesseract-OCR\tesseract.exe")) {
        $tessExe = "C:\Program Files\Tesseract-OCR\tesseract.exe"
    }
    if ($tessExe) {
        $srcDir = Split-Path -Parent $tessExe
        $dst = Join-Path $Tools "tesseract"
        New-Item -ItemType Directory -Force -Path (Join-Path $dst "tessdata") | Out-Null
        Copy-Item (Join-Path $srcDir "*") $dst -Recurse -Force `
            -Exclude "tessdata" -EA SilentlyContinue
        # Language packs. Copy what the local install has; fetch the rest from
        # Google's tessdata repo (Apache-2.0, so redistribution is fine).
        $tdDir = Join-Path $dst "tessdata"
        $got = @(); $missed = @()
        foreach ($lang in $Languages) {
            $target = Join-Path $tdDir "$lang.traineddata"
            $local  = Join-Path $srcDir "tessdata\$lang.traineddata"
            if (Test-Path $local) {
                Copy-Item $local $target -Force; $got += $lang; continue
            }
            $url = "https://raw.githubusercontent.com/tesseract-ocr/tessdata/main/$lang.traineddata"
            try {
                Write-Host "  downloading $lang.traineddata ..." -NoNewline
                Invoke-WebRequest -Uri $url -OutFile $target -UseBasicParsing -TimeoutSec 180
                Write-Host " ok"; $got += $lang
            } catch {
                Write-Host " FAILED"; $missed += $lang
            }
        }
        Write-Host ("Languages embedded: " + ($got -join ", ")) -ForegroundColor Green
        if ($missed) {
            Write-Host ("Could not obtain: " + ($missed -join ", ") +
                        " - those languages will be unavailable.") -ForegroundColor Yellow
        }
        Write-Host "Embedded Tesseract from $srcDir (OCR works out of the box)." -ForegroundColor Green
    } else {
        Write-Host "Tesseract not on this machine - scanned PDFs will not OCR." -ForegroundColor Yellow
        Write-Host "  Install it, then re-run:  winget install UB-Mannheim.TesseractOCR" -ForegroundColor Yellow
    }
}
# NOTE: Ghostscript is deliberately NOT embedded - it is AGPL, and bundling it
# into a proprietary installer creates licence obligations. The installer offers
# to fetch it separately; only PDF compression depends on it.

# --- regression suite (build must not proceed over a known-broken tree) -----
if (-not $SkipTests) {
    Write-Host "Running regression suite..." -ForegroundColor Cyan
    & $VPy (Join-Path $Root "tests\test_suite.py")
    if ($LASTEXITCODE -ne 0) {
        throw "Regression tests FAILED - build aborted. Use -SkipTests to override."
    }
    Write-Host "All tests passed." -ForegroundColor Green
}

# --- freeze -----------------------------------------------------------------
Remove-Item (Join-Path $Root "build"), (Join-Path $Root "dist") -Recurse -Force -EA SilentlyContinue
& $VPy -m PyInstaller FileForge.spec --noconfirm --clean
$Exe = Join-Path $Root "dist\FileForge\FileForge.exe"
if (-not (Test-Path $Exe)) { throw "Build failed - FileForge.exe not produced." }
$size = [math]::Round((Get-ChildItem (Join-Path $Root "dist\FileForge") -Recurse |
         Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "Built dist\FileForge\FileForge.exe  ($size MB total)" -ForegroundColor Green

# --- verify bundled resources ----------------------------------------------
$Internal = Join-Path $Root "dist\FileForge\_internal"
$checks = @{
    "PyMuPDF layout model (PDF->Markdown)" = "pymupdf\layout\resources"
    "python-docx template (PDF->Word)"     = "docx\templates"
}
foreach ($k in $checks.Keys) {
    if (Test-Path (Join-Path $Internal $checks[$k])) {
        Write-Host "  OK      $k" -ForegroundColor Green
    } else {
        Write-Host "  MISSING $k  ->  _internal\$($checks[$k])" -ForegroundColor Red
        Write-Host "          That feature will fall back or fail at runtime." -ForegroundColor Red
    }
}

# --- smoke test: app must at least import and start -------------------------
$p = Start-Process $Exe -PassThru
Start-Sleep -Seconds 6
if ($p.HasExited) { throw "Smoke test failed: exe exited immediately (code $($p.ExitCode))." }
Stop-Process -Id $p.Id -Force
Write-Host "Smoke test passed." -ForegroundColor Green

# --- code signing -----------------------------------------------------------
# Sign the EXE *before* the installer is built, then sign the installer too:
# the payload has to be trusted as well as the wrapper.
function Invoke-Sign([string]$Path) {
    if (-not $SignThumbprint) { return }
    $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" `
                -Filter signtool.exe -Recurse -EA SilentlyContinue |
                Where-Object { $_.FullName -match "x64" } |
                Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $signtool) { throw "signtool.exe not found - install the Windows SDK." }
    # /tr + /td: an RFC-3161 timestamp keeps the signature valid after the
    # certificate expires. Without it every shipped copy breaks in a year.
    & $signtool.FullName sign /fd SHA256 /tr $TimestampUrl /td SHA256 `
        /sha1 $SignThumbprint /d "FileForge" $Path
    if ($LASTEXITCODE -ne 0) { throw "Signing failed for $Path" }
    & $signtool.FullName verify /pa /v $Path | Out-Null
    Write-Host "Signed: $Path" -ForegroundColor Green
}
Invoke-Sign $Exe

# --- installer --------------------------------------------------------------
if (-not $SkipInstaller) {
    $iscc = $null
    if ($IsccPath -and (Test-Path $IsccPath)) { $iscc = $IsccPath }

    # 1) on PATH (rare - Inno Setup does not add itself)
    if (-not $iscc) { $iscc = (Get-Command iscc -EA SilentlyContinue).Source }

    # 2) uninstall registry entry, machine-wide and per-user (winget uses either)
    if (-not $iscc) {
        foreach ($hive in @("HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                            "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
                            "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")) {
            if ($iscc) { break }
            Get-ChildItem $hive -EA SilentlyContinue |
                Where-Object { $_.PSChildName -like "Inno Setup*" } | ForEach-Object {
                    $loc = (Get-ItemProperty $_.PSPath -EA SilentlyContinue).InstallLocation
                    if ($loc -and (Test-Path (Join-Path $loc "ISCC.exe"))) {
                        $iscc = Join-Path $loc "ISCC.exe"
                    }
                }
        }
    }

    # 3) the .iss "Compile" shell verb points straight at the compiler
    if (-not $iscc) {
        $cmd = (Get-ItemProperty "Registry::HKEY_CLASSES_ROOT\InnoSetupScriptFile\shell\Compile\command" `
                -EA SilentlyContinue)."(default)"
        if ($cmd -match '"([^"]*ISCC\.exe)"') { $iscc = $Matches[1] }
    }

    # 4) brute-force the usual roots, any version number
    if (-not $iscc) {
        foreach ($root in @($env:ProgramFiles, ${env:ProgramFiles(x86)},
                            "$env:LOCALAPPDATA\Programs")) {
            if ($iscc -or -not $root) { continue }
            $hit = Get-ChildItem $root -Filter "ISCC.exe" -Recurse -Depth 2 -EA SilentlyContinue |
                   Select-Object -First 1
            if ($hit) { $iscc = $hit.FullName }
        }
    }
    if ($iscc) {
        Write-Host "Inno Setup compiler: $iscc"
        & $iscc "installer.iss"
        if ($LASTEXITCODE -ne 0) { throw "ISCC failed with exit code $LASTEXITCODE." }
        $setup = Join-Path $Root "dist\FileForge-Setup.exe"
        Invoke-Sign $setup
        $sMB = [math]::Round((Get-Item $setup).Length / 1MB, 1)
        Write-Host "Installer: $setup  ($sMB MB) - this is the file you send." -ForegroundColor Green
    } else {
        Write-Host "Inno Setup not found - skipping installer." -ForegroundColor Yellow
        Write-Host "  If it IS installed, locate ISCC.exe and pass it explicitly:" -ForegroundColor Yellow
        Write-Host '    .\build.ps1 -IsccPath "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"' -ForegroundColor Yellow
    }
}
Write-Host "`nDone." -ForegroundColor Cyan
