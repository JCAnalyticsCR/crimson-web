"""Arma docs/nuevo/index.html con shell.html + las partes de parts/ en orden de nombre (1-*, 2-*, ...).
Cada parte: N-nombre.html (obligatorio), N-nombre.css y N-nombre.js (opcionales). Uso: python armar.py
Las versiones (?v=) salen de la fecha de modificacion, asi el navegador nunca usa una copia vieja."""

import os
from pathlib import Path

AQUI = Path(__file__).resolve().parent
PARTES = AQUI / "parts"


def version(p: Path) -> str:
    return str(int(p.stat().st_mtime)) if p.exists() else "0"


def main() -> None:
    shell = (AQUI / "shell.html").read_text(encoding="utf-8")
    html, css, js = [], [], []
    for h in sorted(PARTES.glob("*.html")):
        base = h.with_suffix("")
        html.append(f"<!-- ===== parte {h.name} ===== -->\n" + h.read_text(encoding="utf-8").strip() + "\n")
        c, j = base.with_suffix(".css"), base.with_suffix(".js")
        if c.exists():
            css.append(f'<link rel="stylesheet" href="parts/{c.name}?v={version(c)}">')
        if j.exists():
            js.append(f'<script src="parts/{j.name}?v={version(j)}" defer></script>')
    out = (
        shell.replace("<!--PARTS-->", "\n".join(html))
        .replace("<!--CSS-->", "\n".join(css))
        .replace("<!--JS-->", "\n".join(js))
        .replace("__V__", version(AQUI / "base.css") + version(AQUI / "base.js"))
    )
    (AQUI / "index.html").write_text(out, encoding="utf-8", newline="")
    print(f"index.html armado con {len(html)} partes")


if __name__ == "__main__":
    os.chdir(AQUI)
    main()
