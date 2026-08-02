# FileForge installer: virtualenv, dependencies, Start Menu shortcut.
# Run:  powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
$App  = "FileForge"
$Root = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$Venv = Join-Path $Root ".venv"

Write-Host "== $App setup ==" -ForegroundColor Cyan

$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
if (-not $py) { throw "Python 3.10+ not found. Install from https://python.org and re-run." }

if (-not (Test-Path $Venv)) {
    Write-Host "Creating virtual environment..."
    & $py.Source -m venv $Venv
}
$VPy  = Join-Path $Venv "Scripts\python.exe"
$VPyw = Join-Path $Venv "Scripts\pythonw.exe"

Write-Host "Installing dependencies (a few minutes)..."
& $VPy -m pip install --upgrade pip --quiet
& $VPy -m pip install -r (Join-Path $Root "requirements.txt")

# Optional native helpers
foreach ($t in @(@("gswin64c","Ghostscript  (text-preserving PDF compression)","winget install --id ArtifexSoftware.GhostScript -e"),
                 @("tesseract","Tesseract-OCR (OCR for scanned PDFs)","winget install --id UB-Mannheim.TesseractOCR -e"),
                 @("soffice","LibreOffice   (DOCX->PDF without MS Word)","winget install --id TheDocumentFoundation.LibreOffice -e"))) {
    if (-not (Get-Command $t[0] -ErrorAction SilentlyContinue)) {
        Write-Host ("Optional: {0}  ->  {1}" -f $t[1], $t[2]) -ForegroundColor Yellow
    }
}

# Start Menu shortcut
$StartMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$Lnk = Join-Path $StartMenu "$App.lnk"
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($Lnk)
$sc.TargetPath       = $VPyw
$sc.Arguments        = '"' + (Join-Path $Root "main.py") + '"'
$sc.WorkingDirectory = $Root
$sc.IconLocation     = "$env:SystemRoot\System32\shell32.dll,167"
$sc.Description      = "$App - local PDF/Word/Markdown toolkit"
$sc.Save()

New-Item -ItemType Directory -Force -Path "D:\$App\result_files","D:\$App\temp","D:\$App\logs" -EA SilentlyContinue | Out-Null
Write-Host "`nDone. Launch '$App' from the Start Menu." -ForegroundColor Green
