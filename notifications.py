"""
Sistema de notificações toast para o EstokeAoCubo.
Exibe alertas visuais não-bloqueantes dentro da janela da aplicação.
"""
import customtkinter as ctk
import logging

logger = logging.getLogger('EstokeAoCubo.notifications')


class ToastNotification(ctk.CTkFrame):
    """Notificação toast que aparece no canto da tela e desaparece automaticamente."""

    CORES = {
        "info":    {"bg": "#E3F2FD", "border": "#2196F3", "text": "#0D47A1", "icon": "ℹ️"},
        "success": {"bg": "#E8F5E9", "border": "#4CAF50", "text": "#1B5E20", "icon": "✅"},
        "warning": {"bg": "#FFF3E0", "border": "#FF9800", "text": "#E65100", "icon": "⚠️"},
        "error":   {"bg": "#FFEBEE", "border": "#F44336", "text": "#B71C1C", "icon": "🔴"},
    }

    def __init__(self, parent, titulo, mensagem, tipo="info", duracao=5000, on_click=None):
        cores = self.CORES.get(tipo, self.CORES["info"])

        super().__init__(parent, fg_color=cores["bg"], corner_radius=12,
                        border_width=2, border_color=cores["border"])

        self._on_click = on_click
        self._duracao = duracao

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(padx=12, pady=8, fill="x")

        # Ícone + Título
        header = ctk.CTkFrame(content, fg_color="transparent")
        header.pack(fill="x")

        ctk.CTkLabel(header, text=cores["icon"], font=("Segoe UI Emoji", 16)).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(header, text=titulo, font=("Aileron", 12, "bold"),
                    text_color=cores["text"]).pack(side="left", fill="x", expand=True)

        # Botão fechar
        close_btn = ctk.CTkButton(header, text="✕", width=24, height=24, corner_radius=12,
                                  fg_color="transparent", hover_color="#E0E0E0",
                                  text_color=cores["text"], font=("Aileron", 12),
                                  command=self._fechar)
        close_btn.pack(side="right")

        # Mensagem
        ctk.CTkLabel(content, text=mensagem, font=("Aileron", 10),
                    text_color=cores["text"], wraplength=280, justify="left").pack(anchor="w", pady=(2, 0))

        # Clicável
        if on_click:
            self.bind("<Button-1>", lambda e: self._handle_click())
            content.bind("<Button-1>", lambda e: self._handle_click())

        # Auto-fechar
        if duracao > 0:
            self._after_id = self.after(duracao, self._fechar)

    def _fechar(self):
        try:
            self.destroy()
        except Exception:
            pass

    def _handle_click(self):
        if self._on_click:
            self._on_click()
        self._fechar()


class NotificationManager:
    """Gerencia a exibição de notificações toast na janela principal."""

    def __init__(self, parent_widget, max_visible=4):
        self.parent = parent_widget
        self.max_visible = max_visible
        self._toasts = []
        self._container = None

    def _ensure_container(self):
        if self._container is None or not self._container.winfo_exists():
            self._container = ctk.CTkFrame(self.parent, fg_color="transparent")
            self._container.place(relx=1.0, rely=0.0, anchor="ne", x=-20, y=80)

    def notify(self, titulo, mensagem, tipo="info", duracao=5000, on_click=None):
        """Exibe uma notificação toast."""
        self._ensure_container()
        self._limpar_destruidos()

        # Limitar quantidade visível
        while len(self._toasts) >= self.max_visible:
            oldest = self._toasts.pop(0)
            try:
                oldest.destroy()
            except Exception:
                pass

        toast = ToastNotification(self._container, titulo, mensagem, tipo, duracao, on_click)
        toast.pack(pady=4, padx=4, fill="x")
        self._toasts.append(toast)

        # Agendar limpeza após duração
        if duracao > 0:
            self.parent.after(duracao + 500, self._limpar_destruidos)

    def _limpar_destruidos(self):
        self._toasts = [t for t in self._toasts if t.winfo_exists()]

    def notify_alertas(self, alertas):
        """Exibe alertas do sistema como toasts."""
        for alerta in alertas:
            tipo_map = {"alta": "error", "media": "warning", "baixa": "info"}
            tipo = tipo_map.get(alerta.get('prioridade', 'info'), 'info')
            icone = alerta.get('icone', '')
            self.notify(
                f"{icone} {alerta['titulo']}",
                alerta['mensagem'],
                tipo=tipo,
                duracao=8000,
            )
