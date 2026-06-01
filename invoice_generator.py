import os
import datetime
import subprocess
import sys
import logging
import sqlite3
import re

logger = logging.getLogger('EstokeAoCubo.invoice')

# Importação do gerador de Pix e caminhos do banco de dados
try:
    from database import get_app_data_path
except ImportError:
    def get_app_data_path():
        if os.name == 'nt':
            return os.path.join(os.getenv('LOCALAPPDATA'), 'EstokeAoCubo')
        return os.path.expanduser('~/.local/share/EstokeAoCubo')

try:
    from printer import gerar_payload_pix
except ImportError:
    def gerar_payload_pix(chave, valor, nome, cidade, txid="***"):
        return ""


# ============================================================
# INICIALIZAÇÃO DE TABELAS E COLUNAS PIX (AUTO-HEALING)
# ============================================================

def _garantir_migracao_pix():
    """Garante que as colunas de Pix existam na tabela config_nota no SQLite."""
    # 1 linha para explicar o motivo: Auto-healing garante que o banco de dados se adapte sem intervenção manual do usuário.
    try:
        db_path = os.path.join(get_app_data_path(), "estoque.db")
        if os.path.exists(db_path):
            with sqlite3.connect(db_path, timeout=5.0) as conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA table_info(config_nota)")
                colunas = [c[1] for c in cursor.fetchall()]
                if 'pix_key' not in colunas:
                    cursor.execute("ALTER TABLE config_nota ADD COLUMN pix_key TEXT DEFAULT ''")
                if 'pix_recebedor' not in colunas:
                    cursor.execute("ALTER TABLE config_nota ADD COLUMN pix_recebedor TEXT DEFAULT ''")
                if 'pix_cidade' not in colunas:
                    cursor.execute("ALTER TABLE config_nota ADD COLUMN pix_cidade TEXT DEFAULT ''")
                conn.commit()
    except Exception as e:
        logger.debug("Falha na migração automática de colunas Pix: %s", e)

# Rodar migração ao carregar o módulo
_garantir_migracao_pix()


# ============================================================
# CONSULTA DE PROGRAMA DE PONTOS (FIDELIDADE)
# ============================================================

