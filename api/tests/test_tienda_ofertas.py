"""EX-OPT: casilla opcional y desmarcada de ofertas en el checkout. Se guarda en el pedido con su fecha y el panel lo
muestra; sin marcarla, el pedido queda en «no» (la Politica de privacidad promete publicidad solo a quien la acepte)."""

from tests.test_tienda_checkout import SLUG, _body, _shop


def _pedido(client, numero):
    return next(o for o in client.get("/store-orders").json()["items"] if o["number"] == numero)


def test_ofertas_solo_si_la_persona_marca_la_casilla(client, auth):
    prods = _shop(client)
    pid = prods[0]["id"]
    sin = client.post(f"{SLUG}/checkout", json=_body(pid, idempotency_key="ofertas-0001"))
    assert sin.status_code == 201, sin.text
    con = client.post(f"{SLUG}/checkout", json=_body(pid, idempotency_key="ofertas-0002", marketing_opt_in=True))
    assert con.status_code == 201, con.text
    a, b = _pedido(client, sin.json()["number"]), _pedido(client, con.json()["number"])
    assert a["marketing_opt_in"] is False and a["marketing_opt_in_at"] is None
    assert b["marketing_opt_in"] is True and b["marketing_opt_in_at"]
    # el detalle del pedido en el panel trae lo mismo
    det = client.get(f"/store-orders/{b['id']}").json()
    assert det["marketing_opt_in"] is True and det["marketing_opt_in_at"]
    # un valor que no es si/no se rechaza (nada de consentimientos ambiguos)
    assert client.post(f"{SLUG}/checkout", json=_body(pid, idempotency_key="ofertas-0003", marketing_opt_in="tal vez")).status_code == 422
