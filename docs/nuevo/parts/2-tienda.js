/* 2 · TIENDA (nvt-): precio vigente de los destacados y pausa de los bucles fuera de pantalla.
   Precio: GET {api}/public/store/{store}/products/{id} cuando la fila se acerca (IntersectionObserver, 50%).
   Si la API no responde (o en localhost sin API), queda "Ver precio" y la tarjeta sigue llevando a su ficha. */
(() => {
  'use strict';
  const CFG = window.NV_CFG || {};
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const hasIO = 'IntersectionObserver' in window;

  /* ---------- bucles (pulso de la red, nodo del collage): solo corren en pantalla ---------- */
  const loops = [document.querySelector('.nvt-quick'), document.querySelector('.nvt-feat')].filter(Boolean);
  if (!reduced && hasIO && loops.length) {
    const io = new IntersectionObserver(es => es.forEach(en => en.target.classList.toggle('is-live', en.isIntersecting)), { rootMargin: '10% 0px' });
    loops.forEach(el => io.observe(el));
  }

  /* ---------- precio vigente ---------- */
  const rail = document.querySelector('[data-nvt-rail]');
  const cards = rail ? [...rail.querySelectorAll('[data-pid]')] : [];
  if (!cards.length) return;
  const local = /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
  const api = String((local && CFG.apiLocal) || CFG.api || '').replace(/\/$/, '');
  const store = encodeURIComponent(CFG.store || 'crimson');
  if (!api) return;
  const crc = new Intl.NumberFormat('es-CR', { style: 'currency', currency: 'CRC', maximumFractionDigits: 0 });

  const precio = async card => {
    const out = card.querySelector('[data-price]');
    const pid = card.dataset.pid;
    if (!out || !/^\d+$/.test(pid || '')) return;
    const ctl = 'AbortController' in window ? new AbortController() : null;
    const t = ctl && setTimeout(() => ctl.abort(), 8000);
    try {
      const r = await fetch(`${api}/public/store/${store}/products/${pid}`, { credentials: 'omit', signal: ctl?.signal });
      if (!r.ok) return;
      const p = await r.json();
      const n = Number(p?.display_price);
      const vende = p?.availability?.accion === 'comprar' && !p?.price_on_request && n > 0;
      if (!vende) { out.textContent = 'Consultá disponibilidad'; return; }
      const cur = p.display_currency || 'CRC';
      const fmt = cur === 'CRC' ? crc : new Intl.NumberFormat('es-CR', { style: 'currency', currency: cur, maximumFractionDigits: 0 });
      out.textContent = fmt.format(n);
      const s = document.createElement('small');
      s.textContent = 'IVA incluido';
      out.append(s);
      out.classList.add('is-live');
    } catch { /* sin red o bloqueado: queda "Ver precio" */ }
    finally { if (t) clearTimeout(t); }
  };

  const cargar = () => cards.forEach(precio);
  if (!hasIO) { cargar(); return; }
  // se observa la fila completa: en el carrusel movil las tarjetas 3 y 4 quedan recortadas por el scroll
  // horizontal y no "entrarian" hasta deslizarlas; asi las cuatro llegan juntas al acercarse.
  const io = new IntersectionObserver(([en]) => { if (en?.isIntersecting) { io.disconnect(); cargar(); } }, { rootMargin: '50% 0px' });
  io.observe(rail);
})();
