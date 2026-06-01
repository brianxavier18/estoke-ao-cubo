import sqlite3
import pandas as pd
import datetime
import os
import shutil
import sys
import logging
from cryptography.fernet import Fernet

logger = logging.getLogger('EstokeAoCubo.database')


# --- Criptografia do Banco de Dados ---
def _get_key_path():
    """Caminho do arquivo que armazena a chave de criptografia."""
    data_dir = get_app_data_path()
    return os.path.join(data_dir, ".db_key")


def _load_or_create_key():
    """Carrega a chave Fernet existente ou gera uma nova."""
    key_path = _get_key_path()
    if os.path.exists(key_path):
        with open(key_path, "rb") as f:
            key = f.read()
    else:
        key = Fernet.generate_key()
        with open(key_path, "wb") as f:
            f.write(key)
        # Ocultar o arquivo no Windows
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(key_path, 0x02)  # FILE_ATTRIBUTE_HIDDEN
        except Exception:
            pass
        logger.info("Chave de criptografia gerada.")
    return key


def _encrypt_file(src_path, enc_path, key):
    """Criptografa um arquivo com Fernet (AES-128-CBC)."""
    fernet = Fernet(key)
    with open(src_path, "rb") as f:
        data = f.read()
    encrypted = fernet.encrypt(data)
    with open(enc_path, "wb") as f:
        f.write(encrypted)


def _decrypt_file(enc_path, dst_path, key):
    """Descriptografa um arquivo Fernet para o destino."""
    fernet = Fernet(key)
    with open(enc_path, "rb") as f:
        encrypted = f.read()
    data = fernet.decrypt(encrypted)
    with open(dst_path, "wb") as f:
        f.write(data)

def get_app_data_path():
    """Retorna o caminho para a pasta de dados do aplicativo no AppData"""
    if os.name == 'nt':
        app_data = os.getenv('LOCALAPPDATA')
    else:
        app_data = os.path.expanduser('~/.local/share')
    data_dir = os.path.join(app_data, 'EstokeAoCubo')
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
    return data_dir


