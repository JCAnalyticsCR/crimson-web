#!/usr/bin/env bash
# Guion de humo posdespliegue de la tienda (plan de la tienda, O0-06). Se corre UNA vez despues de cada despliegue
# (infra/DESPLIEGUE.md, seccion 6). Solo GET, 9 peticiones (tope duro: 12), cada una con 8 s de limite; en total
# tiene que terminar en menos de 30 s. Sale con 0 si todo esta bien y con 1 si algo falla (2 = mal uso).
#
# Revisa:
#   1. portada: 200 y el <title> esperado
#   2. /robots.txt: 200, la linea Sitemap y «Allow: /api/public/» (O1-01)
#   3. /sitemap.xml: 200; cuantas direcciones /p/ trae
#   4. /api/public/store/<slug>: 200 en JSON y con X-Robots-Tag: noindex (O0-05a)
#   5. snapshot: su "n" frente a las /p/ del sitemap (mas o menos HUMO_TOLERANCIA); con HUMO_N_ESPERADO, frente a ese
#      numero (y el sitemap solo avisa); nunca por debajo de HUMO_N_MIN
#   6. una ficha y 7. una categoria del sitemap: 200, sin noindex, con canonica a su propia direccion
#   8. /catalogo: 200 con <title>
#   9. una ficha inexistente: 404 de verdad (no un 200 blando)
#
# Uso:   bash scripts/humo_tienda.sh [URL_BASE]
# Variables (todas opcionales):
#   TIENDA_URL            base de la tienda (por defecto https://tienda.crimsoncr.com; el argumento manda)
#   TIENDA_SLUG           slug de la tienda en el API (crimson)
#   HUMO_TITULO           texto que el <title> de la portada tiene que contener (Crimson)
#   HUMO_TOLERANCIA       diferencia permitida entre el sitemap (o HUMO_N_ESPERADO) y el snapshot (5)
#   HUMO_N_ESPERADO       n del snapshot de la ultima corrida buena: mientras el sitemap y el snapshot no coincidan
#                         (hasta O1-07) se compara contra este numero, y la diferencia con el sitemap solo avisa
#   HUMO_N_MIN            el snapshot no puede traer menos productos que esto (por defecto 1)
#   HUMO_EXIGIR_NOINDEX   1 = el JSON del API tiene que llevar noindex (por defecto); 0 = solo avisa (antes de O0-05a)
#   HUMO_HOST             manda esta cabecera Host (probar un nginx local como si fuera tienda.*)
# Prueba del propio guion (tiene que fallar):  TIENDA_SLUG=crimsom bash scripts/humo_tienda.sh
set -u

BASE="${1:-${TIENDA_URL:-https://tienda.crimsoncr.com}}"
BASE="${BASE%/}"
SLUG="${TIENDA_SLUG:-crimson}"
TITULO="${HUMO_TITULO:-Crimson}"
TOL="${HUMO_TOLERANCIA:-5}"
N_ESPERADO="${HUMO_N_ESPERADO:-}"
N_MIN="${HUMO_N_MIN:-1}"
case "$TOL$N_MIN$N_ESPERADO" in *[!0-9]*) echo "HUMO_TOLERANCIA, HUMO_N_MIN y HUMO_N_ESPERADO son enteros" >&2; exit 2 ;; esac
EXIGIR_NOINDEX="${HUMO_EXIGIR_NOINDEX:-1}"
MAX=12
UA="crimson-humo/1 (posdespliegue; solo GET)"

command -v curl >/dev/null 2>&1 || { echo "falta curl" >&2; exit 2; }
case "$BASE" in http://* | https://*) ;; *) echo "URL_BASE invalida: $BASE" >&2; exit 2 ;; esac

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fallas=0
pedidas=0
inicio=$(date +%s)

ok() { printf 'OK     %s\n' "$*"; }
falla() { printf 'FALLA  %s\n' "$*"; fallas=$((fallas + 1)); }
aviso() { printf 'AVISO  %s\n' "$*"; }

