"""Prepara una foto nueva para la galería "Así se ve tu operación" (CAM 02, #monitoreo).

Uso (desde la raíz del repo; requiere Python 3.10+ y Pillow: pip install pillow):

    python scripts/medios/optimizar-foto.py FOTO.jpg residencia-camara-porton \
        --rotulo "Cámara ColorVu en acceso residencial" --categoria Residencias

    # varias fotos de una vez, nombre = slug del archivo:
    python scripts/medios/optimizar-foto.py carpeta/*.jpg

Qué hace:
- Endereza según la orientación EXIF del teléfono y convierte a sRGB.
- Quita TODOS los metadatos: EXIF (GPS, fecha, modelo del teléfono), XMP, ICC y comentarios.
- Escribe en docs/assets/img/galeria/:
      <slug>-480.webp, <slug>-960.webp, <slug>-1600.webp   (solo los anchos menores o iguales
                                                             al original: nunca agranda)
      <slug>.jpg                                            (respaldo JPG del tamaño mayor)
- Imprime la línea lista para pegar en la lista de la galería (index.html, #galeria-data).

Foto recomendada: 1600 px o más de ancho, horizontal o vertical, sin zoom digital.
El nombre (slug) y el rótulo NUNCA llevan nombres de clientes, condominios ni lugares.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import unicodedata
from pathlib import Path

try:
    from PIL import Image, ImageCms, ImageOps
except ImportError:  # pragma: no cover
    sys.exit("Falta Pillow: pip install pillow")

RAIZ = Path(__file__).resolve().parents[2]
DESTINO = RAIZ / "docs" / "assets" / "img" / "galeria"
ANCHOS = (480, 960, 1600)  # la misma regla vive en main.js (anchosDe): no cambiar uno sin el otro
CALIDAD_WEBP = 78
CALIDAD_JPG = 82


def slugify(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


def anchos_para(ancho_original: int) -> list[int]:
    """480/960/1600 que quepan, y el original si es más angosto que 1600 (sin duplicados)."""
    tope = min(ancho_original, ANCHOS[-1])
    return sorted({w for w in ANCHOS if w < tope} | {tope})


def a_srgb(im: Image.Image) -> Image.Image:
    icc = im.info.get("icc_profile")
    if icc:
        try:
            origen = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            im = ImageCms.profileToProfile(im, origen, ImageCms.createProfile("sRGB"), outputMode="RGB")
        except Exception:  # perfil roto: seguimos sin convertir
            pass
    return im.convert("RGB")


def procesar(ruta: Path, slug: str) -> dict:
    with Image.open(ruta) as original:
        im = ImageOps.exif_transpose(original)  # endereza ANTES de tirar el EXIF
        im = a_srgb(im)
    # Imagen "limpia": se copian solo los píxeles, nada de info/exif/xmp heredado.
    limpia = Image.new("RGB", im.size)
    limpia.paste(im)

    DESTINO.mkdir(parents=True, exist_ok=True)
    escritos = []
    for w in anchos_para(limpia.width):
        h = round(limpia.height * w / limpia.width)
        copia = limpia if w == limpia.width else limpia.resize((w, h), Image.LANCZOS)
        out = DESTINO / f"{slug}-{w}.webp"
        copia.save(out, "WEBP", quality=CALIDAD_WEBP, method=6)
        escritos.append((out, w, h))
    mayor, w, h = escritos[-1]
    jpg = DESTINO / f"{slug}.jpg"
    (limpia if w == limpia.width else limpia.resize((w, h), Image.LANCZOS)).save(
        jpg, "JPEG", quality=CALIDAD_JPG, optimize=True, progressive=True)

    for out, *_ in escritos + [(jpg,)]:
        print(f"  {out.relative_to(RAIZ)}  {out.stat().st_size / 1024:.0f} KB")
    return {"archivo": slug, "ancho": w, "alto": h}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("fotos", nargs="+", type=Path, help="foto(s) de origen; si la última palabra no es archivo, es el slug")
    p.add_argument("--rotulo", default="", help='p. ej. "Cámara ColorVu en residencia de lujo"')
    p.add_argument("--categoria", default="", help="Residencias, Condominios, Comunidades, Comercios, "
                                                   "Control de acceso, Perímetro y enlaces, Redes y cableado")
    args = p.parse_args()

    fotos = list(args.fotos)
    slug_fijo = None
    if len(fotos) == 2 and not fotos[1].exists():
        slug_fijo = slugify(str(fotos.pop()))

    for ruta in fotos:
        if not ruta.is_file():
            print(f"no existe: {ruta}", file=sys.stderr)
            return 1
        slug = slug_fijo or slugify(ruta.stem)
        print(f"{ruta.name} -> {slug}")
        entrada = procesar(ruta, slug)
        entrada = {"archivo": entrada["archivo"], "rotulo": args.rotulo or "ESCRIBIR RÓTULO",
                   "categoria": args.categoria or "ESCRIBIR CATEGORÍA",
                   "ancho": entrada["ancho"], "alto": entrada["alto"]}
        print("\nPegar en index.html, dentro de #galeria-data:")
        print("  " + json.dumps(entrada, ensure_ascii=False) + ",\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
