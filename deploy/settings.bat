@echo off
REM Vse nastroyki bota v odnom meste.
REM
REM Etot fail NICHEGO NE ZAPUSKAET - on tolko zadaet peremennye. Ego
REM vyzyvayut i start-windows.bat, i run.bat, poetomu nastroyki ne
REM razezzhayutsya mezhdu rezhimami.
REM
REM Pochemu eto voobshche nuzhno: komandy "set" deystvuyut TOLKO v tom okne,
REM gde oni vypolnilis. Zapusk "py gift_sniper.py --test-telegram" v chistom
REM cmd ne videl ni klyucha, ni tokena, ni banka - i bot chestno soobshchal,
REM chto tokena net, hotya v faile on byl.

REM --- Kollekcii cherez zapyatuyu, bez probelov ---
REM PEREKLYUCHATEL: 12 kollekciy (bystro) ili 32 (shiroko).
REM
REM Progon --rank 21.09.2026 (zapis 15 ch, 1699 snapshotov) pokazal oborot
REM ROVNO U ETIH 12; u ostalnyh 20 on byl nulevoy. Dvadcat zamershih zhgut
REM 300 zaprosov iz 480 za cikl i nichego ne dayut vzamen.
REM
REM   32 kollekcii -> 480 zaprosov/cikl -> nablyudenie raz v 17.3 min
REM   12 kollekciy -> 180 zaprosov/cikl -> nablyudenie raz v  6.5 min
REM
REM Oshibochnyy listing zhivet minuty, poetomu shag nablyudeniya - eto NE
REM udobstvo, a veroyatnost voobshche uvidet nahodku.
REM
REM CHEGO ETOT ZAMER NE DOKAZYVAET: oborot schitaetsya po 5 samym deshevym
REM lotam (CANDIDATES_TO_ANALYZE), a zapis vsego 15 chasov. "Zamerla" znachit
REM "ni odin iz pyati deshevyh ne ischez", a ne "v kollekcii ne bylo sdelok".
REM Poetomu spisok iz 32 NE UDALEN - vernutsya mozhno, pomenyav mestami REM.
REM
REM --- AKTIVNO: 12 kollekciy s nenulevym oborotom ---
set TARGET_COLLECTIONS=EQDLda715GocP1sYDkCecPhO7eFNsNvARD4pumbGSan96wvZ,EQCZ4-h65iTiWDPRPcLlS63gbcS40YBadEFLA4W-iIWUZld0,EQD9z87hRZAV7C2MV1gk39-bSg5Yfs2EdMr9HfK81IuB2Rlc,EQCBK_JBASAA5XVz1D17Pn--kQaMWm0b9wReVtsEdRO4Tgy9,EQBEngWldzev9oqzctu59Go9afX8HF8HksZ9pJ7x1bRJXsc7,EQA0EzRYX5wm_q46_NX8b7EYhtOkXfXgsr06ETbov1a7StZl,EQC212djrq0gglQXi8MSFX1bcw4LHw3Es62lKvt1lZzzsYuF,EQDLM65t0shS7gZAg0lMltGHYhsU94PzsMJHhYibmRV7kdUs,EQDc08YxzZWtlKAohSybNc3kXAkAPPtHch-jY_E6KMQ3b1mn,EQBlBJ4n01pmYez5VPd8Wo598s8agbQCyVOjucXKxLDAi9r7,EQD1YFp12AGEgX6C3uiWh751EcRxPZo6GtBmHziY29jcbQzS,EQBCe75G0AhjqC64B7H_BHP0wgfONX_x98rszmsEwndDVAjG

