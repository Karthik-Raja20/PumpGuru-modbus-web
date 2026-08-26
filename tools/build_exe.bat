@echo off
REM ============================================================================
REM  Build modbus_quick_test.exe -- run this ON WINDOWS, once.
REM  Produces a single standalone .exe that needs no Python installed to run.
REM ============================================================================

echo Installing required packages...
pip install pymodbus pyserial pyinstaller

echo.
echo Building modbus_quick_test.exe ...
pyinstaller --onefile --name modbus_quick_test modbus_quick_test.py

echo.
echo ============================================================================
echo Done. Your executable is at:  dist\modbus_quick_test.exe
echo You can copy that single file anywhere and run it directly, e.g.:
echo    modbus_quick_test.exe --port COM3 --slave 1 --start 0 --count 20
echo ============================================================================
pause
