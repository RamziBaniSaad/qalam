# ============================================================
#  Qalam - Rueckmeldung fuer den Toggle (Strg+Alt+W).
#
#  Spielt einen Ton UND zeigt eine kurze Sprechblase neben der Uhr.
#  Bewusst KEIN Meldungsfenster: das wuerde den Fokus stehlen und
#  mitten im Tippen stoeren. Die Sprechblase tut das nicht.
#
#  Aufruf:  powershell -File signal.ps1 -Zustand an|aus
# ============================================================
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('an', 'aus')]
    [string]$Zustand
)

$ErrorActionPreference = 'SilentlyContinue'
$basis = $PSScriptRoot

if ($Zustand -eq 'an') {
    $wav   = Join-Path $basis 'assets\tool_on.wav'
    $titel = 'Qalam ist AN'
    $text  = 'Diktat bereit. Strg+Alt+W schaltet wieder aus.'
    $icon  = [System.Windows.Forms.ToolTipIcon]::Info
} else {
    $wav   = Join-Path $basis 'assets\tool_off.wav'
    $titel = 'Qalam ist AUS'
    $text  = 'VRAM frei. Strg+Alt+W schaltet wieder ein.'
    $icon  = [System.Windows.Forms.ToolTipIcon]::None
}

# Ton zuerst -- er ist die eigentliche Rueckmeldung, die Blase nur die Bestaetigung.
if (Test-Path $wav) {
    try {
        $player = New-Object System.Media.SoundPlayer $wav
        $player.Play()
    } catch { }
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$blase = New-Object System.Windows.Forms.NotifyIcon
$blase.Icon = [System.Drawing.SystemIcons]::Information
$blase.BalloonTipTitle = $titel
$blase.BalloonTipText  = $text
$blase.BalloonTipIcon  = $icon
$blase.Visible = $true
$blase.ShowBalloonTip(1500)

# Windows blendet die Blase nur ein, solange der Prozess lebt.
Start-Sleep -Milliseconds 1800
$blase.Visible = $false
$blase.Dispose()
