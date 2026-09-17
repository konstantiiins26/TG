@echo off
chcp 65001 >nul
REM Zapusk zapisi rynka na Windows. Sohranite ryadom s gift_sniper.py.
REM Peremennye cherez "set" zhivut tolko v odnom okne cmd, poetomu posle
REM perezagruzki ih nuzhno zadavat zanovo. Etot fail delaet eto za vas.

REM Fail lezhit v deploy\, a gift_sniper.py - na uroven vyshe.
cd /d "%~dp0.."

REM --- Vpishite svoi adresa kollekciy cherez zapyatuyu (bez probelov) ---
set TARGET_COLLECTIONS=EQC212djrq0gglQXi8MSFX1bcw4LHw3Es62lKvt1lZzzsYuF
set COLLECTION_WHITELIST=%TARGET_COLLECTIONS%

REM 15 stranic x 100 lotov pri intervale 60s = 15 zaprosov v minutu.
set FLOOR_SAMPLE_PAGES=15
set POLL_INTERVAL_SEC=60
set DRY_RUN=1

echo === Zapis rynka. Ne zakryvayte eto okno. ===
echo Zapis idet v market_history.jsonl i dopisyvaetsya posle perezapuska.
echo.
py gift_sniper.py --record

echo.
echo === Bot ostanovilsya. Okno ostavleno otkrytym, chtoby vidno bylo prichinu. ===
pause
