"""Texto de los correos: plurales de verdad (nunca "(s)") y trato de usted. El cliente se quejó de correos con el texto mal
("1 oportunidades esperan seguimiento", "con la resolucion atrasada", "factura(s)")."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from app.core.texto import forma, plural, plural_de, pluralizar

RAIZ = Path(__file__).resolve().parents[1]

# los módulos que arman asuntos y cuerpos de correo (queue_email / notify_roles / queue_once) y el worker
CORREOS = [
    "app/routers/events.py",
    "app/routers/fieldwork.py",
    "app/routers/ops.py",
    "app/routers/projects.py",
    "app/services/cartera.py",
    "app/services/einvoice_delivery.py",
    "app/services/hacienda_tasks.py",
    "app/services/margin_guard.py",
    "app/services/sla.py",
    "app/tasks/cobranza.py",
    "app/tasks/correo.py",
    "app/tasks/fiscal.py",
    "app/tasks/mensualidades.py",
    "app/tasks/salud.py",
    "app/tasks/ventas.py",
    "app/tools/respaldo_diario.py",
    "../worker/celery_app.py",
]
PLURAL_FALSO = re.compile(r"[a-záéíóúñ]\((s|es)\)")


def test_plural_concuerda_con_la_cantidad():
    assert plural(1, "factura", "facturas") == "1 factura"
    assert plural(0, "factura", "facturas") == "0 facturas"
    assert plural(6, "factura", "facturas") == "6 facturas"
    assert plural(1, "oportunidad", "oportunidades") == "1 oportunidad"
    assert f"{plural(1, 'oportunidad', 'oportunidades')} {forma(1, 'espera', 'esperan')} seguimiento" == "1 oportunidad espera seguimiento"
    assert f"{plural(3, 'oportunidad', 'oportunidades')} {forma(3, 'espera', 'esperan')} seguimiento" == "3 oportunidades esperan seguimiento"


def test_plural_de_dias_fraccionarios():
    assert plural(1.0, "día", "días") == "1 día"
    assert plural(1.5, "día", "días") == "1,5 días"
    assert plural(2.0, "día", "días") == "2 días"
    assert plural(1500000, "factura", "facturas") == "1500000 facturas"


def test_pluralizar_frases_de_puntos_de_levantamiento():
    assert pluralizar("cámara") == "cámaras"
    assert pluralizar("punto de red") == "puntos de red"
    assert pluralizar("terminal") == "terminales"
    assert pluralizar("equipo a respaldar") == "equipos a respaldar"
    assert pluralizar("carril") == "carriles"
    assert plural_de(1, "puerta") == "1 puerta"
    assert plural_de(4, "puerta") == "4 puertas"


def test_los_correos_no_usan_plural_con_parentesis():
    """Ningún texto de los módulos de correo trae «factura(s)», «ticket(s)», «día(s)»…: se concuerda con plural()/forma()."""
    malos = []
    for rel in CORREOS:
        ruta = RAIZ / rel
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
        docs = set()
        for n in ast.walk(arbol):
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body:
                p = n.body[0]
                if isinstance(p, ast.Expr) and isinstance(getattr(p, "value", None), ast.Constant):
                    docs.add(id(p.value))
        for n in ast.walk(arbol):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs and "%s" not in n.value and PLURAL_FALSO.search(n.value):
                malos.append(f"{rel}:{n.lineno}: {n.value[:80]!r}")
    assert not malos, "Plural con paréntesis en un texto de correo:\n" + "\n".join(malos)


def test_asuntos_de_atrasos_de_ticket_con_tilde_y_plural():
    from app.services import sla

    codigo = (RAIZ / "app/services/sla.py").read_text(encoding="utf-8")
    assert "con la resolución atrasada" in codigo and "resolucion atrasada" not in codigo
    assert hasattr(sla, "avisar_atrasos")
