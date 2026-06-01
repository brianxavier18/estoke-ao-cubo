@echo off
echo ========================================================
echo   1. Limpando e preparando diretorios de build locais
echo ========================================================
if exist "build_temp" rmdir /s /q "build_temp"
if exist "dist" rmdir /s /q "dist"

echo.
echo ========================================================
echo   2. Iniciando compilacao do executavel com PyInstaller...
echo ========================================================

:: Habilita a resolucao dinamica do path do PyZbar
setlocal enabledelayedexpansion
for /f "delims=" %%I in ('py -c "import pyzbar, os; print(os.path.dirname(pyzbar.__file__))"') do set "PYZBAR_DIR=%%I"

:: Executa a compilacao robusta usando caminhos absolutos baseados em %%cd%% para evitar erros de especificação de caminhos relativos ao specpath
py -m PyInstaller ^
  --clean ^
  --noconfirm ^
  --onefile ^
  --windowed ^
  --paths="." ^
  --workpath="%cd%\build_temp\build" ^
  --distpath="%cd%\dist" ^
  --specpath="%cd%\build_temp" ^
  --name "EstokeAoCubo" ^
  --icon="%cd%\icon\icon-cubo.ico" ^
  --add-data "%cd%\icon;icon" ^
  --add-data "%cd%\dist_package\GUIA_DO_USUARIO.txt;." ^
  --add-data "%cd%\dist_package\PLANILHA_PADRAO_ITENS.csv;." ^
  --add-binary "!PYZBAR_DIR!\*.dll;." ^
  --add-binary "!PYZBAR_DIR!\*.dll;pyzbar" ^
  --collect-all customtkinter ^
  --collect-all google.generativeai ^
  --collect-all escpos ^
  --collect-all pdfplumber ^
  --collect-all pyzbar ^
  --collect-all qrcode ^
  --collect-all barcode ^
  --collect-all dotenv ^
  --collect-all pywin32 ^
  --collect-all requests ^
  --collect-all pandas ^
  --collect-all numpy ^
  --collect-all scikit-learn ^
  --collect-all matplotlib ^
  --collect-all cryptography ^
  --collect-all PIL ^
  --hidden-import=google.generativeai ^
  --hidden-import=dotenv ^
  --hidden-import=escpos ^
  --hidden-import=serial ^
  --hidden-import=win32print ^
  --hidden-import=win32ui ^
  --hidden-import=pdfplumber ^
  --hidden-import=flask ^
  --hidden-import=cv2 ^
  --hidden-import=pyzbar ^
  --hidden-import=barcode ^
  --hidden-import=qrcode ^
  --hidden-import=notifications ^
  --hidden-import=label_generator ^
  --hidden-import=barcode_scanner ^
  --hidden-import=api_server ^
  --hidden-import=ai_module ^
  --hidden-import=database ^
  --hidden-import=config ^
  --hidden-import=printer ^
  --hidden-import=services.venda_service ^
  --hidden-import=services.caixa_service ^
  --hidden-import=services.estoque_service ^
  --hidden-import=services.dreams_service ^
  --hidden-import=services.security ^
  --hidden-import=pandas ^
  --hidden-import=numpy ^
  --hidden-import=sklearn ^
  --hidden-import=cryptography ^
  --hidden-import=PIL ^
  --hidden-import=PIL._tkinter_finder ^
  --hidden-import=PIL._webp ^
  main.py

echo.
echo ========================================================
echo   3. Copiando executavel para o dist_package
echo ========================================================
if not exist "dist_package" mkdir dist_package
copy "dist\EstokeAoCubo.exe" "dist_package\EstokeAoCubo.exe" /y
if exist ".env.exemplo" copy ".env.exemplo" "dist_package\" /y
if exist "README.md" copy "README.md" "dist_package\" /y

echo.
echo ========================================================
echo   4. Compilando instalador Setup (.exe) com Inno Setup 6
echo ========================================================
"C:\Users\xavie\AppData\Local\Programs\Inno Setup 6\ISCC.exe" "EstokeSetup.iss"

echo.
echo ========================================================
echo   5. Compilacao Concluida com Sucesso!
echo ========================================================

:: Limpeza final de diretorios temporarios locais
if exist "build_temp" rmdir /s /q "build_temp"
