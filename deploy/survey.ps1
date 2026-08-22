<#
    Reports what is currently configured on this VM for the Operations Portal.

    READ ONLY. This script starts nothing, stops nothing, installs nothing and
    changes no configuration. Run it before touching anything, so we know what
    the previous attempt left behind.

    Run in an ADMINISTRATOR PowerShell:
        cd C:\apps\dash\Operations_Portal
        powershell -ExecutionPolicy Bypass -File .\deploy\survey.ps1 > survey.txt
    Then send survey.txt back.
#>

$ErrorActionPreference = 'SilentlyContinue'

function Section($name) {
    Write-Output ""
    Write-Output ("=" * 70)
    Write-Output "  $name"
    Write-Output ("=" * 70)
}

Section "Machine and network"
Write-Output "Hostname      : $env:COMPUTERNAME"
Write-Output "Windows       : $((Get-CimInstance Win32_OperatingSystem).Caption)"
Write-Output ""
Write-Output "Local IPv4 addresses:"
Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike '127.*' } |
    Select-Object IPAddress, InterfaceAlias | Format-Table -AutoSize | Out-String

Write-Output "Public IP as seen from outside (this is what the DNS A record must point at):"
try { (Invoke-RestMethod -Uri 'https://api.ipify.org?format=json' -TimeoutSec 10).ip }
catch { "  could not reach ipify - no outbound internet?" }

Section "What is listening"
Get-NetTCPConnection -State Listen |
    Where-Object { $_.LocalPort -in 80, 443, 8000, 8080, 5432 } |
    Select-Object LocalAddress, LocalPort,
        @{n='Process'; e={ (Get-Process -Id $_.OwningProcess).ProcessName }} |
    Sort-Object LocalPort -Unique | Format-Table -AutoSize | Out-String

Section "IIS sites and bindings"
Import-Module WebAdministration
if (Get-Module WebAdministration) {
    Get-Website | Select-Object Name, State, PhysicalPath,
        @{n='Bindings'; e={ ($_.Bindings.Collection | ForEach-Object { $_.bindingInformation + ' ' + $_.protocol }) -join ' | ' }} |
        Format-List | Out-String
    Write-Output "Application pools:"
    Get-ChildItem IIS:\AppPools | Select-Object Name, State, ManagedRuntimeVersion |
        Format-Table -AutoSize | Out-String
} else {
    Write-Output "  WebAdministration module not available - is IIS installed?"
}