REM --- ZAPASNOY: vse 32 (ubrat REM zdes i postavit na stroku vyshe) ---
REM set TARGET_COLLECTIONS=EQBlBJ4n01pmYez5VPd8Wo598s8agbQCyVOjucXKxLDAi9r7,EQDc08YxzZWtlKAohSybNc3kXAkAPPtHch-jY_E6KMQ3b1mn,EQBCe75G0AhjqC64B7H_BHP0wgfONX_x98rszmsEwndDVAjG,EQD1YFp12AGEgX6C3uiWh751EcRxPZo6GtBmHziY29jcbQzS,EQDLM65t0shS7gZAg0lMltGHYhsU94PzsMJHhYibmRV7kdUs,EQC2lsUy1SKxJEJBwj5ZCfVnLPvAqDqy5c26Xg8xS_pDTXGk,EQAo_snApDDqF6GKV0xe_T5oe28r842gJtgmkgPMhX0-dRkh,EQA8DCWyCWyywgOKYORerRoSVevWrUQ_FjKQgNihxY1227x7,EQB1ATaKGNYk6T5R2cA18BOF-KB_idaKKigwYI2jtjWuLg8n,EQAXHW9KVYYgDmLaUNcgzNPZ4WKGek97-ldsd0fPUHg4K7SU,EQCZ4-h65iTiWDPRPcLlS63gbcS40YBadEFLA4W-iIWUZld0,EQC212djrq0gglQXi8MSFX1bcw4LHw3Es62lKvt1lZzzsYuF,EQC8WVW9DSN4PPfFlCW2AHJkXxBUHBFsvnhXiYqSTpD7tXsp,EQD6mH9bwbn6S3M_tCRWOvqAIW8M34kRwbI01niGLRPeDPsl,EQBEngWldzev9oqzctu59Go9afX8HF8HksZ9pJ7x1bRJXsc7,EQDIruSTyxvq60gUH8j2kkj3qzoBrBaJy9WkKbeNNRasWe4j,EQBV5XozKA0e06Z5y6eL7pWrUUpEolbPhNdcNS0K4ZDk1jCs,EQAPNu648fe_uqUoeH6V_-fIDJYea_5Xu2rXn6iZFil49bMY,EQCBK_JBASAA5XVz1D17Pn--kQaMWm0b9wReVtsEdRO4Tgy9,EQC-ZdsouFU-xMa509yP8kzKceZnGV7lSQskxima1Mr3iDYB,EQDLda715GocP1sYDkCecPhO7eFNsNvARD4pumbGSan96wvZ,EQDycOgkLwcfPDokh8q-2DIUzVhPetdFuZmwrFYFP6i1nZ_u,EQCgaTxb2wA_3Bi8Ec4FFNu8CauoHo0VPpnwxdrhAgOrOXvA,EQDRrfw5pgIC4e6NafUAx52Z9Ym6q1k26xxaXR_qx0LKJJ7D,EQD9z87hRZAV7C2MV1gk39-bSg5Yfs2EdMr9HfK81IuB2Rlc,EQBAXR68f1UgRhToFR_bXY1zPJy5O6sm2St0CRTo92BTxGiH,EQBSIId7sMmlqN8oBGaMNtUeuaLeSQPUR1ByMwpnfWL3hhZq,EQAUffQWl09_yhXDTp8oN13Px8ygPm0xcyNGhHOiONV-x3om,EQDx-SqQEhP9Rzfi2cqdehTVUvQbArsUz1X7t-ul8IiKZpYb,EQA0EzRYX5wm_q46_NX8b7EYhtOkXfXgsr06ETbov1a7StZl,EQA2lHcvZWW_bN_2NMKrkEUv9xz6fx8wTE5upa8u1neZb6hJ,EQCeTSJOPXP_SSvOjILY-kui4bGHUmsa-U7TXP4DjUANTl4s

REM Whitelist raskryvaetsya SRAZU, poetomu stoit POSLE vybora spiska.
REM Esli postavit ego vyshe, on ostanetsya ot starogo znacheniya.
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

REM 12 kollekciy x 15 stranic = 180 zaprosov. Pauza 1.1s mezhdu nimi
REM zadana v kode, znachit sam cikl zanimaet ne menshe ~3.3 minut.
REM POLL_INTERVAL_SEC nizhe etogo prosto ne sobludaetsya - cikl dlinnee.
REM Fakticheskiy shag pri byudzhete 40000: ~6.5 min (schitaet sam bot).
REM
REM 23.09.2026: FLOOR_PAGE_SIZE podnyat so 100 do 1000 (maksimum TonAPI).
REM 15 stranic x 1000 = 15 000 predmetov vmesto 1500. Chislo ZAPROSOV to zhe,
REM no kollekciya iz 13 000 teper pokryvaetsya CELIKOM, a ne na 12%.
REM Pochemu eto vazhno: floor po 12% kollekcii byl VDVOE vyshe nastoyashchego
REM (11 TON protiv 5.65 na vitrine), to est bot pridumyval skidki, kotoryh net.
REM Otvety stali v 10 raz bolshe po obemu - esli kvota TonAPI schitaetsya po
REM trafiku, bot izmerit eto sam (budget_learn_limit) i rastyanet interval.
set FLOOR_SAMPLE_PAGES=40
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
REM Podnyat s 10000: 32 kollekcii x 15 stranic = 480 zaprosov na cikl, i
REM pri 10000 interval rastyanulsya by do 69 minut. Eto ne "raskrutit bota" -
REM 10000 bylo MOEY DOGADKOY, chtoby ostanovit szhiganie anonimnoy kvoty.
REM Realnogo limita vashego klyucha nikto ne izmeryal.
REM
REM Bot teper izmerit ego sam: v moment otkaza servera on zapominaet, skolko
REM zaprosov proshlo, i dalshe planiruet po etomu chislu s zapasom 5%.
REM Odin den slepoty - cena za edinstvennyy IZMERENNYY fakt o limite.
set TONAPI_DAILY_BUDGET=40000

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

