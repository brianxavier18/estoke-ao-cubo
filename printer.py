import os
import datetime
import subprocess
import sys
import logging
import urllib.parse
import queue
import threading
import time

from config import get_printer_config

logger = logging.getLogger('EstokeAoCubo.printer')

# Importação de bibliotecas opcionais com fallbacks seguros para multiplataforma
try:
    from escpos.printer import Usb, Network, Serial
except ImportError:
    Usb = None
    Network = None
    Serial = None

try:
    import win32print
    import win32ui
except ImportError:
    win32print = None
    win32ui = None

try:
    import qrcode
except ImportError:
    qrcode = None


# ============================================================
# GERADOR DE PAYLOAD PIX ESTÁTICO (PADRÃO BR CODE / BACEN EMV)
# ============================================================

def calcular_crc16_ccitt(payload: str) -> str:
    """Calcula o CRC-16/CCITT-FALSE conforme especificação do Banco Central do Brasil."""
    # O polinômio padrão do CCITT é 0x1021, valor inicial 0xFFFF
    crc = 0xFFFF
    for byte in payload.encode('utf-8'):
        crc ^= (byte << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return f"{crc:04X}"


def gerar_payload_pix(chave: str, valor: float, nome_recebedor: str, cidade_recebedor: str, txid: str = "***") -> str:
    """Gera a string de payload de pagamento Pix estático no formato BR Code."""
    # 1 linha para explicar o motivo: Usamos o formato EMV BR Code nativo para compatibilidade com qualquer app bancário brasileiro.
    def formatar_campo(id_campo: str, valor_campo: str) -> str:
        return f"{id_campo}{len(valor_campo):02d}{valor_campo}"

    # Limpeza e limite de tamanho dos campos strings conforme regras do BACEN
    nome = nome_recebedor[:25].upper()
    cidade = cidade_recebedor[:15].upper()
    chave_limpa = chave.strip()

    # Montagem dos subcampos da Merchant Account Information (ID 26)
    gui = formatar_campo("00", "br.gov.bcb.pix")
    chave_fmt = formatar_campo("01", chave_limpa)
    merchant_account = formatar_campo("26", gui + chave_fmt)

    # Campos obrigatórios e opcionais
    payload_format = formatar_campo("00", "01")
    merchant_category = formatar_campo("52", "0000")
    currency = formatar_campo("53", "986")  # 986 = Real Brasileiro (ISO 4217)
    
    # Campo valor é opcional, se 0.0 o cliente insere o valor manualmente no app do banco
    amount_str = f"{valor:.2f}"
    amount = formatar_campo("54", amount_str) if valor > 0 else ""

    country = formatar_campo("58", "BR")
    name = formatar_campo("59", nome)
    city = formatar_campo("60", cidade)
    
    # Detalhe do ID da transação (ID 62)
    reference = formatar_campo("05", txid)
    additional_data = formatar_campo("62", reference)

    # Concatenação do payload base para cálculo do CRC
    payload_base = payload_format + merchant_account + merchant_category + currency + amount + country + name + city + additional_data
    payload_base += "6304"  # ID do CRC16 + Tamanho do CRC16
    
    crc = calcular_crc16_ccitt(payload_base)
    return payload_base + crc


# ============================================================
# AUDITORIA E LOG ROTACIONADO DE RECIBOS
# ============================================================

class ReceiptAuditor:
    """Grava logs estruturados dos recibos emitidos e gerencia recuperação em caso de falha."""
    def __init__(self, folder=None):
        if folder is None:
            folder = os.path.join(os.path.expanduser("~"), "Documents", "EstokeAoCubo_Recibos")
        self.log_dir = os.path.join(folder, "logs")
        os.makedirs(self.log_dir, exist_ok=True)
        self.log_file = os.path.join(self.log_dir, "recibos_auditoria.log")

    def registrar_impressao(self, job_id: str, text: str):
        """Registra a emissão com rotação simples para evitar consumo excessivo de disco."""
        # 1 linha para explicar o motivo: Mantemos auditoria offline rotacionada para segurança de dados do operador de caixa.
        try:
            # Rotação simples: se passar de 10MB, arquiva o log anterior
            if os.path.exists(self.log_file) and os.path.getsize(self.log_file) > 10 * 1024 * 1024:
                backup = self.log_file + ".bak"
                if os.path.exists(backup):
                    os.remove(backup)
                os.rename(self.log_file, backup)

            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(f"=== INICIO RECIBO [{job_id}] EMISSÃO: {ts} ===\n")
                f.write(text)
                f.write(f"\n=== FIM RECIBO [{job_id}] ===\n\n")
        except Exception as e:
            logger.warning("Falha ao registrar auditoria de recibo: %s", e)

    def recuperar_ultimo_recibo(self) -> str:
        """Lê e retorna o último recibo gerado no arquivo de logs de auditoria."""
        if not os.path.exists(self.log_file):
            return "Nenhum recibo em cache."
        try:
            with open(self.log_file, "r", encoding="utf-8") as f:
                content = f.read()
            recibos = content.split("=== INICIO RECIBO [")
            if len(recibos) > 1:
                return "=== INICIO RECIBO [" + recibos[-1].strip()
            return "Nenhum recibo íntegro encontrado."
        except Exception as e:
            logger.error("Erro ao ler log de auditoria: %s", e)
            return "Falha na leitura do cache."


# ============================================================
# FILA DE IMPRESSÃO ASSÍNCRONA E ANTI-DUPLICIDADE
# ============================================================

class PrintJob:
    """Estrutura interna para encapsular um trabalho de impressão."""
    def __init__(self, text: str, logo_path: str = None, job_id: str = None, pix_payload: str = None):
        self.text = text
        self.logo_path = logo_path
        self.job_id = job_id or datetime.datetime.now().strftime("%Y%m%d%H%M%S%f")
        self.pix_payload = pix_payload
        self.created_at = time.time()


class PrintQueueManager(threading.Thread):
    """Worker Thread gerenciador de fila assíncrona para evitar gargalo e congelamento da UI."""
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls):
        """Implementação Singleton Thread-safe do gerenciador da fila."""
        # 1 linha para explicar o motivo: Singleton garante fila única de impressão, prevenindo acessos concorrentes ao spooler físico.
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
                cls._instance.start()
            return cls._instance

    def __init__(self):
        super().__init__(name="PrintQueueManagerThread", daemon=True)
        self.queue = queue.Queue()
        self.processed_jobs = {}  # Cache para controle anti-duplicidade (job_id: timestamp)
        self.auditor = ReceiptAuditor()

    def run(self):
        """Loop permanente em background consumindo os jobs da fila."""
        # 1 linha para explicar o motivo: Thread consome jobs de forma assíncrona para manter a resposta do caixa instantânea.
        while True:
            try:
                job = self.queue.get()
                self._execute_job(job)
                self.queue.task_done()
            except Exception as e:
                logger.error("Falha crítica na thread background do spooler: %s", e)
                time.sleep(1)

    def enqueue(self, job: PrintJob) -> bool:
        """Enfileira um job se ele não for duplicado."""
        # Lock de duplicidade por 10 segundos baseado no ID do job
        # 1 linha para explicar o motivo: Bloqueia reenvios acidentais e duplo-clique do operador no botão de finalizar.
        agora = time.time()
        if job.job_id in self.processed_jobs:
            tempo_decorrido = agora - self.processed_jobs[job.job_id]
            if tempo_decorrido < 10.0:
                logger.warning("Trabalho de impressão duplicado ignorado: %s", job.job_id)
                return False

        self.processed_jobs[job.job_id] = agora
        self.queue.put(job)
        
        # Limpar cache de duplicidade antigo para evitar memory leaks
        # 1 linha para explicar o motivo: Evitamos vazamentos limpando chaves de cache mais antigas que 10 minutos.
        chaves_excluir = [k for k, v in self.processed_jobs.items() if agora - v > 600.0]
        for k in chaves_excluir:
            del self.processed_jobs[k]

        return True

    def _execute_job(self, job: PrintJob):
        """Imprime fisicamente o documento usando o driver correto configurado."""
        self.auditor.registrar_impressao(job.job_id, job.text)
        try:
            printer = _create_raw_printer()
            printer.print_text(job.text, logo_path=job.logo_path, pix_payload=job.pix_payload)
        except Exception as e:
            logger.error("Erro ao imprimir assincronamente o job %s: %s", job.job_id, e)
            # Aciona Fallback Automático salvando em arquivo e abrindo na tela
            try:
                fallback = TextFilePrinter()
                fallback.print_text(job.text, logo_path=job.logo_path, pix_payload=job.pix_payload)
            except Exception as fe:
                logger.critical("Falha no fallback de impressão: %s", fe)