class Database:

    def __init__(self, db_name="estoque.db"):
        self.data_dir = get_app_data_path()
        self.db_path = os.path.join(self.data_dir, db_name)
        self.enc_path = self.db_path + ".enc"
        self._enc_key = _load_or_create_key()

        # Migração Automática: Mover DB local para AppData se existir localmente mas não no AppData
        local_db = os.path.abspath(db_name)
        if os.path.exists(local_db) and not os.path.exists(self.db_path) and not os.path.exists(self.enc_path):
            try:
                logger.info("Migrando banco de dados para local seguro: %s", self.db_path)
                shutil.copy2(local_db, self.db_path)
                base, ext = os.path.splitext(local_db)
                os.rename(local_db, f"{base}_backup_migracao{ext}")
            except (OSError, shutil.Error) as e:
                logger.error("Erro ao migrar banco de dados: %s", e)

        # --- Descriptografar banco se necessário ---
        # Prioridade: .enc (criptografado) > .db (plaintext de crash anterior)
        if os.path.exists(self.enc_path):
            try:
                _decrypt_file(self.enc_path, self.db_path, self._enc_key)
                logger.info("Banco de dados descriptografado com sucesso.")
            except Exception as e:
                logger.error("Erro ao descriptografar banco: %s", e)
                # Se falhar e existir .db plaintext (crash anterior), usa ele como fallback
                if os.path.exists(self.db_path):
                    logger.warning("Usando banco plaintext como fallback (possível crash anterior).")
                else:
                    raise RuntimeError("Não foi possível descriptografar o banco de dados.") from e
        elif os.path.exists(self.db_path):
            # Primeira vez com criptografia: DB plaintext existe, será encriptado no close
            logger.info("Banco plaintext detectado. Será criptografado ao fechar.")

        # Migração de Imagens (Se houver pasta local product_images)
        if getattr(sys, 'frozen', False):
            base_app_path = os.path.dirname(sys.executable)
        else:
            base_app_path = os.path.dirname(os.path.abspath(__file__))

        local_img_dir = os.path.join(base_app_path, "product_images")
        app_data_img_dir = os.path.join(self.data_dir, "product_images")

        if not os.path.exists(app_data_img_dir):
            os.makedirs(app_data_img_dir)

        if os.path.exists(local_img_dir):
            try:
                for item in os.listdir(local_img_dir):
                    s = os.path.join(local_img_dir, item)
                    d = os.path.join(app_data_img_dir, item)
                    if os.path.isfile(s) and not os.path.exists(d):
                        shutil.copy2(s, d)
            except (OSError, shutil.Error) as e:
                logger.error("Erro ao migrar imagens: %s", e)

        self.conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        self.conn.execute("PRAGMA foreign_keys = ON")
        try:
            self.conn.execute("PRAGMA journal_mode = WAL")
        except Exception as e:
            logger.warning("Nao foi possivel ativar o modo WAL: %s", e)
        self.create_tables()

    def adicionar_kit(self, nome, quantidade, preco_venda, preco_compra, itens, imagem=None):
        cursor = self.conn.cursor()
        # Quantidade do kit é virtual (0), mas mantemos para compatibilidade
        cursor.execute("""
            INSERT INTO kits (nome, quantidade, preco_venda, preco_compra, imagem)
            VALUES (?, ?, ?, ?, ?)
        """, (nome, 0, preco_venda, preco_compra, imagem))
        kit_id = cursor.lastrowid
        for item in itens:
            # item deve ser dict com 'nome' e 'quantidade'
            cursor.execute("""
                INSERT INTO kit_itens (kit_id, produto_nome, quantidade)
                VALUES (?, ?, ?)
            """, (kit_id, item['nome'], item['quantidade']))
        self.conn.commit()
        self.registrar_alteracao("Adicionar Kit", f"Kit '{nome}' criado com {len(itens)} componentes.")

    def listar_kits(self):
        try:
            return pd.read_sql_query("SELECT * FROM kits", self.conn)
        except Exception as e:
            logger.error("Erro ao listar kits: %s", e)
            return pd.DataFrame()

    def get_kit_components(self, kit_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT produto_nome, quantidade FROM kit_itens WHERE kit_id=?", (kit_id,))
        return [{'nome': row[0], 'quantidade': row[1]} for row in cursor.fetchall()]

    def verificar_disponibilidade_kit(self, kit_id):
        """Retorna a quantidade máxima de kits que podem ser montados com o estoque atual"""
        componentes = self.get_kit_components(kit_id)
        if not componentes:
            return 0
        
        # Query otimizada: buscar todos os componentes de uma vez
        nomes = [comp['nome'] for comp in componentes]
        placeholders = ','.join(['?' for _ in nomes])
        cursor = self.conn.cursor()
        cursor.execute(f"SELECT nome, quantidade FROM produtos WHERE nome IN ({placeholders})", nomes)
        estoque_map = {row[0]: row[1] for row in cursor.fetchall()}
        
        max_kits = float('inf')
        for comp in componentes:
            nome_prod = comp['nome']
            qtd_necessaria = comp['quantidade']
            
            qtd_estoque = estoque_map.get(nome_prod)
            if qtd_estoque is None:
                return 0  # Produto não encontrado
            
            if qtd_necessaria > 0:
                possiveis = qtd_estoque // qtd_necessaria
                if possiveis < max_kits:
                    max_kits = possiveis
        
        return max_kits if max_kits != float('inf') else 0

    def vender_kit(self, kit_id, qtd_venda=1, forma_pagamento="Dinheiro", cliente="Consumidor Final", registrar_entrada=True):
        """Deduz do estoque os componentes do kit e registra a venda.
        
        Args:
            registrar_entrada: Se False, NÃO registra na tabela 'entradas' (evita duplicata quando chamado pelo PDV).
        """
        componentes = self.get_kit_components(kit_id)
        cursor = self.conn.cursor()
        
        missing = []
        # Verificação prévia
        for comp in componentes:
            cursor.execute("SELECT quantidade FROM produtos WHERE nome = ?", (comp['nome'],))
            res = cursor.fetchone()
            if not res or res[0] < (comp['quantidade'] * qtd_venda):
                missing.append(comp['nome'])
        
        if missing:
            return False, f"Estoque insuficiente para: {', '.join(missing)}"
        
        # Calcular valor total do kit
        valor_total = 0.0
        custo_total = 0.0
        for comp in componentes:
            cursor.execute("SELECT preco, preco_compra FROM produtos WHERE nome = ?", (comp['nome'],))
            res = cursor.fetchone()
            if res:
                valor_total += (res[0] or 0.0) * comp['quantidade'] * qtd_venda
                custo_total += (res[1] or 0.0) * comp['quantidade'] * qtd_venda
        
        lucro = valor_total - custo_total
            
        # Baixa efetiva
        for comp in componentes:
            total_deduzir = comp['quantidade'] * qtd_venda
            cursor.execute("UPDATE produtos SET quantidade = quantidade - ? WHERE nome = ?", 
                          (total_deduzir, comp['nome']))
        
        # Registrar venda na tabela vendas
        data_venda = datetime.datetime.now().isoformat()
        cursor.execute("""INSERT INTO vendas (data_venda, quantidade, valor_total, lucro, forma_pagamento, cliente, status)
                         VALUES (?, ?, ?, ?, ?, ?, 'Concluída')""",
                      (data_venda, qtd_venda, valor_total, lucro, forma_pagamento, cliente))
        
        # Registrar entrada financeira (apenas se não for chamado pelo PDV que já registra)
        if registrar_entrada:
            cursor.execute("""INSERT INTO entradas (data, valor, descricao)
                             VALUES (?, ?, ?)""",
                          (data_venda[:10], valor_total, f"Venda Kit ID {kit_id} (Qtd: {qtd_venda})"))
        
        self.conn.commit()
        self.registrar_alteracao("Venda Kit", f"Kit ID {kit_id} vendido (Qtd: {qtd_venda}). Total: R$ {valor_total:.2f}")
        return True, f"Venda de kit realizada com sucesso! Total: R$ {valor_total:.2f}"

    def create_tables(self):
        cursor = self.conn.cursor()
        # Tabelas principais
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS produtos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                quantidade INTEGER NOT NULL,
                preco REAL NOT NULL,
                categoria TEXT,
                quantidade_inicial INTEGER,
                horas_trabalho REAL,
                valor_hora REAL,
                margem_lucro REAL,
                fator_risco REAL,
                fator_raridade REAL,
                preco_compra REAL,
                imagem TEXT,
                data_validade TEXT,
                codigo_barras TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS kits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                quantidade INTEGER NOT NULL,
                preco_venda REAL NOT NULL,
                preco_compra REAL NOT NULL,
                imagem TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS kit_itens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kit_id INTEGER NOT NULL,
                produto_nome TEXT NOT NULL,
                quantidade INTEGER NOT NULL,
                FOREIGN KEY (kit_id) REFERENCES kits(id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS vendas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                produto_id INTEGER,
                quantidade INTEGER,
                valor_total REAL,
                data_venda TEXT,
                lucro REAL
            )
        ''')
        # Tabela de Gastos (para a IA)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS gastos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data TEXT NOT NULL,
                valor REAL NOT NULL,
                categoria TEXT NOT NULL,
                descricao TEXT,
                tipo TEXT DEFAULT 'Variável'
            )
        ''')
        # Migração para adicionar coluna 'tipo' em gastos se não existir
        try:
            cursor.execute("PRAGMA table_info(gastos)")
            colunas = [coluna[1] for coluna in cursor.fetchall()]
            if 'tipo' not in colunas:
                cursor.execute("ALTER TABLE gastos ADD COLUMN tipo TEXT DEFAULT 'Variável'")
        except sqlite3.DatabaseError as e:
            logger.debug("Aviso migração gastos: %s", e)
        # Tabela de Histórico
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS historico (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data_hora TEXT NOT NULL,
                acao TEXT NOT NULL,
                detalhe TEXT
            )
        ''')
        # Tabela de Entradas (Saldo)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS entradas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data TEXT NOT NULL,
                valor REAL NOT NULL,
                descricao TEXT
            )
        ''')
        # Novas Tabelas
        cursor.execute('''CREATE TABLE IF NOT EXISTS metas (id INTEGER PRIMARY KEY AUTOINCREMENT, titulo TEXT, descricao TEXT, status TEXT, prazo TEXT)''')
        # Migração: adicionar coluna prazo em metas se não existir
        try:
            cursor.execute("PRAGMA table_info(metas)")
            colunas_metas = [coluna[1] for coluna in cursor.fetchall()]
            if 'prazo' not in colunas_metas:
                cursor.execute("ALTER TABLE metas ADD COLUMN prazo TEXT")
        except sqlite3.DatabaseError:
            pass
        cursor.execute('''CREATE TABLE IF NOT EXISTS fornecedores (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT, contato TEXT, produto_fornecido TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS clientes (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT, contato TEXT, observacoes TEXT, compras_recorrentes INTEGER DEFAULT 0)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS estabelecimento (id INTEGER PRIMARY KEY AUTOINCREMENT, area_local TEXT, tarefa TEXT, status TEXT, prioridade TEXT)''')
        # Migração: verificar se a tabela estabelecimento tem a estrutura antiga
        try:
            cursor.execute("PRAGMA table_info(estabelecimento)")
            colunas = [coluna[1] for coluna in cursor.fetchall()]
            # Se a tabela tem a coluna 'item' (estrutura antiga), fazer migração
            if 'item' in colunas and 'area_local' not in colunas:
                # Renomear tabela antiga
                cursor.execute("ALTER TABLE estabelecimento RENAME TO estabelecimento_old")
                # Criar tabela nova com estrutura correta
                cursor.execute('''CREATE TABLE estabelecimento (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    area_local TEXT,
                    tarefa TEXT,
                    status TEXT DEFAULT 'Pendente',
                    prioridade TEXT DEFAULT 'Média'
                )''')
                # Copiar dados da tabela antiga (se houver)
                cursor.execute("SELECT COUNT(*) FROM estabelecimento_old")
                if cursor.fetchone()[0] > 0:
                    cursor.execute("INSERT INTO estabelecimento (area_local, tarefa, status, prioridade) SELECT item, detalhe, 'Pendente', 'Média' FROM estabelecimento_old")
                # Deletar tabela antiga
                cursor.execute("DROP TABLE estabelecimento_old")
        except sqlite3.DatabaseError as e:
            logger.debug("Aviso migração estabelecimento: %s", e)

        # --- Tabela Contas a Receber (Fiado) ---
        cursor.execute('''CREATE TABLE IF NOT EXISTS contas_receber (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER,
            valor REAL NOT NULL,
            data_venda TEXT NOT NULL,
            data_vencimento TEXT,
            status TEXT DEFAULT 'Pendente',
            data_pagamento TEXT,
            descricao TEXT,
            FOREIGN KEY (cliente_id) REFERENCES clientes(id)
        )''')

        # --- Tabela Fidelidade (Pontos) ---
        cursor.execute('''CREATE TABLE IF NOT EXISTS fidelidade (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER UNIQUE,
            pontos INTEGER DEFAULT 0,
            total_acumulado REAL DEFAULT 0,
            data_ultima_compra TEXT,
            FOREIGN KEY (cliente_id) REFERENCES clientes(id)
        )''')

        # --- Migração: adicionar colunas em vendas ---
        try:
            cursor.execute("PRAGMA table_info(vendas)")
            colunas_vendas = [c[1] for c in cursor.fetchall()]
            if 'forma_pagamento' not in colunas_vendas:
                cursor.execute("ALTER TABLE vendas ADD COLUMN forma_pagamento TEXT DEFAULT 'Dinheiro'")
            if 'cliente' not in colunas_vendas:
                cursor.execute("ALTER TABLE vendas ADD COLUMN cliente TEXT DEFAULT 'Consumidor Final'")
            if 'status' not in colunas_vendas:
                cursor.execute("ALTER TABLE vendas ADD COLUMN status TEXT DEFAULT 'Concluída'")
            if 'desconto' not in colunas_vendas:
                cursor.execute("ALTER TABLE vendas ADD COLUMN desconto REAL DEFAULT 0")
            if 'vendedor' not in colunas_vendas:
                cursor.execute("ALTER TABLE vendas ADD COLUMN vendedor TEXT DEFAULT ''")
            if 'caixa_id' not in colunas_vendas:
                cursor.execute("ALTER TABLE vendas ADD COLUMN caixa_id INTEGER DEFAULT NULL")
        except sqlite3.DatabaseError as e:
            logger.debug("Aviso migração vendas: %s", e)

        # --- Migração: adicionar coluna vendedor em servicos ---
        try:
            cursor.execute("PRAGMA table_info(servicos)")
            colunas_servicos = [c[1] for c in cursor.fetchall()]
            if 'vendedor' not in colunas_servicos:
                cursor.execute("ALTER TABLE servicos ADD COLUMN vendedor TEXT DEFAULT ''")
        except sqlite3.DatabaseError as e:
            logger.debug("Aviso migração servicos: %s", e)

        # --- Migração: adicionar coluna estoque_minimo em produtos ---
        try:
            cursor.execute("PRAGMA table_info(produtos)")
            colunas_produtos = [c[1] for c in cursor.fetchall()]
            if 'estoque_minimo' not in colunas_produtos:
                cursor.execute("ALTER TABLE produtos ADD COLUMN estoque_minimo INTEGER DEFAULT 5")
        except sqlite3.DatabaseError as e:
            logger.debug("Aviso migração estoque_minimo: %s", e)

        # --- Índices para performance ---
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_produtos_nome ON produtos(nome)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_produtos_codigo ON produtos(codigo_barras)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_gastos_data ON gastos(data)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_gastos_categoria ON gastos(categoria)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_entradas_data ON entradas(data)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_historico_data ON historico(data_hora)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_historico_acao ON historico(acao)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_kit_itens_kit ON kit_itens(kit_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_estabelecimento_status ON estabelecimento(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_vendas_data ON vendas(data_venda)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_vendas_cliente ON vendas(cliente)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_vendas_caixa ON vendas(caixa_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_contas_receber_status ON contas_receber(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_fidelidade_cliente ON fidelidade(cliente_id)")

        # Tabela de categorias customizáveis
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS categorias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                tipo TEXT NOT NULL DEFAULT 'produto'
            )
        ''')

        # Tabela de subcategorias (vinculada a uma categoria)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS subcategorias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                categoria_id INTEGER NOT NULL,
                FOREIGN KEY (categoria_id) REFERENCES categorias(id) ON DELETE CASCADE
            )
        ''')

        # Tabela de tamanhos (vinculada a uma subcategoria)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tamanhos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                subcategoria_id INTEGER NOT NULL,
                FOREIGN KEY (subcategoria_id) REFERENCES subcategorias(id) ON DELETE CASCADE
            )
        ''')

        # Tabela de sub-subcategorias (vinculada a uma subcategoria)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sub_subcategorias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                subcategoria_id INTEGER NOT NULL,
                FOREIGN KEY (subcategoria_id) REFERENCES subcategorias(id) ON DELETE CASCADE
            )
        ''')

        # Tabela de configuração da nota personalizada
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS config_nota (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                empresa_nome TEXT DEFAULT 'Estoke Ao Cubo',
                empresa_email TEXT DEFAULT '',
                empresa_telefone TEXT DEFAULT '',
                empresa_endereco TEXT DEFAULT '',
                empresa_cnpj TEXT DEFAULT '',
                empresa_logo TEXT DEFAULT '',
                mensagem_rodape TEXT DEFAULT 'Obrigado pela preferência!'
            )
        ''')
        # Inserir config padrão se vazia
        cursor.execute("SELECT COUNT(*) FROM config_nota")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO config_nota (empresa_nome) VALUES ('Estoke Ao Cubo')")

        # --- Migração: adicionar colunas subcategoria e tamanho em produtos ---
        try:
            cursor.execute("PRAGMA table_info(produtos)")
            colunas_prods2 = [c[1] for c in cursor.fetchall()]
            if 'subcategoria' not in colunas_prods2:
                cursor.execute("ALTER TABLE produtos ADD COLUMN subcategoria TEXT")
            if 'tamanho' not in colunas_prods2:
                cursor.execute("ALTER TABLE produtos ADD COLUMN tamanho TEXT")
            if 'sub_subcategoria' not in colunas_prods2:
                cursor.execute("ALTER TABLE produtos ADD COLUMN sub_subcategoria TEXT")
            if 'desconto_item' not in colunas_prods2:
                cursor.execute("ALTER TABLE produtos ADD COLUMN desconto_item REAL DEFAULT 0")
            if 'estoque_p' not in colunas_prods2:
                cursor.execute("ALTER TABLE produtos ADD COLUMN estoque_p INTEGER DEFAULT 0")
            if 'estoque_m' not in colunas_prods2:
                cursor.execute("ALTER TABLE produtos ADD COLUMN estoque_m INTEGER DEFAULT 0")
            if 'estoque_g' not in colunas_prods2:
                cursor.execute("ALTER TABLE produtos ADD COLUMN estoque_g INTEGER DEFAULT 0")
            if 'estoque_gg' not in colunas_prods2:
                cursor.execute("ALTER TABLE produtos ADD COLUMN estoque_gg INTEGER DEFAULT 0")
            if 'unidade_medida' not in colunas_prods2:
                cursor.execute("ALTER TABLE produtos ADD COLUMN unidade_medida TEXT DEFAULT 'un'")
            if 'preco_por_peso' not in colunas_prods2:
                cursor.execute("ALTER TABLE produtos ADD COLUMN preco_por_peso REAL DEFAULT 0")
            if 'tipo_produto' not in colunas_prods2:
                cursor.execute("ALTER TABLE produtos ADD COLUMN tipo_produto TEXT DEFAULT 'produto'")
            if 'sob_encomenda' not in colunas_prods2:
                cursor.execute("ALTER TABLE produtos ADD COLUMN sob_encomenda INTEGER DEFAULT 0")
            if 'lote_serie' not in colunas_prods2:
                cursor.execute("ALTER TABLE produtos ADD COLUMN lote_serie TEXT DEFAULT ''")
        except sqlite3.DatabaseError as e:
            logger.debug("Aviso migração subcategoria/tamanho: %s", e)

        # --- Migração: servicos - campos OS ---
        try:
            cursor.execute("PRAGMA table_info(servicos)")
            colunas_sv = [c[1] for c in cursor.fetchall()]
            if 'status_os' not in colunas_sv:
                cursor.execute("ALTER TABLE servicos ADD COLUMN status_os TEXT DEFAULT 'Concluído'")
            if 'contato_cliente' not in colunas_sv:
                cursor.execute("ALTER TABLE servicos ADD COLUMN contato_cliente TEXT DEFAULT ''")
            if 'equipamento' not in colunas_sv:
                cursor.execute("ALTER TABLE servicos ADD COLUMN equipamento TEXT DEFAULT ''")
            if 'data_previsao' not in colunas_sv:
                cursor.execute("ALTER TABLE servicos ADD COLUMN data_previsao TEXT DEFAULT ''")
        except sqlite3.DatabaseError as e:
            logger.debug("Aviso migração servicos OS: %s", e)

        # --- Tabelas de Ficha Técnica (Receitas/Fórmulas) ---
        cursor.execute('''CREATE TABLE IF NOT EXISTS fichas_tecnicas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            produto_id INTEGER NOT NULL,
            nome TEXT NOT NULL,
            rendimento INTEGER DEFAULT 1,
            FOREIGN KEY (produto_id) REFERENCES produtos(id) ON DELETE CASCADE
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS ficha_tecnica_itens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ficha_id INTEGER NOT NULL,
            insumo_id INTEGER NOT NULL,
            quantidade REAL NOT NULL,
            FOREIGN KEY (ficha_id) REFERENCES fichas_tecnicas(id) ON DELETE CASCADE,
            FOREIGN KEY (insumo_id) REFERENCES produtos(id)
        )''')

        # --- Tabela de Produção Diária ---
        cursor.execute('''CREATE TABLE IF NOT EXISTS producao_diaria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ficha_id INTEGER NOT NULL,
            quantidade_produzida INTEGER NOT NULL,
            data TEXT NOT NULL,
            observacao TEXT,
            FOREIGN KEY (ficha_id) REFERENCES fichas_tecnicas(id)
        )''')

        # --- Tabela de Baixa de Insumos (uso manual) ---
        cursor.execute('''CREATE TABLE IF NOT EXISTS baixa_insumos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            insumo_id INTEGER NOT NULL,
            quantidade REAL NOT NULL,
            data TEXT NOT NULL,
            motivo TEXT,
            FOREIGN KEY (insumo_id) REFERENCES produtos(id)
        )''')

        # --- Tabela de Trocas/Devoluções ---
        cursor.execute('''CREATE TABLE IF NOT EXISTS trocas_devolucoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT NOT NULL,
            cliente TEXT,
            produto_devolvido_id INTEGER,
            produto_devolvido_nome TEXT,
            qtd_devolvida INTEGER DEFAULT 1,
            produto_novo_id INTEGER,
            produto_novo_nome TEXT,
            qtd_nova INTEGER DEFAULT 1,
            diferenca_valor REAL DEFAULT 0,
            tipo TEXT DEFAULT 'troca',
            observacao TEXT,
            FOREIGN KEY (produto_devolvido_id) REFERENCES produtos(id),
            FOREIGN KEY (produto_novo_id) REFERENCES produtos(id)
        )''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS servicos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT NOT NULL,
            cliente TEXT DEFAULT 'Consumidor Final',
            descricao TEXT NOT NULL,
            valor REAL NOT NULL,
            custo REAL DEFAULT 0,
            forma_pagamento TEXT DEFAULT 'Dinheiro',
            observacao TEXT,
            status TEXT DEFAULT 'Concluído'
        )''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS caixa (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_abertura TEXT NOT NULL,
            data_fechamento TEXT,
            valor_abertura REAL NOT NULL DEFAULT 0,
            valor_fechamento REAL,
            total_vendas REAL DEFAULT 0,
            total_dinheiro REAL DEFAULT 0,
            total_pix REAL DEFAULT 0,
            total_cartao REAL DEFAULT 0,
            diferenca REAL,
            observacao TEXT,
            status TEXT DEFAULT 'aberto'
        )''')

        # ============ TABELA AUDITORIA DE CAIXA ============
        cursor.execute('''CREATE TABLE IF NOT EXISTS auditoria_caixa (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            caixa_id INTEGER NOT NULL,
            data_hora TEXT NOT NULL,
            tipo_operacao TEXT NOT NULL,
            usuario TEXT DEFAULT 'sistema',
            venda_id INTEGER,
            forma_pagamento TEXT,
            valor REAL,
            descricao TEXT,
            valores_antes TEXT,
            valores_depois TEXT,
            FOREIGN KEY (caixa_id) REFERENCES caixa(id)
        )''')

        # Criar índice para auditoria
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_auditoria_caixa_id ON auditoria_caixa(caixa_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_auditoria_tipo ON auditoria_caixa(tipo_operacao)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_auditoria_data ON auditoria_caixa(data_hora)")

        cursor.execute('''CREATE TABLE IF NOT EXISTS vendedores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            horario_inicio TEXT DEFAULT '08:00',
            horario_fim TEXT DEFAULT '18:00',
            comissao REAL DEFAULT 0,
            ativo INTEGER DEFAULT 1
        )''')

        # ============ TABELAS RESTAURANTE (PDV) ============
        cursor.execute('''CREATE TABLE IF NOT EXISTS mesas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            numero INTEGER NOT NULL UNIQUE,
            capacidade INTEGER DEFAULT 4,
            status TEXT DEFAULT 'livre',
            area TEXT DEFAULT 'Salão'
        )''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS comandas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mesa_id INTEGER,
            tipo_atendimento TEXT DEFAULT 'mesa',
            cliente TEXT DEFAULT 'Consumidor Final',
            data_abertura TEXT NOT NULL,
            data_fechamento TEXT,
            status TEXT DEFAULT 'aberta',
            taxa_servico REAL DEFAULT 0,
            desconto REAL DEFAULT 0,
            total REAL DEFAULT 0,
            forma_pagamento TEXT,
            observacao TEXT,
            garcom TEXT DEFAULT '',
            FOREIGN KEY (mesa_id) REFERENCES mesas(id)
        )''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS comanda_itens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comanda_id INTEGER NOT NULL,
            produto_id INTEGER NOT NULL,
            produto_nome TEXT NOT NULL,
            quantidade INTEGER DEFAULT 1,
            preco_unitario REAL NOT NULL,
            observacao TEXT DEFAULT '',
            status TEXT DEFAULT 'Recebido',
            data_hora TEXT NOT NULL,
            FOREIGN KEY (comanda_id) REFERENCES comandas(id) ON DELETE CASCADE,
            FOREIGN KEY (produto_id) REFERENCES produtos(id)
        )''')

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_mesas_status ON mesas(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_comandas_status ON comandas(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_comandas_mesa ON comandas(mesa_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_comanda_itens_comanda ON comanda_itens(comanda_id)")

        # ============ MULTI-USUÁRIO / PERMISSÕES ============
        cursor.execute('''CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            login TEXT NOT NULL UNIQUE,
            senha_hash TEXT NOT NULL,
            perfil TEXT NOT NULL DEFAULT 'vendedor',
            ativo INTEGER DEFAULT 1,
            loja_id INTEGER DEFAULT NULL,
            data_criacao TEXT NOT NULL
        )''')
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_usuarios_login ON usuarios(login)")

        # ============ CONTAS A PAGAR ============
        cursor.execute('''CREATE TABLE IF NOT EXISTS contas_pagar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fornecedor_id INTEGER,
            descricao TEXT NOT NULL,
            valor REAL NOT NULL,
            data_emissao TEXT NOT NULL,
            data_vencimento TEXT NOT NULL,
            data_pagamento TEXT,
            status TEXT DEFAULT 'Pendente',
            categoria TEXT DEFAULT 'Outro',
            recorrente INTEGER DEFAULT 0,
            parcela_atual INTEGER DEFAULT 1,
            total_parcelas INTEGER DEFAULT 1,
            observacao TEXT,
            FOREIGN KEY (fornecedor_id) REFERENCES fornecedores(id)
        )''')
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_contas_pagar_status ON contas_pagar(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_contas_pagar_vencimento ON contas_pagar(data_vencimento)")

        # ============ MÚLTIPLAS LOJAS ============
        cursor.execute('''CREATE TABLE IF NOT EXISTS lojas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            endereco TEXT,
            telefone TEXT,
            cnpj TEXT,
            ativa INTEGER DEFAULT 1,
            data_criacao TEXT NOT NULL
        )''')

        # Migração: adicionar coluna loja_id em tabelas principais
        for tabela in ['produtos', 'vendas', 'gastos', 'entradas', 'servicos']:
            try:
                cursor.execute(f"PRAGMA table_info({tabela})")
                colunas = [c[1] for c in cursor.fetchall()]
                if 'loja_id' not in colunas:
                    cursor.execute(f"ALTER TABLE {tabela} ADD COLUMN loja_id INTEGER DEFAULT NULL")
            except sqlite3.DatabaseError:
                pass

        # Inserir loja padrão se vazia
        cursor.execute("SELECT COUNT(*) FROM lojas")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO lojas (nome, data_criacao) VALUES ('Loja Principal', ?)",
                          (datetime.datetime.now().isoformat(),))

        # Inserir admin padrão se vazia (com hash seguro PBKDF2)
        cursor.execute("SELECT COUNT(*) FROM usuarios")
        if cursor.fetchone()[0] == 0:
            from services.security import hash_password
            admin_hash = hash_password("admin9438")
            cursor.execute("""INSERT INTO usuarios (nome, login, senha_hash, perfil, data_criacao)
                             VALUES ('Administrador', 'admincentral', ?, 'admin', ?)""",
                          (admin_hash, datetime.datetime.now().isoformat()))

        # Inserir categorias padrão se tabela estiver vazia
        cursor.execute("SELECT COUNT(*) FROM categorias")
        if cursor.fetchone()[0] == 0:
            cats_padrao = [
                ('Produto Pronto', 'produto'), ('Material', 'produto'), ('Insumo', 'produto'), ('Outro', 'produto'),
                ('Material', 'gasto'), ('Transporte', 'gasto'), ('Serviços', 'gasto'), ('Aluguel', 'gasto'), ('Outro', 'gasto'),
            ]
            cursor.executemany("INSERT INTO categorias (nome, tipo) VALUES (?, ?)", cats_padrao)

        self.conn.commit()

    # --- Categorias Customizáveis ---
    def listar_categorias(self, tipo='produto'):
        """Retorna lista de nomes de categorias do tipo informado ('produto' ou 'gasto')."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT nome FROM categorias WHERE tipo=? ORDER BY nome", (tipo,))
        return [row[0] for row in cursor.fetchall()]

    def adicionar_categoria(self, nome, tipo='produto'):
        """Adiciona uma nova categoria se ainda não existir."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM categorias WHERE nome=? AND tipo=?", (nome, tipo))
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO categorias (nome, tipo) VALUES (?, ?)", (nome, tipo))
            self.conn.commit()
            return True
        return False

    def remover_categoria(self, nome, tipo='produto'):
        """Remove uma categoria (não remove as padrão se quiser, mas aqui remove qualquer)."""
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM categorias WHERE nome=? AND tipo=?", (nome, tipo))
        self.conn.commit()

    # --- Subcategorias ---
    def get_categoria_id(self, nome, tipo='produto'):
        """Retorna o id de uma categoria pelo nome e tipo."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT id FROM categorias WHERE nome=? AND tipo=?", (nome, tipo))
        row = cursor.fetchone()
        return row[0] if row else None

    def listar_subcategorias(self, categoria_nome, tipo='produto'):
        """Retorna lista de nomes de subcategorias de uma categoria."""
        cat_id = self.get_categoria_id(categoria_nome, tipo)
        if cat_id is None:
            return []
        cursor = self.conn.cursor()
        cursor.execute("SELECT nome FROM subcategorias WHERE categoria_id=? ORDER BY nome", (cat_id,))
        return [row[0] for row in cursor.fetchall()]

    def adicionar_subcategoria(self, categoria_nome, subcategoria_nome, tipo='produto'):
        """Adiciona uma subcategoria a uma categoria."""
        cat_id = self.get_categoria_id(categoria_nome, tipo)
        if cat_id is None:
            return False
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM subcategorias WHERE nome=? AND categoria_id=?", (subcategoria_nome, cat_id))
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO subcategorias (nome, categoria_id) VALUES (?, ?)", (subcategoria_nome, cat_id))
            self.conn.commit()
            return True
        return False

    def remover_subcategoria(self, categoria_nome, subcategoria_nome, tipo='produto'):
        """Remove uma subcategoria."""
        cat_id = self.get_categoria_id(categoria_nome, tipo)
        if cat_id is None:
            return
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM subcategorias WHERE nome=? AND categoria_id=?", (subcategoria_nome, cat_id))
        self.conn.commit()

    def get_subcategoria_id(self, categoria_nome, subcategoria_nome, tipo='produto'):
        """Retorna o id de uma subcategoria."""
        cat_id = self.get_categoria_id(categoria_nome, tipo)
        if cat_id is None:
            return None
        cursor = self.conn.cursor()
        cursor.execute("SELECT id FROM subcategorias WHERE nome=? AND categoria_id=?", (subcategoria_nome, cat_id))
        row = cursor.fetchone()
        return row[0] if row else None

    # --- Tamanhos ---
    def listar_tamanhos(self, categoria_nome, subcategoria_nome, tipo='produto'):
        """Retorna lista de tamanhos de uma subcategoria."""
        sub_id = self.get_subcategoria_id(categoria_nome, subcategoria_nome, tipo)
        if sub_id is None:
            return []
        cursor = self.conn.cursor()
        cursor.execute("SELECT nome FROM tamanhos WHERE subcategoria_id=? ORDER BY id", (sub_id,))
        return [row[0] for row in cursor.fetchall()]

    def adicionar_tamanho(self, categoria_nome, subcategoria_nome, tamanho_nome, tipo='produto'):
        """Adiciona um tamanho a uma subcategoria."""
        sub_id = self.get_subcategoria_id(categoria_nome, subcategoria_nome, tipo)
        if sub_id is None:
            return False
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM tamanhos WHERE nome=? AND subcategoria_id=?", (tamanho_nome, sub_id))
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO tamanhos (nome, subcategoria_id) VALUES (?, ?)", (tamanho_nome, sub_id))
            self.conn.commit()
            return True
        return False

    def remover_tamanho(self, categoria_nome, subcategoria_nome, tamanho_nome, tipo='produto'):
        """Remove um tamanho de uma subcategoria."""
        sub_id = self.get_subcategoria_id(categoria_nome, subcategoria_nome, tipo)
        if sub_id is None:
            return
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM tamanhos WHERE nome=? AND subcategoria_id=?", (tamanho_nome, sub_id))
        self.conn.commit()

    # --- Sub-Subcategorias ---
    def listar_sub_subcategorias(self, categoria_nome, subcategoria_nome, tipo='produto'):
        """Retorna lista de sub-subcategorias de uma subcategoria."""
        sub_id = self.get_subcategoria_id(categoria_nome, subcategoria_nome, tipo)
        if sub_id is None:
            return []
        cursor = self.conn.cursor()
        cursor.execute("SELECT nome FROM sub_subcategorias WHERE subcategoria_id=? ORDER BY nome", (sub_id,))
        return [row[0] for row in cursor.fetchall()]

    def adicionar_sub_subcategoria(self, categoria_nome, subcategoria_nome, sub_sub_nome, tipo='produto'):
        """Adiciona uma sub-subcategoria a uma subcategoria."""
        sub_id = self.get_subcategoria_id(categoria_nome, subcategoria_nome, tipo)
        if sub_id is None:
            return False
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sub_subcategorias WHERE nome=? AND subcategoria_id=?", (sub_sub_nome, sub_id))
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO sub_subcategorias (nome, subcategoria_id) VALUES (?, ?)", (sub_sub_nome, sub_id))
            self.conn.commit()
            return True
        return False

    def remover_sub_subcategoria(self, categoria_nome, subcategoria_nome, sub_sub_nome, tipo='produto'):
        """Remove uma sub-subcategoria."""
        sub_id = self.get_subcategoria_id(categoria_nome, subcategoria_nome, tipo)
        if sub_id is None:
            return
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM sub_subcategorias WHERE nome=? AND subcategoria_id=?", (sub_sub_nome, sub_id))
        self.conn.commit()

    # --- Configuração de Nota Personalizada ---
    def get_config_nota(self):
        """Retorna a configuração da nota personalizada."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM config_nota LIMIT 1")
        row = cursor.fetchone()
        if row:
            cols = [d[0] for d in cursor.description]
            return dict(zip(cols, row))
        return {
            'empresa_nome': 'Estoke Ao Cubo', 'empresa_email': '', 'empresa_telefone': '',
            'empresa_endereco': '', 'empresa_cnpj': '', 'empresa_logo': '', 'mensagem_rodape': 'Obrigado pela preferência!'
        }

    def salvar_config_nota(self, config):
        """Salva a configuração da nota personalizada."""
        cursor = self.conn.cursor()
        cursor.execute("""UPDATE config_nota SET 
            empresa_nome=?, empresa_email=?, empresa_telefone=?, empresa_endereco=?,
            empresa_cnpj=?, empresa_logo=?, mensagem_rodape=?
            WHERE id=1""", (
            config.get('empresa_nome', 'Estoke Ao Cubo'),
            config.get('empresa_email', ''),
            config.get('empresa_telefone', ''),
            config.get('empresa_endereco', ''),
            config.get('empresa_cnpj', ''),
            config.get('empresa_logo', ''),
            config.get('mensagem_rodape', 'Obrigado pela preferência!')
        ))
        self.conn.commit()

    # --- Estoque por Tamanho ---
    def atualizar_estoque_tamanhos(self, produto_id, estoque_p=0, estoque_m=0, estoque_g=0, estoque_gg=0):
        """Atualiza o estoque por tamanho de um produto."""
        cursor = self.conn.cursor()
        cursor.execute("UPDATE produtos SET estoque_p=?, estoque_m=?, estoque_g=?, estoque_gg=? WHERE id=?",
                       (estoque_p, estoque_m, estoque_g, estoque_gg, produto_id))
        self.conn.commit()

    def get_estoque_tamanhos(self, produto_id):
        """Retorna o estoque por tamanho de um produto."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT estoque_p, estoque_m, estoque_g, estoque_gg FROM produtos WHERE id=?", (produto_id,))
        row = cursor.fetchone()
        if row:
            return {'P': row[0] or 0, 'M': row[1] or 0, 'G': row[2] or 0, 'GG': row[3] or 0}
        return {'P': 0, 'M': 0, 'G': 0, 'GG': 0}

    # --- Ranking por Lucro e Vendedor ---
    def ranking_produtos_por_lucro(self, periodo="mes", limit=10):
        """Retorna ranking de produtos por lucro no período."""
        where = ""
        params = []
        hoje = datetime.date.today()
        if periodo == "mes":
            inicio = hoje.replace(day=1)
            where = " WHERE data_venda >= ? AND status='Concluída'"
            params.append(inicio.isoformat())
        elif periodo == "todos":
            where = " WHERE status='Concluída'"
        else:
            where = " WHERE status='Concluída'"

        query = f"""SELECT p.nome, p.tamanho, SUM(v.lucro) as lucro_total, SUM(v.quantidade) as total_vendido,
                    SUM(v.valor_total) as faturamento
                    FROM vendas v LEFT JOIN produtos p ON v.produto_id = p.id
                    {where} GROUP BY v.produto_id ORDER BY lucro_total DESC LIMIT ?"""
        params.append(limit)
        try:
            return pd.read_sql_query(query, self.conn, params=params)
        except Exception as e:
            logger.error("Erro ranking lucro: %s", e)
            return pd.DataFrame()

    def registrar_alteracao(self, acao, detalhe):
        cursor = self.conn.cursor()
        data_hora = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("INSERT INTO historico (data_hora, acao, detalhe) VALUES (?, ?, ?)",
                       (data_hora, acao, detalhe))
        self.conn.commit()

    def adicionar_produto(self, nome, quantidade, preco_venda, categoria=None, preco_compra=None, imagem=None, **kwargs):
        cursor = self.conn.cursor()
        if categoria is None: categoria = ''
        if preco_compra is None: preco_compra = 0
        if imagem is None: imagem = ''
        
        # Extract extra fields with defaults
        horas = kwargs.get('horas_trabalho', 0)
        v_hora = kwargs.get('valor_hora', 0)
        margem = kwargs.get('margem_lucro', 0.20)
        risco = kwargs.get('fator_risco', 0.10)
        raridade = kwargs.get('fator_raridade', 1.0)
        codigo_barras = kwargs.get('codigo_barras', None)
        validade = kwargs.get('data_validade', None)
        lote_serie = kwargs.get('lote_serie', '')
        loja_id = kwargs.get('loja_id', None)

        cursor.execute("""
            INSERT INTO produtos (nome, quantidade, preco, categoria, quantidade_inicial, 
                                horas_trabalho, valor_hora, margem_lucro, fator_risco, fator_raridade, 
                                preco_compra, imagem, data_validade, codigo_barras, lote_serie, loja_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            nome, quantidade, preco_venda, categoria, quantidade, 
            horas, v_hora, margem, risco, raridade, 
            preco_compra, imagem, validade, codigo_barras, lote_serie, loja_id
        ))
        self.conn.commit()
        self.registrar_alteracao("Adicionar Produto", f"Produto '{nome}' adicionado. Qtd: {quantidade}, Preço: {preco_venda}")

    def atualizar_produto(self, id_produto, nome, quantidade, preco, categoria, horas_trabalho=0, valor_hora=0, margem_lucro=0.20, fator_risco=0.10, fator_raridade=1.0, imagem=None, codigo_barras=None, lote_serie=None):
        cursor = self.conn.cursor()
        
        # Verificar se é um reabastecimento (quantidade aumentou significativamente)
        cursor.execute("SELECT quantidade, quantidade_inicial FROM produtos WHERE id=?", (id_produto,))
        row = cursor.fetchone()
        
        # Query base para update
        query_update = (
            "UPDATE produtos SET nome=?, quantidade=?, preco=?, categoria=?, "
            "horas_trabalho=?, valor_hora=?, margem_lucro=?, fator_risco=?, fator_raridade=?, imagem=?, codigo_barras=?, lote_serie=? "
            "WHERE id=?"
        )
        # Se for reabastecimento, atualizar também quantidade_inicial
        if row:
            _, qtd_inicial_antiga = row
            if quantidade > qtd_inicial_antiga:
                query_update = (
                    "UPDATE produtos SET nome=?, quantidade=?, preco=?, categoria=?, quantidade_inicial=?, "
                    "horas_trabalho=?, valor_hora=?, margem_lucro=?, fator_risco=?, fator_raridade=?, imagem=?, codigo_barras=?, lote_serie=? "
                    "WHERE id=?"
                )
                cursor.execute(query_update, (nome, quantidade, preco, categoria, quantidade, horas_trabalho, valor_hora, margem_lucro, fator_risco, fator_raridade, imagem, codigo_barras, lote_serie or '', id_produto))
            else:
                cursor.execute(query_update, (nome, quantidade, preco, categoria, horas_trabalho, valor_hora, margem_lucro, fator_risco, fator_raridade, imagem, codigo_barras, lote_serie or '', id_produto))
        else:
            cursor.execute(query_update, (nome, quantidade, preco, categoria, horas_trabalho, valor_hora, margem_lucro, fator_risco, fator_raridade, imagem, codigo_barras, lote_serie or '', id_produto))
        self.conn.commit()
        self.registrar_alteracao("Editar Produto", f"Produto ID {id_produto} atualizado para '{nome}'")

    def remover_produto(self, id_produto):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM produtos WHERE id=?", (id_produto,))
        self.conn.commit()
        self.registrar_alteracao("Remover Produto", f"Produto ID {id_produto} removido")

    def reabastecer_produto(self, id_produto, quantidade_adicional):
        if quantidade_adicional <= 0:
            return False
        cursor = self.conn.cursor()
        # Pegar quantidade atual e inicial
        cursor.execute("SELECT quantidade, quantidade_inicial FROM produtos WHERE id=?", (id_produto,))
        row = cursor.fetchone()
        if row:
            qtd_atual, qtd_inicial = row
            nova_qtd = qtd_atual + quantidade_adicional
            # Atualizar quantidade e quantidade_inicial (pois é um reabastecimento, o "novo 100%" sobe)
            nova_inicial = max(qtd_inicial, nova_qtd)
            cursor.execute("UPDATE produtos SET quantidade=?, quantidade_inicial=? WHERE id=?", (nova_qtd, nova_inicial, id_produto))
            self.conn.commit()
            self.registrar_alteracao("Reabastecer", f"Produto ID {id_produto} reabastecido (+{quantidade_adicional}). Nova Qtd: {nova_qtd}")
            return True
        return False

    def buscar_produto_por_codigo(self, codigo):
        cursor = self.conn.cursor()
        # Busca por código de barras
        cursor.execute("SELECT * FROM produtos WHERE codigo_barras = ?", (codigo,))
        
        logger.debug("Buscando produto com codigo_barras='%s'", codigo)

        # Retorna DataFrame para compatibilidade
        rows = cursor.fetchall()
        if not rows:
            logger.debug("Nenhum produto encontrado com codigo_barras='%s'", codigo)
            return pd.DataFrame()
        
        logger.debug("Produto encontrado: %s", rows[0])
        # Recuperar nome das colunas
        col_names = [description[0] for description in cursor.description]
        return pd.DataFrame(rows, columns=col_names)

    def listar_produtos(self):
        return pd.read_sql_query("SELECT * FROM produtos", self.conn)

    def adicionar_gasto(self, data, valor, categoria, descricao, tipo="Variável"):
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO gastos (data, valor, categoria, descricao, tipo) VALUES (?, ?, ?, ?, ?)",
                       (data, valor, categoria, descricao, tipo))
        self.conn.commit()
        self.registrar_alteracao("Adicionar Gasto", f"Gasto de R${valor} em '{categoria}' ({descricao}) - {tipo}")

    def atualizar_gasto(self, id_gasto, data, valor, categoria, descricao, tipo="Variável"):
        cursor = self.conn.cursor()
        cursor.execute("UPDATE gastos SET data=?, valor=?, categoria=?, descricao=?, tipo=? WHERE id=?",
                       (data, valor, categoria, descricao, tipo, id_gasto))
        self.conn.commit()
        self.registrar_alteracao("Editar Gasto", f"Gasto ID {id_gasto} atualizado")

    def remover_gasto(self, id_gasto):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM gastos WHERE id=?", (id_gasto,))
        self.conn.commit()
        self.registrar_alteracao("Remover Gasto", f"Gasto ID {id_gasto} removido")

    def adicionar_entrada(self, valor, descricao):
        cursor = self.conn.cursor()
        data = datetime.date.today().isoformat()
        cursor.execute("INSERT INTO entradas (data, valor, descricao) VALUES (?, ?, ?)",
                       (data, valor, descricao))
        self.conn.commit()
        self.registrar_alteracao("Adicionar Saldo", f"Entrada de R${valor} ({descricao})")

    def listar_entradas(self):
        return pd.read_sql_query("SELECT * FROM entradas", self.conn)

    def get_saldo_atual(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT SUM(valor) FROM entradas")
        total_entradas = cursor.fetchone()[0] or 0.0
        
        cursor.execute("SELECT SUM(valor) FROM gastos")
        total_gastos = cursor.fetchone()[0] or 0.0
        
        return total_entradas - total_gastos

    # --- Metas ---
    def adicionar_meta(self, titulo, descricao, status="Pendente", prazo=None):
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO metas (titulo, descricao, status, prazo) VALUES (?, ?, ?, ?)", (titulo, descricao, status, prazo))
        self.conn.commit()
        self.registrar_alteracao("Adicionar Meta", f"Meta '{titulo}' adicionada")

    def remover_meta(self, id_meta):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM metas WHERE id=?", (id_meta,))
        self.conn.commit()
        self.registrar_alteracao("Remover Meta", f"Meta ID {id_meta} removida")

    def atualizar_status_meta(self, id_meta, novo_status):
        cursor = self.conn.cursor()
        cursor.execute("UPDATE metas SET status=? WHERE id=?", (novo_status, id_meta))
        self.conn.commit()
        self.registrar_alteracao("Atualizar Meta", f"Meta ID {id_meta} status: {novo_status}")

    def atualizar_meta(self, id_meta, titulo, descricao, status, prazo=None):
        cursor = self.conn.cursor()
        cursor.execute("UPDATE metas SET titulo=?, descricao=?, status=?, prazo=? WHERE id=?", 
                     (titulo, descricao, status, prazo, id_meta))
        self.conn.commit()
        self.registrar_alteracao("Atualizar Meta", f"Meta '{titulo}' atualizada")

    def listar_metas(self):
        return pd.read_sql_query("SELECT * FROM metas", self.conn)

    # --- Fornecedores ---
    def adicionar_fornecedor(self, nome, contato, produto_fornecido):
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO fornecedores (nome, contato, produto_fornecido) VALUES (?, ?, ?)", (nome, contato, produto_fornecido))
        self.conn.commit()
        self.registrar_alteracao("Adicionar Fornecedor", f"Fornecedor '{nome}' adicionado")

    def remover_fornecedor(self, id_fornecedor):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM fornecedores WHERE id=?", (id_fornecedor,))
        self.conn.commit()
        self.registrar_alteracao("Remover Fornecedor", f"Fornecedor ID {id_fornecedor} removido")

    def listar_fornecedores(self):
        return pd.read_sql_query("SELECT * FROM fornecedores", self.conn)

    # --- Estabelecimento (Organização do Espaço de Trabalho) ---
    def adicionar_tarefa_estabelecimento(self, area_local, tarefa, prioridade="Média", status="Pendente"):
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO estabelecimento (area_local, tarefa, status, prioridade) VALUES (?, ?, ?, ?)", 
                       (area_local, tarefa, status, prioridade))
        self.conn.commit()
        self.registrar_alteracao("Adicionar Tarefa Estab.", f"Tarefa '{tarefa}' adicionada à área '{area_local}'")

    def criar_tarefas_estoque_baixo(self):
        """Cria tarefas automáticas para produtos abaixo do estoque mínimo (evita duplicatas)."""
        df_baixo = self.produtos_abaixo_estoque_minimo()
        if df_baixo.empty:
            return 0
        # Buscar tarefas existentes de estoque para evitar duplicatas
        df_tarefas = self.listar_tarefas_estabelecimento()
        tarefas_existentes = set()
        if not df_tarefas.empty:
            for _, t in df_tarefas.iterrows():
                if t['area_local'] == 'Estoque' and t['status'] != 'Concluído':
                    tarefas_existentes.add(t['tarefa'])
        count = 0
        for _, prod in df_baixo.iterrows():
            desc = f"Repor {prod['nome']} (atual: {prod['quantidade']}, mín: {prod['estoque_minimo']})"
            if desc not in tarefas_existentes:
                prioridade = "Alta" if prod['quantidade'] == 0 else "Média"
                self.adicionar_tarefa_estabelecimento("Estoque", desc, prioridade)
                count += 1
        return count

    def remover_tarefa_estabelecimento(self, id_tarefa):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM estabelecimento WHERE id=?", (id_tarefa,))
        self.conn.commit()
        self.registrar_alteracao("Remover Tarefa Estab.", f"Tarefa ID {id_tarefa} removida")

    def atualizar_status_estabelecimento(self, id_tarefa, novo_status):
        cursor = self.conn.cursor()
        cursor.execute("UPDATE estabelecimento SET status=? WHERE id=?", (novo_status, id_tarefa))
        self.conn.commit()
        self.registrar_alteracao("Atualizar Status Estab.", f"Tarefa ID {id_tarefa} marcada como '{novo_status}'")

    def atualizar_tarefa_estabelecimento(self, id_tarefa, area_local, tarefa, prioridade, status):
        cursor = self.conn.cursor()
        cursor.execute("UPDATE estabelecimento SET area_local=?, tarefa=?, prioridade=?, status=? WHERE id=?", 
                       (area_local, tarefa, prioridade, status, id_tarefa))
        self.conn.commit()
        self.registrar_alteracao("Atualizar Tarefa Estab.", f"Tarefa ID {id_tarefa} atualizada")

    def listar_tarefas_estabelecimento(self):
        return pd.read_sql_query(
            "SELECT * FROM estabelecimento ORDER BY CASE prioridade WHEN 'Alta' THEN 1 WHEN 'Média' THEN 2 WHEN 'Baixa' THEN 3 ELSE 4 END",
            self.conn)

    # --- Clientes ---
    def adicionar_cliente(self, nome, contato, observacoes):
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO clientes (nome, contato, observacoes) VALUES (?, ?, ?)", (nome, contato, observacoes))
        self.conn.commit()
        self.registrar_alteracao("Adicionar Cliente", f"Cliente '{nome}' adicionado")

    def remover_cliente(self, id_cliente):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM clientes WHERE id=?", (id_cliente,))
        self.conn.commit()
        self.registrar_alteracao("Remover Cliente", f"Cliente ID {id_cliente} removido")

    def listar_clientes(self):
        return pd.read_sql_query("SELECT * FROM clientes", self.conn)

    def atualizar_cliente(self, id_cliente, nome, contato, observacoes):
        cursor = self.conn.cursor()
        cursor.execute("UPDATE clientes SET nome=?, contato=?, observacoes=? WHERE id=?", 
                       (nome, contato, observacoes, id_cliente))
        self.conn.commit()
        self.registrar_alteracao("Editar Cliente", f"Cliente ID {id_cliente} atualizado")

    # --- Vendas (Baixa de Estoque) ---
    def registrar_venda(self, produto_id, quantidade, valor_total, data_venda=None):
        # Verificar se o caixa está aberto
        if not self.caixa_aberto():
            raise ValueError("Caixa fechado. Abra o caixa antes de registrar vendas.")

        if data_venda is None:
            data_venda = datetime.date.today().isoformat()
            
        cursor = self.conn.cursor()
        
        # 1. Verificar Estoque
        cursor.execute("SELECT nome, quantidade, quantidade_inicial FROM produtos WHERE id = ?", (produto_id,))
        prod = cursor.fetchone()
        if not prod:
            raise ValueError("Produto não encontrado")
        
        nome, qtd_atual, qtd_inicial = prod
        if qtd_atual < quantidade:
            raise ValueError(f"Estoque insuficiente. Disponível: {qtd_atual}")
        
        # 2. Atualizar Estoque (Baixa)
        nova_qtd = qtd_atual - quantidade
        cursor.execute("UPDATE produtos SET quantidade = ? WHERE id = ?", (nova_qtd, produto_id))
        
        # 3. Registrar Entrada (Saldo)
        cursor.execute("INSERT INTO entradas (data, valor, descricao) VALUES (?, ?, ?)", 
                       (data_venda, valor_total, f"Venda: {quantidade}x {nome}"))
        
        # 4. Registrar Histórico
        self.registrar_alteracao("Venda", f"Venda de {quantidade}un de {nome}. Total: R$ {valor_total:.2f}")
        
        # 5. Verificar Estoque Baixo (30% da quantidade inicial)
        # Se qtd_inicial for 0 ou None (migração falha), usar nova_qtd como base (evita erro div por zero)
        base_calculo = qtd_inicial if qtd_inicial and qtd_inicial > 0 else (nova_qtd + quantidade)
        if base_calculo > 0:
            porcentagem = nova_qtd / base_calculo
            if porcentagem <= 0.30:
                cursor.execute("SELECT id FROM estabelecimento WHERE tarefa LIKE ? AND status != 'Concluído'", (f"%{nome}%",))
                if not cursor.fetchone():
                    cursor.execute("INSERT INTO estabelecimento (area_local, tarefa, status, prioridade) VALUES (?, ?, ?, ?)", 
                                   ("Estoque", f"Repor {nome} (Estoque Baixo: {nova_qtd})", "Pendente", "Alta"))
                    self.registrar_alteracao("Alerta Estoque", f"Gerada tarefa de reposição para {nome}")

        self.conn.commit()

    def listar_gastos(self):
        return pd.read_sql_query("SELECT * FROM gastos", self.conn)

    def listar_historico(self):
        return pd.read_sql_query("SELECT * FROM historico ORDER BY id DESC LIMIT 50", self.conn)

    # --- Relatórios Avançados ---
    def buscar_por_id(self, tabela, id_busca):
        """Busca um registro pelo ID em uma tabela específica"""
        # Validação de segurança: impedir SQL injection no nome da tabela
        tabelas_permitidas = {'produtos', 'gastos', 'entradas', 'historico', 'metas', 
                              'fornecedores', 'clientes', 'estabelecimento', 'kits', 'kit_itens', 'vendas',
                              'fichas_tecnicas', 'ficha_tecnica_itens', 'producao_diaria', 'baixa_insumos', 'trocas_devolucoes', 'servicos'}
        if tabela not in tabelas_permitidas:
            logger.warning("Tentativa de acesso a tabela não permitida: %s", tabela)
            return pd.DataFrame()
        try:
            query = f"SELECT * FROM {tabela} WHERE id = ?"
            return pd.read_sql_query(query, self.conn, params=(id_busca,))
        except sqlite3.DatabaseError as e:
            logger.error("Erro ao buscar por ID: %s", e)
            return pd.DataFrame()

    def listar_transacoes_unificadas(self, periodo="todos", termo_busca=""):
        """
        Lista todas as transações (gastos e entradas/vendas) unificadas.
        Retorna DataFrame com colunas: id, data, tipo, descricao, valor
        """
        params = []
        where_clause = " WHERE 1=1"
        
        # Filtro de Data
        if periodo and periodo != "todos":
            hoje = datetime.date.today()
            inicio = None
            if periodo == "semana":
                inicio = hoje - datetime.timedelta(days=hoje.weekday())
            elif periodo == "mes":
                inicio = hoje.replace(day=1)
            elif periodo == "ano":
                inicio = hoje.replace(month=1, day=1)
            
            if inicio:
                where_clause += " AND data >= ?"
                params.append(inicio.isoformat())
                params.append(inicio.isoformat()) # Precisa duplicar pois será usado em 2 queries UNION

        # Filtro de Busca
        if termo_busca:
            where_clause += " AND descricao LIKE ?"
            params.append(f"%{termo_busca}%")
            params.append(f"%{termo_busca}%") # Duplicar para UNION

        query = f"""
            SELECT id, data, 'Gasto' as tipo, descricao, valor FROM gastos {where_clause}
            UNION ALL
            SELECT id, data, 'Venda' as tipo, descricao, valor FROM entradas {where_clause}
            ORDER BY data DESC
        """
        
        return pd.read_sql_query(query, self.conn, params=params)

    def listar_transacoes_filtradas(self, tabela, periodo="todos", termo_busca=""):
        """
        Lista transações (gastos ou entradas) com filtros de período e busca.
        periodo: 'todos', 'semana', 'mes', 'ano'
        termo_busca: ID ou parte da descrição
        """
        # Validação de segurança: impedir SQL injection no nome da tabela
        tabelas_permitidas = {'gastos', 'entradas'}
        if tabela not in tabelas_permitidas:
            logger.warning("Tentativa de acesso a tabela não permitida em filtro: %s", tabela)
            return pd.DataFrame()
        
        cursor = self.conn.cursor()
        
        query = f"SELECT * FROM {tabela} WHERE 1=1"
        params = []
        
        # Filtro de Data
        if periodo and periodo != "todos":
            hoje = datetime.date.today()
            inicio = None
            if periodo == "semana":
                inicio = hoje - datetime.timedelta(days=hoje.weekday())
            elif periodo == "mes":
                inicio = hoje.replace(day=1)
            elif periodo == "ano":
                inicio = hoje.replace(month=1, day=1)
            
            if inicio:
                query += " AND data >= ?"
                params.append(inicio.isoformat())
            
        # Filtro de Busca (ID ou Descrição/Categoria)
        if termo_busca:
            if termo_busca.isdigit():
                query += " AND id = ?"
                params.append(int(termo_busca))
            else:
                if tabela == 'gastos':
                    query += " AND (descricao LIKE ? OR categoria LIKE ?)"
                    params.extend([f"%{termo_busca}%", f"%{termo_busca}%"])
                else: # entradas
                    query += " AND descricao LIKE ?"
                    params.append(f"%{termo_busca}%")
                    
        query += " ORDER BY data DESC"
        
        return pd.read_sql_query(query, self.conn, params=params)

    # ==================== INSUMOS / MATÉRIA-PRIMA ====================

    def listar_insumos(self):
        """Retorna produtos do tipo 'insumo'."""
        return pd.read_sql_query("SELECT * FROM produtos WHERE tipo_produto='insumo'", self.conn)

    def listar_produtos_vendaveis(self):
        """Retorna produtos que NÃO são insumos (aparecem no PDV)."""
        return pd.read_sql_query("SELECT * FROM produtos WHERE tipo_produto != 'insumo'", self.conn)

    def registrar_baixa_insumo(self, insumo_id, quantidade, motivo="Uso diário"):
        """Registra baixa manual de insumo e atualiza estoque."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT nome, quantidade FROM produtos WHERE id=? AND tipo_produto='insumo'", (insumo_id,))
        row = cursor.fetchone()
        if not row:
            return False, "Insumo não encontrado."
        nome, qtd_atual = row
        if quantidade > qtd_atual:
            return False, f"Estoque insuficiente. Disponível: {qtd_atual}"
        data = datetime.date.today().isoformat()
        cursor.execute("INSERT INTO baixa_insumos (insumo_id, quantidade, data, motivo) VALUES (?, ?, ?, ?)",
                       (insumo_id, quantidade, data, motivo))
        cursor.execute("UPDATE produtos SET quantidade = quantidade - ? WHERE id=?", (quantidade, insumo_id))
        self.conn.commit()
        self.registrar_alteracao("Baixa Insumo", f"{quantidade} de '{nome}' usado ({motivo})")
        return True, f"Baixa de {quantidade} de '{nome}' registrada."

    def listar_baixas_insumos(self, dias=30):
        """Retorna baixas de insumos dos últimos N dias."""
        inicio = (datetime.date.today() - datetime.timedelta(days=dias)).isoformat()
        query = """SELECT bi.id, bi.data, bi.quantidade, bi.motivo, p.nome as insumo_nome
                   FROM baixa_insumos bi LEFT JOIN produtos p ON bi.insumo_id = p.id
                   WHERE bi.data >= ? ORDER BY bi.data DESC"""
        return pd.read_sql_query(query, self.conn, params=[inicio])

    # ==================== FICHAS TÉCNICAS (RECEITAS) ====================

    def adicionar_ficha_tecnica(self, produto_id, nome, rendimento, itens):
        """Cria uma ficha técnica vinculando insumos a um produto final.
        itens: lista de dicts {'insumo_id': int, 'quantidade': float}"""
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO fichas_tecnicas (produto_id, nome, rendimento) VALUES (?, ?, ?)",
                       (produto_id, nome, rendimento))
        ficha_id = cursor.lastrowid
        for item in itens:
            cursor.execute("INSERT INTO ficha_tecnica_itens (ficha_id, insumo_id, quantidade) VALUES (?, ?, ?)",
                           (ficha_id, item['insumo_id'], item['quantidade']))
        self.conn.commit()
        self.registrar_alteracao("Ficha Técnica", f"Ficha '{nome}' criada para produto ID {produto_id}")
        return ficha_id

    def listar_fichas_tecnicas(self):
        """Retorna todas as fichas técnicas com nome do produto."""
        query = """SELECT ft.id, ft.produto_id, ft.nome, ft.rendimento, p.nome as produto_nome
                   FROM fichas_tecnicas ft LEFT JOIN produtos p ON ft.produto_id = p.id
                   ORDER BY ft.nome"""
        return pd.read_sql_query(query, self.conn)

    def get_ficha_tecnica_itens(self, ficha_id):
        """Retorna os insumos de uma ficha técnica."""
        query = """SELECT fti.id, fti.insumo_id, fti.quantidade, p.nome as insumo_nome, 
                          p.quantidade as estoque_atual, p.unidade_medida
                   FROM ficha_tecnica_itens fti LEFT JOIN produtos p ON fti.insumo_id = p.id
                   WHERE fti.ficha_id = ?"""
        return pd.read_sql_query(query, self.conn, params=[ficha_id])

    def remover_ficha_tecnica(self, ficha_id):
        """Remove uma ficha técnica e seus itens."""
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM fichas_tecnicas WHERE id=?", (ficha_id,))
        self.conn.commit()
        self.registrar_alteracao("Remover Ficha", f"Ficha ID {ficha_id} removida")

    # ==================== PRODUÇÃO DIÁRIA ====================

    def registrar_producao(self, ficha_id, quantidade_produzida, observacao=""):
        """Registra produção: dá entrada no produto final e baixa nos insumos."""
        cursor = self.conn.cursor()
        # Buscar dados da ficha
        cursor.execute("SELECT produto_id, rendimento FROM fichas_tecnicas WHERE id=?", (ficha_id,))
        ficha = cursor.fetchone()
        if not ficha:
            return False, "Ficha técnica não encontrada."
        produto_id, rendimento = ficha

        # Buscar itens da ficha
        cursor.execute("SELECT insumo_id, quantidade FROM ficha_tecnica_itens WHERE ficha_id=?", (ficha_id,))
        itens = cursor.fetchall()

        # Calcular multiplicador (quantas vezes a receita)
        multiplicador = quantidade_produzida / rendimento if rendimento > 0 else quantidade_produzida

        # Verificar estoque dos insumos
        for insumo_id, qtd_base in itens:
            qtd_necessaria = qtd_base * multiplicador
            cursor.execute("SELECT nome, quantidade FROM produtos WHERE id=?", (insumo_id,))
            ins = cursor.fetchone()
            if not ins:
                return False, f"Insumo ID {insumo_id} não encontrado."
            if ins[1] < qtd_necessaria:
                return False, f"Insumo '{ins[0]}' insuficiente. Precisa: {qtd_necessaria:.2f}, Tem: {ins[1]}"

        # Dar baixa nos insumos
        for insumo_id, qtd_base in itens:
            qtd_necessaria = qtd_base * multiplicador
            cursor.execute("UPDATE produtos SET quantidade = quantidade - ? WHERE id=?", (qtd_necessaria, insumo_id))

        # Dar entrada no produto final
        cursor.execute("UPDATE produtos SET quantidade = quantidade + ? WHERE id=?", (quantidade_produzida, produto_id))

        # Registrar produção
        data = datetime.date.today().isoformat()
        cursor.execute("INSERT INTO producao_diaria (ficha_id, quantidade_produzida, data, observacao) VALUES (?, ?, ?, ?)",
                       (ficha_id, quantidade_produzida, data, observacao))
        self.conn.commit()

        cursor.execute("SELECT nome FROM produtos WHERE id=?", (produto_id,))
        prod_nome = cursor.fetchone()[0]
        self.registrar_alteracao("Produção", f"{quantidade_produzida}un de '{prod_nome}' produzidos (Ficha ID {ficha_id})")
        return True, f"{quantidade_produzida} unidades de '{prod_nome}' produzidas com sucesso!"

    def listar_producoes(self, dias=30):
        """Retorna produções dos últimos N dias."""
        inicio = (datetime.date.today() - datetime.timedelta(days=dias)).isoformat()
        query = """SELECT pd.id, pd.data, pd.quantidade_produzida, pd.observacao, 
                          ft.nome as ficha_nome, p.nome as produto_nome
                   FROM producao_diaria pd 
                   LEFT JOIN fichas_tecnicas ft ON pd.ficha_id = ft.id
                   LEFT JOIN produtos p ON ft.produto_id = p.id
                   WHERE pd.data >= ? ORDER BY pd.data DESC"""
        return pd.read_sql_query(query, self.conn, params=[inicio])

    # ==================== TROCAS / DEVOLUÇÕES ====================

    def registrar_troca(self, cliente, prod_devolvido_id, qtd_devolvida, prod_novo_id=None, qtd_nova=0, diferenca=0.0, tipo='troca', obs=""):
        """Registra troca/devolução. Ajusta estoque de ambos os produtos."""
        cursor = self.conn.cursor()
        data = datetime.datetime.now().isoformat()

        # Nome do produto devolvido
        cursor.execute("SELECT nome FROM produtos WHERE id=?", (prod_devolvido_id,))
        dev_row = cursor.fetchone()
        dev_nome = dev_row[0] if dev_row else "Desconhecido"

        # Devolver ao estoque
        cursor.execute("UPDATE produtos SET quantidade = quantidade + ? WHERE id=?", (qtd_devolvida, prod_devolvido_id))

        novo_nome = ""
        if tipo == 'troca' and prod_novo_id and qtd_nova > 0:
            # Verificar estoque do produto novo
            cursor.execute("SELECT nome, quantidade FROM produtos WHERE id=?", (prod_novo_id,))
            novo_row = cursor.fetchone()
            if not novo_row:
                return False, "Produto novo não encontrado."
            novo_nome = novo_row[0]
            if novo_row[1] < qtd_nova:
                return False, f"Estoque insuficiente do produto novo. Disponível: {novo_row[1]}"
            cursor.execute("UPDATE produtos SET quantidade = quantidade - ? WHERE id=?", (qtd_nova, prod_novo_id))

        cursor.execute("""INSERT INTO trocas_devolucoes 
                         (data, cliente, produto_devolvido_id, produto_devolvido_nome, qtd_devolvida,
                          produto_novo_id, produto_novo_nome, qtd_nova, diferenca_valor, tipo, observacao)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       (data, cliente, prod_devolvido_id, dev_nome, qtd_devolvida,
                        prod_novo_id, novo_nome, qtd_nova, diferenca, tipo, obs))

        # Se diferença > 0, cliente paga mais; se < 0, loja devolve
        if diferenca != 0:
            desc = f"{'Troca' if tipo == 'troca' else 'Devolução'}: {dev_nome}"
            if diferenca > 0:
                cursor.execute("INSERT INTO entradas (data, valor, descricao) VALUES (?, ?, ?)",
                               (data[:10], diferenca, desc))
            else:
                cursor.execute("INSERT INTO gastos (data, valor, categoria, descricao, tipo) VALUES (?, ?, ?, ?, ?)",
                               (data[:10], abs(diferenca), "Devolução", desc, "Variável"))
        self.conn.commit()
        self.registrar_alteracao("Troca/Devolução", f"{tipo.title()}: '{dev_nome}' → '{novo_nome}' (Cliente: {cliente})")
        return True, f"{'Troca' if tipo == 'troca' else 'Devolução'} registrada com sucesso!"

    def listar_trocas(self, dias=90):
        """Retorna trocas/devoluções dos últimos N dias."""
        inicio = (datetime.date.today() - datetime.timedelta(days=dias)).isoformat()
        return pd.read_sql_query(
            "SELECT * FROM trocas_devolucoes WHERE data >= ? ORDER BY data DESC",
            self.conn, params=[inicio])

    # ==================== LISTA DE REPOSIÇÃO ====================

    def gerar_lista_reposicao(self):
        """Gera lista de produtos abaixo do estoque mínimo para reposição."""
        query = """SELECT id, nome, quantidade, estoque_minimo, categoria, 
                          (estoque_minimo - quantidade) as qtd_repor
                   FROM produtos 
                   WHERE tipo_produto != 'insumo' AND quantidade <= estoque_minimo AND estoque_minimo > 0
                   ORDER BY qtd_repor DESC"""
        return pd.read_sql_query(query, self.conn)

    # ==================== PRODUTOS SOB ENCOMENDA ====================

    def listar_sob_encomenda(self):
        """Retorna produtos sob encomenda."""
        return pd.read_sql_query("SELECT * FROM produtos WHERE sob_encomenda=1", self.conn)

    # --- Serviços ---
    def registrar_servico(self, cliente, descricao, valor, custo=0, forma_pagamento='Dinheiro', observacao='', vendedor='', **kwargs):
        """Registra um serviço prestado (com suporte a OS)."""
        try:
            cursor = self.conn.cursor()
            data = datetime.datetime.now().isoformat()
            status_os = kwargs.get('status_os', 'Concluído')
            contato_cliente = kwargs.get('contato_cliente', '')
            equipamento = kwargs.get('equipamento', '')
            data_previsao = kwargs.get('data_previsao', '')
            cursor.execute(
                "INSERT INTO servicos (data, cliente, descricao, valor, custo, forma_pagamento, observacao, vendedor, status_os, contato_cliente, equipamento, data_previsao) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (data, cliente, descricao, valor, custo, forma_pagamento, observacao, vendedor, status_os, contato_cliente, equipamento, data_previsao)
            )
            servico_id = cursor.lastrowid
            # Registrar como entrada financeira
            self.adicionar_entrada(valor, f"Serviço: {descricao[:50]}")
            self.conn.commit()
            self.registrar_alteracao("Serviço", f"#{servico_id} | {descricao[:40]} | R$ {valor:.2f} | Cliente: {cliente}")
            return True, servico_id
        except Exception as e:
            logger.error("Erro ao registrar serviço: %s", e)
            return False, str(e)

    def listar_servicos(self, dias=90):
        """Lista serviços dos últimos N dias."""
        data_limite = (datetime.datetime.now() - datetime.timedelta(days=dias)).isoformat()
        return pd.read_sql_query(
            "SELECT * FROM servicos WHERE data >= ? ORDER BY data DESC",
            self.conn, params=[data_limite]
        )

    def atualizar_status_servico(self, servico_id, novo_status):
        """Atualiza status da Ordem de Serviço."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("UPDATE servicos SET status_os = ? WHERE id = ?", (novo_status, servico_id))
            self.conn.commit()
            self.registrar_alteracao("OS Status", f"Serviço #{servico_id} -> {novo_status}")
            return True
        except Exception as e:
            logger.error("Erro ao atualizar status OS: %s", e)
            return False

    def servicos_por_status(self, status):
        """Lista serviços filtrados por status_os."""
        return pd.read_sql_query(
            "SELECT * FROM servicos WHERE status_os = ? ORDER BY data DESC",
            self.conn, params=[status]
        )

    # ==================== CAIXA (ABERTURA/FECHAMENTO) ====================

    def abrir_caixa(self, valor_abertura, observacao=""):
        """Abre um novo caixa com valor inicial."""
        try:
            cursor = self.conn.cursor()
            # Verificar se já tem caixa aberto
            cursor.execute("SELECT id FROM caixa WHERE status = 'aberto'")
            if cursor.fetchone():
                return False, "Já existe um caixa aberto. Feche-o antes de abrir um novo."
            data = datetime.datetime.now().isoformat()
            cursor.execute(
                "INSERT INTO caixa (data_abertura, valor_abertura, observacao, status) VALUES (?, ?, ?, 'aberto')",
                (data, valor_abertura, observacao)
            )
            self.conn.commit()
            self.registrar_alteracao("Caixa", f"Aberto com R$ {valor_abertura:.2f}")
            return True, "Caixa aberto com sucesso."
        except Exception as e:
            logger.error("Erro ao abrir caixa: %s", e)
            return False, str(e)

    def resumo_caixa_atual(self):
        """Retorna as parciais do caixa atualmente aberto com breakdown detalhado por forma de pagamento."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT id, data_abertura, valor_abertura FROM caixa WHERE status = 'aberto'")
            row = cursor.fetchone()
            if not row:
                return None
            caixa_id, data_abertura, valor_abertura = row

            # Total geral de vendas
            cursor.execute(
                "SELECT COALESCE(SUM(valor_total - desconto), 0) FROM vendas WHERE caixa_id = ? AND status = 'Concluída'",
                (caixa_id,)
            )
            total_vendas = cursor.fetchone()[0]

            # Breakdown por forma de pagamento
            cursor.execute(
                "SELECT COALESCE(SUM(valor_total - desconto), 0) FROM vendas WHERE caixa_id = ? AND status = 'Concluída' AND forma_pagamento = 'Dinheiro'",
                (caixa_id,)
            )
            total_dinheiro = cursor.fetchone()[0]

            cursor.execute(
                "SELECT COALESCE(SUM(valor_total - desconto), 0) FROM vendas WHERE caixa_id = ? AND status = 'Concluída' AND forma_pagamento = 'PIX'",
                (caixa_id,)
            )
            total_pix = cursor.fetchone()[0]

            cursor.execute(
                "SELECT COALESCE(SUM(valor_total - desconto), 0) FROM vendas WHERE caixa_id = ? AND status = 'Concluída' AND forma_pagamento LIKE 'Cartão%'",
                (caixa_id,)
            )
            total_cartao = cursor.fetchone()[0]

            # Vendas a Fiado (informativo, não afeta caixa esperado)
            cursor.execute(
                "SELECT COALESCE(SUM(valor_total - desconto), 0) FROM vendas WHERE caixa_id = ? AND status = 'Concluída' AND forma_pagamento = 'Fiado'",
                (caixa_id,)
            )
            total_fiado = cursor.fetchone()[0]

            # Contar itens por forma de pagamento
            cursor.execute(
                "SELECT COUNT(*) FROM vendas WHERE caixa_id = ? AND status = 'Concluída' AND forma_pagamento = ?",
                (caixa_id, 'Dinheiro')
            )
            qtd_vendas_dinheiro = cursor.fetchone()[0]

            cursor.execute(
                "SELECT COUNT(*) FROM vendas WHERE caixa_id = ? AND status = 'Concluída' AND forma_pagamento = ?",
                (caixa_id, 'PIX')
            )
            qtd_vendas_pix = cursor.fetchone()[0]

            cursor.execute(
                "SELECT COUNT(*) FROM vendas WHERE caixa_id = ? AND status = 'Concluída' AND forma_pagamento LIKE 'Cartão%'",
                (caixa_id,)
            )
            qtd_vendas_cartao = cursor.fetchone()[0]

            # Cálculo do esperado (apenas considerando dinheiro e valor de abertura)
            esperado = valor_abertura + total_dinheiro
            
            return {
                "id": caixa_id,
                "data_abertura": data_abertura,
                "valor_abertura": valor_abertura,
                "total_vendas": total_vendas,
                "dinheiro": {
                    "total": total_dinheiro,
                    "quantidade": qtd_vendas_dinheiro,
                    "esperado": esperado
                },
                "pix": {
                    "total": total_pix,
                    "quantidade": qtd_vendas_pix
                },
                "cartao": {
                    "total": total_cartao,
                    "quantidade": qtd_vendas_cartao
                },
                "fiado": {
                    "total": total_fiado,
                    "quantidade": 0  # Será contado depois se necessário
                },
                "esperado_dinheiro": esperado  # Manter para compatibilidade com código antigo
            }
        except Exception as e:
            logger.error("Erro ao gerar resumo do caixa: %s", e)
            return None

    def fechar_caixa(self, valor_fechamento, observacao=""):
        """Fecha o caixa aberto, calculando totais, diferença e registrando em auditoria."""
        try:
            resumo = self.resumo_caixa_atual()
            if not resumo:
                return False, "Nenhum caixa aberto para fechar."
            
            caixa_id = resumo['id']
            esperado = resumo['esperado_dinheiro']
            diferenca = valor_fechamento - esperado

            data_fechamento = datetime.datetime.now().isoformat()
            cursor = self.conn.cursor()
            
            # Obter valores finalizados
            total_dinheiro = resumo['dinheiro']['total']
            total_pix = resumo['pix']['total']
            total_cartao = resumo['cartao']['total']
            total_vendas = resumo['total_vendas']
            
            # Atualizar tabela caixa
            cursor.execute(
                """UPDATE caixa SET data_fechamento=?, valor_fechamento=?, total_vendas=?,
                   total_dinheiro=?, total_pix=?, total_cartao=?, diferenca=?, observacao=?, status='fechado'
                   WHERE id=?""",
                (data_fechamento, valor_fechamento, total_vendas, 
                 total_dinheiro, total_pix, total_cartao, 
                 diferenca, observacao, caixa_id)
            )
            
            # Registrar fechamento em auditoria com detalhes
            valores_antes = f"Abertura: R$ {resumo['valor_abertura']:.2f}"
            valores_depois = f"Dinheiro: R$ {total_dinheiro:.2f}, PIX: R$ {total_pix:.2f}, Cartão: R$ {total_cartao:.2f}, Fiado: R$ {resumo['fiado']['total']:.2f}"
            
            cursor.execute('''INSERT INTO auditoria_caixa (caixa_id, data_hora, tipo_operacao, descricao, valores_antes, valores_depois)
                VALUES (?, ?, ?, ?, ?, ?)''',
                (caixa_id, data_fechamento, 'FECHAMENTO', 
                 f"Diferença: R$ {diferenca:.2f} | Observação: {observacao}", 
                 valores_antes, valores_depois))
            
            self.conn.commit()
            self.registrar_alteracao("Caixa", f"Fechado | Esperado: R$ {esperado:.2f} | Informado: R$ {valor_fechamento:.2f} | Diferença: R$ {diferenca:.2f}")
            
            resumo["informado"] = valor_fechamento
            resumo["esperado"] = esperado
            resumo["diferenca"] = diferenca
            return True, resumo
        except Exception as e:
            logger.error("Erro ao fechar caixa: %s", e)
            return False, str(e)

    def caixa_aberto(self):
        """Retorna dados do caixa aberto ou None."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM caixa WHERE status = 'aberto'")
        row = cursor.fetchone()
        if row:
            col_names = [d[0] for d in cursor.description]
            return dict(zip(col_names, row))
        return None

    def calcular_discrepancia_parcial(self, limite_alerta=10.0):
        """Calcula discrepâncias parciais durante o expediente do caixa aberto.
        
        Args:
            limite_alerta: Percentual de discrepância que gera alerta (padrão 10%)
        
        Returns:
            Dict com status de discrepâncias ou None se sem caixa aberto
        """
        try:
            resumo = self.resumo_caixa_atual()
            if not resumo:
                return None
            
            cursor = self.conn.cursor()
            caixa_id = resumo['id']
            
            # Identificar discrepâncias (faltam operações registradas?)
            discrepancias = {
                "caixa_id": caixa_id,
                "timestamp": datetime.datetime.now().isoformat(),
                "alertas": [],
                "status": "OK"
            }
            
            # Analisar: todas as vendas têm caixa_id?
            cursor.execute("""
                SELECT COUNT(*) FROM vendas 
                WHERE status = 'Concluída' 
                AND (caixa_id IS NULL OR caixa_id != ?)
                AND data_venda >= (SELECT data_abertura FROM caixa WHERE id = ?)
            """, (caixa_id, caixa_id))
            vendas_orfas = cursor.fetchone()[0]
            if vendas_orfas > 0:
                discrepancias["alertas"].append(f"⚠️ {vendas_orfas} vendas sem vinculação ao caixa atual")
                discrepancias["status"] = "ALERTA"
            
            # Comparar cada forma de pagamento
            total_dinheiro = resumo['dinheiro']['total']
            total_esperado_dinheiro = resumo['dinheiro']['esperado']
            
            if total_esperado_dinheiro > 0:
                percentual_diff = abs(total_dinheiro - total_esperado_dinheiro) / total_esperado_dinheiro * 100
                if percentual_diff > limite_alerta:
                    discrepancias["alertas"].append(
                        f"⚠️ Discrepância DINHEIRO: {percentual_diff:.1f}% | "
                        f"Esperado: R$ {total_esperado_dinheiro:.2f} | Vendas: R$ {total_dinheiro:.2f}"
                    )
                    discrepancias["status"] = "ALERTA"
            
            # Validar se há vendas simultâneas (possível indicador de erro)
            cursor.execute("""
                SELECT forma_pagamento, COUNT(*) as qtd 
                FROM vendas 
                WHERE caixa_id = ? AND status = 'Concluída'
                GROUP BY forma_pagamento
                HAVING qtd > 5
            """, (caixa_id,))
            vendas_por_forma = cursor.fetchall()
            if vendas_por_forma:
                for forma, qtd in vendas_por_forma:
                    discrepancias["alertas"].append(f"ℹ️ {qtd} vendas em {forma}")
            
            return discrepancias
        except Exception as e:
            logger.error("Erro ao calcular discrepância parcial: %s", e)
            return None

    def relatorio_discrepancia(self, caixa_id):
        """Gera relatório detalhado de discrepâncias para um caixa fechado.
        
        Args:
            caixa_id: ID do caixa a analisar
        
        Returns:
            Dict com análise detalhada das discrepâncias
        """
        try:
            cursor = self.conn.cursor()
            
            # Obter dados do caixa
            cursor.execute("SELECT * FROM caixa WHERE id = ?", (caixa_id,))
            col_names = [d[0] for d in cursor.description]
            row = cursor.fetchone()
            if not row:
                return None
            caixa = dict(zip(col_names, row))
            
            relatorio = {
                "caixa_id": caixa_id,
                "data_abertura": caixa['data_abertura'],
                "data_fechamento": caixa['data_fechamento'],
                "valor_abertura": caixa['valor_abertura'],
                "valor_fechamento": caixa['valor_fechamento'],
                "diferenca": caixa['diferenca'],
                "vendas_resumo": {
                    "total": {
                        "valor": caixa['total_vendas'],
                        "quantidade": 0
                    },
                    "dinheiro": {
                        "valor": caixa['total_dinheiro'],
                        "quantidade": 0,
                        "esperado": caixa['valor_abertura'] + caixa['total_dinheiro']
                    },
                    "pix": {
                        "valor": caixa['total_pix'],
                        "quantidade": 0
                    },
                    "cartao": {
                        "valor": caixa['total_cartao'],
                        "quantidade": 0
                    }
                },
                "vendas_por_forma": [],
                "auditoria_eventos": []
            }
            
            # Contar vendas por forma de pagamento
            cursor.execute("""
                SELECT forma_pagamento, COUNT(*) as qtd, SUM(valor_total - desconto) as total
                FROM vendas
                WHERE caixa_id = ? AND status = 'Concluída'
                GROUP BY forma_pagamento
            """, (caixa_id,))
            
            for forma, qtd, total in cursor.fetchall():
                relatorio["vendas_resumo"]["total"]["quantidade"] += qtd
                relatorio["vendas_por_forma"].append({
                    "forma": forma,
                    "quantidade": qtd,
                    "total": total
                })
                
                if forma == 'Dinheiro':
                    relatorio["vendas_resumo"]["dinheiro"]["quantidade"] = qtd
                elif forma == 'PIX':
                    relatorio["vendas_resumo"]["pix"]["quantidade"] = qtd
                elif forma.startswith('Cartão'):
                    relatorio["vendas_resumo"]["cartao"]["quantidade"] += qtd
            
            # Obter eventos de auditoria
            cursor.execute("""
                SELECT tipo_operacao, COUNT(*) as qtd, SUM(valor) as total
                FROM auditoria_caixa
                WHERE caixa_id = ?
                GROUP BY tipo_operacao
                ORDER BY tipo_operacao
            """, (caixa_id,))
            
            for tipo, qtd, total in cursor.fetchall():
                relatorio["auditoria_eventos"].append({
                    "tipo": tipo,
                    "quantidade": qtd,
                    "total": total
                })
            
            # Análise de discrepância
            esperado_dinheiro = relatorio["vendas_resumo"]["dinheiro"]["esperado"]
            informado = caixa['valor_fechamento']
            diferenca_abs = abs(caixa['diferenca'])
            
            if esperado_dinheiro > 0:
                pct_diff = (caixa['diferenca'] / esperado_dinheiro) * 100
            else:
                pct_diff = 0
            
            relatorio["analise"] = {
                "esperado_dinheiro": esperado_dinheiro,
                "valor_informado": informado,
                "diferenca_valor": caixa['diferenca'],
                "diferenca_percentual": round(pct_diff, 2),
                "status": "OK" if diferenca_abs <= 1.0 else ("ALERTA" if diferenca_abs <= 10.0 else "CRÍTICO")
            }
            
            return relatorio
        except Exception as e:
            logger.error("Erro ao gerar relatório de discrepância: %s", e)
            return None

    def historico_caixa(self, dias=30):
        """Retorna histórico de caixas fechados."""
        data_limite = (datetime.datetime.now() - datetime.timedelta(days=dias)).isoformat()
        return pd.read_sql_query(
            "SELECT * FROM caixa WHERE status = 'fechado' AND data_abertura >= ? ORDER BY data_abertura DESC",
            self.conn, params=[data_limite]
        )

    # ==================== PRODUTOS MAIS VENDIDOS ====================

    def obter_produtos_mais_vendidos(self, limite=50):
        """Retorna IDs dos produtos mais vendidos ordenados por frequência."""
        try:
            query = """SELECT produto_id, SUM(quantidade) as total_vendido
                       FROM vendas WHERE produto_id IS NOT NULL AND status = 'Concluída'
                       GROUP BY produto_id ORDER BY total_vendido DESC LIMIT ?"""
            df = pd.read_sql_query(query, self.conn, params=[limite])
            return df['produto_id'].tolist() if not df.empty else []
        except Exception:
            return []

    # ==================== VENDEDORES/FUNCIONÁRIOS ====================

    def adicionar_vendedor(self, nome, horario_inicio="08:00", horario_fim="18:00", comissao=0):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO vendedores (nome, horario_inicio, horario_fim, comissao) VALUES (?, ?, ?, ?)",
            (nome, horario_inicio, horario_fim, comissao)
        )
        self.conn.commit()
        self.registrar_alteracao("Vendedor", f"Adicionado: {nome} ({horario_inicio}-{horario_fim})")

    def listar_vendedores(self, apenas_ativos=True):
        filtro = "WHERE ativo = 1" if apenas_ativos else ""
        return pd.read_sql_query(f"SELECT * FROM vendedores {filtro} ORDER BY nome", self.conn)

    def atualizar_vendedor(self, vendedor_id, nome, horario_inicio, horario_fim, comissao):
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE vendedores SET nome=?, horario_inicio=?, horario_fim=?, comissao=? WHERE id=?",
            (nome, horario_inicio, horario_fim, comissao, vendedor_id)
        )
        self.conn.commit()

    def remover_vendedor(self, vendedor_id):
        cursor = self.conn.cursor()
        cursor.execute("UPDATE vendedores SET ativo = 0 WHERE id = ?", (vendedor_id,))
        self.conn.commit()
        self.registrar_alteracao("Vendedor", f"Desativado ID #{vendedor_id}")

    def vendedor_no_turno(self):
        """Retorna o vendedor cujo horário inclui a hora atual, ou '' se nenhum."""
        agora = datetime.datetime.now().strftime("%H:%M")
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT nome FROM vendedores WHERE ativo = 1 AND horario_inicio <= ? AND horario_fim > ? ORDER BY horario_inicio",
            (agora, agora)
        )
        row = cursor.fetchone()
        return row[0] if row else ""

    def vendas_por_vendedor(self, dias=30):
        """Retorna resumo de vendas agrupado por vendedor."""
        data_limite = (datetime.datetime.now() - datetime.timedelta(days=dias)).isoformat()
        return pd.read_sql_query(
            """SELECT vendedor, COUNT(*) as num_vendas, SUM(valor_total - desconto) as total_vendido,
               SUM(lucro) as total_lucro
               FROM vendas WHERE vendedor != '' AND vendedor IS NOT NULL
               AND data_venda >= ? AND status = 'Concluída'
               GROUP BY vendedor ORDER BY total_vendido DESC""",
            self.conn, params=[data_limite]
        )

    # ==================== RESTAURANTE (PDV) ====================

    def criar_mesas(self, quantidade, capacidade=4, area="Salão"):
        """Cria mesas em lote. Ignora números já existentes."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT COALESCE(MAX(numero), 0) FROM mesas")
        ultimo = cursor.fetchone()[0]
        criadas = 0
        for i in range(1, quantidade + 1):
            num = ultimo + i
            try:
                cursor.execute("INSERT INTO mesas (numero, capacidade, status, area) VALUES (?, ?, 'livre', ?)",
                               (num, capacidade, area))
                criadas += 1
            except sqlite3.IntegrityError:
                pass
        self.conn.commit()
        return criadas

    def listar_mesas(self):
        return pd.read_sql_query("SELECT * FROM mesas ORDER BY numero", self.conn)

    def atualizar_status_mesa(self, mesa_id, status):
        cursor = self.conn.cursor()
        cursor.execute("UPDATE mesas SET status = ? WHERE id = ?", (status, mesa_id))
        self.conn.commit()

    def remover_mesa(self, mesa_id):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM mesas WHERE id = ?", (mesa_id,))
        self.conn.commit()

    def abrir_comanda(self, mesa_id=None, tipo_atendimento="mesa", cliente="Consumidor Final", garcom=""):
        """Abre uma comanda. Para mesa, marca a mesa como ocupada."""
        cursor = self.conn.cursor()
        data = datetime.datetime.now().isoformat()
        if mesa_id and tipo_atendimento == "mesa":
            # Verificar se já existe comanda aberta nesta mesa
            cursor.execute("SELECT id FROM comandas WHERE mesa_id = ? AND status = 'aberta'", (mesa_id,))
            existente = cursor.fetchone()
            if existente:
                return existente[0]  # Retorna comanda já aberta
            cursor.execute("UPDATE mesas SET status = 'ocupada' WHERE id = ?", (mesa_id,))
        cursor.execute(
            "INSERT INTO comandas (mesa_id, tipo_atendimento, cliente, data_abertura, status, garcom) VALUES (?, ?, ?, ?, 'aberta', ?)",
            (mesa_id, tipo_atendimento, cliente, data, garcom))
        self.conn.commit()
        return cursor.lastrowid

    def adicionar_item_comanda(self, comanda_id, produto_id, produto_nome, quantidade, preco_unitario, observacao=""):
        cursor = self.conn.cursor()
        data = datetime.datetime.now().isoformat()
        cursor.execute(
            "INSERT INTO comanda_itens (comanda_id, produto_id, produto_nome, quantidade, preco_unitario, observacao, status, data_hora) VALUES (?, ?, ?, ?, ?, ?, 'Recebido', ?)",
            (comanda_id, produto_id, produto_nome, quantidade, preco_unitario, observacao, data))
        # Atualizar total da comanda
        self._recalcular_total_comanda(comanda_id)
        self.conn.commit()

    def remover_item_comanda(self, item_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT comanda_id FROM comanda_itens WHERE id = ?", (item_id,))
        row = cursor.fetchone()
        if row:
            comanda_id = row[0]
            cursor.execute("DELETE FROM comanda_itens WHERE id = ?", (item_id,))
            self._recalcular_total_comanda(comanda_id)
            self.conn.commit()

    def atualizar_status_item_comanda(self, item_id, status):
        cursor = self.conn.cursor()
        cursor.execute("UPDATE comanda_itens SET status = ? WHERE id = ?", (status, item_id))
        self.conn.commit()

    def listar_itens_comanda(self, comanda_id):
        return pd.read_sql_query(
            "SELECT * FROM comanda_itens WHERE comanda_id = ? ORDER BY data_hora",
            self.conn, params=[comanda_id])

    def _recalcular_total_comanda(self, comanda_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT COALESCE(SUM(quantidade * preco_unitario), 0) FROM comanda_itens WHERE comanda_id = ?",
                        (comanda_id,))
        subtotal = cursor.fetchone()[0]
        cursor.execute("UPDATE comandas SET total = ? WHERE id = ?", (subtotal, comanda_id))

    def get_comanda(self, comanda_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM comandas WHERE id = ?", (comanda_id,))
        row = cursor.fetchone()
        if row:
            cols = [d[0] for d in cursor.description]
            return dict(zip(cols, row))
        return None

    def get_comanda_por_mesa(self, mesa_id):
        """Retorna comanda aberta de uma mesa ou None."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM comandas WHERE mesa_id = ? AND status = 'aberta' ORDER BY id DESC LIMIT 1",
                        (mesa_id,))
        row = cursor.fetchone()
        if row:
            cols = [d[0] for d in cursor.description]
            return dict(zip(cols, row))
        return None

    def fechar_comanda(self, comanda_id, forma_pagamento="Dinheiro", taxa_servico=0, desconto=0, observacao=""):
        """Fecha a comanda, registra a venda e libera a mesa."""
        try:
            cursor = self.conn.cursor()
            comanda = self.get_comanda(comanda_id)
            if not comanda:
                return False, "Comanda não encontrada."
            if comanda['status'] != 'aberta':
                return False, "Comanda já está fechada."

            data_fechamento = datetime.datetime.now().isoformat()
            total_bruto = comanda['total']
            total_final = total_bruto + taxa_servico - desconto

            cursor.execute("""UPDATE comandas SET status='fechada', data_fechamento=?, forma_pagamento=?,
                             taxa_servico=?, desconto=?, total=?, observacao=? WHERE id=?""",
                          (data_fechamento, forma_pagamento, taxa_servico, desconto, total_final, observacao, comanda_id))

            # Liberar a mesa
            if comanda['mesa_id']:
                cursor.execute("UPDATE mesas SET status = 'livre' WHERE id = ?", (comanda['mesa_id'],))

            # Baixar estoque dos itens
            itens = self.listar_itens_comanda(comanda_id)
            for _, item in itens.iterrows():
                cursor.execute("UPDATE produtos SET quantidade = MAX(0, quantidade - ?) WHERE id = ?",
                              (item['quantidade'], item['produto_id']))

            # Registrar venda
            cursor.execute(
                "INSERT INTO vendas (quantidade, valor_total, data_venda, lucro, forma_pagamento, cliente, status, desconto, vendedor) VALUES (?, ?, ?, ?, ?, ?, 'Concluída', ?, ?)",
                (len(itens), total_final, data_fechamento, 0, forma_pagamento,
                 comanda.get('cliente', 'Consumidor Final'), desconto, comanda.get('garcom', '')))

            # Registrar entrada financeira
            cursor.execute("INSERT INTO entradas (data, valor, descricao) VALUES (?, ?, ?)",
                          (data_fechamento[:10], total_final, f"Comanda #{comanda_id}"))

            self.conn.commit()
            self.registrar_alteracao("Fechar Comanda",
                f"Comanda #{comanda_id} | Total: R$ {total_final:.2f} | Pgto: {forma_pagamento}")
            return True, f"Comanda #{comanda_id} fechada. Total: R$ {total_final:.2f}"
        except Exception as e:
            logger.error("Erro ao fechar comanda: %s", e)
            return False, str(e)

    def listar_comandas_abertas(self):
        return pd.read_sql_query(
            """SELECT c.*, m.numero as mesa_numero FROM comandas c
               LEFT JOIN mesas m ON c.mesa_id = m.id
               WHERE c.status = 'aberta' ORDER BY c.data_abertura""", self.conn)

    def listar_comandas_historico(self, dias=7):
        limite = (datetime.date.today() - datetime.timedelta(days=dias)).isoformat()
        return pd.read_sql_query(
            """SELECT c.*, m.numero as mesa_numero FROM comandas c
               LEFT JOIN mesas m ON c.mesa_id = m.id
               WHERE c.data_abertura >= ? ORDER BY c.data_abertura DESC""",
            self.conn, params=[limite])

    def itens_cozinha_pendentes(self):
        """Retorna itens que estão pendentes na cozinha (Recebido ou Preparando)."""
        return pd.read_sql_query(
            """SELECT ci.*, c.mesa_id, m.numero as mesa_numero, c.tipo_atendimento
               FROM comanda_itens ci
               JOIN comandas c ON ci.comanda_id = c.id
               LEFT JOIN mesas m ON c.mesa_id = m.id
               WHERE ci.status IN ('Recebido', 'Preparando') AND c.status = 'aberta'
               ORDER BY ci.data_hora""", self.conn)

    def dividir_conta(self, comanda_id, num_pessoas):
        """Calcula divisão da conta."""
        comanda = self.get_comanda(comanda_id)
        if not comanda:
            return None
        total = comanda['total'] + comanda.get('taxa_servico', 0) - comanda.get('desconto', 0)
        valor_por_pessoa = total / max(num_pessoas, 1)
        return {"total": total, "pessoas": num_pessoas, "valor_por_pessoa": round(valor_por_pessoa, 2)}

    def close(self):
        conn_closed = False
        try:
            self.conn.close()
            conn_closed = True
            logger.info("Conexão com banco de dados fechada.")
        except Exception as e:
            logger.error("Erro ao fechar conexão: %s", e)

        if not conn_closed:
            # Forçar checkpoint WAL e tentar fechar novamente
            try:
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self.conn.close()
                conn_closed = True
            except Exception:
                pass

        # Criptografar o banco ao fechar
        try:
            if os.path.exists(self.db_path):
                _encrypt_file(self.db_path, self.enc_path, self._enc_key)
                # Verificar que o .enc foi realmente criado antes de remover plaintext
                if os.path.exists(self.enc_path) and os.path.getsize(self.enc_path) > 0:
                    os.remove(self.db_path)
                    # Remover também arquivos WAL/SHM do SQLite se existirem
                    for suffix in ("-wal", "-shm"):
                        wal_path = self.db_path + suffix
                        if os.path.exists(wal_path):
                            os.remove(wal_path)
                    logger.info("Banco de dados criptografado com sucesso.")
                else:
                    logger.error("Criptografia gerou arquivo vazio — mantendo plaintext.")
        except Exception as e:
            logger.error("Erro ao criptografar banco de dados: %s", e)

    # --- Backup Automático ---
    def criar_backup(self):
        """Cria backup criptografado do banco de dados com timestamp. Mantém últimos 5 backups."""
        try:
            backup_dir = os.path.join(self.data_dir, "backups")
            os.makedirs(backup_dir, exist_ok=True)
            
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_plain = os.path.join(backup_dir, f"estoque_backup_{timestamp}.db")
            backup_enc = backup_plain + ".enc"
            
            # Usar backup API do SQLite para cópia segura
            backup_conn = sqlite3.connect(backup_plain)
            try:
                self.conn.backup(backup_conn)
            finally:
                backup_conn.close()
            
            # Criptografar o backup e remover plaintext
            _encrypt_file(backup_plain, backup_enc, self._enc_key)
            if os.path.exists(backup_enc) and os.path.getsize(backup_enc) > 0:
                os.remove(backup_plain)
            else:
                logger.error("Criptografia do backup falhou — mantendo plaintext.")
                return True, backup_plain
            
            logger.info("Backup criptografado criado: %s", backup_enc)
            
            # Manter apenas os últimos 5 backups
            backups = sorted([
                f for f in os.listdir(backup_dir) 
                if f.startswith("estoque_backup_") and f.endswith(".db.enc")
            ])
            while len(backups) > 5:
                oldest = backups.pop(0)
                try:
                    os.remove(os.path.join(backup_dir, oldest))
                    logger.info("Backup antigo removido: %s", oldest)
                except OSError as e:
                    logger.warning("Não foi possível remover backup antigo %s: %s", oldest, e)
            
            # Limpar backups plaintext legados (.db sem .enc)
            for f in list(os.listdir(backup_dir)):
                if f.startswith("estoque_backup_") and f.endswith(".db") and not f.endswith(".db.enc"):
                    try:
                        legacy_path = os.path.join(backup_dir, f)
                        _encrypt_file(legacy_path, legacy_path + ".enc", self._enc_key)
                        if os.path.exists(legacy_path + ".enc") and os.path.getsize(legacy_path + ".enc") > 0:
                            os.remove(legacy_path)
                            logger.info("Backup legado migrado para criptografado: %s", f)
                    except Exception as e:
                        logger.warning("Erro ao migrar backup legado %s: %s", f, e)
            
            return True, backup_enc
        except Exception as e:
            logger.error("Erro ao criar backup: %s", e)
            return False, str(e)

    def verificar_backup_diario(self):
        """Verifica se já existe backup de hoje. Se não, cria automaticamente."""
        try:
            backup_dir = os.path.join(self.data_dir, "backups")
            if not os.path.exists(backup_dir):
                return self.criar_backup()
            
            hoje = datetime.date.today().strftime("%Y%m%d")
            backups_hoje = [
                f for f in os.listdir(backup_dir)
                if f.startswith(f"estoque_backup_{hoje}")
            ]
            
            if not backups_hoje:
                return self.criar_backup()
            return True, "Backup de hoje já existe."
        except Exception as e:
            logger.error("Erro ao verificar backup: %s", e)
            return False, str(e)

    def restaurar_backup(self, backup_path):
        """Restaura banco de dados a partir de um arquivo de backup (criptografado ou legado)."""
        try:
            if not os.path.exists(backup_path):
                return False, "Arquivo de backup não encontrado."
            
            # Criar backup de segurança antes de restaurar
            self.criar_backup()
            
            # Fechar conexão atual
            self.conn.close()
            
            # Guardar cópia de segurança do db atual caso restauração falhe
            db_backup_tmp = self.db_path + ".restore_tmp"
            if os.path.exists(self.db_path):
                shutil.copy2(self.db_path, db_backup_tmp)
            
            try:
                if backup_path.endswith(".db.enc"):
                    _decrypt_file(backup_path, self.db_path, self._enc_key)
                else:
                    shutil.copy2(backup_path, self.db_path)
                
                # Verificar integridade do banco restaurado
                test_conn = sqlite3.connect(self.db_path)
                test_conn.execute("PRAGMA integrity_check")
                test_conn.close()
            except Exception as e:
                # Restaurar cópia de segurança
                logger.error("Falha na restauração, revertendo: %s", e)
                if os.path.exists(db_backup_tmp):
                    shutil.copy2(db_backup_tmp, self.db_path)
                self.conn = sqlite3.connect(self.db_path)
                return False, f"Erro ao restaurar: {e}"
            finally:
                # Limpar arquivo temporário
                if os.path.exists(db_backup_tmp):
                    os.remove(db_backup_tmp)
            
            # Reconectar
            self.conn = sqlite3.connect(self.db_path)
            
            logger.info("Backup restaurado de: %s", backup_path)
            return True, "Backup restaurado com sucesso! Reinicie o aplicativo para aplicar todas as alterações."
        except Exception as e:
            logger.error("Erro ao restaurar backup: %s", e)
            # Tentar reconectar
            try:
                if not hasattr(self, 'conn') or self.conn is None:
                    self.conn = sqlite3.connect(self.db_path)
            except Exception:
                pass
            return False, str(e)

    def listar_backups(self):
        """Lista todos os backups disponíveis (criptografados e legados) com data e tamanho."""
        backup_dir = os.path.join(self.data_dir, "backups")
        if not os.path.exists(backup_dir):
            return []
        backups = []
        for f in sorted(os.listdir(backup_dir), reverse=True):
            if f.startswith("estoque_backup_") and (f.endswith(".db.enc") or f.endswith(".db")):
                path = os.path.join(backup_dir, f)
                size_mb = os.path.getsize(path) / (1024 * 1024)
                # Extrair data do nome
                try:
                    date_str = f.replace("estoque_backup_", "").replace(".db.enc", "").replace(".db", "")
                    dt = datetime.datetime.strptime(date_str, "%Y%m%d_%H%M%S")
                    data_fmt = dt.strftime("%d/%m/%Y %H:%M:%S")
                except:
                    data_fmt = f
                criptografado = "Sim" if f.endswith(".db.enc") else "Não"
                backups.append({"nome": f, "path": path, "data": data_fmt, "tamanho": f"{size_mb:.1f} MB", "criptografado": criptografado})
        return backups

    def produtos_estoque_baixo(self, limite=None):
        """Lista produtos com estoque abaixo do limite configurável."""
        if limite is None:
            limite = 5  # Padrão
        return pd.read_sql_query(
            "SELECT id, nome, quantidade, preco, categoria FROM produtos WHERE quantidade <= ? AND quantidade >= 0 ORDER BY quantidade ASC",
            self.conn, params=(limite,))

    def set_estoque_minimo(self, produto_id, valor):
        """Define o estoque mínimo para um produto específico."""
        cursor = self.conn.cursor()
        cursor.execute("UPDATE produtos SET estoque_minimo = ? WHERE id = ?", (valor, produto_id))
        self.conn.commit()

    def produtos_abaixo_estoque_minimo(self):
        """Lista produtos cuja quantidade está abaixo do estoque mínimo configurado."""
        return pd.read_sql_query(
            "SELECT id, nome, quantidade, preco, categoria, estoque_minimo FROM produtos WHERE quantidade <= estoque_minimo AND quantidade >= 0 ORDER BY quantidade ASC",
            self.conn)

    def exportar_relatorio_pdf(self, tipo="vendas", periodo="mes"):
        """Exporta relatório de vendas/estoque em formato PDF usando pdfplumber/reportlab ou texto formatado."""
        try:
            from datetime import timedelta
            hoje = datetime.date.today()
            
            if periodo == "hoje":
                inicio = hoje
            elif periodo == "semana":
                inicio = hoje - timedelta(days=hoje.weekday())
            elif periodo == "mes":
                inicio = hoje.replace(day=1)
            elif periodo == "ano":
                inicio = hoje.replace(month=1, day=1)
            else:
                inicio = None
            
            if tipo == "vendas":
                query = "SELECT id, data_venda, quantidade, valor_total, lucro, forma_pagamento, cliente, status FROM vendas WHERE status != 'Cancelada'"
                params = []
                if inicio:
                    query += " AND data_venda >= ?"
                    params.append(inicio.isoformat())
                query += " ORDER BY data_venda DESC"
                df = pd.read_sql_query(query, self.conn, params=params)
                titulo = f"Relatório de Vendas - {periodo.title()}"
            elif tipo == "estoque":
                df = pd.read_sql_query("SELECT id, nome, quantidade, preco, categoria, preco_compra FROM produtos ORDER BY nome", self.conn)
                titulo = "Relatório de Estoque"
            elif tipo == "financeiro":
                df_e = pd.read_sql_query("SELECT data, valor, descricao, 'Entrada' as tipo FROM entradas ORDER BY data DESC LIMIT 100", self.conn)
                df_g = pd.read_sql_query("SELECT data, valor, descricao, categoria as tipo FROM gastos ORDER BY data DESC LIMIT 100", self.conn)
                df = pd.concat([df_e, df_g]).sort_values('data', ascending=False)
                titulo = f"Relatório Financeiro - {periodo.title()}"
            else:
                return False, "Tipo de relatório inválido."
            
            # Gerar arquivo de relatório em texto formatado (compatível sem reportlab)
            report_dir = os.path.join(self.data_dir, "relatorios")
            os.makedirs(report_dir, exist_ok=True)
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = os.path.join(report_dir, f"relatorio_{tipo}_{timestamp}.html")
            
            with open(filepath, 'w', encoding='utf-8') as f:
                # Gerar HTML estilizado para melhor apresentação
                f.write("<!DOCTYPE html>\n<html lang='pt-BR'>\n<head>\n<meta charset='UTF-8'>\n")
                f.write(f"<title>{titulo}</title>\n")
                f.write("<style>\n")
                f.write("body{font-family:Arial,sans-serif;margin:40px;background:#f5f5f5;color:#333}\n")
                f.write(".container{max-width:900px;margin:0 auto;background:white;padding:30px;border-radius:8px;box-shadow:0 2px 10px rgba(0,0,0,0.1)}\n")
                f.write("h1{color:#0A4174;border-bottom:3px solid #0A4174;padding-bottom:10px;font-size:22px}\n")
                f.write(".meta{color:#666;font-size:13px;margin-bottom:20px}\n")
                f.write("table{width:100%;border-collapse:collapse;margin:20px 0}\n")
                f.write("th{background:#0A4174;color:white;padding:10px;text-align:left;font-size:13px}\n")
                f.write("td{padding:8px 10px;border-bottom:1px solid #eee;font-size:12px}\n")
                f.write("tr:nth-child(even){background:#f9f9f9}\n")
                f.write("tr:hover{background:#e8f4fd}\n")
                f.write(".summary{background:#f0f7ff;padding:15px;border-radius:6px;margin-top:20px}\n")
                f.write(".summary b{color:#0A4174}\n")
                f.write(".footer{text-align:center;color:#999;font-size:11px;margin-top:30px;border-top:1px solid #eee;padding-top:15px}\n")
                f.write("@media print{body{margin:0;background:white}.container{box-shadow:none}}\n")
                f.write("</style>\n</head>\n<body>\n<div class='container'>\n")
                f.write(f"<h1>📊 {titulo}</h1>\n")
                f.write(f"<p class='meta'>Gerado em: {datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</p>\n")
                
                if df.empty:
                    f.write("<p>Nenhum dado encontrado para o período selecionado.</p>\n")
                else:
                    cols = df.columns.tolist()
                    f.write("<table>\n<thead><tr>\n")
                    for c in cols:
                        f.write(f"<th>{c}</th>\n")
                    f.write("</tr></thead>\n<tbody>\n")
                    
                    for _, row in df.iterrows():
                        f.write("<tr>")
                        for c in cols:
                            val = row[c]
                            if isinstance(val, float):
                                f.write(f"<td>{val:.2f}</td>")
                            else:
                                f.write(f"<td>{val}</td>")
                        f.write("</tr>\n")
                    
                    f.write("</tbody>\n</table>\n")
                    
                    f.write("<div class='summary'>\n")
                    f.write(f"<p><b>Total de registros:</b> {len(df)}</p>\n")
                    
                    if tipo == "vendas" and 'valor_total' in df.columns:
                        f.write(f"<p><b>Faturamento Total:</b> R$ {df['valor_total'].sum():.2f}</p>\n")
                        if 'lucro' in df.columns:
                            f.write(f"<p><b>Lucro Total:</b> R$ {df['lucro'].sum():.2f}</p>\n")
                    elif tipo == "estoque" and 'preco' in df.columns:
                        valor_estoque = (df['quantidade'] * df['preco']).sum()
                        f.write(f"<p><b>Valor Total em Estoque:</b> R$ {valor_estoque:.2f}</p>\n")
                    f.write("</div>\n")
                
                f.write("<div class='footer'>EstokeAoCubo - Sistema de Gestão Inteligente</div>\n")
                f.write("</div>\n</body>\n</html>\n")
            
            return True, filepath
        except Exception as e:
            logger.error("Erro ao exportar relatório: %s", e)
            return False, str(e)

    # --- Vendas Detalhadas ---
    def registrar_venda_detalhada(self, itens, total, desconto=0.0, cliente="Consumidor Final", forma_pagamento="Dinheiro", vendedor=""):
        """Registra venda detalhada na tabela vendas com itens individuais e vincula ao caixa aberto."""
        try:
            # Verificar se o caixa está aberto e obter ID
            caixa_aberto = self.caixa_aberto()
            if not caixa_aberto:
                return False, "Caixa fechado. Abra o caixa antes de registrar vendas."

            cursor = self.conn.cursor()
            caixa_id = caixa_aberto['id']
            data_venda = datetime.datetime.now().isoformat()
            
            # Desconto proporcional por item (evita duplicar o valor total do desconto em cada linha)
            total_bruto = sum(item['preco'] * item['qtd'] for item in itens)
            venda_ids = []  # Rastrear IDs das vendas para auditoria
            
            for item in itens:
                produto_id = item.get('id')
                qtd = item['qtd']
                valor_item = item['preco'] * qtd
                # Distribuir desconto proporcionalmente ao valor de cada item
                desconto_item = (valor_item / total_bruto * desconto) if total_bruto > 0 else 0
                lucro = (item['preco'] - item.get('preco_compra', 0)) * qtd - desconto_item
                cursor.execute(
                    "INSERT INTO vendas (produto_id, quantidade, valor_total, data_venda, lucro, forma_pagamento, cliente, status, desconto, vendedor, caixa_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (produto_id, qtd, valor_item, data_venda, lucro, forma_pagamento, cliente, 'Concluída', round(desconto_item, 2), vendedor, caixa_id)
                )
                venda_ids.append(cursor.lastrowid)

                # Baixa de estoque
                if produto_id:
                    cursor.execute("SELECT nome, quantidade, quantidade_inicial FROM produtos WHERE id = ?", (produto_id,))
                    prod = cursor.fetchone()
                    if prod:
                        nome_prod, qtd_atual, qtd_inicial = prod
                        nova_qtd = max(0, qtd_atual - qtd)
                        cursor.execute("UPDATE produtos SET quantidade = ? WHERE id = ?", (nova_qtd, produto_id))

                        # Verificar estoque baixo (30%)
                        base = qtd_inicial if qtd_inicial and qtd_inicial > 0 else (nova_qtd + qtd)
                        if base > 0 and (nova_qtd / base) <= 0.30:
                            cursor.execute("SELECT id FROM estabelecimento WHERE tarefa LIKE ? AND status != 'Concluído'", (f"%{nome_prod}%",))
                            if not cursor.fetchone():
                                cursor.execute("INSERT INTO estabelecimento (area_local, tarefa, status, prioridade) VALUES (?, ?, ?, ?)",
                                    ("Estoque", f"Repor {nome_prod} (Estoque Baixo: {nova_qtd})", "Pendente", "Alta"))

            # Registrar entrada (saldo)
            cursor.execute("INSERT INTO entradas (data, valor, descricao) VALUES (?, ?, ?)",
                (data_venda[:10], total - desconto, f"Venda Bot: {len(itens)} itens | {cliente} | {forma_pagamento}"))
            
            # Atualizar compras_recorrentes do cliente
            if cliente != "Consumidor Final":
                cursor.execute("UPDATE clientes SET compras_recorrentes = compras_recorrentes + 1 WHERE nome = ?", (cliente,))
                # Atualizar fidelidade (1 ponto a cada R$10)
                pontos_ganhos = int(total // 10)
                if pontos_ganhos > 0:
                    cursor.execute("SELECT id FROM clientes WHERE nome = ?", (cliente,))
                    c_row = cursor.fetchone()
                    if c_row:
                        cursor.execute('''INSERT INTO fidelidade (cliente_id, pontos, total_acumulado, data_ultima_compra)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(cliente_id) DO UPDATE SET
                                pontos = pontos + excluded.pontos,
                                total_acumulado = total_acumulado + excluded.total_acumulado,
                                data_ultima_compra = excluded.data_ultima_compra''',
                            (c_row[0], pontos_ganhos, total, data_venda))
            
            # Registrar em AUDITORIA_CAIXA para cada venda
            for venda_id in venda_ids:
                cursor.execute('''INSERT INTO auditoria_caixa (caixa_id, data_hora, tipo_operacao, venda_id, forma_pagamento, valor, descricao)
                    VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (caixa_id, data_venda, 'VENDA', venda_id, forma_pagamento, total / len(itens), f"Venda registrada: {cliente}"))
            
            self.conn.commit()
            self.registrar_alteracao("Venda Detalhada", 
                f"{len(itens)} itens | Total: R$ {total:.2f} | Desc: R$ {desconto:.2f} | Cliente: {cliente} | Pgto: {forma_pagamento}")
            return True
        except Exception as e:
            logger.error("Erro ao registrar venda detalhada: %s", e)
            return False

    def listar_vendas(self, periodo="todos"):
        """Lista vendas da tabela vendas com filtro de período (exclui canceladas)."""
        where = "WHERE status != 'Cancelada'"
        params = []
        
        if periodo and periodo != "todos":
            hoje = datetime.date.today()
            inicio = None
            if periodo == "hoje":
                inicio = hoje
            elif periodo == "semana":
                inicio = hoje - datetime.timedelta(days=hoje.weekday())
            elif periodo == "mes":
                inicio = hoje.replace(day=1)
            elif periodo == "ano":
                inicio = hoje.replace(month=1, day=1)
            
            if inicio:
                where += " AND data_venda >= ?"
                params.append(inicio.isoformat())
        
        query = f"SELECT * FROM vendas {where} ORDER BY data_venda DESC"
        return pd.read_sql_query(query, self.conn, params=params)

    # --- Cancelamento de Venda ---
    def cancelar_venda(self, venda_id):
        """Cancela uma venda: marca como Cancelada, devolve estoque e registra em auditoria."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT produto_id, quantidade, valor_total, status, caixa_id, forma_pagamento FROM vendas WHERE id = ?", (venda_id,))
            row = cursor.fetchone()
            if not row:
                return False, "Venda não encontrada."
            prod_id, qtd, valor, status, caixa_id, forma_pagamento = row
            if status == 'Cancelada':
                return False, "Venda já foi cancelada anteriormente."
            
            # Devolver estoque
            if prod_id:
                cursor.execute("UPDATE produtos SET quantidade = quantidade + ? WHERE id = ?", (qtd, prod_id))
            
            # Marcar venda como cancelada e desvincula do caixa
            cursor.execute("UPDATE vendas SET status = 'Cancelada', caixa_id = NULL WHERE id = ?", (venda_id,))
            
            # Estornar entrada financeira (registrar gasto de estorno)
            cursor.execute("INSERT INTO entradas (data, valor, descricao) VALUES (?, ?, ?)",
                (datetime.date.today().isoformat(), -valor, f"Estorno venda #{venda_id}"))
            
            # Registrar cancelamento em auditoria se houve caixa vinculado
            if caixa_id:
                data_cancelamento = datetime.datetime.now().isoformat()
                cursor.execute('''INSERT INTO auditoria_caixa (caixa_id, data_hora, tipo_operacao, venda_id, forma_pagamento, valor, descricao)
                    VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (caixa_id, data_cancelamento, 'CANCELAMENTO', venda_id, forma_pagamento, valor, f"Venda #{venda_id} cancelada"))
            
            self.conn.commit()
            self.registrar_alteracao("Cancelar Venda", f"Venda #{venda_id} cancelada. Estoque devolvido.")
            return True, "Venda cancelada com sucesso."
        except Exception as e:
            logger.error("Erro ao cancelar venda: %s", e)
            return False, str(e)

    # --- Contas a Receber (Fiado) ---
    def registrar_fiado(self, cliente_id, valor, descricao, data_vencimento=None):
        """Registra uma venda fiado (conta a receber)."""
        cursor = self.conn.cursor()
        data_venda = datetime.date.today().isoformat()
        if not data_vencimento:
            data_vencimento = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        cursor.execute(
            "INSERT INTO contas_receber (cliente_id, valor, data_venda, data_vencimento, status, descricao) VALUES (?, ?, ?, ?, 'Pendente', ?)",
            (cliente_id, valor, data_venda, data_vencimento, descricao))
        self.conn.commit()
        self.registrar_alteracao("Venda Fiado", f"Fiado R$ {valor:.2f} p/ cliente ID {cliente_id}. Venc: {data_vencimento}")

    def quitar_fiado(self, conta_id):
        """Marca uma conta fiado como paga."""
        cursor = self.conn.cursor()
        cursor.execute("UPDATE contas_receber SET status = 'Pago', data_pagamento = ? WHERE id = ?",
                       (datetime.date.today().isoformat(), conta_id))
        self.conn.commit()
        self.registrar_alteracao("Quitar Fiado", f"Conta #{conta_id} quitada.")

    def listar_fiados(self, status=None, cliente_id=None):
        """Lista contas a receber com filtros opcionais."""
        query = """SELECT cr.*, c.nome as cliente_nome FROM contas_receber cr
                   LEFT JOIN clientes c ON cr.cliente_id = c.id WHERE 1=1"""
        params = []
        if status:
            query += " AND cr.status = ?"
            params.append(status)
        if cliente_id:
            query += " AND cr.cliente_id = ?"
            params.append(cliente_id)
        query += " ORDER BY cr.data_vencimento ASC"
        return pd.read_sql_query(query, self.conn, params=params)

    def total_fiado_pendente(self):
        """Retorna total de fiados pendentes."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT SUM(valor) FROM contas_receber WHERE status = 'Pendente'")
        return cursor.fetchone()[0] or 0.0

    # --- Fidelidade ---
    def get_pontos_cliente(self, cliente_id):
        """Retorna pontos de fidelidade de um cliente."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT pontos, total_acumulado FROM fidelidade WHERE cliente_id = ?", (cliente_id,))
        row = cursor.fetchone()
        return (row[0], row[1]) if row else (0, 0.0)

    def usar_pontos_fidelidade(self, cliente_id, pontos):
        """Consome pontos de fidelidade para desconto."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT pontos FROM fidelidade WHERE cliente_id = ?", (cliente_id,))
        row = cursor.fetchone()
        if not row or row[0] < pontos:
            return False, "Pontos insuficientes."
        cursor.execute("UPDATE fidelidade SET pontos = pontos - ? WHERE cliente_id = ?", (pontos, cliente_id))
        self.conn.commit()
        self.registrar_alteracao("Usar Pontos", f"Cliente ID {cliente_id} usou {pontos} pontos.")
        return True, pontos

    # --- Relatórios Avançados de Vendas ---
    def ranking_produtos_vendidos(self, periodo="todos", limite=10):
        """Ranking dos produtos mais vendidos por quantidade."""
        where = "WHERE v.status != 'Cancelada'"
        params = []
        if periodo != "todos":
            hoje = datetime.date.today()
            inicio = None
            if periodo == "mes":
                inicio = hoje.replace(day=1)
            elif periodo == "semana":
                inicio = hoje - datetime.timedelta(days=hoje.weekday())
            elif periodo == "ano":
                inicio = hoje.replace(month=1, day=1)
            if inicio:
                where += " AND v.data_venda >= ?"
                params.append(inicio.isoformat())
        params.append(limite)
        query = f"""SELECT p.nome, SUM(v.quantidade) as total_vendido, SUM(v.valor_total) as faturamento, SUM(v.lucro) as lucro_total
                    FROM vendas v LEFT JOIN produtos p ON v.produto_id = p.id
                    {where} GROUP BY v.produto_id ORDER BY total_vendido DESC LIMIT ?"""
        return pd.read_sql_query(query, self.conn, params=params)

    def curva_abc(self, periodo="todos"):
        """Análise Curva ABC (Pareto) — classifica produtos em A (80%), B (15%), C (5%) por faturamento."""
        where = "WHERE v.status != 'Cancelada'"
        params = []
        if periodo != "todos":
            hoje = datetime.date.today()
            inicio = None
            if periodo == "mes":
                inicio = hoje.replace(day=1)
            elif periodo == "semana":
                inicio = hoje - datetime.timedelta(days=hoje.weekday())
            elif periodo == "ano":
                inicio = hoje.replace(month=1, day=1)
            if inicio:
                where += " AND v.data_venda >= ?"
                params.append(inicio.isoformat())
        query = f"""SELECT p.nome, SUM(v.quantidade) as total_vendido,
                    SUM(v.valor_total) as faturamento, SUM(v.lucro) as lucro_total
                    FROM vendas v LEFT JOIN produtos p ON v.produto_id = p.id
                    {where} GROUP BY v.produto_id ORDER BY faturamento DESC"""
        df = pd.read_sql_query(query, self.conn, params=params)
        if df.empty:
            return df
        total_fat = df['faturamento'].sum()
        if total_fat > 0:
            df['pct'] = (df['faturamento'] / total_fat * 100).round(2)
            df['pct_acum'] = df['pct'].cumsum().round(2)
            df['classe'] = df['pct_acum'].apply(lambda x: 'A' if x <= 80 else ('B' if x <= 95 else 'C'))
        else:
            df['pct'] = 0
            df['pct_acum'] = 0
            df['classe'] = 'C'
        return df

    def lucro_por_periodo(self, periodo="mes"):
        """Retorna lucro total em um período."""
        where = "WHERE status != 'Cancelada'"
        params = []
        hoje = datetime.date.today()
        inicio = None
        if periodo == "hoje":
            inicio = hoje
        elif periodo == "semana":
            inicio = hoje - datetime.timedelta(days=hoje.weekday())
        elif periodo == "mes":
            inicio = hoje.replace(day=1)
        elif periodo == "ano":
            inicio = hoje.replace(month=1, day=1)
        if inicio:
            where += " AND data_venda >= ?"
            params.append(inicio.isoformat())
        query = f"SELECT SUM(lucro) as lucro, SUM(valor_total) as faturamento FROM vendas {where}"
        cursor = self.conn.cursor()
        cursor.execute(query, params)
        row = cursor.fetchone()
        return (row[0] or 0.0, row[1] or 0.0)

    def vendas_por_dia(self, dias=30):
        """Retorna vendas agrupadas por dia para gráfico temporal."""
        inicio = (datetime.date.today() - datetime.timedelta(days=dias)).isoformat()
        query = """SELECT DATE(data_venda) as dia, SUM(valor_total) as faturamento, SUM(lucro) as lucro, COUNT(*) as qtd_vendas
                   FROM vendas WHERE status != 'Cancelada' AND data_venda >= ?
                   GROUP BY DATE(data_venda) ORDER BY dia"""
        return pd.read_sql_query(query, self.conn, params=[inicio])

    def comparativo_mensal(self):
        """Compara faturamento/lucro do mês atual vs. mês anterior."""
        hoje = datetime.date.today()
        inicio_mes = hoje.replace(day=1)
        if inicio_mes.month == 1:
            inicio_anterior = inicio_mes.replace(year=inicio_mes.year - 1, month=12)
        else:
            inicio_anterior = inicio_mes.replace(month=inicio_mes.month - 1)
        
        cursor = self.conn.cursor()
        # Mês atual
        cursor.execute("SELECT SUM(valor_total), SUM(lucro), COUNT(*) FROM vendas WHERE status != 'Cancelada' AND data_venda >= ?",
                       (inicio_mes.isoformat(),))
        atual = cursor.fetchone()
        # Mês anterior
        cursor.execute("SELECT SUM(valor_total), SUM(lucro), COUNT(*) FROM vendas WHERE status != 'Cancelada' AND data_venda >= ? AND data_venda < ?",
                       (inicio_anterior.isoformat(), inicio_mes.isoformat()))
        anterior = cursor.fetchone()
        return {
            'atual': {'faturamento': atual[0] or 0, 'lucro': atual[1] or 0, 'vendas': atual[2] or 0},
            'anterior': {'faturamento': anterior[0] or 0, 'lucro': anterior[1] or 0, 'vendas': anterior[2] or 0}
        }

    def vendas_por_cliente(self, cliente_nome):
        """Histórico de compras de um cliente específico."""
        query = """SELECT v.*, p.nome as produto_nome FROM vendas v
                   LEFT JOIN produtos p ON v.produto_id = p.id
                   WHERE v.cliente = ? AND v.status != 'Cancelada'
                   ORDER BY v.data_venda DESC"""
        return pd.read_sql_query(query, self.conn, params=[cliente_nome])

    def verificar_produtos_vencendo(self, dias_alerta=7):
        """Retorna produtos com validade próxima ou vencidos."""
        hoje = datetime.date.today()
        limite = (hoje + datetime.timedelta(days=dias_alerta)).isoformat()
        query = """SELECT id, nome, quantidade, data_validade FROM produtos
                   WHERE data_validade IS NOT NULL AND data_validade != '' AND data_validade <= ?
                   ORDER BY data_validade ASC"""
        return pd.read_sql_query(query, self.conn, params=[limite])

    # Mapeamento inteligente de colunas para importação
    _COLUMN_ALIASES = {
        'nome': ['nome', 'item', 'produto', 'name', 'descricao', 'descrição', 'desc', 'mercadoria'],
        'quantidade': ['quantidade', 'qtd', 'qtde', 'quant', 'qty', 'estoque', 'unidades', 'un'],
        'preco': ['preco', 'preço', 'preco (r$)', 'preço (r$)', 'preco_venda', 'preço_venda',
                  'preco venda', 'preço venda', 'valor', 'valor_venda', 'valor venda',
                  'price', 'vl_venda', 'vl venda', 'p.venda', 'p. venda', 'unitario', 'unitário',
                  'valor unitario', 'valor unitário', 'preco unit', 'preço unit'],
        'categoria': ['categoria', 'cat', 'category', 'tipo', 'grupo', 'departamento', 'setor', 'classe'],
        'subcategoria': ['subcategoria', 'sub_categoria', 'sub categoria', 'subcategory', 'subgrupo', 'sub grupo', 'sub'],
        'sub_subcategoria': ['sub_subcategoria', 'subsubcategoria', 'sub subcategoria', 'sub-subcategoria',
                             'sub_sub_categoria', 'sub sub categoria', 'subsubcat', 'sub_subcat'],
        'tamanho': ['tamanho', 'tam', 'size', 'medida', 'numeracao', 'numeração', 'num'],
        'preco_compra': ['preco_compra', 'preço_compra', 'preco compra', 'preço compra',
                         'custo', 'valor_compra', 'valor compra', 'cost', 'p.compra',
                         'vl_compra', 'vl compra', 'preco custo', 'preço custo'],
        'codigo_barras': ['codigo_barras', 'código_barras', 'codigo barras', 'código barras',
                          'barcode', 'cod_barras', 'cod barras', 'ean', 'sku', 'codigo', 'código', 'cod'],
        'data_validade': ['data_validade', 'validade', 'vencimento', 'data_vencimento',
                          'data vencimento', 'data validade', 'expiry', 'expiration'],
        'imagem': ['imagem', 'image', 'img', 'foto', 'photo', 'picture', 'imagem_path',
                   'image_path', 'foto_path', 'arquivo_imagem'],
        'lote_serie': ['lote_serie', 'lote', 'serie', 'série', 'lote/serie', 'lote/série',
                       'num_serie', 'número_serie', 'numero_serie', 'imei', 'sif', 'batch',
                       'serial', 'serial_number', 'lot', 'ref', 'referencia', 'referência'],
    }

    def _normalizar_colunas(self, df):
        """Renomeia colunas do DataFrame com base em aliases conhecidos.
        Retorna o DataFrame com colunas renomeadas e a lista de mapeamentos aplicados."""
        rename_map = {}
        colunas_lower = {c: c.strip().lower() for c in df.columns}

        for campo_padrao, aliases in self._COLUMN_ALIASES.items():
            # Se já existe a coluna padrão, não precisa mapear
            if campo_padrao in colunas_lower.values():
                # Encontrar a coluna original e mapear para o nome padrão
                for col_orig, col_low in colunas_lower.items():
                    if col_low == campo_padrao and col_orig != campo_padrao:
                        rename_map[col_orig] = campo_padrao
                continue
            # Procurar nas aliases
            for col_orig, col_low in colunas_lower.items():
                if col_low in aliases:
                    rename_map[col_orig] = campo_padrao
                    break

        if rename_map:
            df = df.rename(columns=rename_map)
        return df, rename_map

    def _processar_imagem_importacao(self, img_valor, nome_produto, arquivo_origem):
        """Processa imagem durante importação: copia arquivo local para product_images."""
        try:
            img_dir = os.path.join(self.data_dir, 'product_images')
            os.makedirs(img_dir, exist_ok=True)

            img_path = img_valor.strip()
            # Resolver caminho relativo ao arquivo de origem
            if not os.path.isabs(img_path):
                base_dir = os.path.dirname(os.path.abspath(arquivo_origem))
                img_path = os.path.join(base_dir, img_path)

            if os.path.isfile(img_path):
                ext = os.path.splitext(img_path)[1].lower()
                if ext not in ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'):
                    return ''
                safe_name = "".join(c if c.isalnum() or c in ('_', '-') else '_' for c in nome_produto)
                dest_name = f"{safe_name}_{int(datetime.datetime.now().timestamp())}{ext}"
                dest_path = os.path.join(img_dir, dest_name)
                import shutil
                shutil.copy2(img_path, dest_path)
                return dest_name
            return ''
        except Exception:
            return ''

    def importar_produtos_csv(self, filepath):
        """Importa produtos de um arquivo CSV. Reconhece variações comuns de nomes de colunas."""
        try:
            df = pd.read_csv(filepath)
            df, mapeamentos = self._normalizar_colunas(df)

            cols_obrigatorias = {'nome', 'quantidade', 'preco'}
            if not cols_obrigatorias.issubset(set(df.columns)):
                faltando = cols_obrigatorias - set(df.columns)
                return False, (f"Colunas obrigatórias não encontradas: {faltando}.\n"
                              f"Encontradas: {set(df.columns)}\n\n"
                              f"Nomes aceitos:\n"
                              f"  nome → {', '.join(self._COLUMN_ALIASES['nome'])}\n"
                              f"  quantidade → {', '.join(self._COLUMN_ALIASES['quantidade'])}\n"
                              f"  preco → {', '.join(self._COLUMN_ALIASES['preco'][:6])}")
            
            cursor = self.conn.cursor()
            count = 0
            cursor.execute("BEGIN TRANSACTION")
            try:
                for _, row in df.iterrows():
                    nome = str(row['nome']).strip()
                    if not nome or nome.lower() == 'nan':
                        continue
                    try:
                        qtd = int(float(str(row['quantidade']).strip()))
                        preco = float(str(row['preco']).replace(',', '.').strip())
                    except (ValueError, TypeError):
                        continue
                    cat = str(row.get('categoria', '')).strip() if 'categoria' in df.columns else ''
                    if cat.lower() == 'nan': cat = ''
                    subcat = str(row.get('subcategoria', '')).strip() if 'subcategoria' in df.columns else ''
                    if subcat.lower() == 'nan': subcat = ''
                    sub_subcat = str(row.get('sub_subcategoria', '')).strip() if 'sub_subcategoria' in df.columns else ''
                    if sub_subcat.lower() == 'nan': sub_subcat = ''
                    tamanho = str(row.get('tamanho', '')).strip() if 'tamanho' in df.columns else ''
                    if tamanho.lower() == 'nan': tamanho = ''
                    preco_compra = 0
                    if 'preco_compra' in df.columns:
                        try:
                            preco_compra = float(str(row.get('preco_compra', 0)).replace(',', '.').strip())
                        except (ValueError, TypeError):
                            preco_compra = 0
                    codigo = str(row.get('codigo_barras', '')).strip() if 'codigo_barras' in df.columns else ''
                    if codigo.lower() == 'nan': codigo = ''
                    validade = str(row.get('data_validade', '')).strip() if 'data_validade' in df.columns else ''
                    if validade.lower() == 'nan': validade = ''
                    lote = str(row.get('lote_serie', '')).strip() if 'lote_serie' in df.columns else ''
                    if lote.lower() == 'nan': lote = ''
                    
                    # Processar imagem se disponível
                    img_name = ''
                    if 'imagem' in df.columns:
                        img_val = str(row.get('imagem', '')).strip()
                        if img_val and img_val.lower() != 'nan':
                            img_name = self._processar_imagem_importacao(img_val, nome, filepath)

                    cursor.execute('''INSERT INTO produtos (nome, quantidade, preco, categoria, quantidade_inicial, preco_compra, codigo_barras, data_validade, imagem, subcategoria, tamanho, sub_subcategoria, lote_serie)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                        (nome, qtd, preco, cat, qtd, preco_compra, codigo, validade, img_name, subcat, tamanho, sub_subcat, lote))
                    count += 1
                
                self.conn.commit()
            except Exception as e:
                self.conn.rollback()
                raise e
            info_map = ''
            if mapeamentos:
                info_map = '\nColunas mapeadas: ' + ', '.join(f'{k}→{v}' for k, v in mapeamentos.items())
            self.registrar_alteracao("Importar CSV", f"{count} produtos importados de {os.path.basename(filepath)}")
            return True, f"{count} produtos importados com sucesso.{info_map}"
        except Exception as e:
            logger.error("Erro ao importar CSV: %s", e)
            return False, str(e)

    def importar_produtos_pdf(self, filepath):
        """Importa produtos de um arquivo PDF com tabelas. Reconhece variações comuns de nomes de colunas."""
        try:
            import pdfplumber
            all_rows = []
            header = None
            with pdfplumber.open(filepath) as pdf:
                for page in pdf.pages:
                    tables = page.extract_tables()
                    for table in tables:
                        if not table:
                            continue
                        if header is None:
                            header = [str(c).strip() if c else f'col_{i}' for i, c in enumerate(table[0])]
                            data_rows = table[1:]
                        else:
                            data_rows = table
                        for row in data_rows:
                            all_rows.append(row)
            if not all_rows or header is None:
                return False, "Nenhuma tabela encontrada no PDF."
            
            df = pd.DataFrame(all_rows, columns=header)
            df.columns = [c.strip() for c in df.columns]
            df, mapeamentos = self._normalizar_colunas(df)
            
            cols_obrigatorias = {'nome', 'quantidade', 'preco'}
            if not cols_obrigatorias.issubset(set(df.columns)):
                faltando = cols_obrigatorias - set(df.columns)
                return False, (f"Colunas obrigatórias não encontradas: {faltando}.\n"
                              f"Encontradas: {set(df.columns)}\n\n"
                              f"Nomes aceitos:\n"
                              f"  nome → {', '.join(self._COLUMN_ALIASES['nome'])}\n"
                              f"  quantidade → {', '.join(self._COLUMN_ALIASES['quantidade'])}\n"
                              f"  preco → {', '.join(self._COLUMN_ALIASES['preco'][:6])}")
            
            cursor = self.conn.cursor()
            count = 0
            cursor.execute("BEGIN TRANSACTION")
            try:
                for _, row in df.iterrows():
                    nome = str(row['nome']).strip()
                    if not nome or nome.lower() == 'nan':
                        continue
                    try:
                        qtd = int(float(str(row['quantidade']).strip()))
                        preco = float(str(row['preco']).replace(',', '.').strip())
                    except (ValueError, TypeError):
                        continue
                    cat = str(row.get('categoria', '')).strip() if 'categoria' in df.columns else ''
                    if cat.lower() == 'nan': cat = ''
                    subcat = str(row.get('subcategoria', '')).strip() if 'subcategoria' in df.columns else ''
                    if subcat.lower() == 'nan': subcat = ''
                    sub_subcat = str(row.get('sub_subcategoria', '')).strip() if 'sub_subcategoria' in df.columns else ''
                    if sub_subcat.lower() == 'nan': sub_subcat = ''
                    tamanho = str(row.get('tamanho', '')).strip() if 'tamanho' in df.columns else ''
                    if tamanho.lower() == 'nan': tamanho = ''
                    preco_compra = 0
                    if 'preco_compra' in df.columns:
                        try:
                            preco_compra = float(str(row.get('preco_compra', 0)).replace(',', '.').strip())
                        except (ValueError, TypeError):
                            preco_compra = 0
                    codigo = str(row.get('codigo_barras', '')).strip() if 'codigo_barras' in df.columns else ''
                    if codigo.lower() == 'nan': codigo = ''
                    validade = str(row.get('data_validade', '')).strip() if 'data_validade' in df.columns else ''
                    if validade.lower() == 'nan': validade = ''
                    lote = str(row.get('lote_serie', '')).strip() if 'lote_serie' in df.columns else ''
                    if lote.lower() == 'nan': lote = ''
                    
                    # Processar imagem se disponível
                    img_name = ''
                    if 'imagem' in df.columns:
                        img_val = str(row.get('imagem', '')).strip()
                        if img_val and img_val.lower() != 'nan':
                            img_name = self._processar_imagem_importacao(img_val, nome, filepath)

                    cursor.execute('''INSERT INTO produtos (nome, quantidade, preco, categoria, quantidade_inicial, preco_compra, codigo_barras, data_validade, imagem, subcategoria, tamanho, sub_subcategoria, lote_serie)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                        (nome, qtd, preco, cat, qtd, preco_compra, codigo, validade, img_name, subcat, tamanho, sub_subcat, lote))
                    count += 1
                
                self.conn.commit()
            except Exception as e:
                self.conn.rollback()
                raise e
            info_map = ''
            if mapeamentos:
                info_map = '\nColunas mapeadas: ' + ', '.join(f'{k}→{v}' for k, v in mapeamentos.items())
            self.registrar_alteracao("Importar PDF", f"{count} produtos importados de {os.path.basename(filepath)}")
            return True, f"{count} produtos importados com sucesso.{info_map}"
        except ImportError:
            return False, "Biblioteca 'pdfplumber' não instalada. Instale com: pip install pdfplumber"
        except Exception as e:
            logger.error("Erro ao importar PDF: %s", e)
            return False, str(e)

    def vendas_por_forma_pagamento(self, periodo="mes"):
        """Agrupamento de vendas por forma de pagamento."""
        where = "WHERE status != 'Cancelada'"
        params = []
        hoje = datetime.date.today()
        if periodo == "mes":
            where += " AND data_venda >= ?"
            params.append(hoje.replace(day=1).isoformat())
        elif periodo == "semana":
            where += " AND data_venda >= ?"
            params.append((hoje - datetime.timedelta(days=hoje.weekday())).isoformat())
        query = f"""SELECT COALESCE(forma_pagamento, 'Dinheiro') as forma, SUM(valor_total) as total, COUNT(*) as qtd
                    FROM vendas {where} GROUP BY forma_pagamento ORDER BY total DESC"""
        return pd.read_sql_query(query, self.conn, params=params)

    # ============================================================
    # ============ MULTI-USUÁRIO / PERMISSÕES ============
    # ============================================================

    PERFIS_PERMISSOES = {
        "admin":    {"tudo": True},
        "gerente":  {"ver_relatorio": True, "ver_config": True, "vender": True, "editar": True,
                     "remover": True, "adicionar": True, "gastos": True, "caixa": True,
                     "kits": True, "clientes": True, "fornecedores": True, "vendedores": True,
                     "categorias": True, "insumos": True, "producao": True, "mesas": True,
                     "contas_pagar": True, "etiquetas": True, "chat_ia": True},
        "vendedor": {"vender": True, "adicionar": True, "clientes": True, "kits": True,
                     "chat_ia": True, "etiquetas": True},
        "caixa":    {"vender": True, "caixa": True, "clientes": True},
    }

    def usuario_tem_permissao(self, perfil, acao):
        """Verifica se o perfil tem permissão para a ação."""
        perms = self.PERFIS_PERMISSOES.get(perfil, {})
        return perms.get("tudo", False) or perms.get(acao, False)

    def adicionar_usuario(self, nome, login, senha, perfil='vendedor', loja_id=None):
        from services.security import hash_password, validate_password_strength
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM usuarios WHERE login=?", (login,))
        if cursor.fetchone()[0] > 0:
            return False, "Login já existe."
        
        is_strong, msg = validate_password_strength(senha)
        if not is_strong:
            return False, msg
            
        senha_hash = hash_password(senha)
        cursor.execute("""INSERT INTO usuarios (nome, login, senha_hash, perfil, loja_id, data_criacao)
                         VALUES (?, ?, ?, ?, ?, ?)""",
                      (nome, login, senha_hash, perfil, loja_id, datetime.datetime.now().isoformat()))
        self.conn.commit()
        self.registrar_alteracao("Criar Usuário", f"Usuário '{login}' criado com perfil '{perfil}'")
        return True, "Usuário criado com sucesso."

    def autenticar_usuario(self, login, senha):
        from services.security import verify_password, needs_rehash, hash_password
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, nome, perfil, loja_id, senha_hash FROM usuarios WHERE login=? AND ativo=1",
                      (login,))
        row = cursor.fetchone()
        if row and verify_password(senha, row[4]):
            # Migrar hash legado para PBKDF2 automaticamente
            if needs_rehash(row[4]):
                new_hash = hash_password(senha)
                cursor.execute("UPDATE usuarios SET senha_hash=? WHERE id=?", (new_hash, row[0]))
                self.conn.commit()
            return {"id": row[0], "nome": row[1], "perfil": row[2], "loja_id": row[3]}
        return None

    def listar_usuarios(self):
        return pd.read_sql_query("SELECT id, nome, login, perfil, ativo, loja_id, data_criacao FROM usuarios ORDER BY nome", self.conn)

    def atualizar_usuario(self, user_id, nome, perfil, ativo, loja_id=None):
        cursor = self.conn.cursor()
        cursor.execute("UPDATE usuarios SET nome=?, perfil=?, ativo=?, loja_id=? WHERE id=?",
                      (nome, perfil, ativo, loja_id, user_id))
        self.conn.commit()

    def alterar_senha_usuario(self, user_id, nova_senha):
        from services.security import hash_password
        cursor = self.conn.cursor()
        senha_hash = hash_password(nova_senha)
        cursor.execute("UPDATE usuarios SET senha_hash=? WHERE id=?", (senha_hash, user_id))
        self.conn.commit()

    def remover_usuario(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute("UPDATE usuarios SET ativo=0 WHERE id=?", (user_id,))
        self.conn.commit()

    # ============================================================
    # ============ CONTAS A PAGAR ============
    # ============================================================

    def adicionar_conta_pagar(self, descricao, valor, data_vencimento, fornecedor_id=None,
                              categoria='Outro', recorrente=0, total_parcelas=1, observacao=''):
        cursor = self.conn.cursor()
        data_emissao = datetime.date.today().isoformat()
        for parcela in range(1, total_parcelas + 1):
            venc = datetime.date.fromisoformat(data_vencimento)
            if total_parcelas > 1:
                venc = venc + datetime.timedelta(days=30 * (parcela - 1))
            cursor.execute("""INSERT INTO contas_pagar
                (fornecedor_id, descricao, valor, data_emissao, data_vencimento, categoria,
                 recorrente, parcela_atual, total_parcelas, observacao)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (fornecedor_id, descricao, valor / total_parcelas, data_emissao,
                 venc.isoformat(), categoria, recorrente, parcela, total_parcelas, observacao))
        self.conn.commit()
        self.registrar_alteracao("Conta a Pagar", f"'{descricao}' R${valor:.2f} ({total_parcelas}x)")

    def listar_contas_pagar(self, status=None, dias_futuros=None):
        where = []
        params = []
        if status:
            where.append("cp.status = ?")
            params.append(status)
        if dias_futuros is not None:
            limite = (datetime.date.today() + datetime.timedelta(days=dias_futuros)).isoformat()
            where.append("cp.data_vencimento <= ?")
            params.append(limite)
        where_str = "WHERE " + " AND ".join(where) if where else ""
        query = f"""SELECT cp.*, COALESCE(f.nome, 'Sem fornecedor') as fornecedor_nome
                    FROM contas_pagar cp LEFT JOIN fornecedores f ON cp.fornecedor_id = f.id
                    {where_str} ORDER BY cp.data_vencimento ASC"""
        return pd.read_sql_query(query, self.conn, params=params)

    def pagar_conta(self, conta_id):
        cursor = self.conn.cursor()
        cursor.execute("UPDATE contas_pagar SET status='Pago', data_pagamento=? WHERE id=?",
                      (datetime.date.today().isoformat(), conta_id))
        # Registrar como gasto
        cursor.execute("SELECT descricao, valor, categoria FROM contas_pagar WHERE id=?", (conta_id,))
        row = cursor.fetchone()
        if row:
            self.adicionar_gasto(datetime.date.today().isoformat(), row[1], row[2], f"Pgto: {row[0]}")
        self.conn.commit()
        self.registrar_alteracao("Pagar Conta", f"Conta #{conta_id} quitada")

    def cancelar_conta_pagar(self, conta_id):
        cursor = self.conn.cursor()
        cursor.execute("UPDATE contas_pagar SET status='Cancelado' WHERE id=?", (conta_id,))
        self.conn.commit()

    def contas_vencendo(self, dias=7):
        """Retorna contas a pagar vencendo nos próximos N dias."""
        hoje = datetime.date.today().isoformat()
        limite = (datetime.date.today() + datetime.timedelta(days=dias)).isoformat()
        return pd.read_sql_query(
            "SELECT * FROM contas_pagar WHERE status='Pendente' AND data_vencimento BETWEEN ? AND ? ORDER BY data_vencimento",
            self.conn, params=[hoje, limite])

    def contas_vencidas(self):
        hoje = datetime.date.today().isoformat()
        return pd.read_sql_query(
            "SELECT * FROM contas_pagar WHERE status='Pendente' AND data_vencimento < ? ORDER BY data_vencimento",
            self.conn, params=[hoje])

    def total_contas_pagar_pendente(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT COALESCE(SUM(valor), 0) FROM contas_pagar WHERE status='Pendente'")
        return cursor.fetchone()[0]

    # ============================================================
    # ============ MÚLTIPLAS LOJAS ============
    # ============================================================

    def adicionar_loja(self, nome, endereco='', telefone='', cnpj=''):
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO lojas (nome, endereco, telefone, cnpj, data_criacao) VALUES (?, ?, ?, ?, ?)",
                      (nome, endereco, telefone, cnpj, datetime.datetime.now().isoformat()))
        self.conn.commit()
        self.registrar_alteracao("Criar Loja", f"Loja '{nome}' criada")
        return cursor.lastrowid

    def listar_lojas(self):
        return pd.read_sql_query("SELECT * FROM lojas WHERE ativa=1 ORDER BY nome", self.conn)

    def remover_loja(self, loja_id):
        cursor = self.conn.cursor()
        cursor.execute("UPDATE lojas SET ativa=0 WHERE id=?", (loja_id,))
        self.conn.commit()

    def atualizar_loja(self, loja_id, nome, endereco='', telefone='', cnpj=''):
        cursor = self.conn.cursor()
        cursor.execute("UPDATE lojas SET nome=?, endereco=?, telefone=?, cnpj=? WHERE id=?",
                      (nome, endereco, telefone, cnpj, loja_id))
        self.conn.commit()

    # ============================================================
    # ============ DRE SIMPLIFICADO ============
    # ============================================================

    def gerar_dre(self, periodo="mes"):
        """Gera Demonstrativo de Resultado do Exercício simplificado."""
        hoje = datetime.date.today()
        if periodo == "mes":
            inicio = hoje.replace(day=1).isoformat()
            fim = hoje.isoformat()
        elif periodo == "ano":
            inicio = hoje.replace(month=1, day=1).isoformat()
            fim = hoje.isoformat()
        elif periodo == "trimestre":
            mes_inicio = ((hoje.month - 1) // 3) * 3 + 1
            inicio = hoje.replace(month=mes_inicio, day=1).isoformat()
            fim = hoje.isoformat()
        else:
            inicio = hoje.replace(day=1).isoformat()
            fim = hoje.isoformat()

        cursor = self.conn.cursor()

        # Receita Bruta (vendas + serviços)
        cursor.execute("SELECT COALESCE(SUM(valor_total), 0) FROM vendas WHERE status='Concluída' AND data_venda >= ? AND data_venda <= ?",
                      (inicio, fim))
        receita_vendas = cursor.fetchone()[0]

        cursor.execute("SELECT COALESCE(SUM(valor), 0) FROM servicos WHERE data >= ? AND data <= ?",
                      (inicio, fim))
        receita_servicos = cursor.fetchone()[0]

        receita_bruta = receita_vendas + receita_servicos

        # Deduções (descontos dados)
        cursor.execute("SELECT COALESCE(SUM(desconto), 0) FROM vendas WHERE status='Concluída' AND data_venda >= ? AND data_venda <= ?",
                      (inicio, fim))
        deducoes = cursor.fetchone()[0]

        receita_liquida = receita_bruta - deducoes

        # CMV (custo das mercadorias vendidas / custo dos serviços)
        cursor.execute("SELECT COALESCE(SUM(valor_total - lucro), 0) FROM vendas WHERE status='Concluída' AND data_venda >= ? AND data_venda <= ?",
                      (inicio, fim))
        cmv = cursor.fetchone()[0]

        cursor.execute("SELECT COALESCE(SUM(custo), 0) FROM servicos WHERE data >= ? AND data <= ?",
                      (inicio, fim))
        custo_servicos = cursor.fetchone()[0]

        custo_total = cmv + custo_servicos
        lucro_bruto = receita_liquida - custo_total

        # Despesas Operacionais (gastos fixos + variáveis)
        cursor.execute("SELECT COALESCE(SUM(valor), 0) FROM gastos WHERE data >= ? AND data <= ? AND tipo='Fixo'",
                      (inicio, fim))
        despesas_fixas = cursor.fetchone()[0]

        cursor.execute("SELECT COALESCE(SUM(valor), 0) FROM gastos WHERE data >= ? AND data <= ? AND tipo != 'Fixo'",
                      (inicio, fim))
        despesas_variaveis = cursor.fetchone()[0]

        despesas_total = despesas_fixas + despesas_variaveis
        resultado_operacional = lucro_bruto - despesas_total

        # Contas a pagar pagas no período
        cursor.execute("SELECT COALESCE(SUM(valor), 0) FROM contas_pagar WHERE status='Pago' AND data_pagamento >= ? AND data_pagamento <= ?",
                      (inicio, fim))
        contas_pagas = cursor.fetchone()[0]

        resultado_liquido = resultado_operacional

        return {
            "periodo": periodo,
            "inicio": inicio,
            "fim": fim,
            "receita_vendas": receita_vendas,
            "receita_servicos": receita_servicos,
            "receita_bruta": receita_bruta,
            "deducoes": deducoes,
            "receita_liquida": receita_liquida,
            "cmv": cmv,
            "custo_servicos": custo_servicos,
            "custo_total": custo_total,
            "lucro_bruto": lucro_bruto,
            "despesas_fixas": despesas_fixas,
            "despesas_variaveis": despesas_variaveis,
            "despesas_total": despesas_total,
            "resultado_operacional": resultado_operacional,
            "contas_pagas": contas_pagas,
            "resultado_liquido": resultado_liquido,
            "margem_bruta": (lucro_bruto / receita_liquida * 100) if receita_liquida > 0 else 0,
            "margem_liquida": (resultado_liquido / receita_liquida * 100) if receita_liquida > 0 else 0,
        }

    # ============================================================
    # ============ FLUXO DE CAIXA FUTURO ============
    # ============================================================

    def projetar_fluxo_caixa(self, dias=90):
        """Projeta fluxo de caixa futuro baseado em recorrências e IA."""
        hoje = datetime.date.today()
        projecao = []

        # Saldo atual
        saldo_atual = self.get_saldo_atual()

        # 1. Contas a pagar pendentes (saídas programadas)
        df_contas = self.listar_contas_pagar(status='Pendente', dias_futuros=dias)
        if not df_contas.empty:
            for _, row in df_contas.iterrows():
                projecao.append({
                    "data": row['data_vencimento'],
                    "tipo": "saida",
                    "descricao": f"Conta: {row['descricao']}",
                    "valor": -row['valor'],
                })

        # 2. Gastos fixos recorrentes (projetar meses futuros)
        cursor = self.conn.cursor()
        cursor.execute("SELECT descricao, valor FROM gastos WHERE tipo='Fixo' GROUP BY descricao ORDER BY data DESC")
        gastos_fixos = cursor.fetchall()
        for desc, valor in gastos_fixos:
            for mes_offset in range(1, (dias // 30) + 1):
                data_proj = (hoje + datetime.timedelta(days=30 * mes_offset)).isoformat()
                if data_proj <= (hoje + datetime.timedelta(days=dias)).isoformat():
                    projecao.append({
                        "data": data_proj,
                        "tipo": "saida_fixa",
                        "descricao": f"Fixo: {desc}",
                        "valor": -valor,
                    })

        # 3. Fiados a receber
        df_fiados = self.listar_fiados(status='Pendente')
        if not df_fiados.empty:
            for _, row in df_fiados.iterrows():
                venc = row.get('data_vencimento', '')
                if venc and venc <= (hoje + datetime.timedelta(days=dias)).isoformat():
                    projecao.append({
                        "data": venc,
                        "tipo": "entrada",
                        "descricao": f"Fiado: {row.get('descricao', 'Recebível')}",
                        "valor": row['valor'],
                    })

        # 4. Média diária de vendas (projeção)
        cursor.execute("""SELECT AVG(total_dia) FROM (
            SELECT SUM(valor_total) as total_dia FROM vendas 
            WHERE status='Concluída' AND data_venda >= ? GROUP BY DATE(data_venda))""",
            ((hoje - datetime.timedelta(days=30)).isoformat(),))
        media_diaria = cursor.fetchone()[0] or 0
        if media_diaria > 0:
            for d in range(1, dias + 1, 7):  # Projeção semanal
                data_proj = (hoje + datetime.timedelta(days=d)).isoformat()
                projecao.append({
                    "data": data_proj,
                    "tipo": "projecao_vendas",
                    "descricao": f"Projeção vendas (semana)",
                    "valor": media_diaria * 7,
                })

        projecao.sort(key=lambda x: x['data'])
        
        # Calcular saldo acumulado
        saldo = saldo_atual
        for item in projecao:
            saldo += item['valor']
            item['saldo_acumulado'] = saldo
        
        return {"saldo_atual": saldo_atual, "projecao": projecao, "media_diaria_vendas": media_diaria}

    # ============================================================
    # ============ CONTROLE DE COMISSÕES ============
    # ============================================================

    def calcular_comissoes(self, periodo="mes"):
        """Calcula comissões detalhadas por vendedor no período."""
        hoje = datetime.date.today()
        if periodo == "mes":
            inicio = hoje.replace(day=1).isoformat()
        elif periodo == "semana":
            inicio = (hoje - datetime.timedelta(days=hoje.weekday())).isoformat()
        else:
            inicio = hoje.replace(month=1, day=1).isoformat()

        query = """
            SELECT v.vendedor, vd.comissao as taxa_comissao,
                   COUNT(*) as num_vendas,
                   SUM(v.valor_total) as faturamento,
                   SUM(v.lucro) as lucro,
                   SUM(v.valor_total) * (vd.comissao / 100.0) as comissao_vendas
            FROM vendas v
            LEFT JOIN vendedores vd ON v.vendedor = vd.nome
            WHERE v.status = 'Concluída' AND v.data_venda >= ? AND v.vendedor != ''
            GROUP BY v.vendedor
            ORDER BY comissao_vendas DESC
        """
        df = pd.read_sql_query(query, self.conn, params=[inicio])

        # Adicionar serviços
        query_serv = """
            SELECT s.vendedor, vd.comissao as taxa_comissao,
                   COUNT(*) as num_servicos,
                   SUM(s.valor) as faturamento_servicos,
                   SUM(s.valor) * (vd.comissao / 100.0) as comissao_servicos
            FROM servicos s
            LEFT JOIN vendedores vd ON s.vendedor = vd.nome
            WHERE s.data >= ? AND s.vendedor != ''
            GROUP BY s.vendedor
        """
        df_serv = pd.read_sql_query(query_serv, self.conn, params=[inicio])

        return {"vendas": df, "servicos": df_serv, "periodo_inicio": inicio}

    # ============================================================
    # ============ NOTIFICAÇÕES / ALERTAS ============
    # ============================================================

    def coletar_alertas(self):
        """Coleta todos os alertas pendentes do sistema."""
        alertas = []

        # Estoque baixo
        try:
            df_baixo = self.produtos_abaixo_estoque_minimo()
            if not df_baixo.empty:
                alertas.append({
                    "tipo": "estoque_baixo",
                    "icone": "📦",
                    "titulo": "Estoque Baixo",
                    "mensagem": f"{len(df_baixo)} produto(s) abaixo do mínimo",
                    "prioridade": "alta",
                    "dados": df_baixo,
                })
        except Exception:
            pass

        # Validade
        try:
            df_vencendo = self.verificar_produtos_vencendo(dias_alerta=7)
            if not df_vencendo.empty:
                alertas.append({
                    "tipo": "validade",
                    "icone": "⏰",
                    "titulo": "Validade Próxima",
                    "mensagem": f"{len(df_vencendo)} produto(s) próximo(s) de vencer",
                    "prioridade": "alta",
                    "dados": df_vencendo,
                })
        except Exception:
            pass

        # Fiados vencidos
        try:
            df_fiados = self.listar_fiados(status='Pendente')
            if not df_fiados.empty:
                hoje = datetime.date.today().isoformat()
                vencidos = df_fiados[df_fiados['data_vencimento'] < hoje] if 'data_vencimento' in df_fiados.columns else pd.DataFrame()
                if not vencidos.empty:
                    alertas.append({
                        "tipo": "fiado_vencido",
                        "icone": "💳",
                        "titulo": "Fiados Vencidos",
                        "mensagem": f"{len(vencidos)} fiado(s) vencido(s)",
                        "prioridade": "alta",
                        "dados": vencidos,
                    })
        except Exception:
            pass

        # Contas a pagar vencidas
        try:
            df_contas = self.contas_vencidas()
            if not df_contas.empty:
                alertas.append({
                    "tipo": "conta_vencida",
                    "icone": "🔴",
                    "titulo": "Contas Vencidas",
                    "mensagem": f"{len(df_contas)} conta(s) a pagar vencida(s)",
                    "prioridade": "alta",
                    "dados": df_contas,
                })
        except Exception:
            pass

        # Contas vencendo em 7 dias
        try:
            df_vencendo_contas = self.contas_vencendo(dias=7)
            if not df_vencendo_contas.empty:
                alertas.append({
                    "tipo": "conta_vencendo",
                    "icone": "🟡",
                    "titulo": "Contas Próximas",
                    "mensagem": f"{len(df_vencendo_contas)} conta(s) a pagar nos próximos 7 dias",
                    "prioridade": "media",
                    "dados": df_vencendo_contas,
                })
        except Exception:
            pass

        return alertas
