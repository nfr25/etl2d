@echo off
rem etl2d with the .venv's Python: etl2d.bat (the window), etl2d.bat ui job.json, etl2d.bat run job.json, etl2d.bat nodes.
rem Works from any folder: the workflow's relative paths start at the workflow's own folder.
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
"%~dp0.venv\Scripts\python" -m etl2d %*
