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

REM =====================================================================
REM  KLYUCHI I TOKENY lezhat v OTDELNOM faile deploy\my-secrets.bat
REM
REM  Pochemu ne zdes: etot fail lezhit v git. Kogda vy vpisyvali klyuch
REM  pryamo v nego, ocherednoy "git pull" libo otkazyvalsya obnovlyatsya,
REM  libo zatiral vashu stroku - i klyuch tiho propadal. Imenno tak kvota
REM  TonAPI konchilas 21.09.2026 na uzhe poluchennom klyuche.
REM  Vtoraya prichina: token, vpisannyy v otslezhivaemyy fail, legko
REM  sluchayno zakommitit v publichnyy repozitoriy.
REM
REM  my-secrets.bat v .gitignore: pull ego ne trogaet NIKOGDA.
REM =====================================================================
REM Bez skobok (...) namerenno: vnutri bloka cmd razbiraet perenapravleniya
REM i simvol ^ po svoim pravilam, i sozdanie faila moglo by tiho ne srabotat.
if exist "%~dp0my-secrets.bat" goto secrets_ready

echo.
echo === Sozdayu deploy\my-secrets.bat ===
>  "%~dp0my-secrets.bat" echo @echo off
>> "%~dp0my-secrets.bat" echo REM Vashi klyuchi. Fail v .gitignore - git pull ego NE trogaet.
>> "%~dp0my-secrets.bat" echo REM Bez probelov vokrug znaka "=" i bez kavychek.
>> "%~dp0my-secrets.bat" echo REM.
>> "%~dp0my-secrets.bat" echo REM TONAPI_KEY - besplatnyy klyuch. Gde vzyat:
>> "%~dp0my-secrets.bat" echo REM   tonconsole.com, vhod cherez Telegram, sozdat proekt,
>> "%~dp0my-secrets.bat" echo REM   razdel TonAPI, skopirovat API key.
>> "%~dp0my-secrets.bat" echo REM Bez nego rabotaet anonimnyy dostup, a u nego SUTOCHNAYA
>> "%~dp0my-secrets.bat" echo REM kvota na neskolko ciklov - potom slepota do polunochi UTC.
>> "%~dp0my-secrets.bat" echo set TONAPI_KEY=
>> "%~dp0my-secrets.bat" echo REM.
>> "%~dp0my-secrets.bat" echo REM Telegram: token u @BotFather, chat_id u @userinfobot.
>> "%~dp0my-secrets.bat" echo REM Bez nih nahodki nekuda slat - bot budet rabotat molcha.
>> "%~dp0my-secrets.bat" echo set TELEGRAM_BOT_TOKEN=
>> "%~dp0my-secrets.bat" echo set TELEGRAM_CHAT_ID=
echo Fail sozdan. Vpishite v nego klyuchi i zapustite etot bat zanovo.
echo.

:secrets_ready
call "%~dp0my-secrets.bat"

REM 5 kollekciy x 15 stranic = 75 zaprosov. Pauza 1.1s mezhdu nimi
REM zadana v kode, znachit odin cikl zanimaet ~83 sekundy.
REM Interval 60s byl by KOROCHE cikla - cikly nalezali by drug na druga.
set FLOOR_SAMPLE_PAGES=15
set POLL_INTERVAL_SEC=120
set DRY_RUN=1

REM --- Sutochnyy byudzhet zaprosov k TonAPI ---
REM Klyuch snimaet ogranichenie po CHASTOTE, no NE sutochnoe. 75 zaprosov
REM kazhdye 120s = 54 000 v sutki, i kvota sgoraet k obedu (progon 18.09.2026).
REM Bot sam rastyagivaet interval tak, chtoby ostatka hvatilo do polunochi UTC.
REM Znachenie po umolchaniyu uzhe 10000 - stroka nizhe nuzhna tolko chtoby
REM postavit drugoe. 0 = vyklyuchit ogranichenie.
REM
REM NE umenshayte FLOOR_SAMPLE_PAGES radi ekonomii kvoty: vyborka na sale-lotah
REM padaet proporcionalno stranicam, a pri vyborke menshe MIN_FLOOR_SAMPLE (40)
REM torgovlya propuskaetsya voobshche. Luchshe redkiy shag, chem sleploy floor.
set TONAPI_DAILY_BUDGET=10000

REM --- Komissii ---
REM 2%% - stavka Getgems imenno dlya Telegram-podarkov (ih spravka).
REM Obshchaya stavka 5%%, no k podarkam ona ne otnositsya.
set MARKETPLACE_FEE_PCT=0.02
REM Royalti sozdatelya. Na kartochke Timeless Books bylo 0, no eto odna
REM kollekciya. Proverte Creator Fee v svoih i postavte fakticheskoe:
REM snizhenie royalti UVELICHIVAET raschetnuyu pribyl.
set ROYALTY_PCT=0

REM --- Pokupka tolko na proverennyh ploshchadkah ---
REM U lota na ploshchadke "Other" adres kontrakta prodazhi SOVPADAL
REM s adresom samogo predmeta - protokol tam drugoy. Platit tuda po
REM nashey sheme znachit otpravlyat dengi vslepuyu.
set ALLOWED_MARKETS=Getgems Sales

REM --- Bank ---
REM VAZHNO: eto ne "skolko ne zhalko", a FAKTICHESKIY balans koshelka.
REM Bot schitaet potolok sdelki ot etogo chisla, i esli ono zanizheno -
REM on budet otklonyat sdelki, kotorye na samom dele po karmanu.
REM Imenno tak i sluchilos 21.09.2026: v faile stoyalo 10 pri realnyh 19,
REM i raschet dostupnosti kollekciy byl pro nesushchestvuyushchiy bank.
set BANKROLL_TON=19

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

REM --- Uvedomleniya v Telegram ---
REM Token i chat_id zadayutsya v deploy\my-secrets.bat (sm. vyshe).
REM Bez nih bot rabotaet tak zhe, prosto molcha.
set HEARTBEAT_MIN=60

REM Skolko uvedomleniy o NAHODKAH slat maksimum za chas. 0 = ne slat.
set FIND_NOTIFY_MAX_PER_HOUR=10

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
