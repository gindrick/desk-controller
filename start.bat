@echo off
title DeskControl Launcher
cd /d "%~dp0"

echo ==========================================
echo   DESKCONTROL - spousteni sluzeb
echo ==========================================
echo.

REM --- Mosquitto ---
echo [1/3] Mosquitto MQTT broker...
sc query mosquitto >nul 2>&1
if %errorlevel% == 0 (
    sc start mosquitto >nul 2>&1
    echo       OK - spusten jako Windows sluzba
) else (
    where mosquitto >nul 2>&1
    if %errorlevel% == 0 (
        start "Mosquitto" /min mosquitto
        echo       OK - spusten v pozadi
    ) else (
        echo       CHYBA - mosquitto nenalezeno, spust rucne
    )
)

timeout /t 2 /nobreak >nul

REM --- ESPHome dashboard ---
echo [2/3] ESPHome dashboard...
where esphome >nul 2>&1
if %errorlevel% == 0 (
    start "ESPHome Dashboard" cmd /k "cd /d "%~dp0" && esphome dashboard . && pause"
    echo       OK - http://localhost:6052
) else (
    echo       PRESKOCENO - esphome nenalezeno v PATH
)

timeout /t 1 /nobreak >nul

REM --- Flask web interface ---
echo [3/3] DeskControl web interface...
start "DeskControl Web" cmd /k "cd /d "%~dp0" && python main.py && pause"
echo       OK - http://localhost:5001

echo.
echo ==========================================
echo   Vse spusteno
echo   Web: http://localhost:5001
echo   ESPHome: http://localhost:6052
echo ==========================================
echo.
pause