# get NOMBRE URL [opciones de curl]: cuerpo en $tmp/NOMBRE.b, cabeceras en $tmp/NOMBRE.h; deja el estado en $CODE
get() {
  local n="$1" url="$2"
  shift 2
  pedidas=$((pedidas + 1))
  if [ "$pedidas" -gt "$MAX" ]; then
    echo "el guion paso su tope de $MAX peticiones (error del guion)" >&2
    exit 2
  fi
  local extra=()
  [ -n "${HUMO_HOST:-}" ] && extra=(-H "Host: $HUMO_HOST")
  CODE="$(curl -sS -o "$tmp/$n.b" -D "$tmp/$n.h" -w '%{http_code}' --max-time 8 -A "$UA" ${extra[@]+"${extra[@]}"} "$@" "$url" 2>"$tmp/$n.e")"
  [ -n "$CODE" ] || CODE=000
  touch "$tmp/$n.b" "$tmp/$n.h"
}
# hdr NOMBRE Cabecera -> valor (ultima aparicion, sin \r)
hdr() { grep -i "^$2:" "$tmp/$1.h" | tail -n 1 | cut -d: -f2- | tr -d '\r' | sed 's/^ *//'; }
# titulo NOMBRE -> texto del primer <title>
titulo() { tr '\n' ' ' <"$tmp/$1.b" | grep -io '<title[^>]*>[^<]*</title>' | head -n 1 | sed 's/<[^>]*>//g'; }
# contiene TEXTO BUSCADO: sin distinguir mayusculas (sin grep -iF: el grep de Git para Windows aborta con eso)
contiene() {
  local a b
  a="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  b="$(printf '%s' "$2" | tr '[:upper:]' '[:lower:]')"
  case "$a" in *"$b"*) return 0 ;; *) return 1 ;; esac
}
# ruta URL -> solo la ruta (sin esquema ni dominio): el sitemap trae el dominio que el API cree, se pide sobre BASE
ruta() { printf '%s' "$1" | sed -E 's#^https?://[^/]+##'; }
# indexable NOMBRE QUE: sin noindex ni en la cabecera ni en la meta robots
indexable() {
  local xr meta
  xr="$(hdr "$1" X-Robots-Tag)"
  meta="$(tr '\n' ' ' <"$tmp/$1.b" | grep -io '<meta[^>]*name="robots"[^>]*>' | head -n 1)"
  if printf '%s %s' "$xr" "$meta" | grep -qi noindex; then
    falla "$2: marcada noindex (X-Robots-Tag: '${xr}'; meta: '${meta}')"
    return 1
  fi
}
# canonica NOMBRE RUTA QUE: <link rel="canonical"> que apunta a esa misma ruta
canonica() {
  local href
  href="$(tr '\n' ' ' <"$tmp/$1.b" | grep -io '<link[^>]*rel="canonical"[^>]*>' | head -n 1 | sed -nE 's/.*href="([^"]*)".*/\1/p')"
  if [ -z "$href" ]; then
    falla "$3: sin <link rel=\"canonical\"> (el SSI no trajo el <head> del API?)"
    return 1
  fi
  if [ "$(ruta "$href")" != "$2" ]; then
    falla "$3: la canonica apunta a $href y no a $2"
    return 1
  fi
}

echo "Humo de la tienda: $BASE (slug $SLUG)"

# 1. portada
get portada "$BASE/"
t="$(titulo portada)"
if [ "$CODE" != 200 ]; then
  falla "portada: estado $CODE $(cat "$tmp/portada.e")"
elif ! contiene "$t" "$TITULO"; then
  falla "portada: el <title> es '${t}' y no contiene '$TITULO'"
else
  ok "portada 200 · <title> '${t}'"
fi

# 2. robots.txt
get robots "$BASE/robots.txt"
if [ "$CODE" != 200 ]; then
  falla "robots.txt: estado $CODE"
elif ! tr -d '\r' <"$tmp/robots.b" | grep -qiE '^Sitemap:[[:space:]]*https?://[^[:space:]]+'; then
  falla "robots.txt: sin la linea Sitemap"
elif ! tr -d '\r' <"$tmp/robots.b" | grep -qx 'Allow: /api/public/'; then
  # O1-01: sin esta linea el buscador no puede cargar los datos de la vitrina y ve «Tienda no disponible»
  falla "robots.txt: sin «Allow: /api/public/» (O1-01)"
else
  ok "robots.txt 200 · $(tr -d '\r' <"$tmp/robots.b" | grep -iE '^Sitemap:' | head -n 1)"
fi

# 3. sitemap
get sitemap "$BASE/sitemap.xml"
locs="$tmp/locs.txt"
grep -o '<loc>[^<]*</loc>' "$tmp/sitemap.b" | sed 's#</\?loc>##g' >"$locs"
n_sitemap=$(grep -c '/p/' "$locs")
ficha="$(grep -m 1 '/p/' "$locs")"
categoria="$(grep -m 1 '/c/' "$locs")"
if [ "$CODE" != 200 ]; then
  falla "sitemap.xml: estado $CODE"
elif [ "$n_sitemap" -eq 0 ]; then
  falla "sitemap.xml: ninguna direccion /p/"
else
  ok "sitemap.xml 200 · $n_sitemap fichas, $(grep -c '/c/' "$locs") categorias"
fi

# 4. JSON publico de la tienda
get api "$BASE/api/public/store/$SLUG"
ct="$(hdr api Content-Type)"
xr="$(hdr api X-Robots-Tag)"
if [ "$CODE" != 200 ]; then
  falla "/api/public/store/$SLUG: estado $CODE"
elif ! printf '%s' "$ct" | grep -qi 'application/json' || [ "$(head -c 1 "$tmp/api.b")" != "{" ]; then
  falla "/api/public/store/$SLUG: no es JSON (Content-Type '${ct}')"
else
  ok "/api/public/store/$SLUG 200 JSON"
fi
if [ "$CODE" = 200 ]; then
  if printf '%s' "$xr" | grep -qi noindex; then
    ok "/api/public/store/$SLUG con X-Robots-Tag: $xr"
  elif [ "$EXIGIR_NOINDEX" = 1 ]; then
    falla "/api/public/store/$SLUG sin X-Robots-Tag: noindex (O0-05a)"
  else
    aviso "/api/public/store/$SLUG sin X-Robots-Tag: noindex (HUMO_EXIGIR_NOINDEX=0)"
  fi
fi

# 5. snapshot frente al sitemap
get snapshot "$BASE/api/public/store/$SLUG/snapshot" --compressed
# la clave "n" de la raiz con un lector de JSON si hay Python; si no, la primera "n" (el API la escribe primero)
n_snap=""
for py in python3 python; do
  if command -v "$py" >/dev/null 2>&1; then
    n_snap="$("$py" -c 'import json,sys; v=json.load(open(sys.argv[1],encoding="utf-8")).get("n"); print(v if isinstance(v,int) else "")' "$tmp/snapshot.b" 2>/dev/null)"
    break
  fi
done
[ -z "$n_snap" ] && n_snap="$(grep -o '"n"[[:space:]]*:[[:space:]]*[0-9]\+' "$tmp/snapshot.b" | head -n 1 | grep -o '[0-9]\+$')"
if [ "$CODE" != 200 ] || [ -z "$n_snap" ]; then
  falla "snapshot: estado $CODE, sin el campo n"
else
  dif=$((n_sitemap - n_snap))
  [ "$dif" -lt 0 ] && dif=$((-dif))
  if [ "$n_snap" -lt "$N_MIN" ]; then
    falla "snapshot con n=$n_snap, menos que el minimo $N_MIN"
  fi
  if [ -n "$N_ESPERADO" ]; then
    # con HUMO_N_ESPERADO la referencia es la ultima corrida buena (antes de O1-07 el sitemap traia los ocultos)
    dife=$((N_ESPERADO - n_snap))
    [ "$dife" -lt 0 ] && dife=$((-dife))
    if [ "$dife" -gt "$TOL" ]; then
      falla "snapshot con n=$n_snap y se esperaban $N_ESPERADO (diferencia $dife > $TOL)"
    else
      ok "snapshot n=$n_snap · esperado $N_ESPERADO (diferencia $dife, tolerancia $TOL)"
    fi
    [ "$dif" -gt "$TOL" ] && aviso "sitemap con $n_sitemap fichas y snapshot con n=$n_snap (diferencia $dif; desde O1-07 el sitemap sale del snapshot y deberian coincidir)"
  elif [ "$dif" -gt "$TOL" ]; then
    falla "sitemap con $n_sitemap fichas y snapshot con n=$n_snap (diferencia $dif > $TOL)"
  else
    ok "snapshot n=$n_snap · sitemap $n_sitemap (diferencia $dif, tolerancia $TOL)"
  fi
fi

# 6. y 7. una ficha y una categoria tomadas del sitemap
for par in "ficha:$ficha" "categoria:$categoria"; do
  que="${par%%:*}"
  url="${par#*:}"
  if [ -z "$url" ]; then
    falla "$que: el sitemap no trae ninguna"
    continue
  fi
  r="$(ruta "$url")"
  get "$que" "$BASE$r"
  if [ "$CODE" != 200 ]; then
    falla "$que $r: estado $CODE"
    continue
  fi
  indexable "$que" "$que $r" && canonica "$que" "$r" "$que $r" && ok "$que $r 200 · indexable · canonica propia"
done

# 8. catalogo
get catalogo "$BASE/catalogo"
t="$(titulo catalogo)"
if [ "$CODE" != 200 ] || [ -z "$t" ]; then
  falla "/catalogo: estado $CODE, <title> '${t}'"
else
  ok "/catalogo 200 · <title> '${t}'"
fi

# 9. ficha inexistente: 404 real
get inexistente "$BASE/p/humo-producto-inexistente-999999999"
if [ "$CODE" != 404 ]; then
  falla "ficha inexistente: estado $CODE (se esperaba 404 real)"
else
  ok "ficha inexistente 404"
fi

seg=$(($(date +%s) - inicio))
if [ "$seg" -ge 30 ]; then
  falla "el guion tardo ${seg} s (limite 30 s)"
fi
echo "---- $pedidas peticiones GET en ${seg} s · $fallas falla(s)"
[ "$fallas" -eq 0 ] || exit 1
exit 0
