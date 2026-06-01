import os
import json
import copy
import logging
from database import get_app_data_path

logger = logging.getLogger('EstokeAoCubo.config')

CONFIG_FILE = os.path.join(get_app_data_path(), "config.json")

default_config = {
    "printer": {
        "enabled": True,
        "type": "windows",
        "interface": "file",
        "settings": {}
    }
}


def load_config():
    """Lê o arquivo de configuração ou retorna padrões."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            # se falhar na leitura, retornar cópia dos padrões
            return copy.deepcopy(default_config)
    else:
        return copy.deepcopy(default_config)


def save_config(config):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        logger.error("Erro ao salvar configuração: %s", e)


def get_printer_config():
    cfg = load_config()
    return cfg.get("printer", default_config["printer"])


def set_printer_config(printer_cfg):
    cfg = load_config()
    cfg["printer"] = printer_cfg
    save_config(cfg)


def get_business_type():
    """Retorna o tipo de negócio configurado ou None se primeiro acesso."""
    cfg = load_config()
    return cfg.get("business_type", None)


def set_business_type(btype):
    """Define o tipo de negócio: artesanato, padaria_acougue, loja_roupa, variados."""
    cfg = load_config()
    cfg["business_type"] = btype
    save_config(cfg)


# ====== CREDENCIAIS DE ADMINISTRADOR ======

from services.security import hash_password, verify_password, needs_rehash

_DEFAULT_ADMIN_USER = "admin"
_DEFAULT_ADMIN_PASSWORD = "admin"


def _ensure_admin_configured():
    """Garante que o admin tenha credenciais configuradas.
    Se não houver, marca como 'primeiro acesso' para forçar configuração."""
    cfg = load_config()
    admin = cfg.get("admin", {})
    return bool(admin.get("password_hash"))


def get_admin_credentials():
    """Retorna (usuario, hash_senha) do admin."""
    cfg = load_config()
    admin = cfg.get("admin", {})
    user = admin.get("user", _DEFAULT_ADMIN_USER)
    pw_hash = admin.get("password_hash", "")
    return user, pw_hash


def set_admin_credentials(new_user, new_password):
    """Salva novas credenciais de admin com hash seguro PBKDF2."""
    cfg = load_config()
    cfg["admin"] = {
        "user": new_user,
        "password_hash": hash_password(new_password),
    }
    save_config(cfg)


def verify_admin(user, password):
    """Verifica se usuário e senha conferem. Migra hash legado se necessário."""
    stored_user, stored_hash = get_admin_credentials()
    # Se não houver hash configurado, aceitar credenciais padrão
    if not stored_hash:
        if user == _DEFAULT_ADMIN_USER and password == _DEFAULT_ADMIN_PASSWORD:
            # Salvar as credenciais padrão com hash seguro no primeiro acesso
            set_admin_credentials(_DEFAULT_ADMIN_USER, _DEFAULT_ADMIN_PASSWORD)
            return True
        return False
    if user != stored_user:
        return False
    if verify_password(password, stored_hash):
        # Migrar hash legado SHA-256 para PBKDF2 automaticamente
        if needs_rehash(stored_hash):
            set_admin_credentials(user, password)
        return True
    return False


def admin_needs_setup():
    """Retorna True se o admin ainda não configurou credenciais."""
    return not _ensure_admin_configured()