# ============================================================
# COMPONENTES DE IMPRESSÃO (DRIVERS E INTERFACES)
# ============================================================

class BasePrinter:
    def print_text(self, text: str, logo_path=None, pix_payload=None):
        raise NotImplementedError("Subclasses devem implementar este método")


class AsyncPrinterWrapper(BasePrinter):
    """Proxy para redirecionar impressões síncronas para a fila assíncrona."""
    # 1 linha para explicar o motivo: Fornece retrocompatibilidade perfeita com as chamadas de método síncrono existentes na UI.
    def __init__(self, real_printer):
        self.real_printer = real_printer

    def print_text(self, text: str, logo_path=None, pix_payload=None):
        # Gerar um ID de job unificado baseado no conteúdo
        import hashlib
        job_hash = hashlib.md5(text.encode('utf-8')).hexdigest()
        job = PrintJob(text, logo_path=logo_path, job_id=job_hash, pix_payload=pix_payload)
        PrintQueueManager.get_instance().enqueue(job)


class TextFilePrinter(BasePrinter):
    """Imprime renderizando como imagem PNG estilizada ou gravando em arquivo de texto (fallback)."""
    def __init__(self, folder=None):
        if folder is None:
            folder = os.path.join(os.path.expanduser("~"), "Documents", "EstokeAoCubo_Recibos")
        self.folder = folder
        os.makedirs(self.folder, exist_ok=True)

    def print_text(self, text: str, logo_path=None, pix_payload=None):
        import re
        ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        
        # O pdv estava tentando imprimir como png e a impressora térmica estava tendo problemas.
        # Agora geramos sempre um arquivo de texto (.txt) para garantir impressão com excelência.
        # Ignoramos a conversão para imagem para manter a compatibilidade perfeita.
        
        # Extrair pix_payload do texto se ele vier incorporado como metadado [PIX_PAYLOAD: ...]
        if not pix_payload:
            match = re.search(r'\[PIX_PAYLOAD:\s*([^\]]+)\s*\]', text)
            if match:
                pix_payload = match.group(1).strip()
        
        # Limpar metadados de PIX existentes no texto
        text = re.sub(r'\[PIX_PAYLOAD:\s*[^\]]+\s*\]', '', text).strip()
        
        # Se houver pix_payload, adicionar a informação textual do Pix ao final da nota
        if pix_payload:
            text += f"\n\n================================\n      PAGAMENTO VIA PIX\n================================\nCopia e Cola:\n{pix_payload}\n"
            
        filename = f"Recibo_{ts}.txt"
        file_path = os.path.join(self.folder, filename)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(text)
        
        # Abertura automática no SO
        if os.name == 'nt':
            os.startfile(file_path)
        else:
            opener = 'open' if sys.platform == 'darwin' else 'xdg-open'
            subprocess.call([opener, file_path])
        return file_path

    def _print_as_image(self, text, logo_path, pix_payload, ts):
        """Renderiza o recibo completo como imagem PNG, formatando fontes, logo e QR Code Pix."""
        try:
            from PIL import Image, ImageDraw, ImageFont
            margin = 14
            receipt_width = 384  # Largura física da bobina de 58mm compacta
            font_size = 13
            line_spacing = 3

            # Carregamento de fontes monoespaçadas legíveis no Windows/Linux
            try:
                font = ImageFont.truetype("consola.ttf", font_size)
            except Exception:
                try:
                    font = ImageFont.truetype("DejaVuSansMono.ttf", font_size)
                except Exception:
                    font = ImageFont.load_default()

            lines = text.split('\n')
            
            # Calcular altura do texto
            dummy_img = Image.new('RGB', (1, 1))
            dummy_draw = ImageDraw.Draw(dummy_img)
            bbox = dummy_draw.textbbox((0, 0), 'Ag', font=font)
            line_h = bbox[3] - bbox[1] + line_spacing

            # Processar tamanho do logo se presente
            logo_img = None
            logo_h = 0
            if logo_path and os.path.isfile(logo_path):
                logo_img = Image.open(logo_path)
                max_w = receipt_width - 2 * margin
                logo_img.thumbnail((max_w, 75), Image.Resampling.LANCZOS)
                logo_h = logo_img.height + 8

            # Processar tamanho do QR Code Pix
            qr_img = None
            qr_h = 0
            if pix_payload and qrcode:
                qr = qrcode.QRCode(version=1, box_size=3, border=1)
                qr.add_data(pix_payload)
                qr.make(fit=True)
                qr_img = qr.make_image(fill_color="black", back_color="white")
                qr_h = qr_img.height + 15

            total_height = margin + logo_h + (len(lines) * line_h) + qr_h + margin
            
            # Instanciar imagem monocromática nítida
            img = Image.new('RGB', (receipt_width, total_height), 'white')
            draw = ImageDraw.Draw(img)

            y = margin
            # Centralizar e colar o Logo
            if logo_img:
                logo_x = (receipt_width - logo_img.width) // 2
                if logo_img.mode == 'RGBA':
                    img.paste(logo_img, (logo_x, y), logo_img)
                else:
                    img.paste(logo_img, (logo_x, y))
                y += logo_img.height + 8

            # Desenhar texto do recibo
            for line in lines:
                stripped = line.strip()
                # Centralizar separadores
                if stripped and all(c == '-' for c in stripped):
                    t_bbox = draw.textbbox((0, 0), stripped, font=font)
                    t_w = t_bbox[2] - t_bbox[0]
                    draw.text(((receipt_width - t_w) // 2, y), stripped, fill='black', font=font)
                else:
                    draw.text((margin, y), line, fill='black', font=font)
                y += line_h

            # Centralizar e desenhar o QR Code Pix no final
            if qr_img:
                qr_x = (receipt_width - qr_img.width) // 2
                img.paste(qr_img, (qr_x, y + 5))
                y += qr_img.height + 10
                # Texto informativo sob o QR Code
                lbl_pix = "PAGAMENTO VIA PIX"
                t_bbox = draw.textbbox((0, 0), lbl_pix, font=font)
                t_w = t_bbox[2] - t_bbox[0]
                draw.text(((receipt_width - t_w) // 2, y), lbl_pix, fill='black', font=font)

            # Salvar imagem
            filename = f"Recibo_{ts}.png"
            file_path = os.path.join(self.folder, filename)
            img.save(file_path, 'PNG')
            
            if os.name == 'nt':
                os.startfile(file_path)
            else:
                opener = 'open' if sys.platform == 'darwin' else 'xdg-open'
                subprocess.call([opener, file_path])
            return file_path

        except Exception as e:
            logger.warning("Falha ao desenhar recibo gráfico: %s. Revertendo para arquivo de texto.", e)
            # Reversão automática para arquivo TXT plano
            filename = f"Recibo_{ts}.txt"
            file_path = os.path.join(self.folder, filename)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(text)
            if os.name == 'nt':
                os.startfile(file_path)
            return file_path


class EscPosPrinter(BasePrinter):
    """Imprime diretamente na impressora térmica utilizando os comandos ESC/POS do protocolo."""
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.interface = cfg.get("interface")
        self.settings = cfg.get("settings", {})
        self.device = None

    def _get_device(self):
        if self.device:
            return self.device
        # 1 linha para explicar o motivo: Mantemos suporte nativo a USB/Rede para flexibilidade de hardware do varejista.
        if self.interface == 'network' and Network:
            host = self.settings.get('host')
            port = int(self.settings.get('port', 9100))
            self.device = Network(host, port)
        elif self.interface == 'usb' and Usb:
            vendor = int(self.settings.get('idVendor', 0))
            product = int(self.settings.get('idProduct', 0))
            self.device = Usb(vendor, product)
        elif self.interface == 'serial' and Serial:
            port = self.settings.get('port')
            baudrate = int(self.settings.get('baudrate', 9600))
            self.device = Serial(port, baudrate=baudrate)
        else:
            raise RuntimeError(f"Interface ESC/POS sem suporte ou biblioteca ausente: {self.interface}")
        return self.device

    def print_text(self, text: str, logo_path=None, pix_payload=None):
        dev = self._get_device()
        try:
            # --- INICIALIZAÇÃO DA IMPRESSORA E FONTE A (RESOLVE FONTE MUITO PEQUENA) ---
            # Envia ESC @ (inicializar), ESC M 0 (Fonte A) e ESC 2 (espaçamento padrão) via _raw se disponível
            if hasattr(dev, '_raw'):
                dev._raw(b'\x1b\x40\x1b\x4d\x00\x1b\x32')

            # Imprimir o Logo
            if logo_path and os.path.isfile(logo_path) and hasattr(dev, 'image'):
                try:
                    from PIL import Image
                    img = Image.open(logo_path)
                    img.thumbnail((300, 100), Image.Resampling.LANCZOS)
                    dev.image(img)
                except Exception as le:
                    logger.warning("Falha ao enviar logo para ESC/POS: %s", le)

            # Imprimir o corpo de texto codificado para CP850
            if hasattr(dev, 'text'):
                dev.text(text)
            
            # Imprimir QR Code PIX
            if pix_payload and qrcode and hasattr(dev, 'image'):
                try:
                    qr = qrcode.QRCode(version=1, box_size=3, border=1)
                    qr.add_data(pix_payload)
                    qr.make(fit=True)
                    qr_img = qr.make_image(fill_color="black", back_color="white")
                    dev.image(qr_img)
                    dev.text("\n\n      PAGUE COM PIX\n\n")
                except Exception as qre:
                    logger.warning("Falha ao imprimir QR Code Pix no ESC/POS: %s", qre)

            # --- AVANÇO E CORTE DE PAPEL (RESOLVE FOLHA SEM CORTE / CORTE PREMATURO) ---
            # Avança manualmente antes de cortar para garantir que passe do cortador físico
            if hasattr(dev, 'text'):
                dev.text('\n\n\n\n\n\n')
                
            if hasattr(dev, 'cut'):
                # Desativa o feed automático da biblioteca para usar o nosso avanço manual preciso
                dev.cut(feed=False)
            elif hasattr(dev, '_raw'):
                dev._raw(b'\x1b\x6d')  # ESC m clássico Elgin
        finally:
            self.close()

    def close(self):
        if self.device:
            try:
                self.device.close()
            except Exception:
                pass
            self.device = None

    def __del__(self):
        self.close()


class WindowsRawPrinter(BasePrinter):
    """Imprime diretamente enviando os bytes brutos (RAW) ao spooler do Windows."""
    def __init__(self, printer_name=None):
        self.printer_name = printer_name

    def _get_printer_name(self):
        if self.printer_name:
            return self.printer_name
        if win32print:
            return win32print.GetDefaultPrinter()
        return None

    def status_health_check(self) -> str:
        """Verifica a saúde do spooler e status da impressora via API do Windows."""
        # 1 linha para explicar o motivo: Verificação preventiva para alertar o caixa antes de gerar transações pendentes no spooler.
        if not win32print:
            return "Sem suporte Win32."
        name = self._get_printer_name()
        if not name:
            return "Nenhuma impressora disponível."
        try:
            hprinter = win32print.OpenPrinter(name)
            try:
                pinfo = win32print.GetPrinter(hprinter, 2)
                status = pinfo.get('Status', 0)
                if status & win32print.PRINTER_STATUS_PAPER_OUT:
                    return "Falta papel na impressora!"
                if status & win32print.PRINTER_STATUS_OFFLINE:
                    return "Impressora offline!"
                if status & win32print.PRINTER_STATUS_PAUSED:
                    return "Spooler pausado!"
                if status & win32print.PRINTER_STATUS_ERROR:
                    return "Erro físico detectado na impressora!"
                return "Pronto"
            finally:
                win32print.ClosePrinter(hprinter)
        except Exception as e:
            return f"Erro de comunicação: {e}"

    def _logo_to_escpos_bytes(self, logo_path) -> bytes:
        """Converte imagem de cabeçalho para comandos gráficos binários ESC/POS (GS v 0)."""
        try:
            from PIL import Image
            img = Image.open(logo_path)
            img.thumbnail((384, 100), Image.Resampling.LANCZOS)
            img = img.convert('1')
            
            w_bytes = (img.width + 7) // 8
            # Comando ESC/POS GS v 0 (raster graphics)
            data = b'\x1b\x61\x01'  # Alinhamento centralizado
            data += b'\x1d\x76\x30\x00'
            data += bytes([w_bytes & 0xFF, (w_bytes >> 8) & 0xFF])
            data += bytes([img.height & 0xFF, (img.height >> 8) & 0xFF])
            
            pixels = img.load()
            for y in range(img.height):
                for xb in range(w_bytes):
                    val = 0
                    for bit in range(8):
                        x = xb * 8 + bit
                        if x < img.width and pixels[x, y] == 0:  # 0 = preto
                            val |= (0x80 >> bit)
                    data += bytes([val])
            data += b'\x1b\x61\x00\n'  # Retorna alinhamento à esquerda
            return data
        except Exception as e:
            logger.warning("Falha na conversão binária do logo: %s", e)
            return b''

    def print_text(self, text: str, logo_path=None, pix_payload=None):
        if not win32print:
            raise RuntimeError("Módulo de impressão pywin32 indisponível.")
        name = self._get_printer_name()
        if not name:
            raise RuntimeError("Impressora Windows não configurada ou ausente.")
            
        hprinter = win32print.OpenPrinter(name)
        try:
            win32print.StartDocPrinter(hprinter, 1, ("Recibo EstokeAoCubo", None, "RAW"))
            win32print.StartPagePrinter(hprinter)
            
            # --- INICIALIZAÇÃO DA IMPRESSORA E FONTE A (RESOLVE FONTE MUITO PEQUENA) ---
            # \x1b\x40 = ESC @ (Inicializa a impressora)
            # \x1b\x4d\x00 = ESC M 0 (Força Fonte A - padrão nítida de 12x24 pontos)
            # \x1b\x32 = ESC 2 (Define espaçamento de linha padrão)
            data = b'\x1b\x40\x1b\x4d\x00\x1b\x32'

            # Processar e adicionar logo binário no fluxo
            if logo_path and os.path.isfile(logo_path):
                logo_bytes = self._logo_to_escpos_bytes(logo_path)
                if logo_bytes:
                    data += logo_bytes

            # Adicionar corpo de texto codificado para página de códigos CP850
            data += text.encode('cp850', errors='replace')

            # Processar e adicionar QR Code Pix se presente
            if pix_payload and qrcode:
                try:
                    qr = qrcode.QRCode(version=1, box_size=3, border=1)
                    qr.add_data(pix_payload)
                    qr.make(fit=True)
                    qr_img = qr.make_image(fill_color="black", back_color="white")
                    
                    # Salvar temporariamente para converter em bytes ESC/POS
                    temp_qr = os.path.join(os.path.expanduser("~"), "AppData", "Local", "Temp", "temp_qr.png")
                    os.makedirs(os.path.dirname(temp_qr), exist_ok=True)
                    qr_img.save(temp_qr)
                    
                    qr_bytes = self._logo_to_escpos_bytes(temp_qr)
                    if qr_bytes:
                        data += b'\n' + qr_bytes + b'\n      PAGAMENTO VIA PIX\n\n'
                        
                    # Limpeza preventiva do arquivo temporário
                    if os.path.exists(temp_qr):
                        os.remove(temp_qr)
                except Exception as qre:
                    logger.warning("Erro ao anexar QR Code Pix no Windows RAW: %s", qre)

            # --- AVANÇO E CORTE DE PAPEL (RESOLVE FOLHA SEM CORTE / CORTE PREMATURO) ---
            # Avança 6 linhas para garantir que o texto ultrapasse fisicamente a guilhotina da Elgin i8
            data += b'\n\n\n\n\n\n'
            # Envia o comando clássico da Elgin ESC m (\x1b\x6d) e GS V 0 (\x1d\x56\x00) como redundância robusta
            data += b'\x1b\x6d\x1d\x56\x00'
            
            win32print.WritePrinter(hprinter, data)
            win32print.EndPagePrinter(hprinter)
            win32print.EndDocPrinter(hprinter)
        finally:
            win32print.ClosePrinter(hprinter)

    @staticmethod
    def listar_impressoras():
        if not win32print:
            return []
        try:
            printers = win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)
            return [p[2] for p in printers]
        except Exception:
            return []


# ============================================================
# ENVIO ASSÍNCRONO DE RECIBO DIGITAL (WHATSAPP E E-MAIL)
# ============================================================

def enviar_whatsapp(telefone: str, mensagem: str):
    """Envia recibo via WhatsApp de forma assíncrona, evitando congelamentos de tela."""
    # 1 linha para explicar o motivo: O envio é despachado em uma thread separada para manter a agilidade no PDV.
    def worker():
        try:
            num = ''.join(c for c in telefone if c.isdigit())
            if not num:
                return
            if not num.startswith('55') and len(num) <= 11:
                num = '55' + num
            encoded = urllib.parse.quote(mensagem, safe='')
            url = f"https://wa.me/{num}?text={encoded}"
            import webbrowser
            webbrowser.open(url)
        except Exception as e:
            logger.error("Erro na thread do WhatsApp: %s", e)

    threading.Thread(target=worker, name="WhatsAppSenderThread", daemon=True).start()
    return True


def enviar_email_assincrono(destinatario: str, assunto: str, html_content: str):
    """Envia recibo digital via SMTP usando thread em background para não atrasar o caixa."""
    # 1 linha para explicar o motivo: Evitamos gargalos de rede nos checkouts enviando o email em segundo plano.
    def worker():
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        
        # Lê credenciais do ambiente ou config (substituir por variáveis de produção configuradas)
        smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USER", "")
        smtp_pass = os.getenv("SMTP_PASSWORD", "")
        
        if not smtp_user or not smtp_pass:
            logger.warning("Credenciais SMTP ausentes. E-mail de recibo não enviado.")
            return

        try:
            msg = MIMEMultipart()
            msg["From"] = smtp_user
            msg["To"] = destinatario
            msg["Subject"] = assunto
            msg.attach(MIMEText(html_content, "html", "utf-8"))
            
            with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, destinatario, msg.as_string())
            logger.info("Recibo enviado por email com sucesso para %s", destinatario)
        except Exception as e:
            logger.error("Falha ao despachar email de recibo: %s", e)

    threading.Thread(target=worker, name="EmailSenderThread", daemon=True).start()


# ============================================================
# INSTANCIAÇÃO DINÂMICA
# ============================================================

def _create_raw_printer() -> BasePrinter:
    """Instancia o driver físico real configurado sem envoltório assíncrono."""
    cfg = get_printer_config()
    if not cfg.get('enabled', False):
        return TextFilePrinter()
    ptype = cfg.get('type', 'text')
    if ptype == 'escpos':
        return EscPosPrinter(cfg)
    if ptype == 'windows':
        name = cfg.get('settings', {}).get('printer_name', None)
        return WindowsRawPrinter(name)
    return TextFilePrinter()


def get_printer() -> BasePrinter:
    """Instancia o driver de impressão envelopado em Proxy Assíncrono para o caixa."""
    # 1 linha para explicar o motivo: O envoltório garante que qualquer chamada de UI torne-se assíncrona instantaneamente.
    real_printer = _create_raw_printer()
    if isinstance(real_printer, TextFilePrinter):
        return real_printer  # Salvar arquivo de texto local pode rodar síncrono para abrir em tela.
    return AsyncPrinterWrapper(real_printer)
