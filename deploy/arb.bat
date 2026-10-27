@echo off
chcp 65001 >nul
REM =====================================================================
REM  ODNA KOMANDA: svyazki mezhdu marketami po see.tg, s proverkoy po
REM  LESTNICE cen. Nichego ne pokupaet i ne prodaet.
REM
REM  Otchet pishet SAM BOT cherez --log-file, a NE perenapravlenie `>`.
REM  Prichina izmerena 10.10.2026 na odnoy i toy zhe komande:
REM    cmd        -> Python pisal kodirovku lokali cp1251, gde net strelki
REM                  i otchet prevratilsya v trejsbeki UnicodeEncodeError.
REM    PowerShell -> Python pisal verniy UTF-8, no PowerShell dekodiroval
REM                  vyvod po cp866 i perekodiroval v UTF-16LE: fail nachalsya
REM                  s BOM ff fe, a kirillica prishla dvoynym musorom.
REM  Vtoroy sluchay nashey kodirovkoy ne lechitsya voobshche - perekodiruet
REM  potrebitel, uzhe posle nas. Poetomu fail otkryvaem sami.
REM =====================================================================

cd /d "%~dp0.."
call "%~dp0settings.bat"

set LOG=%~dp0..\arb-report.txt
if exist "%LOG%" del "%LOG%"

echo.
echo ================================================================
echo  Schitayu svyazki. Eto minuta-dve: po zaprosu na kollekciyu
echo  plyus po odnomu na kazhduyu paru, kotoruyu proveryaem lestnicey.
echo  Otchet budet v: %LOG%
echo ================================================================
echo.

py gift_sniper.py --seetg-arb --log-file "%LOG%"

echo.
echo ================================================================
echo  Gotovo. Otkryvayu otchet - otpravte ego v chat celikom.
echo ================================================================
start notepad "%LOG%"
