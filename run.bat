@echo off
rem etl2d with the .venv's Python: run.bat (the window), run.bat ui job.json, run.bat run job.json, run.bat nodes.
rem Works from any folder: the workflow's relative paths start at the workflow's own folder.
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
"%~dp0.venv\Scripts\python" -m etl2d %*
