@echo off
REM Wrapper para o Agendador de Tarefas do Windows — ciclo de hora em hora.
REM Extrai e classifica o que chegou HOJE e reescreve o briefing de hoje
REM (run_nightly.py --intraday). O fechamento do dia, o backlog e os briefings
REM semanal/mensal ficam com run_daily.bat, à noite.
cd /d "%~dp0"
if not exist logs mkdir logs
.venv\Scripts\python.exe scheduler\run_nightly.py --intraday >> "logs\intraday.log" 2>&1
