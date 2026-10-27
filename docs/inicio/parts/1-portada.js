/* 1 · PORTADA: encabezado (solido al bajar), buscador, menu movil y detalles de la portada.
   Sin globales; todo con guarda. La config del sitio llega por window.NV_CFG (base.js, que carga antes). */
(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const cfg = window.NV_CFG || {};
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

  const bar = $('#nvhBar');
  const hero = $('.nvh-hero');
  if (!bar) return;

  /* ---------- Enlaces desde la configuracion (un solo lugar para telefono, WhatsApp y tienda) ---------- */
  const tienda = (cfg.tienda || 'https://tienda.crimsoncr.com').replace(/\/+$/, '');
  $$('[data-nvh-tienda]').forEach(a => { a.href = tienda + '/'; });
  $('[data-nvh-buscar]')?.setAttribute('action', tienda + '/buscar');
  if (cfg.wa) $$('[data-nvh-wa]').forEach(a => { a.href = `https://wa.me/${cfg.wa}?text=${encodeURIComponent(a.dataset.nvhWa || '')}`; });
  if (cfg.tel) $$('[data-nvh-tel]').forEach(a => { a.href = 'tel:' + String(cfg.tel).replace(/[^\d+]/g, ''); });

  /* ---------- Encabezado solido en cuanto se deja la parte de arriba (IO, sin escuchar el scroll) ---------- */
  const sentinel = $('.nvh-sentinel');
  if (sentinel && 'IntersectionObserver' in window) {
    new IntersectionObserver(([en]) => bar.classList.toggle('is-solid', !en.isIntersecting)).observe(sentinel);
  } else {
    bar.classList.add('is-solid');
  }

  /* ---------- Buscador ---------- */
  const sBtn = $('#nvhSearchBtn');
  const sBox = $('#nvhSearch');
  const sInput = $('#nvhQ');
  const searchOpen = () => sBox && !sBox.hidden;
  const setSearch = (open, { focusBack = false } = {}) => {
    if (!sBox || !sBtn) return;
    sBox.hidden = !open;
    bar.classList.toggle('is-search', open);
    sBtn.setAttribute('aria-expanded', String(open));
    sBtn.setAttribute('aria-label', open ? 'Cerrar buscador' : 'Buscar en la tienda');
    if (open) sInput?.focus({ preventScroll: true });
    else if (focusBack) sBtn.focus();
  };
  sBtn?.addEventListener('click', () => {
    if (menuOpen()) setMenu(false);
    setSearch(!searchOpen());
  });
  // Toque fuera del encabezado: se cierra
  document.addEventListener('pointerdown', e => { if (searchOpen() && !bar.contains(e.target)) setSearch(false); });
  // Busqueda vacia: no salir de la pagina
  $('[data-nvh-buscar]')?.addEventListener('submit', e => { if (!sInput?.value.trim()) { e.preventDefault(); sInput?.focus(); } });

  /* ---------- Menu movil / tableta ---------- */
  const burger = $('#nvhBurger');
  const menu = $('#nvhMenu');
  const root = document.documentElement;
  let closeTimer = 0;
  const menuOpen = () => menu && !menu.hidden && !menu.classList.contains('is-closing');

  const lockScroll = on => {
    if (on) {
      const gap = innerWidth - root.clientWidth; // ancho de la barra de desplazamiento (escritorio): sin salto
      root.style.overflow = 'hidden';
      document.body.style.overflow = 'hidden';
      if (gap > 0) document.body.style.paddingRight = gap + 'px';
    } else {
      root.style.overflow = '';
      document.body.style.overflow = '';
      document.body.style.paddingRight = '';
    }
  };

  function setMenu(open, { focusBack = true } = {}) {
    if (!menu || !burger) return;
    clearTimeout(closeTimer);
    burger.setAttribute('aria-expanded', String(open));
    burger.setAttribute('aria-label', open ? 'Cerrar menú' : 'Abrir menú');
    bar.classList.toggle('is-open', open);
    lockScroll(open);
    if (open) {
      setSearch(false);
      menu.classList.remove('is-closing');
      menu.hidden = false;
      $('.nvh-menu__nav a', menu)?.focus({ preventScroll: true });
    } else {
      if (menu.hidden) return;
      const done = () => { menu.hidden = true; menu.classList.remove('is-closing'); };
      if (reduced) done();
      else { menu.classList.add('is-closing'); closeTimer = setTimeout(done, 200); }
      if (focusBack) burger.focus({ preventScroll: true });
    }
  }

  burger?.addEventListener('click', () => setMenu(!menuOpen()));
  // Tocar un enlace o el velo: cerrar (el ancla sigue su curso con el scroll ya liberado)
  menu?.addEventListener('click', e => {
    if (e.target.closest('a')) setMenu(false, { focusBack: false });
    else if (e.target.closest('[data-nvh-close]')) setMenu(false);
  });
  // Foco atrapado entre la X y el panel mientras el menu esta abierto
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      if (menuOpen()) { e.preventDefault(); setMenu(false); }
      else if (searchOpen()) { e.preventDefault(); setSearch(false, { focusBack: true }); }
      return;
    }
    if (e.key !== 'Tab' || !menuOpen()) return;
    const items = [burger, ...$$('a[href], button:not([disabled])', menu)].filter(Boolean);
    const first = items[0], last = items[items.length - 1];
    const i = items.indexOf(document.activeElement);
    if (e.shiftKey && (i <= 0)) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && (i === -1 || i === items.length - 1)) { e.preventDefault(); first.focus(); }
  });
  // Al pasar a escritorio el menu no existe: cerrarlo si quedo abierto
  const mqDesk = matchMedia('(min-width:1024px)');
  mqDesk.addEventListener?.('change', ev => { if (ev.matches && menuOpen()) setMenu(false, { focusBack: false }); });

  if (!hero) return;

  /* ---------- Portada: bucles quietos fuera de cuadro ---------- */
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(([en]) => hero.classList.toggle('is-off', !en.isIntersecting), { rootMargin: '10% 0px' }).observe(hero);
  }

  /* ---------- Paralaje leve con el puntero (solo PC con hover; sin lecturas de layout) ---------- */
  const mqPC = matchMedia('(min-width:1024px) and (hover:hover) and (pointer:fine) and (prefers-reduced-motion:no-preference)');
  let tx = 0, ty = 0, cx = 0, cy = 0, raf = 0;
  const step = () => {
    cx += (tx - cx) * 0.07;
    cy += (ty - cy) * 0.07;
    hero.style.setProperty('--nvh-px', cx.toFixed(4));
    hero.style.setProperty('--nvh-py', cy.toFixed(4));
    raf = (Math.abs(tx - cx) > 0.001 || Math.abs(ty - cy) > 0.001) ? requestAnimationFrame(step) : 0;
  };
  const kick = () => { if (!raf) raf = requestAnimationFrame(step); };
  hero.addEventListener('pointermove', e => {
    if (!mqPC.matches || e.pointerType !== 'mouse') return;
    tx = (e.clientX / innerWidth - 0.5) * 2;
    ty = (e.clientY / innerHeight - 0.5) * 2;
    kick();
  }, { passive: true });
  hero.addEventListener('pointerleave', () => { tx = 0; ty = 0; if (mqPC.matches) kick(); });

  /* ---------- Secciones del encabezado (escritorio): pildora que se desliza y seccion activa al bajar ---------- */
  const nav = document.querySelector('.nvh-nav'), pill = nav?.querySelector('.nvh-nav__pill');
  if (nav && pill) {
    const links = [...nav.querySelectorAll('a')];
    let activo = null;
    const ir = a => {
      if (!a || !a.offsetWidth) { nav.classList.remove('has-pill'); return; }
      pill.style.width = a.offsetWidth + 'px';
      pill.style.transform = `translateX(${a.offsetLeft}px)`;
      nav.classList.add('has-pill');
    };
    links.forEach(a => { a.addEventListener('mouseenter', () => ir(a)); a.addEventListener('focus', () => ir(a)); });
    nav.addEventListener('mouseleave', () => ir(activo));
    nav.addEventListener('focusout', e => { if (!nav.contains(e.relatedTarget)) ir(activo); });
    const marcar = id => {
      activo = links.find(a => a.dataset.spy === id) || null;
      links.forEach(a => a.classList.toggle('is-active', a === activo));
      if (!nav.matches(':hover')) ir(activo);
    };
    const secs = links.map(a => a.dataset.spy && document.getElementById(a.dataset.spy)).filter(Boolean);
    if ('IntersectionObserver' in window && secs.length) {
      const io = new IntersectionObserver(es => es.forEach(en => { if (en.isIntersecting) marcar(en.target.id); }), { rootMargin: '-45% 0px -50% 0px' });
      secs.forEach(s => io.observe(s));
      const portada = document.getElementById('inicio');
      if (portada) new IntersectionObserver(([en]) => { if (en.isIntersecting && en.intersectionRatio > .4) marcar(null); }, { threshold: [0, .4, 1] }).observe(portada);
    }
    addEventListener('resize', () => ir(activo), { passive: true });
  }
})();
