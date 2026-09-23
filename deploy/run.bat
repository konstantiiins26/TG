@echo off
chcp 65001 >nul
REM Zapusk lyuboy komandy bota S NASTROYKAMI.
REM
REM GLAVNYY REZHIM - zapis rynka. On zhe shlet v Telegram nahodki i
REM "Deshevle svoih". Rabotaet, poka otkryto eto okno cmd:
REM   deploy\run.bat --record
REM
REM Ostalnye - razovye otchety, seti i vremeni tratyat po-raznomu:
REM   deploy\run.bat --test-telegram      proverit svyaz s Telegram
REM   deploy\run.bat --afford             kakoy floor po karmanu banku
REM   deploy\run.bat --positions          otkrytye pozicii i ih nomera
REM   deploy\run.bat --backtest           progon resheniy po zapisi
REM   deploy\run.bat --rank               gde vitrina zhivee
REM   deploy\run.bat --markets            raznica cen mezhdu ploshchadkami
REM   deploy\run.bat --premium            est li premiya za redkiy treyt
REM   deploy\run.bat --flip               vzglyad modelyu avtora video
REM   deploy\run.bat --colors             peresobrat model_colors.json
REM   deploy\run.bat --probe EQ...        chto parser izvlek iz otveta API
REM
REM Vnimanie: --record i lyuboy otchet odnovremenno - eto dvoynoy rashod
REM sutochnoy kvoty TonAPI. V logah uzhe vidny 429.
REM
REM Bez etogo faila prishlos by pered kazhdoy komandoy vruchnuyu zadavat
REM desyatok peremennyh - i odna zabytaya menyala by rezultat molcha.

cd /d "%~dp0.."
call "%~dp0settings.bat"
py gift_sniper.py %*
