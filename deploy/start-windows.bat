@echo off
chcp 65001 >nul
REM Zapusk zapisi rynka na Windows.
REM Peremennye cherez "set" zhivut tolko v odnom okne cmd, poetomu posle
REM perezagruzki ih nuzhno zadavat zanovo. Etot fail delaet eto za vas.

REM Fail lezhit v deploy\, a gift_sniper.py - na uroven vyshe.
cd /d "%~dp0.."

REM --- Kollekcii cherez zapyatuyu, bez probelov ---
set TARGET_COLLECTIONS=EQBlBJ4n01pmYez5VPd8Wo598s8agbQCyVOjucXKxLDAi9r7,EQDc08YxzZWtlKAohSybNc3kXAkAPPtHch-jY_E6KMQ3b1mn,EQBCe75G0AhjqC64B7H_BHP0wgfONX_x98rszmsEwndDVAjG,EQD1YFp12AGEgX6C3uiWh751EcRxPZo6GtBmHziY29jcbQzS,EQDLM65t0shS7gZAg0lMltGHYhsU94PzsMJHhYibmRV7kdUs
set COLLECTION_WHITELIST=%TARGET_COLLECTIONS%

REM 5 kollekciy x 15 stranic = 75 zaprosov. Pauza 1.1s mezhdu nimi
REM zadana v kode, znachit odin cikl zanimaet ~83 sekundy.
REM Interval 60s byl by KOROCHE cikla - cikly nalezali by drug na druga.
set FLOOR_SAMPLE_PAGES=15
set POLL_INTERVAL_SEC=120
set DRY_RUN=1

REM --- Bank ---
REM Postavte fakticheskiy razmer banka v TON (posmotrite kurs sami).
set BANKROLL_TON=10

REM Rezerv na gaz. 0.15 TON za operaciyu, flip = ~2 operacii,
REM znachit 1 TON hvataet primerno na 6-7 operaciy.
REM Rezerv 5 pri banke 10 oznachal by, chto polovina banka ne rabotaet.
REM VNIMANIE: esli pozitsiy budet mnogo, 1 TON mozhet konchitsya -
REM togda ne na chto budet vystavit kuplennoe na prodazhu.
set RESERVE_TON=1

REM Potolok odnoy sdelki: 20%% banka = 2 TON pri banke 10.
REM Pri 10%% potolok byl by 1 TON, i rabochiy floor upal by do ~1.3,
REM gde gaz sedaet pochti vsyu pribyl.
set MAX_POSITION_PCT=20

REM --- Povyshennyy limit dlya isklyuchitelno vygodnyh sdelok ---
REM Sdelke s ROI ot 150 procentov razresheno do 20 procentov banka
REM vmesto obychnyh 10. Bank, rezerv i limity chasa/sutok ne otmenyayutsya.
set HIGH_ROI_PCT=150
set MAX_POSITION_PCT_HIGH_ROI=20

REM --- Stop-loss ---
REM Floor upal nizhe ceny pokupki na 25 procentov - vyhodim.
REM Vyderzhka 6 chasov: na tonkom rynke floor skachet ot odnogo lota.
set ENABLE_STOP_LOSS=1
set STOP_LOSS_PCT=25
set STOP_LOSS_MIN_HOURS=6

REM --- Uvedomleniya v Telegram (neobyazatelno) ---
REM Gde vzyat token i chat id - sm. SETUP.md, razdel Uvedomleniya.
REM Bez nih bot rabotaet tak zhe, prosto molcha.
set TELEGRAM_BOT_TOKEN=
set TELEGRAM_CHAT_ID=
set HEARTBEAT_MIN=60

echo === Zapis rynka. Ne zakryvayte eto okno. ===
echo Zapis idet v market_history.jsonl i dopisyvaetsya posle perezapuska.
echo.
py gift_sniper.py --record

echo.
echo === Bot ostanovilsya. Okno ostavleno otkrytym, chtoby vidno bylo prichinu. ===
pause
