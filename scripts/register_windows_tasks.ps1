# X Autopilot — Windowsタスクスケジューラに4スロット登録（GH Actionsを使わずローカル運用する場合）
# 実行: PowerShellを管理者で開き、このスクリプトを実行
# 前提: .env に全APIキーを設定済み・python が PATH 上にある・PCが起動している時間帯のみ発火

$proj = Split-Path -Parent $PSScriptRoot
$cmd  = Join-Path $proj "scripts\run_slot.cmd"

$slots = @(
    @{ Name = "XAutopilot_morning1"; Time = "07:00"; Slot = "morning1" },
    @{ Name = "XAutopilot_morning2"; Time = "08:30"; Slot = "morning2" },
    @{ Name = "XAutopilot_evening1"; Time = "19:00"; Slot = "evening1" },
    @{ Name = "XAutopilot_evening2"; Time = "21:00"; Slot = "evening2" }
)

foreach ($s in $slots) {
    $action  = New-ScheduledTaskAction -Execute $cmd -Argument $s.Slot
    $trigger = New-ScheduledTaskTrigger -Daily -At $s.Time
    Register-ScheduledTask -TaskName $s.Name -Action $action -Trigger $trigger -Force `
        -Description "X Autopilot $($s.Slot) ($($s.Time) JST)"
    Write-Host "登録: $($s.Name) @ $($s.Time) -> $($s.Slot)"
}
# 週次学習（日曜22:00）
$learnCmd = "cmd.exe"
$learnArg = "/c cd /d `"$proj`" && python -m xautopilot.learn"
$la = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c cd /d `"$proj`" && python -m xautopilot.learn"
$lt = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At "22:00"
Register-ScheduledTask -TaskName "XAutopilot_learn" -Action $la -Trigger $lt -Force `
    -Description "X Autopilot 週次学習 (日22:00 JST)"
Write-Host "登録: XAutopilot_learn @ Sun 22:00"
Write-Host "完了。タスクスケジューラで確認してください。"
