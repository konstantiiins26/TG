@echo off
chcp 65001 >nul
REM Zapusk zapisi rynka na Windows.
REM Peremennye cherez "set" zhivut tolko v odnom okne cmd, poetomu posle
REM perezagruzki ih nuzhno zadavat zanovo. Etot fail delaet eto za vas.

REM Fail lezhit v deploy\, a gift_sniper.py - na uroven vyshe.
cd /d "%~dp0.."
call "%~dp0settings.bat"

REM --- Proverka klyuchey PERED startom -------------------------------
REM Molchalivyy start s pustym klyuchom - eto poteryannyy den: kvota
REM anonimnogo dostupa konchaetsya za neskolko ciklov, i bot slepnet
REM do polunochi UTC. Luchshe skazat ob etom srazu.
if "%TONAPI_KEY%"=="" (
    echo.
    echo [!] TONAPI_KEY PUST. Sutochnoy kvoty anonimnogo dostupa hvatit
    echo     na neskolko ciklov, potom bot oslepnet do polunochi UTC.
    echo     Klyuch besplatnyy: tonconsole.com
    echo     Vpishite ego v deploy\my-secrets.bat
    echo.
)
if "%TELEGRAM_BOT_TOKEN%"=="" (
    echo [!] TELEGRAM_BOT_TOKEN pust - nahodki nekuda otpravlyat.
    echo     Vpishite token v deploy\my-secrets.bat
    echo.
)

echo === Zapis rynka. Ne zakryvayte eto okno. ===
echo Zapis idet v market_history.jsonl i dopisyvaetsya posle perezapuska.
echo.
py gift_sniper.py --record

echo.
echo === Bot ostanovilsya. Okno ostavleno otkrytym, chtoby vidno bylo prichinu. ===
pause
