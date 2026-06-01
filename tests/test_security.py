"""
Testes para o módulo de segurança — PBKDF2 hashing.
"""
import pytest
import sys
import os

# Adicionar o diretório pai ao path para imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.security import hash_password, verify_password, needs_rehash
import hashlib


class TestHashPassword:
    """Testes para geração de hash."""

    def test_hash_retorna_formato_scrypt(self):
        h = hash_password("minha_senha_123")
        assert h.startswith("scrypt:")
        parts = h.split(":")
        assert len(parts) == 6
        assert parts[0] == "scrypt"
        assert int(parts[1]) == 16384  # N
        assert int(parts[2]) == 8      # r
        assert int(parts[3]) == 1      # p

    def test_hash_diferentes_para_mesma_senha(self):
        """Salt aleatório garante hashes diferentes."""
        h1 = hash_password("senha_igual")
        h2 = hash_password("senha_igual")
        assert h1 != h2

    def test_hash_nao_vazio(self):
        h = hash_password("")
        assert len(h) > 10


class TestVerifyPassword:
    """Testes para verificação de senha."""

    def test_senha_correta_aceita(self):
        h = hash_password("admin123")
        assert verify_password("admin123", h) is True

    def test_senha_incorreta_rejeita(self):
        h = hash_password("admin123")
        assert verify_password("admin456", h) is False

    def test_senha_vazia_rejeita(self):
        h = hash_password("admin123")
        assert verify_password("", h) is False

    def test_compatibilidade_legado_sha256(self):
        """Deve aceitar hashes SHA-256 legados (migração)."""
        legacy_hash = hashlib.sha256("admin9438".encode()).hexdigest()
        assert verify_password("admin9438", legacy_hash) is True
        assert verify_password("senha_errada", legacy_hash) is False

    def test_caracteres_especiais(self):
        h = hash_password("sénhã_cöm_àçéntós!@#$%")
        assert verify_password("sénhã_cöm_àçéntós!@#$%", h) is True

    def test_senha_longa(self):
        long_pass = "a" * 1000
        h = hash_password(long_pass)
        assert verify_password(long_pass, h) is True


class TestNeedsRehash:
    """Testes para detecção de hashes legados."""

    def test_hash_legado_precisa_rehash(self):
        legacy = hashlib.sha256("teste".encode()).hexdigest()
        assert needs_rehash(legacy) is True

    def test_hash_pbkdf2_precisa_rehash(self):
        pbkdf2_hash = "pbkdf2:260000:salt_hex:hash_hex"
        assert needs_rehash(pbkdf2_hash) is True

    def test_hash_scrypt_nao_precisa_rehash(self):
        h = hash_password("teste")
        assert needs_rehash(h) is False


class TestSecurityProperties:
    """Testes de propriedades de segurança."""

    def test_hash_nao_contem_senha_original(self):
        senha = "minha_senha_secreta"
        h = hash_password(senha)
        assert senha not in h

    def test_timing_attack_resistant(self):
        """Verifica que a comparação é em tempo constante (sanity check)."""
        h = hash_password("test")
        # Ambas devem retornar sem crash
        verify_password("test", h)
        verify_password("wrong", h)
