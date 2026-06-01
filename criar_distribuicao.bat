@echo off
REM Script para criar o pacote de distribuição do EstokeAoCubo

echo ===================================
echo Criando pacote de distribuição
echo ===================================
echo.

REM Criar pasta para distribuição
if not exist "dist_package" mkdir dist_package

REM Copiar arquivos necessários
copy "dist\EstokeAoCubo.exe" "dist_package\"
copy ".env.exemplo" "dist_package\"
copy "GUIA_DO_USUARIO.txt" "dist_package\"
copy "README.md" "dist_package\"

echo.
echo ===================================
echo Arquivos copiados para dist_package
echo ===================================
echo.
echo Conteúdo de dist_package:
echo - EstokeAoCubo.exe (programa principal)
echo - .env.exemplo (template de configuração)
echo - GUIA_INSTALACAO.md (instruções)
echo - README.md (informações)
echo.
echo Próximo passo: Comprima a pasta dist_package em um .zip
echo para enviar aos clientes!
echo.
pause
