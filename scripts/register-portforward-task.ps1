# Rodar como Administrador uma vez para registrar a tarefa agendada.
# Depois disso o port-forward sobe automaticamente a cada login.

$scriptPath = 'C:\Users\pedro\OneDrive\Documentos\GitHub\pedroflix\scripts\pedroflix-portforward.ps1'
$taskPath   = '\pedroflix\'
$taskName   = 'pedroflix-portforward'

$action = New-ScheduledTaskAction `
    -Execute    'powershell.exe' `
    -Argument   "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`""

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit  (New-TimeSpan -Hours 0) `
    -RestartCount        3 `
    -RestartInterval     (New-TimeSpan -Minutes 2) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$principal = New-ScheduledTaskPrincipal `
    -UserId    $env:USERNAME `
    -LogonType Interactive `
    -RunLevel  Highest

Register-ScheduledTask `
    -TaskName   $taskName `
    -TaskPath   $taskPath `
    -Action     $action `
    -Trigger    $trigger `
    -Settings   $settings `
    -Principal  $principal `
    -Description 'Expõe serviços Kubernetes do pedroflix na rede local (port-forward)' `
    -Force

Write-Host ""
Write-Host "Tarefa '$taskName' registrada em '$taskPath'."
Write-Host "Para testar agora sem reiniciar: Start-ScheduledTask -TaskPath '$taskPath' -TaskName '$taskName'"
Write-Host ""
