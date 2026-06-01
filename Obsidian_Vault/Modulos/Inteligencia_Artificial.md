---
tags: [modulo, tecnico, ai]
---

# 🤖 Inteligência Artificial (ai_module.py)

O sistema utiliza IA para análise preditiva e interação por linguagem natural.

## 📈 Previsão de Gastos
- **Algoritmo**: Regressão Linear.
- **Função**: Analisa o histórico de gastos na tabela `gastos` e projeta o valor necessário para o próximo mês.
- **Visualização**: Exibida no Dashboard principal.

## 💬 Chatbot Executável
O Chatbot não apenas responde, mas **age** sobre o [[Banco_de_Dados]].
- **NLP**: Processamento de texto para identificar intenções (intenção de compra, gasto, meta).
- **Ações**:
    - Adicionar produtos ao estoque.
    - Registrar saídas financeiras.
    - Criar metas de venda.

## 🔗 Conexões
- Depende de: [[Banco_de_Dados]]
- Interage com: [[EstokeAoCubo_MOC]]
