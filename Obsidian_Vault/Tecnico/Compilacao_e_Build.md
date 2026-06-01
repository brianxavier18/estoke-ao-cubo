---
tags: [tecnico, build, deployment]
---

# 🏗️ Compilação e Build

Como transformar o código Python em um arquivo `.exe` distribuível.

## 📦 Ferramenta: PyInstaller
O processo é automatizado pelo script `build.bat`, mas aqui estão os detalhes:

### Comando Principal
```bash
pyinstaller --noconfirm --onefile --windowed --name "EstokeAoCubo" \
    --add-data "icon;icon" \
    --add-data "dist_package/GUIA_DO_USUARIO.txt;." \
    --collect-all customtkinter main.py
```

### Arquivos Gerados (Pasta `dist`)
- `EstokeAoCubo.exe`: O aplicativo principal.
- `GUIA_DO_USUARIO.txt`: Manual para o cliente final.
- `PADRÃO DE IMPORTAÇÃO.csv`: Modelo para carga de dados.

## 🛠️ Instalador (Inno Setup)
- O arquivo `EstokeSetup.iss` é usado para criar o instalador profissional que o cliente final utiliza para instalar o software no Windows.

---
- Voltar para: [[EstokeAoCubo_MOC]]
