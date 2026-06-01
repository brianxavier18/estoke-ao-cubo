"""
Serviço de estoque — lógica de negócios para gestão de produtos e kits.
"""
import logging

logger = logging.getLogger('EstokeAoCubo.services.estoque')


class EstoqueService:
    """Lógica de negócios para gestão de estoque."""

    def __init__(self, db):
        self.db = db

    def produtos_abaixo_minimo(self):
        """Retorna produtos com estoque abaixo do mínimo configurado."""
        return self.db.produtos_abaixo_estoque_minimo()

    def produtos_vencendo(self, dias=7):
        """Retorna produtos com validade dentro dos próximos N dias."""
        return self.db.verificar_produtos_vencendo(dias_alerta=dias)

    def disponibilidade_kit(self, kit_id):
        """Calcula quantos kits podem ser montados com estoque atual."""
        return self.db.verificar_disponibilidade_kit(kit_id)

    def lista_reposicao(self):
        """Gera lista de produtos que precisam ser repostos.
        
        Returns:
            list[dict] com 'nome', 'estoque_atual', 'estoque_minimo', 'urgencia'
        """
        df = self.db.produtos_abaixo_estoque_minimo()
        resultado = []
        if df.empty:
            return resultado
        
        for _, row in df.iterrows():
            atual = int(row.get('quantidade', 0))
            minimo = int(row.get('estoque_minimo', 5))
            urgencia = 'critica' if atual == 0 else 'baixa'
            resultado.append({
                'nome': row.get('nome', ''),
                'estoque_atual': atual,
                'estoque_minimo': minimo,
                'repor': max(0, minimo - atual),
                'urgencia': urgencia,
            })
        
        # Ordenar: críticos primeiro
        resultado.sort(key=lambda x: (0 if x['urgencia'] == 'critica' else 1, x['nome']))
        return resultado
