@echo off
REM X Autopilot 1スロット実行（Windowsタスクスケジューラ用フォールバック）
REM 使い方: run_slot.cmd morning1
cd /d "%~dp0.."
python -m xautopilot.run --slot %1
