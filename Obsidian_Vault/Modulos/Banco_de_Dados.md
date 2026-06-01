---
tags: [modulo, tecnico, database]
---

# 🗄️ Camada de Dados (database.py)

O coração do sistema reside no arquivo `database.py`. Ele gerencia toda a persistência usando **SQLite**.

## 📑 Principais Tabelas
- **`produtos`**: Nome, preço, estoque, validade, e o novo campo `lote_serie`.
- **`vendas`**: Cabeçalho da venda (cliente, total, forma_pagamento).
- **`venda_itens`**: Detalhamento dos produtos em cada venda.
- **`usuarios`**: Gestão de acessos com hash de senha SHA-256.
- **`contas_pagar`**: Registro de débitos com fornecedores.
- **`servicos`**: Armazena as [[Ordens_de_Servico|Ordens de Serviço]].

## 🛠️ Funções Críticas
- `autenticar_usuario(login, senha)`: Verifica credenciais.
- `registrar_venda(...)`: Processa a baixa atômica de estoque.
- `verificar_backup_diario()`: Garante a segurança dos dados.

## 🔗 Conexões
- Alimentado por: [[EstokeAoCubo_MOC]]
- Usado por: [[PDV_e_Vendas]], [[Inteligencia_Artificial]]
