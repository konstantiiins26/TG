@echo off
chcp 65001 >nul
REM =====================================================================
REM  ODNA KOMANDA: sobrat tablicu cvetov, nayti loty po modeli avtora
REM  video i proverit ploshchadki. Nichego ne pokupaet i ne prodaet.
REM
REM  Ves vyvod pishetsya v flip-report.txt i otkryvaetsya v bloknote -
REM  ego mozhno celikom otpravit v chat. Na ekrane vidny tolko etapy,
REM  chtoby bylo ponyatno, chto bot ne zavis: kazhdyy etap obhodit vse
REM  kollekcii i zanimaet neskolko minut.
REM =====================================================================

REM VNIMANIE: otchet pishet sam bot cherez --log-file, a NE `>>`. Kodirovku
REM perenapravleniya zadaet obolochka, i ona lomaet otchet: cmd pisal cp1251
REM i uronil kazhduyu stroku so strelkoy, PowerShell perekodiroval v UTF-16LE.
REM Zaodno v fail ne popadayut ANSI-kody, inache kazhdaya stroka v bloknote
REM vyglyadit kak "[90m14:55:27[0m".

cd /d "%~dp0.."
call "%~dp0settings.bat"

set LOG=%~dp0..\flip-report.txt
if exist "%LOG%" del "%LOG%"

echo.
echo ================================================================
echo  Sbor dannyh. Eto neskolko minut na etap - eto normalno.
echo  Otchet budet v: %LOG%
echo ================================================================
echo.

REM VNIMANIE pri pravke: ")" vnutri echo ZAKRYVAET blok if ( ... ) else ( ... ).
REM Stroka "echo [1/3] ... (--colors)..." uronila ves fail s soobshcheniem
REM "... was unexpected at this time". V .bat skobki - eto sintaksis, a ne
REM oformlenie: v echo ih libo net, libo ekraniruyut kak ^( i ^).
REM
REM Tablicu cvetov peresobirat kazhdyy raz NE nado: spisok modeley pochti ne
REM menyaetsya, a progon stoit 12 zaprosov i sekund 15 pri uzhe zhestkom
REM limite TonAPI. Sobiraem tolko esli faila net. Nuzhno obnovit vruchnuyu -
REM deploy\run.bat --colors
if exist "%~dp0..\model_colors.json" (
  echo [1/3] Tablica cvetov uzhe est - propuskayu
  echo [1/3] model_colors.json uzhe sobran, shag propushchen >> "%LOG%"
) else (
  echo [1/3] Tablica cvetov modeley: --colors
  py gift_sniper.py --colors --log-file "%LOG%"
  echo       gotovo
)

echo [2/3] Poisk lotov po modeli avtora: --flip
py gift_sniper.py --flip --log-file "%LOG%"
echo       gotovo

REM Ploshchadki proveryaem na PERVOY kollekcii iz spiska. Spisok zadaetsya
REM v settings.bat, poetomu adres nikuda vpisyvat ne nado.
for /f "tokens=1 delims=," %%a in ("%TARGET_COLLECTIONS%") do set FIRST_COLL=%%a

echo [3/3] Kakie ploshchadki my voobshche vidim: --probe
py gift_sniper.py --probe %FIRST_COLL% --log-file "%LOG%"
echo       gotovo

echo.
echo ================================================================
echo  Gotovo. Otkryvayu otchet - otpravte ego v chat celikom.
echo ================================================================
start notepad "%LOG%"
