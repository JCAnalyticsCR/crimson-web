/* 3 · Soluciones: hojas de detalle (<dialog>), anclas #sol-* y la senal de "Como trabajamos". */
(() => {
  'use strict';
  const root = document.getElementById('soluciones');
  if (!root) return;
  const cfg = window.NV_CFG || {};
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const html = document.documentElement;

  /* ---------- enlaces con la configuracion del sitio (WhatsApp y tienda en un solo lugar) ---------- */
  const wa = String(cfg.wa || '50688889892').replace(/\D/g, '');
  const tienda = String(cfg.tienda || 'https://tienda.crimsoncr.com').replace(/\/+$/, '');
  root.querySelectorAll('[data-nvs-wa]').forEach(a => {
    const que = a.dataset.nvsWa;
    const msg = que === 'ask'
      ? 'Hola Crimson, no sé por dónde empezar: les cuento mi caso.'
      : `Hola Crimson, quiero cotizar ${que}.`;
    a.href = `https://wa.me/${wa}?text=${encodeURIComponent(msg)}`;
  });
  root.querySelectorAll('[data-nvs-cat]').forEach(a => { a.href = tienda + a.dataset.nvsCat; });

  /* ---------- hojas ---------- */
  const cards = new Map();          // id de la tarjeta (sol-x) -> { btn, dlg }
  root.querySelectorAll('[data-nvs-open]').forEach(btn => {
    const dlg = document.getElementById(btn.dataset.nvsOpen);
    if (dlg && typeof dlg.showModal === 'function') cards.set(btn.id, { btn, dlg });
  });
  if (!cards.size) return;

  let current = null;               // { btn, dlg }
  let closing = false;
  let hashMine = false;             // el hash #sol-x lo puso esta hoja (se limpia al cerrar)

  const lock = on => {
    if (on) {
      const sb = window.innerWidth - html.clientWidth;
      if (sb > 0) document.body.style.paddingRight = sb + 'px';
      html.classList.add('nvs-lock');
    } else {
      html.classList.remove('nvs-lock');
      document.body.style.paddingRight = '';
    }
  };
  const setHash = id => {
    try { history.replaceState(history.state, '', id ? '#' + id : location.pathname + location.search); } catch { /* file:// u otro */ }
  };

  function open(entry, { fromHash = false } = {}) {
    if (!entry) return;
    if (current && current !== entry) { current.dlg.close(); }
    const { btn, dlg } = entry;
    if (dlg.open) return;
    current = entry;
    closing = false;
    lock(true);
    dlg.classList.remove('is-open', 'is-drag');
    dlg.style.transform = '';
    dlg.showModal();
    dlg.scrollTop = 0;
    // foco al abrir: el boton cerrar (primer control), sin mover la pagina
    dlg.querySelector('[data-nvs-close]')?.focus({ preventScroll: true });
    void dlg.offsetHeight;          // fija el estado inicial para que la transicion corra
    requestAnimationFrame(() => dlg.classList.add('is-open'));
    if (!fromHash || location.hash !== '#' + btn.id) { setHash(btn.id); hashMine = true; }
  }

  function cleanup(entry) {
    const { btn, dlg } = entry;
    dlg.classList.remove('is-open', 'is-drag');
    dlg.style.transform = '';
    if (current !== entry) return;
    current = null;
    closing = false;
    lock(false);
    if (hashMine && location.hash === '#' + btn.id) setHash('');
    hashMine = false;
    btn.focus({ preventScroll: true });
  }

  function close() {
    const entry = current;
    if (!entry || closing) return;
    const { dlg } = entry;
    closing = true;
    dlg.classList.remove('is-drag');
    dlg.style.transform = '';
    dlg.classList.remove('is-open');
    if (reduced.matches) { dlg.close(); cleanup(entry); return; }
    let done = false;
    const fin = () => { if (done) return; done = true; dlg.removeEventListener('transitionend', onEnd); if (dlg.open) dlg.close(); cleanup(entry); };
    const onEnd = e => { if (e.target === dlg && e.propertyName === 'transform') fin(); };
    dlg.addEventListener('transitionend', onEnd);
    setTimeout(fin, 520);
  }

  cards.forEach(entry => {
    const { btn, dlg } = entry;
    btn.addEventListener('click', () => open(entry));
    // Escape: cerramos con la animacion (si el navegador igual la cierra, 'close' limpia todo)
    dlg.addEventListener('cancel', e => { e.preventDefault(); close(); });
    // limpieza unica, venga de donde venga el cierre (Escape forzado, clic fuera, boton, arrastre)
    dlg.addEventListener('close', () => cleanup(entry));
    dlg.querySelectorAll('[data-nvs-close]').forEach(b => b.addEventListener('click', close));
    // clic fuera: el fondo (::backdrop) cuenta como clic en el propio <dialog>; el contenido vive en __in
    let downOut = false;
    dlg.addEventListener('pointerdown', e => { downOut = e.target === dlg; });
    dlg.addEventListener('click', e => { if (e.target === dlg && downOut) close(); downOut = false; });

    /* arrastrar hacia abajo para cerrar (solo la hoja movil, desde la manija o el encabezado) */
    const grip = dlg.querySelector('.nvs-sheet__top');
    const handle = dlg.querySelector('.nvs-sheet__grab');
    const mobile = matchMedia('(max-width: 767px)');
    let y0 = null, dy = 0, pid = null;
    const down = e => {
      if (!mobile.matches || e.pointerType === 'mouse' || e.target.closest('button')) return;
      y0 = e.clientY; dy = 0; pid = e.pointerId;
      dlg.classList.add('is-drag');
    };
    const move = e => {
      if (y0 === null || e.pointerId !== pid) return;
      dy = Math.max(0, e.clientY - y0);
      dlg.style.transform = `translateY(${dy}px)`;
    };
    const up = e => {
      if (y0 === null || e.pointerId !== pid) return;
      y0 = null;
      dlg.classList.remove('is-drag');
      if (dy > 90) close(); else dlg.style.transform = '';
    };
    [grip, handle].forEach(el => el?.addEventListener('pointerdown', down));
    dlg.addEventListener('pointermove', move);
    dlg.addEventListener('pointerup', up);
    dlg.addEventListener('pointercancel', up);
  });

  /* ---------- anclas #sol-x: resaltar la tarjeta y abrir su hoja ---------- */
  let hlTimer = 0;
  function highlight(btn) {
    cards.forEach(c => c.btn.classList.remove('is-hl'));
    void btn.offsetWidth;           // reinicia la animacion si se repite
    btn.classList.add('is-hl');
    clearTimeout(hlTimer);
    hlTimer = setTimeout(() => btn.classList.remove('is-hl'), 2600);
  }
  function jumpTo(btn) {
    // salto inmediato (la hoja tapa el viaje): sin el smooth global para que el bloqueo de scroll no lo corte
    const prev = html.style.scrollBehavior;
    html.style.scrollBehavior = 'auto';
    btn.scrollIntoView({ block: 'center' });
    html.style.scrollBehavior = prev;
  }
  function fromHash(hash, viaLink) {
    let id = '';
    try { id = decodeURIComponent((hash || '').slice(1)); } catch { return false; }
    const entry = cards.get(id);
    if (!entry) return false;
    // la tarjeta (y su <li>) se muestran ya, sin esperar la entrada al aparecer
    entry.btn.closest('[data-nv-in]')?.classList.add('is-in');
    jumpTo(entry.btn);
    highlight(entry.btn);
    open(entry, { fromHash: !viaLink });
    return true;
  }
  // cualquier enlace de la pagina a #sol-x (p. ej. los accesos de la parte 2)
  document.addEventListener('click', e => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = e.target.closest?.('a[href^="#sol-"]');
    if (!a) return;
    if (cards.has(a.getAttribute('href').slice(1))) {
      e.preventDefault();
      fromHash(a.getAttribute('href'), true);
    }
  });
  addEventListener('hashchange', () => fromHash(location.hash, false));
  // llegada con el ancla en la direccion (crimsoncr.com/#sol-redes)
  if (/^#sol-/.test(location.hash)) {
    const go = () => setTimeout(() => fromHash(location.hash, false), 250);
    document.readyState === 'complete' ? go() : addEventListener('load', go, { once: true });
  }

  /* ---------- Como trabajamos: la senal corre solo en pantalla ---------- */
  const ruta = document.querySelector('[data-nvs-ruta]');
  if (ruta && 'IntersectionObserver' in window) {
    const io = new IntersectionObserver(([en]) => ruta.classList.toggle('is-run', !!en?.isIntersecting && !reduced.matches),
      { rootMargin: '0px 0px -10% 0px', threshold: 0 });
    io.observe(ruta);
  }
})();
