@echo off
REM Script para criar o executável EstokeAoCubo em formato de pasta (onedir)
REM Este formato é mais rápido para abrir e evita falsos positivos de antivírus

echo ===================================
echo Compilando EstokeAoCubo em pasta (onedir)
echo ===================================
echo.

echo Usando Python 3.14 via py launcher...
py -3.14 -m PyInstaller --clean --noconfirm ^
--onedir ^
--windowed ^
--name "EstokeAoCubo" ^
--icon="icon\icon-cubo.ico" ^
--add-data "icon;icon" ^
--add-data "dist_package\GUIA_DO_USUARIO.txt;." ^
--add-data "dist_package\PLANILHA_PADRAO_ITENS.csv;." ^
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
--collect-all PIL ^
--hidden-import=google.generativeai ^
--hidden-import=dotenv ^
--hidden-import=escpos ^
--hidden-import=serial ^
--hidden-import=win32print ^
--hidden-import=win32ui ^
--hidden-import=PIL._webp ^
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
--hidden-import=PIL ^
--hidden-import=requests ^
main.py

echo.
echo ===================================
echo Compilação concluída!
echo ===================================
echo.
