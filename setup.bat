@echo off
rem etl2d: makes the .venv beside this file and installs what etl2d needs (notes, section 2).
rem Run it once; again after an update. To start from scratch: delete the .venv folder.
cd /d "%~dp0"
where python >nul 2>nul || (echo Python not found: install it from python.org, 3.12 or later, with "Add python.exe to PATH" & exit /b 1)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" || (echo etl2d needs Python 3.12 or later & exit /b 1)
if not exist .venv\Scripts\python.exe python -m venv .venv || exit /b 1
.venv\Scripts\python -m pip install --upgrade pip || exit /b 1
rem GDAL for Windows, from Christoph Gohlke's wheels (PyPI has only its sources).
.venv\Scripts\python -m pip install --index-url https://gisidx.github.io/gwi gdal || exit /b 1
.venv\Scripts\python -m pip install -r requirements.txt || exit /b 1
.venv\Scripts\python -c "from osgeo import gdal; print('GDAL', gdal.__version__)"
.venv\Scripts\python -m etl2d nodes
echo.
echo etl2d is ready: etl2d.bat (the window), or etl2d.bat run examples\grand-place.json
