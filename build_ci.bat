@echo off
REM Script para criar o executável EstokeAoCubo
REM Este script compila o programa em um .exe único

echo ===================================
echo Compilando EstokeAoCubo em .exe
echo ===================================
echo.

REM Criar o executável
if exist ".venv\Scripts\python.exe" (
    echo Usando Python do ambiente virtual...
    ".venv\Scripts\python.exe" -m PyInstaller --noconfirm ^
    --onefile ^
    --windowed ^
    --name "EstokeAoCubo" ^
    --icon="icon\icon-cubo.ico" ^
    --add-data "icon;icon" ^
    --add-data "dist_package\GUIA_DO_USUARIO.txt;." ^
    --collect-all customtkinter ^
    --collect-all google.generativeai ^
    --collect-all sklearn ^
    --collect-all matplotlib ^
    --collect-all pandas ^
    --collect-all numpy ^
    --collect-all escpos ^
    --collect-all pdfplumber ^
    --collect-all flask ^
    --add-binary ".\.venv\Lib\site-packages\pyzbar\libiconv.dll;." ^
    --add-binary ".\.venv\Lib\site-packages\pyzbar\libzbar-64.dll;." ^
    --add-binary ".\.venv\Lib\site-packages\pyzbar\msvcr120.dll;." ^
    --add-binary ".\.venv\Lib\site-packages\pyzbar\msvcp120.dll;." ^
    --add-binary ".\.venv\Lib\site-packages\pyzbar\libiconv.dll;pyzbar" ^
    --add-binary ".\.venv\Lib\site-packages\pyzbar\libzbar-64.dll;pyzbar" ^
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
    main.py
) else (
    echo Usando Python do sistema...
    python -m PyInstaller --noconfirm ^
    --onefile ^
    --windowed ^
    --name "EstokeAoCubo" ^
    --icon="icon\icon-cubo.ico" ^
    --add-data "icon;icon" ^
    --add-data "dist_package\GUIA_DO_USUARIO.txt;." ^
    --collect-all customtkinter ^
    --collect-all google.generativeai ^
    --collect-all sklearn ^
    --collect-all matplotlib ^
    --collect-all pandas ^
    --collect-all numpy ^
    --collect-all escpos ^
    --collect-all pdfplumber ^
    --collect-all flask ^
    --add-binary ".\.venv\Lib\site-packages\pyzbar\libiconv.dll;." ^
    --add-binary ".\.venv\Lib\site-packages\pyzbar\libzbar-64.dll;." ^
    --add-binary ".\.venv\Lib\site-packages\pyzbar\msvcr120.dll;." ^
    --add-binary ".\.venv\Lib\site-packages\pyzbar\msvcp120.dll;." ^
    --add-binary ".\.venv\Lib\site-packages\pyzbar\libiconv.dll;pyzbar" ^
    --add-binary ".\.venv\Lib\site-packages\pyzbar\libzbar-64.dll;pyzbar" ^
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
    main.py
)

echo.
echo ===================================
echo Compilação concluída!
echo ===================================
echo.
echo O arquivo EstokeAoCubo.exe foi criado em:
echo %cd%\dist\EstokeAoCubo.exe
echo.
if exist "dist\GUIA_DO_USUARIO.txt" (
    echo Guia do usuário copiado para a pasta dist.
) else (
    copy "dist_package\GUIA_DO_USUARIO.txt" "dist\" >nul
    echo Guia do usuário adicionado manualmente à dist.
)
echo.
