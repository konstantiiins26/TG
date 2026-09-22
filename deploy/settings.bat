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

REM Whitelist vyvoditsya v KONCE faila - sm. tam, prichina vazhnaya.



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

REM =====================================================================
REM  PEREOPREDELENIYA DLYA ETOY KONKRETNOY MASHINY: deploy\my-local.bat
REM
REM  Nuzhno, kogda bot rabotaet na DVUH kompyuterah srazu. Fail v .gitignore,
REM  poetomu u PK i u noutbuka on svoy i git pull ego ne trogaet.
REM
REM  GLAVNOE PRAVILO DVUH MASHIN: kvota zhivet na KLYUCHE, a schetchik
REM  byudzheta - v baze kazhdoy mashiny. Dve mashiny na ODNOM klyuche obe
REM  schitayut byudzhet polnym i vmeste tratyat vdvoe bolshe realnoy kvoty.
REM  Lechitsya odnim iz dvuh sposobov, i tretego net:
REM    a) dva raznyh TONAPI_KEY (po odnomu na mashinu) - togda kazhdaya
REM       nablyudaet svoi 6 kollekciy raz v ~3.2 min. Eto VDVOE bystree,
REM       chem odna mashina na 12 kollekciyah (6.5 min);
REM    b) odin klyuch na oboih, no TONAPI_DAILY_BUDGET=20000 na KAZHDOY.
REM       Skorost ostanetsya 6.5 min - vyigrysha net, kvota ta zhe.
REM
REM  Kollekcii mezhdu mashinami nado RAZDELIT, a ne dublirovat. Dve mashiny,
REM  smotryashchie odnu vitrinu, dayut ne dvoynuyu chastotu, a dvoynoy rashod:
REM  vyborki u nih chut raznye, i pri obedinenii zapisey --merge otbrosit
REM  peresechenie (inache oborot poschitaetsya s fantomnymi prodazhami).
REM =====================================================================

if exist "%~dp0my-local.bat" goto local_ready

echo.
echo === Sozdayu deploy\my-local.bat (pereopredeleniya dlya etoy mashiny) ===
>  "%~dp0my-local.bat" echo @echo off
>> "%~dp0my-local.bat" echo REM Nastroyki TOLKO etoy mashiny. Fail v .gitignore.
>> "%~dp0my-local.bat" echo REM Po umolchaniyu pusto - bot beret vse 12 kollekciy.
>> "%~dp0my-local.bat" echo REM.
>> "%~dp0my-local.bat" echo REM --- Rabota na DVUH mashinah: razdelite kollekcii ---
>> "%~dp0my-local.bat" echo REM Na PERVOY mashine ostavit stroku A, na VTOROY - stroku B,
>> "%~dp0my-local.bat" echo REM ubrav "REM A:" / "REM B:" v nachale nuzhnoy stroki.
>> "%~dp0my-local.bat" echo REM.
>> "%~dp0my-local.bat" echo REM A: set TARGET_COLLECTIONS=EQDLda715GocP1sYDkCecPhO7eFNsNvARD4pumbGSan96wvZ,EQCZ4-h65iTiWDPRPcLlS63gbcS40YBadEFLA4W-iIWUZld0,EQD9z87hRZAV7C2MV1gk39-bSg5Yfs2EdMr9HfK81IuB2Rlc,EQCBK_JBASAA5XVz1D17Pn--kQaMWm0b9wReVtsEdRO4Tgy9,EQBEngWldzev9oqzctu59Go9afX8HF8HksZ9pJ7x1bRJXsc7,EQA0EzRYX5wm_q46_NX8b7EYhtOkXfXgsr06ETbov1a7StZl
>> "%~dp0my-local.bat" echo REM B: set TARGET_COLLECTIONS=EQC212djrq0gglQXi8MSFX1bcw4LHw3Es62lKvt1lZzzsYuF,EQDLM65t0shS7gZAg0lMltGHYhsU94PzsMJHhYibmRV7kdUs,EQDc08YxzZWtlKAohSybNc3kXAkAPPtHch-jY_E6KMQ3b1mn,EQBlBJ4n01pmYez5VPd8Wo598s8agbQCyVOjucXKxLDAi9r7,EQD1YFp12AGEgX6C3uiWh751EcRxPZo6GtBmHziY29jcbQzS,EQBCe75G0AhjqC64B7H_BHP0wgfONX_x98rszmsEwndDVAjG
>> "%~dp0my-local.bat" echo REM.
>> "%~dp0my-local.bat" echo REM Esli klyuch TonAPI na oboih mashinah ODIN - raskommentiruyte:
>> "%~dp0my-local.bat" echo REM set TONAPI_DAILY_BUDGET=20000
echo Fail sozdan. Po umolchaniyu nichego ne menyaet.
echo.

:local_ready
call "%~dp0my-local.bat"

REM --- Whitelist: TOLKO zdes, posle vseh pereopredeleniy ---
REM cmd raskryvaet %%TARGET_COLLECTIONS%% SRAZU. Esli postavit etu stroku
REM vyshe, ona zapomnit staryy spisok, i posle smeny spiska bot stal by
REM otvergat sobstvennye kollekcii kak nedoverennye.
set COLLECTION_WHITELIST=%TARGET_COLLECTIONS%

REM Konec nastroek. Zapusk - v run.bat ili start-windows.bat.
