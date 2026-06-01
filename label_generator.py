"""
Módulo de geração de etiquetas com código de barras e QR Code.
"""
import os
import logging
import datetime

logger = logging.getLogger('EstokeAoCubo.label_generator')

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import barcode as python_barcode
    from barcode.writer import ImageWriter
    BARCODE_LIB = True
except ImportError:
    BARCODE_LIB = False

try:
    import qrcode
    QRCODE_LIB = True
except ImportError:
    QRCODE_LIB = False


def etiquetas_disponiveis():
    """Retorna True se dependências de etiqueta estão instaladas."""
    return PIL_AVAILABLE


def gerar_etiqueta(produto, config_etiqueta=None, output_dir=None):
    """
    Gera uma etiqueta para um produto.
    
    Args:
        produto: dict com 'nome', 'preco', 'codigo_barras', 'categoria' (opcional)
        config_etiqueta: dict com configurações (largura, altura, mostrar_preco, etc.)
        output_dir: pasta de saída (default: Documentos/EstokeAoCubo_Etiquetas)
    
    Returns:
        caminho do arquivo gerado ou None
    """
    if not PIL_AVAILABLE:
        return None

    cfg = config_etiqueta or {}
    largura = cfg.get('largura', 400)
    altura = cfg.get('altura', 250)
    mostrar_preco = cfg.get('mostrar_preco', True)
    mostrar_codigo = cfg.get('mostrar_codigo', True)
    mostrar_qrcode = cfg.get('mostrar_qrcode', False)
    empresa_nome = cfg.get('empresa_nome', '')

    if output_dir is None:
        output_dir = os.path.join(os.path.expanduser("~"), "Documents", "EstokeAoCubo_Etiquetas")
    os.makedirs(output_dir, exist_ok=True)

    nome = produto.get('nome', 'Produto')
    preco = produto.get('preco', 0)
    codigo = produto.get('codigo_barras', '')
    categoria = produto.get('categoria', '')

    # Criar imagem base
    img = Image.new('RGB', (largura, altura), 'white')
    draw = ImageDraw.Draw(img)

    # Carregar fontes
    try:
        font_nome = ImageFont.truetype("arial.ttf", 18)
        font_preco = ImageFont.truetype("arialbd.ttf", 24)
        font_small = ImageFont.truetype("arial.ttf", 11)
        font_empresa = ImageFont.truetype("arial.ttf", 10)
    except Exception:
        font_nome = ImageFont.load_default()
        font_preco = font_nome
        font_small = font_nome
        font_empresa = font_nome

    y_pos = 8

    # Nome da empresa (topo)
    if empresa_nome:
        bbox = draw.textbbox((0, 0), empresa_nome, font=font_empresa)
        tw = bbox[2] - bbox[0]
        draw.text(((largura - tw) // 2, y_pos), empresa_nome, fill='gray', font=font_empresa)
        y_pos += 16

    # Nome do produto
    nome_display = nome[:30]
    bbox = draw.textbbox((0, 0), nome_display, font=font_nome)
    tw = bbox[2] - bbox[0]
    draw.text(((largura - tw) // 2, y_pos), nome_display, fill='black', font=font_nome)
    y_pos += 28

    # Categoria
    if categoria:
        bbox = draw.textbbox((0, 0), categoria, font=font_small)
        tw = bbox[2] - bbox[0]
        draw.text(((largura - tw) // 2, y_pos), categoria, fill='gray', font=font_small)
        y_pos += 18

    # Preço
    if mostrar_preco:
        preco_text = f"R$ {preco:.2f}"
        bbox = draw.textbbox((0, 0), preco_text, font=font_preco)
        tw = bbox[2] - bbox[0]
        draw.text(((largura - tw) // 2, y_pos), preco_text, fill='black', font=font_preco)
        y_pos += 35

    # Código de barras
    if mostrar_codigo and codigo and BARCODE_LIB:
        try:
            barcode_class = python_barcode.get_barcode_class('ean13')
            code_padded = codigo.ljust(12, '0')[:12]
            bc = barcode_class(code_padded, writer=ImageWriter())
            bc_img = bc.render(writer_options={
                'module_width': 0.3,
                'module_height': 8,
                'font_size': 8,
                'text_distance': 2,
                'quiet_zone': 2,
            })
            # Redimensionar código de barras
            bc_w = min(largura - 40, bc_img.width)
            bc_h = min(altura - y_pos - 10, bc_img.height)
            bc_img = bc_img.resize((bc_w, bc_h), Image.LANCZOS)
            x_bc = (largura - bc_w) // 2
            img.paste(bc_img, (x_bc, y_pos))
        except Exception as e:
            logger.debug("Erro ao gerar código de barras: %s", e)
            # Fallback: exibir código como texto
            if codigo:
                bbox = draw.textbbox((0, 0), codigo, font=font_small)
                tw = bbox[2] - bbox[0]
                draw.text(((largura - tw) // 2, y_pos), codigo, fill='black', font=font_small)
    elif mostrar_codigo and codigo:
        bbox = draw.textbbox((0, 0), codigo, font=font_small)
        tw = bbox[2] - bbox[0]
        draw.text(((largura - tw) // 2, y_pos), codigo, fill='black', font=font_small)

    # QR Code (se solicitado)
    if mostrar_qrcode and QRCODE_LIB:
        try:
            qr_data = codigo or nome
            qr = qrcode.make(qr_data)
            qr = qr.resize((60, 60))
            img.paste(qr, (largura - 70, 5))
        except Exception as e:
            logger.debug("Erro QR Code: %s", e)

    # Borda
    draw.rectangle([0, 0, largura - 1, altura - 1], outline='#CCCCCC', width=1)

    # Salvar
    safe_name = "".join(c for c in nome if c.isalnum() or c in "_ -")[:30]
    ts = datetime.datetime.now().strftime("%H%M%S")
    filename = f"Etiqueta_{safe_name}_{ts}.png"
    filepath = os.path.join(output_dir, filename)
    img.save(filepath, "PNG")
    return filepath


def gerar_etiquetas_lote(produtos, config_etiqueta=None, colunas=3, output_dir=None):
    """
    Gera uma folha com múltiplas etiquetas dispostas em grade.
    
    Args:
        produtos: lista de dicts com dados de produto
        config_etiqueta: configurações de cada etiqueta
        colunas: número de colunas na folha
        output_dir: pasta de saída
    
    Returns:
        caminho do arquivo da folha ou None
    """
    if not PIL_AVAILABLE or not produtos:
        return None

    cfg = config_etiqueta or {}
    larg_etiq = cfg.get('largura', 400)
    alt_etiq = cfg.get('altura', 250)
    margem = 10

    linhas = (len(produtos) + colunas - 1) // colunas
    folha_w = colunas * (larg_etiq + margem) + margem
    folha_h = linhas * (alt_etiq + margem) + margem

    folha = Image.new('RGB', (folha_w, folha_h), 'white')

    for i, prod in enumerate(produtos):
        etiq_path = gerar_etiqueta(prod, cfg, output_dir)
        if etiq_path and os.path.exists(etiq_path):
            etiq_img = Image.open(etiq_path)
            col = i % colunas
            row = i // colunas
            x = margem + col * (larg_etiq + margem)
            y = margem + row * (alt_etiq + margem)
            folha.paste(etiq_img, (x, y))
            os.remove(etiq_path)  # Remove individual

    if output_dir is None:
        output_dir = os.path.join(os.path.expanduser("~"), "Documents", "EstokeAoCubo_Etiquetas")
    os.makedirs(output_dir, exist_ok=True)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(output_dir, f"Etiquetas_Lote_{ts}.png")
    folha.save(filepath, "PNG")
    return filepath
