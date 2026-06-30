@echo off
REM Wrapper para o Agendador de Tarefas do Windows.
REM Processa o dia anterior inteiro (run_nightly.py sem flags) e registra log.
REM %~dp0 = pasta deste .bat (raiz do projeto), então funciona em qualquer caminho.
cd /d "%~dp0"
if not exist logs mkdir logs
.venv\Scripts\python.exe scheduler\run_nightly.py >> "logs\nightly.log" 2>&1