def _obter_pontos_fidelidade(cliente_nome: str, total_venda: float) -> dict:
    """Consulta transacional no SQLite para obter pontuação do cliente e cálculo da venda atual."""
    # 1 linha para explicar o motivo: Consulta em tempo real garante a fidelidade de pontos impressa idêntica ao banco de dados.
    if not cliente_nome or cliente_nome == "Consumidor Final":
        return None
    
    try:
        db_path = os.path.join(get_app_data_path(), "estoque.db")
        if not os.path.exists(db_path):
            return None

        with sqlite3.connect(db_path, timeout=5.0) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM clientes WHERE nome = ?", (cliente_nome,))
            c_row = cursor.fetchone()
            if c_row:
                cliente_id = c_row[0]
                cursor.execute("SELECT pontos FROM fidelidade WHERE cliente_id = ?", (cliente_id,))
                f_row = cursor.fetchone()
                saldo_atual = f_row[0] if f_row else 0
                
                # Regra de negócio: 1 ponto a cada R$10,00 gastos
                pontos_ganhos = int(total_venda // 10)
                saldo_final = saldo_atual + pontos_ganhos
                return {
                    "ganhos": pontos_ganhos,
                    "saldo": saldo_final
                }
    except Exception as e:
        logger.warning("Falha ao consultar dados de fidelidade no SQLite: %s", e)
    return None


# ============================================================
# GERADOR DE CUPOM DINÂMICO
# ============================================================

def _gerar_cupom_desconto_dinamico(total_venda: float) -> str:
    """Retorna um cupom de desconto promocional se a venda atingir valor mínimo."""
    # 1 linha para explicar o motivo: Técnica de marketing integrado para estimular o retorno e fidelizar o cliente no PDV.
    if total_venda >= 150.0:
        return "VOLTEJA15"  # R$ 15 reais de desconto na próxima compra
    elif total_venda >= 70.0:
        return "CUPOM10"    # 10% de desconto
    return None


# ============================================================
# MÓDULOS DE GERAÇÃO DE COMPROVANTES
# ============================================================

def gerar_nota_txt(itens, cliente, total_venda, id_venda=None, desconto=0.0, abrir_arquivo=True, config_nota=None):
    """
    Gera recibo de venda térmico (formato vertical responsivo 58mm/80mm) com PIX e Fidelidade.
    """
    # 1 linha para explicar o motivo: Carregamos as propriedades da empresa configuradas ou aplicamos defaults elegantes.
    if config_nota:
        empresa_nome = config_nota.get('empresa_nome', 'Estoke Ao Cubo') or 'Estoke Ao Cubo'
        empresa_email = config_nota.get('empresa_email', '')
        empresa_telefone = config_nota.get('empresa_telefone', '')
        empresa_endereco = config_nota.get('empresa_endereco', '')
        empresa_cnpj = config_nota.get('empresa_cnpj', '')
        mensagem_rodape = config_nota.get('mensagem_rodape', 'Obrigado pela preferência!') or 'Obrigado pela preferência!'
        
        # Novas variáveis de Pix do estabelecimento
        pix_key = config_nota.get('pix_key', '')
        pix_recebedor = config_nota.get('pix_recebedor', empresa_nome)
        pix_cidade = config_nota.get('pix_cidade', 'SAO PAULO')
    else:
        empresa_nome = "Estoke Ao Cubo"
        empresa_email = ""
        empresa_telefone = ""
        empresa_endereco = ""
        empresa_cnpj = ""
        mensagem_rodape = "Obrigado pela preferência!"
        pix_key = ""
        pix_recebedor = "Estoke Ao Cubo"
        pix_cidade = "SAO PAULO"

    if not id_venda:
        id_venda = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        
    data_emissao = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    cliente_nome = cliente.get('nome', 'Consumidor Final') if cliente else 'Consumidor Final'
    cliente_contato = cliente.get('contato', '') if cliente else ''
    # 1 linha para explicar o motivo: Largura dinâmica baseada na impressora configurada (48 colunas para 80mm/i8, 32 para 58mm).
    try:
        from config import get_printer_config
        printer_cfg = get_printer_config()
        # Se page_width estiver definido nas configurações da impressora, usamos.
        # Caso contrário, se for escpos ou windows, o padrão para Elgin i8 (80mm) é 48 colunas, senão 32.
        width = int(printer_cfg.get('settings', {}).get('page_width', 48 if printer_cfg.get('type') in ('escpos', 'windows') else 32))
    except Exception:
        width = 32  
        
    line = "-" * width
    double_line = "=" * width
    
    content = []
    content.append(empresa_nome.upper().center(width))
    if empresa_cnpj:
        content.append(f"CNPJ: {empresa_cnpj}".center(width))
    if empresa_endereco:
        content.append(empresa_endereco.center(width))
    if empresa_telefone:
        content.append(f"Tel: {empresa_telefone}".center(width))
    if empresa_email:
        content.append(empresa_email.center(width))
    content.append(line)
    content.append(f"RECIBO #{id_venda}".center(width))
    content.append(f"Data: {data_emissao}".center(width))
    content.append(line)
    content.append(f"Cliente: {cliente_nome}")
    if cliente_contato:
        content.append(f"Contato: {cliente_contato}")
    content.append(line)
    content.append("ITENS ADQUIRIDOS".center(width))
    
    # Renderização formatada dos itens da compra
    for item in itens:
        nome = item['nome'][:28]
        qtd = item['qtd']
        preco = item['preco']
        subtotal = qtd * preco
        desc_item = item.get('desconto_item', 0.0)
        
        content.append(f" {nome}")
        content.append(f"  {qtd}x R${preco:.2f} = R${subtotal:.2f}")
        if desc_item > 0:
            content.append(f"  Desc: -R${desc_item:.2f}")
    
    content.append(line)
    
    # Subtotal bruto dos itens
    subtotal_itens = sum(item['qtd'] * item['preco'] for item in itens)
    
    if desconto > 0:
        content.append(f" Subtotal:  R${subtotal_itens:.2f}")
        content.append(f" Desconto: -R${desconto:.2f}")
        content.append(line)
    
    content.append(f" TOTAL: R${total_venda:.2f}".center(width))
    content.append(double_line)

    # Inclusão dos pontos de fidelidade
    fidelidade = _obter_pontos_fidelidade(cliente_nome, total_venda)
    if fidelidade:
        content.append("PROGRAMA DE FIDELIDADE".center(width))
        content.append(f" Pontos Ganhos: {fidelidade['ganhos']}")
        content.append(f" Saldo Atual: {fidelidade['saldo']} pts")
        content.append(double_line)

    # Inclusão de cupom promocional no rodapé da nota
    cupom = _gerar_cupom_desconto_dinamico(total_venda)
    if cupom:
        content.append("PARABENS! VOCE GANHOU UM CUPOM".center(width))
        content.append(f" Use: {cupom}".center(width))
        content.append(" na sua proxima compra!".center(width))
        content.append(line)

    content.append(mensagem_rodape.center(width))
    
    # Adicionar metadados do PIX caso a chave esteja configurada
    # 1 linha para explicar o motivo: O metadado é interpretado pelo driver de forma transparente sem poluir o visual do arquivo.
    pix_payload = ""
    if pix_key:
        pix_payload = gerar_payload_pix(pix_key, total_venda, pix_recebedor, pix_cidade, id_venda)
        if pix_payload:
            content.append(f"[PIX_PAYLOAD: {pix_payload}]")
    
    text_content = "\n".join(content)
    
    # Versão WhatsApp do Recibo
    wpp_content = _gerar_nota_whatsapp(
        itens, cliente_nome, cliente_contato, total_venda, id_venda,
        data_emissao, desconto, subtotal_itens,
        empresa_nome, empresa_email, empresa_telefone, empresa_endereco,
        mensagem_rodape, fidelidade, cupom, pix_payload
    )
    
    # Gravação do arquivo físico local de auditoria/reimpressão
    try:
        docs_path = os.path.join(os.path.expanduser("~"), "Documents", "EstokeAoCubo_Recibos")
        os.makedirs(docs_path, exist_ok=True)
        
        filename = f"Recibo_{id_venda}.txt"
        file_path = os.path.join(docs_path, filename)
        
        with open(file_path, "w", encoding="utf-8") as f:
            # Gravamos sem o metadado do payload no arquivo físico nítido
            f.write(re.sub(r'\[PIX_PAYLOAD:\s*[^\]]+\s*\]', '', text_content).strip())
            
        if abrir_arquivo:
            try:
                if os.name == 'nt':
                    os.startfile(file_path)
                else:
                    opener = 'open' if sys.platform == 'darwin' else 'xdg-open'
                    subprocess.call([opener, file_path])
            except Exception:
                pass

        return file_path, text_content, wpp_content
    
    except Exception as e:
        logger.error("Erro ao gerar arquivo físico de nota: %s", e)
        return None, None, None


def _gerar_nota_whatsapp(itens, cliente_nome, cliente_contato, total_venda, id_venda,
                         data_emissao, desconto, subtotal_itens,
                         empresa_nome, empresa_email, empresa_telefone,
                         empresa_endereco, mensagem_rodape, fidelidade=None, cupom=None, pix_payload=None):
    """Gera texto de recibo com excelente formatação de fontes markdown para o WhatsApp."""
    partes = data_emissao.split(' ')
    data_str = partes[0] if len(partes) >= 1 else data_emissao
    hora_str = partes[1] if len(partes) >= 2 else ''
    hora_display = f" às {hora_str}" if hora_str else ''

    lines = []
    lines.append(f"🧾 *COMPROVANTE DE COMPRA | {empresa_nome.upper()}*")
    lines.append("Olá! Segue o detalhamento de seu pedido:")
    lines.append("")
    lines.append(f"Pedido: *#{id_venda}*")
    lines.append(f"Data: {data_str}{hora_display}")
    lines.append(f"Cliente: *{cliente_nome}*")
    lines.append("")

    if len(itens) == 1:
        lines.append('🛒 *ITEM ADQUIRIDO:*')
    else:
        lines.append('🛒 *ITENS ADQUIRIDOS:*')

    for item in itens:
        nome = item['nome']
        qtd = item['qtd']
        preco = item['preco']
        subtotal = qtd * preco
        desc_item = item.get('desconto_item', 0.0)
        lines.append(f"• {qtd}x *{nome}*")
        if desc_item > 0:
            lines.append(f"  Preço: R$ {subtotal:.2f} _(desc: -R$ {desc_item:.2f})_")
        else:
            lines.append(f"  Preço: R$ {subtotal:.2f}")

    lines.append("")

    if desconto > 0:
        lines.append(f"Subtotal bruto: R$ {subtotal_itens:.2f}")
        lines.append(f"Desconto concedido: *-R$ {desconto:.2f}*")

    lines.append(f"💰 *VALOR TOTAL PAGO: R$ {total_venda:.2f}*")
    lines.append("")

    # Fidelidade
    if fidelidade:
        lines.append("⭐ *FIDELIDADE ESTOKE:*")
        lines.append(f" Pontos Ganhos nesta Compra: *{fidelidade['ganhos']}*")
        lines.append(f" Saldo Total Acumulado: *{fidelidade['saldo']} pontos*")
        lines.append("")

    # Cupom de desconto
    if cupom:
        lines.append("🎁 *PRESENTE PARA VOCÊ:*")
        lines.append(f" Ganhe desconto na próxima compra com o cupom: *{cupom}*")
        lines.append("")

    # Link do Pix para cópia
    if pix_payload:
        lines.append("⚡ *PIX COPIA E COLA:*")
        lines.append(f"`{pix_payload}`")
        lines.append("")

    if empresa_endereco:
        lines.append(f"📍 {empresa_endereco}")
    if empresa_telefone:
        lines.append(f"📞 Contato/Suporte: {empresa_telefone}")
    if empresa_email:
        lines.append(f"✉️ E-mail: {empresa_email}")

    lines.append("")
    lines.append(f"_{mensagem_rodape}_")

    return '\n'.join(lines)


def gerar_nota_servico(descricao, valor, cliente, id_servico=None, forma_pagamento='Dinheiro',
                       observacao='', abrir_arquivo=True, config_nota=None):
    """
    Gera recibo de nota de serviço com suporte a PIX, Fidelidade e QR Code.
    """
    if config_nota:
        empresa_nome = config_nota.get('empresa_nome', 'Estoke Ao Cubo') or 'Estoke Ao Cubo'
        empresa_email = config_nota.get('empresa_email', '')
        empresa_telefone = config_nota.get('empresa_telefone', '')
        empresa_endereco = config_nota.get('empresa_endereco', '')
        empresa_cnpj = config_nota.get('empresa_cnpj', '')
        mensagem_rodape = config_nota.get('mensagem_rodape', 'Obrigado pela preferência!') or 'Obrigado pela preferência!'
        
        pix_key = config_nota.get('pix_key', '')
        pix_recebedor = config_nota.get('pix_recebedor', empresa_nome)
        pix_cidade = config_nota.get('pix_cidade', 'SAO PAULO')
    else:
        empresa_nome = "Estoke Ao Cubo"
        empresa_email = ""
        empresa_telefone = ""
        empresa_endereco = ""
        empresa_cnpj = ""
        mensagem_rodape = "Obrigado pela preferência!"
        pix_key = ""
        pix_recebedor = "Estoke Ao Cubo"
        pix_cidade = "SAO PAULO"

    if not id_servico:
        id_servico = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

    data_emissao = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    cliente_nome = cliente.get('nome', 'Consumidor Final') if cliente else 'Consumidor Final'
    cliente_contato = cliente.get('contato', '') if cliente else ''

    # 1 linha para explicar o motivo: Largura dinâmica baseada na impressora configurada (48 colunas para 80mm/i8, 32 para 58mm).
    try:
        from config import get_printer_config
        printer_cfg = get_printer_config()
        # Se page_width estiver definido nas configurações da impressora, usamos.
        # Caso contrário, se for escpos ou windows, o padrão para Elgin i8 (80mm) é 48 colunas, senão 32.
        width = int(printer_cfg.get('settings', {}).get('page_width', 48 if printer_cfg.get('type') in ('escpos', 'windows') else 32))
    except Exception:
        width = 32  
        
    line = "-" * width
    double_line = "=" * width

    content = []
    content.append(empresa_nome.upper().center(width))
    if empresa_cnpj:
        content.append(f"CNPJ: {empresa_cnpj}".center(width))
    if empresa_endereco:
        content.append(empresa_endereco.center(width))
    if empresa_telefone:
        content.append(f"Tel: {empresa_telefone}".center(width))
    if empresa_email:
        content.append(empresa_email.center(width))
    content.append(line)
    content.append("COMPROVANTE DE SERVIÇO".center(width))
    content.append(f"Nº {id_servico}".center(width))
    content.append(f"Data: {data_emissao}".center(width))
    content.append(line)
    content.append(f"Cliente: {cliente_nome}")
    if cliente_contato:
        content.append(f"Contato: {cliente_contato}")
    content.append(line)
    content.append("SERVIÇO PRESTADO".center(width))
    
    desc_lines = [descricao[i:i+30] for i in range(0, len(descricao), 30)]
    for dl in desc_lines:
        content.append(f" {dl}")
    content.append("")
    if observacao:
        content.append(f" Obs: {observacao[:28]}")
        if len(observacao) > 28:
            content.append(f"      {observacao[28:56]}")
    content.append(line)
    content.append(f" Pgto: {forma_pagamento}")
    content.append(f" TOTAL: R${valor:.2f}".center(width))
    content.append(double_line)

    # Inclusão dos pontos de fidelidade
    fidelidade = _obter_pontos_fidelidade(cliente_nome, valor)
    if fidelidade:
        content.append("PROGRAMA DE FIDELIDADE".center(width))
        content.append(f" Pontos Ganhos: {fidelidade['ganhos']}")
        content.append(f" Saldo Atual: {fidelidade['saldo']} pts")
        content.append(double_line)

    content.append(mensagem_rodape.center(width))

    # Anexar metadados Pix de nota de serviço
    pix_payload = ""
    if pix_key:
        pix_payload = gerar_payload_pix(pix_key, valor, pix_recebedor, pix_cidade, id_servico)
        if pix_payload:
            content.append(f"[PIX_PAYLOAD: {pix_payload}]")

    text_content = "\n".join(content)

    # Versão WhatsApp da Nota de Serviço
    partes = data_emissao.split(' ')
    data_str = partes[0] if len(partes) >= 1 else data_emissao
    hora_str = partes[1] if len(partes) >= 2 else ''
    hora_display = f" às {hora_str}" if hora_str else ''

    wpp = []
    wpp.append(f"🛠️ *NOTA DE SERVIÇO | {empresa_nome.upper()}*")
    wpp.append("Olá! Segue o detalhamento de seu serviço:")
    wpp.append("")
    wpp.append(f"Código OS: *#{id_servico}*")
    wpp.append(f"Data: {data_str}{hora_display}")
    wpp.append(f"Cliente: *{cliente_nome}*")
    wpp.append("")
    wpp.append("*SERVIÇO PRESTADO:*")
    wpp.append(f"_{descricao}_")
    if observacao:
        wpp.append(f"Observações: `{observacao}`")
    wpp.append("")
    wpp.append(f"Forma de Pagamento: {forma_pagamento}")
    wpp.append(f"💰 *VALOR TOTAL: R$ {valor:.2f}*")
    wpp.append("")

    if fidelidade:
        wpp.append("⭐ *FIDELIDADE ESTOKE:*")
        wpp.append(f" Pontos Ganhos: *{fidelidade['ganhos']}*")
        wpp.append(f" Saldo Atualizado: *{fidelidade['saldo']} pontos*")
        wpp.append("")

    if pix_payload:
        wpp.append("⚡ *PIX COPIA E COLA:*")
        wpp.append(f"`{pix_payload}`")
        wpp.append("")

    if empresa_telefone:
        wpp.append(f"📞 Telefone/Suporte: {empresa_telefone}")
    if empresa_email:
        wpp.append(f"✉️ E-mail: {empresa_email}")
    wpp.append("")
    wpp.append(f"_{mensagem_rodape}_")

    wpp_content = '\n'.join(wpp)

    # Gravação física local
    try:
        docs_path = os.path.join(os.path.expanduser("~"), "Documents", "EstokeAoCubo_Recibos")
        os.makedirs(docs_path, exist_ok=True)

        filename = f"Servico_{id_servico}.txt"
        file_path = os.path.join(docs_path, filename)

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(re.sub(r'\[PIX_PAYLOAD:\s*[^\]]+\s*\]', '', text_content).strip())

        if abrir_arquivo:
            try:
                if os.name == 'nt':
                    os.startfile(file_path)
                else:
                    opener = 'open' if sys.platform == 'darwin' else 'xdg-open'
                    subprocess.call([opener, file_path])
            except Exception:
                pass

        return file_path, text_content, wpp_content

    except Exception as e:
        logger.error("Erro ao gravar nota de serviço física: %s", e)
        return None, None, None
