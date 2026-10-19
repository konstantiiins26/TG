@echo off
chcp 65001 >nul
REM =====================================================================
REM  ODNA KOMANDA: razryvy VNUTRI odnogo marketa (po umolchaniyu mrkt).
REM  Kupit samyy deshevyy lot modeli i vstat pod vtoroy na toy zhe vitrine.
REM  Nichego ne pokupaet i ne prodaet.
REM
REM  Verdikt vse ravno po LESTNICE vseh marketov: pokupatel vidit pyat
REM  vitrin srazu, i lot s sosednego marketa uvodit ego nezavisimo ot togo,
REM  gde my reshili torgovat. Izmereno 07.10.2026 na Candy Canes: borda
REM  obeshchala +3.29, lestnica dala -0.17.
REM
REM  Otchet pishet SAM BOT cherez --log-file, a NE perenapravlenie.
REM  Prichina izmerena 10.10.2026: cmd pisal cp1251 i lomal strelki,
REM  PowerShell perekodiroval vyvod v UTF-16LE s BOM.
REM
REM  Drugoy market: deploy\mrkt.bat portals
REM =====================================================================

cd /d "%~dp0.."
call "%~dp0settings.bat"

set MKT=%1
if "%MKT%"=="" set MKT=mrkt

set LOG=%~dp0..\mrkt-report.txt
if exist "%LOG%" del "%LOG%"

echo.
echo ================================================================
echo  Schitayu razryvy na vitrine: %MKT%
echo  Raskhod: po zaprosu na kollekciyu plyus po odnomu na kandidata.
echo  Otchet budet v: %LOG%
echo ================================================================
echo.

py gift_sniper.py --market-scan %MKT% --log-file "%LOG%"

echo.
echo ================================================================
echo  Gotovo. Otkryvayu otchet - otpravte ego v chat celikom.
echo ================================================================
start notepad "%LOG%"
