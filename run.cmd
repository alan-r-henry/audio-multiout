@echo off
rem Console-free launch, for the Startup folder.
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py"
