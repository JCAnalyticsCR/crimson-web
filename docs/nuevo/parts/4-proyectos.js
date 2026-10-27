/* 4 · PROYECTOS DESTACADOS (prefijo nvp-): carrusel + visor a pantalla completa.
   - Carrusel: scroll-snap nativo; aqui solo se lee la posicion (1 vez por cuadro) para el paginador de nodos,
     el contador, las flechas y la tarjeta activa. Ningun video corre en las tarjetas.
   - Visor: un <dialog> con un carril de diapositivas (deslizar nativo). El clip de un caso se descarga SOLO cuando
     su diapositiva queda al frente (-640 en movil o con ahorro de datos, -960 en escritorio) y se corta al cerrar.
   - "Ver todos" abre en el mismo visor las fotos de obra de #nvp-galeria (srcset 480/960). */
(() => {
  'use strict';
  const sec = document.getElementById('proyectos');
  const rail = sec?.querySelector('.nvp-rail');
  if (!sec || !rail) return;

  const cfg = window.NV_CFG || {};
  const WA = String(cfg.wa || '50688889892').replace(/\D/g, '');
  const mqReduced = matchMedia('(prefers-reduced-motion: reduce)');
  const mqMovil = matchMedia('(max-width: 767px)');
  const cx = navigator.connection || {};
  const ahorro = !!cx.saveData || /(^|-)2g|3g/.test(cx.effectiveType || '');
  const pad2 = n => String(n).padStart(2, '0');
  const suave = () => (mqReduced.matches ? 'auto' : 'smooth');
  const waHref = txt => `https://wa.me/${WA}?text=${encodeURIComponent(txt)}`;
  const el = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };
  const ICO = {
    chat: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a8.5 8.5 0 0 1-12.4 7.6L3.5 21l1.4-4.9A8.5 8.5 0 1 1 21 12z"/></svg>',
    ext: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
    play: '<svg class="nvp-i-play" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l11-6.5z"/></svg>',
    pause: '<svg class="nvp-i-pause" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z"/></svg>'
  };

  /* ---------- Fotos de obra (misma regla de anchos que la galeria actual, tope 960) ---------- */
  const DIR = '../assets/img/galeria/';
  const anchos = w => { const tope = Math.min(w || 960, 960); return [...new Set([480, 960].filter(a => a < tope).concat(tope))]; };
  const fotoSrc = (archivo, w) => {
    const a = anchos(w);
    return { src: `${DIR}${archivo}-${a[a.length - 1]}.webp`, srcset: a.map(x => `${DIR}${archivo}-${x}.webp ${x}w`).join(', ') };
  };
  let galeria = [];
  try { galeria = JSON.parse(sec.querySelector('#nvp-galeria')?.textContent || '[]').filter(f => f && f.archivo && f.rotulo); }
  catch (err) { console.warn('[proyectos] #nvp-galeria no es JSON valido', err); }

  /* ======================= Carrusel ======================= */
  const items = [...rail.querySelectorAll('.nvp-item')];
  const cards = items.map(li => li.querySelector('.nvp-card'));
  const n = items.length;
  const bPrev = sec.querySelector('[data-nvp-prev]'), bNext = sec.querySelector('[data-nvp-next]');
  const nodesBox = sec.querySelector('.nvp-nodes'), fill = sec.querySelector('.nvp-nodes__line i');
  const countB = sec.querySelector('.nvp-count b'), countS = sec.querySelector('.nvp-count span');
  const nodes = [];
  if (nodesBox) for (let k = 0; k < n; k++) nodes.push(nodesBox.appendChild(el('span', 'nvp-node')));
  if (countS) countS.textContent = `/ ${pad2(n)}`;

  let step = 1, edge = 0, perView = 1, first = -1, shown = -1, hovered = -1, raf = 0;
  const activar = k => {
    if (k === shown) return;
    items[shown]?.classList.remove('is-active');
    items[k]?.classList.add('is-active');
    shown = k;
  };
  const pintar = force => {
    raf = 0;
    const max = Math.max(0, rail.scrollWidth - rail.clientWidth);
    const x = Math.abs(rail.scrollLeft);
    if (fill) fill.style.transform = `scaleX(${max > 0 ? Math.min(1, x / max) : 1})`;
    const tope = Math.max(n - perView, 0);
    let f = x >= max - 2 ? tope : Math.round(x / step);
    f = Math.min(Math.max(f, 0), tope);
    if (f !== first || force === true) {
      first = f;
      nodes.forEach((nd, k) => { nd.classList.toggle('is-on', k >= f && k < f + perView); nd.classList.toggle('is-lead', k === f); });
      if (countB) countB.textContent = perView > 1 ? `${pad2(f + 1)}–${pad2(Math.min(f + perView, n))}` : pad2(f + 1);
    }
    bPrev?.setAttribute('aria-disabled', String(x <= 2));
    bNext?.setAttribute('aria-disabled', String(x >= max - 2));
    activar(hovered >= 0 ? hovered : first);
  };
  const medir = () => {
    const cs = getComputedStyle(rail);
    const gap = parseFloat(cs.columnGap) || 0;
    edge = parseFloat(cs.paddingLeft) || 0;
    step = (items[0]?.getBoundingClientRect().width || 1) + gap;
    const util = rail.clientWidth - edge - (parseFloat(cs.paddingRight) || 0);
    perView = Math.max(1, Math.floor((util + gap) / step + 0.04));
    pintar(true);
  };
  rail.addEventListener('scroll', () => { if (!raf) raf = requestAnimationFrame(pintar); }, { passive: true });
  if ('ResizeObserver' in window) new ResizeObserver(medir).observe(rail); else addEventListener('resize', medir);
  medir();

  const mover = d => {
    const b = d < 0 ? bPrev : bNext;
    if (b?.getAttribute('aria-disabled') === 'true') return;
    const k = Math.min(Math.max(first + d * perView, 0), n - 1);
    rail.scrollTo({ left: items[k].offsetLeft - edge, behavior: suave() });
  };
  bPrev?.addEventListener('click', () => mover(-1));
  bNext?.addEventListener('click', () => mover(1));

  // Tarjeta activa: la que esta bajo el mouse o con foco; si no, la primera a la vista
  const idxDe = t => items.indexOf(t?.closest?.('.nvp-item'));
  rail.addEventListener('pointerover', e => { if (e.pointerType === 'mouse') { hovered = idxDe(e.target); activar(hovered >= 0 ? hovered : first); } });
  rail.addEventListener('pointerleave', () => { hovered = -1; activar(first); });
  rail.addEventListener('focusin', e => { hovered = idxDe(e.target); if (hovered >= 0) activar(hovered); });
  rail.addEventListener('focusout', () => { hovered = -1; activar(first); });

  // Los bucles (REC, pulso del nodo) solo corren con la seccion a la vista
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(([en]) => sec.classList.toggle('is-live', en.isIntersecting), { rootMargin: '0px 0px -10% 0px' }).observe(rail);
  }

  /* ======================= Visor ======================= */
  const visor = (() => {
    const dlg = sec.querySelector('#nvp-visor');
    if (!dlg || typeof dlg.showModal !== 'function') return null;
    const track = dlg.querySelector('[data-nvp-track]');
    const vn = dlg.querySelector('[data-nvp-vn]'), vt = dlg.querySelector('[data-nvp-vt]'), bar = dlg.querySelector('.nvp-v__bar i');
    const vPrev = dlg.querySelector('[data-nvp-vprev]'), vNext = dlg.querySelector('[data-nvp-vnext]'), vClose = dlg.querySelector('[data-nvp-vclose]');
    if (!track || !vClose) return null;
    let lista = [], slides = [], cur = -1, opener = null, vraf = 0, volviendo = false, empujar = false, abierto = false;

    const linea = (b, txt) => { const p = el('p'); if (b) p.append(el('b', '', b)); p.append(txt); return p; };
    const mmss = s => `${pad2(Math.floor((s || 0) / 60))}:${pad2(Math.floor((s || 0) % 60))}`;

    const crearSlide = (it, k, total) => {
      const s = el('section', 'nvp-v__slide');
      s.setAttribute('role', 'group');
      s.setAttribute('aria-roledescription', 'diapositiva');
      s.setAttribute('aria-label', `${k + 1} de ${total}: ${it.titulo}`);

      const media = el('div', 'nvp-v__media');
      const fr = el('div', 'nvp-v__frame');
      fr.dataset.tipo = it.tipo;
      fr.style.setProperty('--ar', (it.w / it.h).toFixed(4));
      const osd = el('div', 'nvp-osd');
      osd.setAttribute('aria-hidden', 'true');
      const rec = el('span', 'nvp-rec' + (it.tipo === 'foto' ? ' nvp-rec--foto' : ''));
      rec.append(el('i'), it.tipo === 'video' ? 'REC' : it.tipo === 'prensa' ? 'En prensa' : 'Foto de obra');
      osd.append(rec, el('span', '', it.cam));
      const corners = el('span', 'nvp-v__corners');
      corners.setAttribute('aria-hidden', 'true');

      if (it.tipo === 'video') {
        const v = document.createElement('video');
        v.className = 'nvp-v__el';
        v.muted = true; v.defaultMuted = true; v.loop = true; v.playsInline = true; v.preload = 'none'; v.disablePictureInPicture = true;
        v.setAttribute('muted', ''); v.setAttribute('playsinline', '');
        v.width = 540; v.height = 960;
        v.setAttribute('aria-label', `Video del caso: ${it.titulo}`);
        v.dataset.clip = it.clip; v.dataset.poster = it.poster;
        const tc = el('span', 'nvp-v__tc', '00:00');
        rec.append(tc);
        const play = el('button', 'nvp-v__play');
        play.type = 'button';
        play.setAttribute('aria-label', 'Pausar video');
        play.innerHTML = ICO.pause + ICO.play;
        const prog = el('span', 'nvp-v__prog'); prog.setAttribute('aria-hidden', 'true');
        const pi = el('i'); prog.append(pi);
        const estado = () => {
          fr.classList.toggle('is-paused', v.paused);
          play.setAttribute('aria-label', v.paused ? 'Reproducir video' : 'Pausar video');
        };
        v.addEventListener('play', estado);
        v.addEventListener('pause', estado);
        v.addEventListener('timeupdate', () => {
          if (v.duration) pi.style.transform = `scaleX(${v.currentTime / v.duration})`;
          tc.textContent = mmss(v.currentTime);
        });
        v.addEventListener('error', () => fr.classList.add('is-off')); // queda el poster
        const alternar = () => { if (v.paused) { arrancar(v, true); } else v.pause(); };
        play.addEventListener('click', e => { e.stopPropagation(); alternar(); });
        fr.addEventListener('click', alternar);
        fr.classList.add('is-paused');
        fr.append(v, corners, osd, play, prog);
      } else {
        const img = new Image();
        img.className = 'nvp-v__el';
        img.alt = it.alt || it.titulo;
        img.decoding = 'async';
        img.width = it.w; img.height = it.h;
        img.dataset.src = it.src;
        if (it.srcset) { img.dataset.srcset = it.srcset; img.sizes = '(max-width:1023px) 100vw, calc(100vw - 400px)'; }
        if (it.prev) img.style.backgroundImage = `url("${it.prev}")`; // la miniatura ya vista hace de respaldo
        fr.append(img, corners, osd);
      }
      media.append(fr);

      const info = el('div', 'nvp-v__info');
      info.append(el('span', 'nvp-tag', it.tag), el('h3', 'nvp-v__t', it.titulo));
      const lines = el('div', 'nvp-v__lines');
      if (it.pidio) lines.append(linea(it.tipo === 'prensa' ? 'El pedido:' : 'Se pidió:', it.pidio));
      if (it.instalo) lines.append(linea(it.tipo === 'prensa' ? 'Lo instalado:' : 'Se instaló:', it.instalo));
      if (it.tipo === 'foto') lines.append(linea('', it.instalo ? 'Foto real de obra.' : 'Foto real de una instalación de Crimson.'));
      info.append(lines);
      const cta = el('div', 'nvp-v__cta');
      if (it.nota) {
        const a = el('a', 'nv-btn nv-btn--line');
        a.href = it.nota; a.target = '_blank'; a.rel = 'noopener';
        a.innerHTML = ICO.ext; a.append('Ver la nota');
        cta.append(a);
      }
      const wa = el('a', 'nv-btn nv-btn--red');
      wa.href = waHref(it.wa); wa.target = '_blank'; wa.rel = 'noopener';
      wa.innerHTML = ICO.chat; wa.append('Quiero algo similar');
      cta.append(wa);
      info.append(cta);

      s.append(media, info);
      return s;
    };

    // Medios: fotos y posters se piden solo para la diapositiva actual y sus vecinas
    const cargar = k => {
      const s = slides[k]; if (!s) return;
      const img = s.querySelector('img.nvp-v__el');
      if (img && !img.getAttribute('src')) {
        if (img.dataset.srcset) img.srcset = img.dataset.srcset;
        img.src = img.dataset.src;
        img.decode?.().then(() => { img.style.backgroundImage = ''; }).catch(() => {});
      }
      const v = s.querySelector('video');
      if (v && !v.poster) v.poster = v.dataset.poster;
    };
    const arrancar = (v, forzar) => {
      if (!forzar && mqReduced.matches) return; // reduced-motion: poster quieto; el clip baja solo si le dan play
      if (!v.getAttribute('src')) { v.preload = 'auto'; v.src = `${v.dataset.clip}-${mqMovil.matches || ahorro ? 640 : 960}.mp4`; }
      v.play()?.catch(() => v.closest('.nvp-v__frame')?.classList.add('is-paused'));
    };
    const soltar = v => { v.pause(); v.removeAttribute('src'); v.load(); };

    const ir = k => {
      if (k === cur || k < 0 || k >= slides.length) return;
      const antes = slides[cur]?.querySelector('video');
      if (antes) antes.pause();
      cur = k;
      slides.forEach((s, j) => { s.inert = j !== k; });
      [k, k + 1, k - 1].forEach(cargar);
      // un solo video decodificando: los que quedaron a 2+ diapositivas sueltan su descarga
      slides.forEach((s, j) => { const v = s.querySelector('video'); if (v && Math.abs(j - k) > 1 && v.getAttribute('src')) soltar(v); });
      const v = slides[k].querySelector('video');
      if (v) arrancar(v, false);
      if (vn) vn.textContent = pad2(k + 1);
      if (bar) bar.style.transform = `scaleX(${(k + 1) / slides.length})`;
      vPrev?.setAttribute('aria-disabled', String(k === 0));
      vNext?.setAttribute('aria-disabled', String(k === slides.length - 1));
    };
    const posicion = () => { vraf = 0; const w = track.clientWidth || 1; ir(Math.round(track.scrollLeft / w)); };
    track.addEventListener('scroll', () => { if (!vraf) vraf = requestAnimationFrame(posicion); }, { passive: true });
    const paso = d => {
      const k = cur + d;
      if (k < 0 || k >= slides.length) return;
      track.scrollTo({ left: k * track.clientWidth, behavior: suave() });
    };
    vPrev?.addEventListener('click', () => paso(-1));
    vNext?.addEventListener('click', () => paso(1));
    vClose.addEventListener('click', () => cerrar());
    dlg.addEventListener('keydown', e => {
      if (e.key === 'ArrowLeft') { e.preventDefault(); paso(-1); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); paso(1); }
    });
    if ('ResizeObserver' in window) new ResizeObserver(() => { if (dlg.open && cur >= 0) track.scrollLeft = cur * track.clientWidth; }).observe(track);

    const abrir = (l, k, desde, etiqueta) => {
      if (!l.length) return;
      if (abierto) limpiar();
      abierto = true;
      lista = l; opener = desde || document.activeElement;
      dlg.setAttribute('aria-label', etiqueta);
      dlg.toggleAttribute('data-solo', l.length < 2);
      if (vt) vt.textContent = `/ ${pad2(l.length)}`;
      track.replaceChildren(...lista.map((it, j) => crearSlide(it, j, lista.length)));
      slides = [...track.children]; cur = -1;
      document.documentElement.classList.add('nvp-lock');
      dlg.showModal();
      track.scrollLeft = k * track.clientWidth;
      ir(k);
      vClose.focus({ preventScroll: true });
      // "Atras" del telefono cierra el visor en vez de salir de la pagina
      if (volviendo) empujar = true; // el "atras" del cierre anterior aun no llega: se empuja cuando llegue
      else if (!history.state?.nvpVisor) history.pushState({ nvpVisor: true }, '');
    };
    // Limpieza sincronica (el evento 'close' llega en otra tarea: si alguien reabre enseguida, no debe borrar lo nuevo)
    const limpiar = () => {
      if (!abierto) return;
      abierto = false;
      track.querySelectorAll('video').forEach(soltar); // corta descarga y reproduccion
      track.replaceChildren(); slides = []; lista = []; cur = -1;
      document.documentElement.classList.remove('nvp-lock');
      if (history.state?.nvpVisor) { volviendo = true; history.back(); }
      opener?.focus?.({ preventScroll: true }); opener = null;
    };
    const cerrar = () => { if (dlg.open) dlg.close(); limpiar(); };
    dlg.addEventListener('close', () => { if (!dlg.open) limpiar(); }); // Esc y cierres nativos
    addEventListener('popstate', () => {
      if (volviendo) { // es el "atras" que pidio el propio cierre
        volviendo = false;
        if (empujar && dlg.open) history.pushState({ nvpVisor: true }, '');
        empujar = false;
        return;
      }
      cerrar();
    });
    return { abrir };
  })();

  /* ---------- Datos de cada tarjeta para el visor ---------- */
  const deCard = (a, k) => {
    const tipo = a.dataset.nvpTipo;
    const img = a.querySelector('img');
    const titulo = (a.querySelector('.nvp-card__t')?.textContent || '').replace(/[“”"]/g, '').trim();
    const it = {
      tipo, titulo, cam: `CAM ${pad2(k + 1)}`, tag: a.querySelector('.nvp-tag')?.textContent || '',
      pidio: a.dataset.nvpPidio || '', instalo: a.dataset.nvpInstalo || '', alt: img?.alt || titulo,
      wa: `Hola Crimson, vi en su página el proyecto "${titulo}" y quiero algo similar. ¿Me pueden asesorar?`
    };
    if (tipo === 'video') return { ...it, clip: a.dataset.nvpClip, poster: a.dataset.nvpPoster, w: 540, h: 960 };
    if (tipo === 'prensa') {
      return { ...it, src: a.dataset.nvpImg, w: +a.dataset.nvpW || 640, h: +a.dataset.nvpH || 436, nota: a.href,
        wa: `Hola Crimson, vi la nota "${titulo}" y quiero algo similar para mi comunidad. ¿Me pueden asesorar?` };
    }
    const w = +a.dataset.nvpW || 960, h = +a.dataset.nvpH || 960;
    return { ...it, ...fotoSrc(a.dataset.nvpFoto, w), w, h, prev: img?.currentSrc || '' };
  };

  rail.addEventListener('click', e => {
    const a = e.target.closest('.nvp-card');
    if (!a || !visor || e.ctrlKey || e.metaKey || e.shiftKey || e.button) return;
    e.preventDefault();
    visor.abrir(cards.map(deCard), cards.indexOf(a), a, 'Proyectos destacados');
  });

  sec.querySelector('[data-nvp-all]')?.addEventListener('click', e => {
    if (!visor || !galeria.length) return;
    visor.abrir(galeria.map((f, k) => ({
      tipo: 'foto', titulo: f.rotulo, tag: f.categoria || 'Obra', cam: `FOTO ${pad2(k + 1)}`, alt: f.rotulo,
      ...fotoSrc(f.archivo, f.ancho), w: f.ancho || 960, h: f.alto || 960,
      wa: `Hola Crimson, vi en su página la foto de obra "${f.rotulo}" y quiero algo similar. ¿Me pueden asesorar?`
    })), 0, e.currentTarget, 'Fotos de obra');
  });
})();
