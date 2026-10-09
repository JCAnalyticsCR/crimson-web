"""Prepara un clip vertical de obra para el muro "Lo que ya está en línea" (CAM 06).

Uso (desde la raíz del repo; requiere ffmpeg y ffprobe en el PATH):

    python scripts/medios/optimizar-video.py ORIGEN.mp4 obra-porton --poster 2.0
    python scripts/medios/optimizar-video.py ORIGEN.mp4 obra-biometrico --hasta 6.5 --poster 2.5

Genera, sin audio y sin metadatos (fecha, GPS, modelo del teléfono):
    docs/assets/video/<slug>-960.mp4   540x960  H.264 High, 30 fps  (escritorio y visor ampliado)
    docs/assets/video/<slug>-640.mp4   360x640  H.264 High, 30 fps  (fichas en móvil)
    docs/assets/img/<slug>-poster.webp 540x960  fotograma nítido     (póster y reduced-motion)

Por qué así (diagnóstico de octubre 2026):
- Los clips del celular llegan a 60 o 120 fps. Tres videos a 120 fps a la vez, con un
  filtro CSS encima, botan fotogramas en una compu normal: "no reproducen". 30 fps sobra
  para una ficha de 260 px y deja el doble o el cuádruple de bits por fotograma.
- WhatsApp recomprime a 478x850. Si se puede, pedir el original (enviarlo como
  "Documento", no como video) y pasar ese: el script nunca agranda más de 540 px.
- +faststart pone el índice (moov) al inicio: el clip arranca sin bajarse completo.
- GOP de 1 s: el bucle y cualquier salto caen siempre cerca de un fotograma clave.

REVISAR SIEMPRE el clip antes de publicarlo: nada de nombres de clientes, rótulos de
condominios, placas, caras ni pantallas con contraseñas (usar --desde/--hasta para recortar).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
VIDEO = RAIZ / "docs" / "assets" / "video"
IMG = RAIZ / "docs" / "assets" / "img"

# (sufijo, ancho, alto, crf): CRF más bajo = más calidad y más peso.
SALIDAS = [("960", 540, 960, 23), ("640", 360, 640, 26)]


def sondear(origen: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=codec_name,profile,width,height,r_frame_rate:format=duration", "-of", "json", str(origen)],
        capture_output=True, text=True, check=True,
    ).stdout
    j = json.loads(out)
    return {**j["streams"][0], "duration": float(j["format"]["duration"])}


def recorte(args) -> list[str]:
    r = []
    if args.desde:
        r += ["-ss", str(args.desde)]
    if args.hasta:
        r += ["-to", str(args.hasta)]
    return r


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("origen", type=Path)
    p.add_argument("slug", help="nombre corto sin lugares ni clientes, p. ej. obra-porton")
    p.add_argument("--desde", type=float, default=0.0, help="segundo de inicio")
    p.add_argument("--hasta", type=float, default=None, help="segundo final (recorta pantallas o rótulos)")
    p.add_argument("--poster", type=float, default=1.5, help="segundo del fotograma del póster")
    args = p.parse_args()

    info = sondear(args.origen)
    print(f"origen: {info['codec_name']} {info.get('profile')} {info['width']}x{info['height']} "
          f"{info['r_frame_rate']} fps, {info['duration']:.2f} s")
    if info["width"] > info["height"]:
        print("aviso: el clip es horizontal; el muro usa fichas 9:16 y lo va a recortar.")

    VIDEO.mkdir(parents=True, exist_ok=True)
    for suf, w, h, crf in SALIDAS:
        destino = VIDEO / f"{args.slug}-{suf}.mp4"
        # Escala a cubrir 9:16 y recorta al centro; lanczos + un toque de nitidez al agrandar.
        vf = (f"fps=30,hqdn3d=1.2:1.2:4:4,scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
              f"crop={w}:{h},unsharp=5:5:0.25:5:5:0,format=yuv420p")
        cmd = ["ffmpeg", "-v", "error", "-y", *recorte(args), "-i", str(args.origen), "-vf", vf,
               "-c:v", "libx264", "-profile:v", "high", "-level:v", "4.0", "-preset", "veryslow",
               "-crf", str(crf), "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
               "-tune", "film", "-an", "-map_metadata", "-1", "-map_chapters", "-1",
               "-movflags", "+faststart", str(destino)]
        subprocess.run(cmd, check=True)
        print(f"  {destino.relative_to(RAIZ)}  {destino.stat().st_size / 1024:.0f} KB")

    poster = IMG / f"{args.slug}-poster.webp"
    t = max(0.0, args.poster - args.desde) if args.desde else args.poster
    w, h = SALIDAS[0][1], SALIDAS[0][2]
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *recorte(args), "-i", str(args.origen), "-ss", str(t), "-frames:v", "1",
         "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},unsharp=5:5:0.4:5:5:0",
         "-c:v", "libwebp", "-quality", "82", "-compression_level", "6", "-map_metadata", "-1", str(poster)],
        check=True,
    )
    print(f"  {poster.relative_to(RAIZ)}  {poster.stat().st_size / 1024:.0f} KB")
    print("\nEn index.html (sección #casos) la ficha usa:")
    print(f'  data-clip="assets/video/{args.slug}" data-poster="assets/img/{args.slug}-poster.webp"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
