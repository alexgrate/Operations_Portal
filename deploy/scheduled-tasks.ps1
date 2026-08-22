<#
    Windows has no cron. These two scheduled tasks are what actually send the
    deadline reminders and the approval digests - without them the portal
    sends nothing on a timer, because Django has no internal scheduler.

    Run once, in an ADMINISTRATOR PowerShell:
        powershell -ExecutionPolicy Bypass -File .\deploy\scheduled-tasks.ps1

    Safe to re-run: it replaces the tasks rather than duplicating them.
#>

$ErrorActionPreference = 'Stop'

$ProjectDir = Split-Path -Parent $PSScriptRoot
$Python     = Join-Path $ProjectDir '.venv\Scripts\python.exe'
$LogDir     = Join-Path $ProjectDir 'logs'

if (-not (Test-Path $Python)) { throw "No virtualenv at $Python. Create it first." }
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Runs as SYSTEM so the jobs survive the deploying account logging out or
# having its password changed.
$Principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

function Register-PortalTask {
    param([string]$Name, [string]$Command, [string]$Description)

    $log = Join-Path $LogDir "$Command.log"
    $action = New-ScheduledTaskAction -Execute 'cmd.exe' `
        -Argument "/c `"$Python`" manage.py $Command >> `"$log`" 2>&1" `
        -WorkingDirectory $ProjectDir

    # Every 5 minutes forever. Each command works out what is actually due, so
    # extra runs send nothing and a missed run catches up.
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 5)

    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -DontStopOnIdleEnd `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
        -MultipleInstances IgnoreNew

    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger `
        -Principal $Principal -Settings $settings -Description $Description | Out-Null

    Write-Host "  registered: $Name  ->  $log"
}

Register-PortalTask -Name 'Dash Portal - task reminders' -Command 'send_task_reminders' `
    -Description 'Emails assignees as their task deadlines approach.'

Register-PortalTask -Name 'Dash Portal - approval digests' -Command 'send_approval_digests' `
    -Description 'Emails each manager the work waiting on their sign-off.'

Write-Host ""
Write-Host "Both tasks registered. Prove them without sending anything:"
Write-Host "  .venv\Scripts\python.exe manage.py send_task_reminders --dry-run"
Write-Host ""
Write-Host "Then watch the logs after five minutes:"
Write-Host "  Get-Content logs\send_task_reminders.log -Tail 20 -Wait"
