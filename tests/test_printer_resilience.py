import sys
import os
import unittest
import time

# Adicionar o diretório pai ao sys.path para permitir importações corretas
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from printer import calcular_crc16_ccitt, gerar_payload_pix, PrintJob, PrintQueueManager
from invoice_generator import gerar_nota_txt

class TestPrinterResilience(unittest.TestCase):

    def test_crc16_ccitt(self):
        """Verifica a correção do cálculo matemático do CRC-16/CCITT-FALSE."""
        # 1 linha para explicar o motivo: O valor de teste clássico da string '123456789' deve resultar no hash hexadecimal '29B1'.
        test_str = "123456789"
        expected_crc = "29B1"
        self.assertEqual(calcular_crc16_ccitt(test_str), expected_crc)

    def test_gerar_payload_pix(self):
        """Verifica a montagem do payload Pix estático padrão BR Code."""
        # 1 linha para explicar o motivo: O payload deve conter os identificadores padrão da especificação EMV.
        chave = "12345678000190"
        valor = 10.50
        nome = "Estoke Ao Cubo"
        cidade = "Sao Paulo"
        
        payload = gerar_payload_pix(chave, valor, nome, cidade)
        
        # Validar assinaturas de tags obrigatórias
        self.assertTrue(payload.startswith("000201"))
        self.assertIn("26", payload)
        self.assertIn("br.gov.bcb.pix", payload)
        self.assertIn("540510.50", payload)  # Tag do valor e tamanho
        self.assertIn("5802BR", payload)      # Tag do país

    def test_print_queue_manager_deduplication(self):
        """Verifica que cliques simultâneos idênticos sofrem lock de duplicidade."""
        # 1 linha para explicar o motivo: Lock impede que o mesmo ID de venda saia impresso múltiplas vezes na bobina física.
        manager = PrintQueueManager.get_instance()
        job1 = PrintJob("Cupom Venda 123", job_id="venda_123")
        job2 = PrintJob("Cupom Venda 123", job_id="venda_123")
        
        # O primeiro deve ser enfileirado com sucesso
        self.assertTrue(manager.enqueue(job1))
        # O segundo, enviado imediatamente após, deve ser rejeitado por duplicidade
        self.assertFalse(manager.enqueue(job2))

    def test_invoice_generator_metadata_passing(self):
        """Verifica que a nota gerada possui a tag oculta de PIX_PAYLOAD transparente."""
        # 1 linha para explicar o motivo: O metadado é necessário para a transmissão transparente de bytes PIX sem romper assinaturas herdadas.
        itens = [{'nome': 'Caneca Personalizada', 'qtd': 1, 'preco': 20.0}]
        cliente = {'nome': 'Consumidor Final'}
        config = {
            'empresa_nome': 'Estoke Ao Cubo',
            'pix_key': '12.345.678/0001-90'
        }
        
        _, text_content, _ = gerar_nota_txt(
            itens, cliente, 20.0, id_venda="999", desconto=0.0,
            abrir_arquivo=False, config_nota=config
        )
        
        self.assertIn("[PIX_PAYLOAD:", text_content)


if __name__ == '__main__':
    unittest.main()
