@echo off
setlocal

cd /d "%~dp0"

echo [1/4] Stopping running MultiTradingLauncher.exe (if any, avoids file lock)...
taskkill /F /IM MultiTradingLauncher.exe >nul 2>&1

echo [2/4] Installing PyInstaller...
python -m pip install --upgrade pyinstaller
if errorlevel 1 (
  echo [ERROR] Failed to install PyInstaller.
  exit /b 1
)

echo [3/4] Building MultiTradingLauncher.exe (uses MultiTradingLauncher.spec)...
python -m PyInstaller --noconfirm MultiTradingLauncher.spec
if errorlevel 1 (
  echo [ERROR] Build failed.
  exit /b 1
)

echo [4/4] Done.
echo Output: dist\MultiTradingLauncher.exe
endlocal
