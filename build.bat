@echo off
REM Build script for Niagara Workbench Setup Tool
REM Produces a single portable EXE in dist\

echo Building Niagara Workbench Setup Tool...
echo.

cd /d "%~dp0"

pip install PySide6 pyinstaller

pyinstaller build.spec --clean --noconfirm

echo.
if exist "dist\NiagaraWorkbenchSetupTool.exe" (
    echo Build successful!
    echo EXE location: %CD%\dist\NiagaraWorkbenchSetupTool.exe
) else (
    echo Build FAILED - check output above
)

pause