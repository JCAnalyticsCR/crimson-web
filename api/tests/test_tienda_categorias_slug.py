"""O3-07a: la direccion /c/<slug> queda guardada; renombrar deja la vieja con 301 y la nueva con 200.

- Sin renombrar nada, todas las /c/ del sitemap responden igual que antes (el relleno de la migracion es la misma
  regla con la que se calculaban, con nombres repetidos y categorias ocultas incluidas).
- Renombrar: la vieja responde 401 con X-Store-Redirect (nginx la convierte en 301), la nueva 204 y una que no es de
  nadie 403 (404 real). El catalogo y el SEO resuelven la vieja con la canonica de la vigente.
- El snapshot lleva la descripcion de la categoria (antes siempre nula en ese modo) y sus direcciones viejas.
Datos ficticios."""

import importlib.util
import re
from pathlib import Path

from sqlalchemy import select

from app.models import Category, CategorySlugAlias, Tenant
from app.services import category_paths
from app.services import store_showcase as sv
from tests.test_store_vitrina import _catalogo, _publicar

H = {"X-Store-Host": "tienda.crimsoncr.com"}
SLUG = "/public/store/crimson"
MIG = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "tienda_o3_categorias_f1b7d3a9c524.py"


def _exists(client, path):
    return client.get("/public/seo/exists", headers={**H, "X-Original-URI": path})


def _old_rule(cats):
    """la regla de antes de O3-07a, copiada tal cual: slug del nombre entre las visibles, por id"""
    out, seen = {}, set()
    for c in sorted(cats, key=lambda c: c.id):
        s = sv.slugify(c.name, 60)
        if s in seen:
            s = f"{s}-{c.id}"
        seen.add(s)
        out[c.id] = s
    return out


def test_relleno_de_la_migracion_da_las_mismas_direcciones(client, auth, db_session):
    _publicar(client)
    _catalogo(db_session)
    t = db_session.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    vig = db_session.scalar(select(Category).where(Category.tenant_id == t.id, Category.name == "Videovigilancia"))
    # nombres repetidos (la segunda lleva el id) y una oculta con el mismo nombre que una visible
    dup = Category(tenant_id=t.id, name="Videovigilancia", show_on_web=True)
    oculta = Category(tenant_id=t.id, name="Redes", show_on_web=False)
    db_session.add_all([dup, oculta])
    db_session.flush()
    for p in db_session.scalars(select(sv.Product).where(sv.Product.category_id == vig.id)).all()[:1]:
        p.category_id = dup.id
    db_session.commit()
    visibles = list(db_session.scalars(select(Category).where(Category.tenant_id == t.id, Category.show_on_web)))
    antes = _old_rule(visibles)
    from app.services import store_snapshot

    store_snapshot.rebuild(db_session, t.id)
    xml_antes = client.get("/public/seo/sitemap.xml", headers=H).text
    cats_antes = re.findall(r"<loc>https://tienda\.crimsoncr\.com(/c/[^<]+)</loc>", xml_antes)
    assert cats_antes and antes[dup.id] == f"videovigilancia-{dup.id}"

    spec = importlib.util.spec_from_file_location("mig_o3_07a", MIG)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    mig._backfill(db_session.connection())
    db_session.commit()
    db_session.expire_all()
    guardados = {c.id: c.slug for c in db_session.scalars(select(Category).where(Category.tenant_id == t.id))}
    assert all(guardados.values())
    assert {i: guardados[i] for i in antes} == antes  # las visibles: exactamente las de antes
    assert len(set(guardados.values())) == len(guardados)  # la oculta no choca con nadie
    assert guardados[oculta.id] != guardados[next(c.id for c in visibles if c.name == "Redes")]

    store_snapshot.rebuild(db_session, t.id)
    xml = client.get("/public/seo/sitemap.xml", headers=H).text
    assert re.findall(r"<loc>https://tienda\.crimsoncr\.com(/c/[^<]+)</loc>", xml) == cats_antes
    for path in cats_antes:
        assert _exists(client, path).status_code == 204, path
        assert client.get(f"{SLUG}/catalog", params={"category": path.split("/")[-1]}).status_code == 200


def test_renombrar_deja_301_la_nueva_200_y_lo_inexistente_404(client, auth, db_session):
    _publicar(client)
    _catalogo(db_session)
    t = db_session.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    alm = db_session.scalar(select(Category).where(Category.tenant_id == t.id, Category.name == "Almacenamiento"))
    alm.description = "Discos para grabadores."
    db_session.commit()
    r = client.patch(f"/categories/{alm.id}", json={"name": "Discos y almacenamiento"})
    assert r.status_code == 200, r.text
    assert r.json()["slug"] == "discos-y-almacenamiento"
    viejo = _exists(client, "/c/almacenamiento")
    assert viejo.status_code == 401 and viejo.headers["X-Store-Redirect"] == "/c/discos-y-almacenamiento"
    assert _exists(client, "/c/discos-y-almacenamiento").status_code == 204
    assert _exists(client, "/c/no-existe-esta").status_code == 403
    assert _exists(client, "/c/videovigilancia").status_code == 204  # las demas no se mueven
    # el catalogo y el SEO resuelven la vieja con la vigente (respaldo si el 301 no llega)
    cat = client.get(f"{SLUG}/catalog", params={"category": "almacenamiento"}).json()
    assert cat["category"]["slug"] == "discos-y-almacenamiento" and cat["total"] == 2
    seo = client.get("/public/seo/head/c/almacenamiento", headers=H).text
    assert 'rel="canonical" href="https://tienda.crimsoncr.com/c/discos-y-almacenamiento"' in seo
    # el snapshot lleva la descripcion y la direccion vieja; la portada manda el slug guardado
    snap = client.get(f"{SLUG}/snapshot").json()

    def flat(nodes):
        for n in nodes:
            yield n
            yield from flat(n.get("children") or [])

    node = next(n for n in flat(snap["categories"]) if n["id"] == alm.id)
    assert node["slug"] == "discos-y-almacenamiento" and node["aliases"] == ["almacenamiento"]
    assert node["description"] == "Discos para grabadores."
    home = client.get(SLUG).json()
    assert next(c for c in home["categories"] if c["id"] == alm.id)["slug"] == "discos-y-almacenamiento"
    # volver al nombre de antes: la vieja vuelve a ser la vigente (sin alias colgando)
    assert client.patch(f"/categories/{alm.id}", json={"name": "Almacenamiento"}).json()["slug"] == "almacenamiento"
    assert _exists(client, "/c/almacenamiento").status_code == 204
    assert _exists(client, "/c/discos-y-almacenamiento").status_code == 401
    assert {a.slug for a in db_session.scalars(select(CategorySlugAlias))} == {"discos-y-almacenamiento"}


def test_categoria_nueva_nace_con_slug_sin_chocar(client, auth, db_session):
    r = client.post("/categories", json={"name": "Cámaras"})
    r2 = client.post("/categories", json={"name": "Camaras"})
    assert r.json()["slug"] == "camaras" and r2.json()["slug"] == f"camaras-{r2.json()['id']}"
    t = db_session.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    c = db_session.get(Category, r.json()["id"])
    assert category_paths.rename(db_session, c, "Cámaras") is None  # mismo slug: nada que redirigir
    assert category_paths.aliases(db_session, t.id) == {}
