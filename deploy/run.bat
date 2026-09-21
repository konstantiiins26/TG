@echo off
chcp 65001 >nul
REM Zapusk lyuboy komandy bota S NASTROYKAMI.
REM
REM Primery:
REM   deploy\run.bat --test-telegram      proverit svyaz s Telegram
REM   deploy\run.bat --afford             kakoy floor po karmanu banku
REM   deploy\run.bat --backtest           progon resheniy po zapisi
REM   deploy\run.bat --markets            raznica cen mezhdu ploshchadkami
REM   deploy\run.bat --probe EQ...        chto parser izvlek iz otveta API
REM
REM Bez etogo faila prishlos by pered kazhdoy komandoy vruchnuyu zadavat
REM desyatok peremennyh - i odna zabytaya menyala by rezultat molcha.

cd /d "%~dp0.."
call "%~dp0settings.bat"
py gift_sniper.py %*