REM Potolok odnoy sdelki: 35%% ot 18 dostupnyh = 6.30 TON.
REM
REM Postavleno po pryamoy prosbe vladeltsa ("potolok vyshe, gotov teryat").
REM 35 - ne proizvolnoe chislo: eto MINIMUM, pri kotorom dostupny vse pyat
REM tekushchih kollekciy. Pri floor 6 pribylnaya pokupka stoit do 5.55 TON,
REM pri 30%% potolok 5.40 - i samaya dorogaya kollekciya otpadala by.
REM
REM CHEGO ETO STOIT, chestno: pri 6.30 na sdelku bank derzhit VSEGO 2
REM pozicii odnovremenno. Odna oshibka - eta tret banka, dve podryad -
REM dve treti. STOP_AFTER_LOSSES=3 (po umolchaniyu) ostanovit bota posle
REM treh ubytkov podryad, i pri takom razmere pozicii eto pravilno.
set MAX_POSITION_PCT=35

REM --- Povyshennyy limit dlya isklyuchitelno vygodnyh sdelok ---
REM Raven obychnomu, to est mehanizm VYKLYUCHEN. Vysokiy raschetnyy ROI -
REM eto rovno to mesto, gde model oshibaetsya chashche vsego (glubokaya
REM skidka chashche znachit prichinu, a ne oshibku prodavtsa), poetomu
REM povyshat stavku imenno tam nado tolko posle bektesta po svoey zapisi.
set HIGH_ROI_PCT=150
set MAX_POSITION_PCT_HIGH_ROI=35

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

REM --- Ssylka na lot v uvedomlenii ---
REM Forma URL svercena s getgems.io i verna. Pervaya versiya otkryvalas
REM pustoy iz-za formy ADRESA: TonAPI otdaet raw (0:48de...), a vitriny
REM ponimayut EQ... Ispravleno v kode (friendly_ton_address).
REM Pustaya stranica vozmozhna i pri vernoy ssylke - esli lot UZHE USHEL
REM s prodazhi. Eto ne bag, a rynok, i obozrevatel ih razlichaet.
REM Ryadom bot vsegda daet ssylku na obozrevatel i sam adres lota.
set GIFT_URL_TEMPLATE=https://getgems.io/nft/{address}
set EXPLORER_URL_TEMPLATE=https://tonviewer.com/{address}

REM --- Rezhim --flip: model avtora video (monohrom + krasivyy nomer) ---
REM ETO CHUZHAYA MODEL i ona NE upravlyaet torgovley. U nee DRUGOY VYHOD:
REM pokupka NA floor (i do +20% vyshe), prodazha x1.5 s holdom do 48ch.
REM Nasha model pokupaet TOLKO NIZHE floor. Smeshivat ih nelzya, poetomu
REM --flip - eto otchet dlya ruk, a ne signal k pokupke.
REM
REM Porog bezubytochnosti poschitan: model okupaetsya, esli po x1.5 uhodit
REM primerno KAZHDYY CHETVERTYY lot. Eta dolya NE IZMERENA.
REM
REM Byudzhet 7 - iz video u avtora. U vas bank bolshe, mozhno podnyat.
set FLIP_BUDGET_TON=7

REM Maksimalnaya pereplata nad floor. 0.20 = +20%, vyshe - SKIP "zavisnet".
set FLIP_MAX_PREMIUM=0.20

REM Ballov dlya BUY i dlya WATCH. Sam avtor svoy porog ne vyderzhivaet:
REM ego primer #16630 nabiraet menshe 70. Snizte do 65 - uvidite bolshe,
REM no i musora bolshe.
set FLIP_BUY_SCORE=70
set FLIP_WATCH_SCORE=50

REM Vo skolko raz vystavlyat otnositelno ceny pokupki.
set FLIP_TARGET_MULT=1.5

REM Tablica "model -> cvet" dlya monohroma. Cveta modeli v API NET.
REM Sobrat spisok modeley bot mozhet sam:  deploy\run.bat --colors
REM Tam, gde cvet nazvan v imeni modeli (Cocoa Bear), on prostavlyaetsya
REM avtomaticheski. Ostalnoe - odno slovo rukami, glyadya na kartinku.
REM Zapolnennoe vami povtornyy zapusk NE zatret.
set FLIP_MODEL_COLORS=model_colors.json

REM Konec nastroek. Zapusk - v run.bat ili start-windows.bat.