Section "IIS modules that the proxy depends on"
foreach ($m in 'IIS URL Rewrite Module 2', 'Application Request Routing', 'Microsoft URL Rewrite') {
    $found = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*' |
             Where-Object DisplayName -like "*$m*" | Select-Object -First 1
    if ($found) { Write-Output ("  INSTALLED : " + $found.DisplayName + "  " + $found.DisplayVersion) }
    else        { Write-Output "  missing   : $m" }
}
Write-Output ""
Write-Output "ARR server-level proxy switch (must be True for any reverse proxy to work):"
try {
    (Get-WebConfigurationProperty -PSPath 'MACHINE/WEBROOT/APPHOST' `
        -Filter 'system.webServer/proxy' -Name 'enabled').Value
} catch { "  could not read - ARR probably not installed" }

Section "Every web.config found under C:\apps"
Get-ChildItem -Path 'C:\apps' -Filter 'web.config' -Recurse -ErrorAction SilentlyContinue |
    ForEach-Object {
        Write-Output ""
        Write-Output ("--- " + $_.FullName + " ---")
        Get-Content $_.FullName | Out-String
    }

Section "Windows services that look related"
Get-Service | Where-Object { $_.Name -match 'nssm|django|creative|operation|portal|waitress' } |
    Select-Object Name, Status, StartType | Format-Table -AutoSize | Out-String
Write-Output "Their command lines:"
Get-CimInstance Win32_Service |
    Where-Object { $_.PathName -match 'python|nssm|waitress|manage.py' } |
    Select-Object Name, State, StartMode, PathName | Format-List | Out-String

Section "Scheduled tasks (Windows has no cron; reminders need these)"
Get-ScheduledTask | Where-Object { $_.TaskName -match 'reminder|digest|portal|django' } |
    Select-Object TaskName, State | Format-Table -AutoSize | Out-String
Write-Output "  (empty above means the reminder and digest jobs are not scheduled)"

Section "Certificates in the machine store"
Get-ChildItem Cert:\LocalMachine\My |
    Select-Object Subject, NotAfter, Thumbprint,
        @{n='SAN'; e={ ($_.DnsNameList -join ', ') }} |
    Format-List | Out-String

Section "PostgreSQL"
$psql = Get-ChildItem 'C:\Program Files\PostgreSQL\*\bin\psql.exe' -ErrorAction SilentlyContinue |
        Select-Object -Last 1
if ($psql) {
    Write-Output "psql found: $($psql.FullName)"
    Write-Output "Databases and roles (you will be prompted for the postgres password):"
    & $psql.FullName -U postgres -c '\l' 2>&1 | Out-String
    & $psql.FullName -U postgres -c '\du' 2>&1 | Out-String
} else {
    Write-Output "  psql.exe not found under C:\Program Files\PostgreSQL"
}

Section "The project folder"
$proj = 'C:\apps\dash\Operations_Portal'
if (Test-Path $proj) {
    Write-Output "Path: $proj"
    Get-ChildItem $proj | Select-Object Mode, LastWriteTime, Length, Name |
        Format-Table -AutoSize | Out-String

    Write-Output "Present?"
    foreach ($f in '.env', '.venv', 'manage.py', 'requirements.txt', 'staticfiles', 'mediafiles', 'db.sqlite3', 'web.config') {
        $p = Join-Path $proj $f
        Write-Output ("  {0,-18} {1}" -f $f, $(if (Test-Path $p) { 'yes' } else { 'no' }))
    }

    Write-Output ""
    Write-Output ".env keys present (VALUES DELIBERATELY HIDDEN):"
    if (Test-Path "$proj\.env") {
        Get-Content "$proj\.env" | Where-Object { $_ -match '^[A-Z_]+=' } |
            ForEach-Object { "  " + ($_ -split '=')[0] }
    } else { Write-Output "  no .env - the app cannot start without one" }

    Write-Output ""
    Write-Output "Git:"
    Push-Location $proj
    git rev-parse --abbrev-ref HEAD 2>&1 | ForEach-Object { "  branch: $_" }
    git log -1 --format='  commit: %h %ad %s' --date=short 2>&1
    git status --porcelain 2>&1 | Select-Object -First 10 | ForEach-Object { "  dirty: $_" }
    Pop-Location

    Write-Output ""
    Write-Output "Python:"
    py -3.13 --version 2>&1 | ForEach-Object { "  launcher: $_" }
    if (Test-Path "$proj\.venv\Scripts\python.exe") {
        & "$proj\.venv\Scripts\python.exe" --version 2>&1 | ForEach-Object { "  venv: $_" }
        & "$proj\.venv\Scripts\python.exe" -m pip list 2>&1 | Out-String
    } else { Write-Output "  no .venv" }
} else {
    Write-Output "  $proj does not exist"
}

Section "Django's own view of it"
if (Test-Path "$proj\.venv\Scripts\python.exe") {
    Push-Location $proj
    Write-Output "manage.py check --deploy:"
    & ".venv\Scripts\python.exe" manage.py check --deploy 2>&1 | Out-String
    Write-Output "Unapplied migrations (any line not marked [X] still needs running):"
    & ".venv\Scripts\python.exe" manage.py showmigrations 2>&1 | Select-String -NotMatch '\[X\]' | Out-String
    Pop-Location
}

Section "DNS as this VM sees it"
foreach ($h in 'operation-portal.dash-mfb.com', 'operations-portal.dash-mfb.com', 'dash-mfb.com') {
    $a = (Resolve-DnsName $h -Type A -ErrorAction SilentlyContinue).IPAddress
    Write-Output ("  {0,-34} {1}" -f $h, $(if ($a) { $a -join ', ' } else { 'NO A RECORD' }))
}

Write-Output ""
Write-Output "Survey complete. Nothing was changed."
