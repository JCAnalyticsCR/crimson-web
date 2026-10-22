"""22/10: cada correo sale tambien en texto plano (los filtros de spam castigan el correo que trae solo HTML)."""

from types import SimpleNamespace

from app.services import email_layout as el
from app.services import mail


def test_html_a_texto_sin_lo_oculto_y_con_enlaces():
    h = (
        "<html><head><style>p{color:red}</style></head><body>" + el._preheader("Texto de vista previa")
        + "<table><tr><td><h1>Pedido ORD-1</h1></td></tr><tr><td><p>Hola,</p><p>Su pedido fue <b>entregado</b>.</p>"
        + el.button("https://tienda.ejemplo.com/p/1", "Ver pedido") + "</td></tr></table></body></html>"
    )
    t = el.html_to_text(h)
    assert "Texto de vista previa" not in t and "color:red" not in t
    assert "Su pedido fue entregado." in t and "Ver pedido (https://tienda.ejemplo.com/p/1)" in t
    assert "\n\n\n" not in t


def test_resend_recibe_la_version_en_texto(monkeypatch):
    enviado = {}

    class R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "x1"}

    def post(url, headers=None, json=None, timeout=None):
        enviado.update(json)
        return R()

    monkeypatch.setenv("RESEND_API_KEY", "re_prueba")
    monkeypatch.setattr(mail.httpx, "post", post)
    m = SimpleNamespace(to="cliente@ejemplo.com", bcc=None, subject="Hola", html="<p>Gracias por su compra</p>", attempts=0,
                        entity="invoice", status=None, provider_id=None, sent_at=None, next_attempt_at=None, error=None)
    mail.deliver(m)
    assert m.status == "enviado" and enviado["text"] == "Gracias por su compra" and enviado["html"].startswith("<p>")
