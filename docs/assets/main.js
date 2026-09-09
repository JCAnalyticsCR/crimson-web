/* Crimson Consulting · prototipo
   Toda la coreografía vive aquí. Sin dependencias. */
(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const isPC = matchMedia('(min-width:1024px) and (hover:hover) and (prefers-reduced-motion: no-preference)').matches;
  const SVG = 'http://www.w3.org/2000/svg';
  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElementNS(SVG, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    kids.forEach(k => n.appendChild(k));
    return n;
  };

  /* ---------- Red animada (idéntica a la del diseño .dc) ---------- */
  const NODES = [[120,140],[380,90],[700,160],[1050,110],[1320,190],[200,420],[520,470],[900,430],[1250,470],[80,680],[420,700],[760,660],[1120,690],[1380,640]];
  const LINKS = [[0,1],[1,2],[2,3],[3,4],[0,5],[1,6],[2,6],[3,7],[4,8],[5,6],[6,7],[7,8],[5,9],[6,10],[7,11],[8,12],[8,13],[10,11],[11,12]];
  const PULSE = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--pulse')) || 6;

  function buildNet(dark, seed) {
    const svg = el('svg', { viewBox: '0 0 1440 810', preserveAspectRatio: 'xMidYMid slice' });
    const line = dark ? 'rgba(255,255,255,.12)' : 'rgba(21,19,26,.14)';
    const node = dark ? '#f4f1ee' : '#15131a';
    LINKS.forEach(([a, b], i) => {
      const [x1, y1] = NODES[a], [x2, y2] = NODES[b];
      const d = `M${x1} ${y1} L${x2} ${y2}`;
      svg.appendChild(el('path', { d, class: 'net-line', stroke: line }));
      const dash = el('path', { d, class: 'net-dash' });
      dash.style.animationDelay = `${-i * 0.7}s`;
      svg.appendChild(dash);
      if (!reduced && i % 2 === seed) {
        const c = el('circle', { r: 4, fill: '#e2233a' });
        c.appendChild(el('animateMotion', { dur: `${PULSE + (i % 3)}s`, repeatCount: 'indefinite', path: d, begin: `${-i * 1.3}s` }));
        svg.appendChild(c);
      }
    });
    NODES.forEach(([x, y], i) => {
      const g = el('circle', { cx: x, cy: y, r: 14, class: 'net-glow' });
      g.style.animationDuration = `${3 + (i % 4)}s`;
      g.style.animationDelay = `${i * 0.4}s`;
      svg.appendChild(g);
      svg.appendChild(el('circle', { cx: x, cy: y, r: 5, fill: node, class: 'net-node' }));
    });
    return svg;
  }
  const netHero = $('#netHero'), netShop = $('#netShop');
  netHero && netHero.appendChild(buildNet(false, 0));
  netShop && netShop.appendChild(buildNet(true, 1));

  // Mini red de la tarjeta 02
  const mini = $('#miniNet');
  if (mini) {
    const pts = [[40,200],[140,90],[260,170],[380,60],[480,190]];
    const svg = el('svg', { viewBox: '0 0 520 260', preserveAspectRatio: 'xMidYMid slice' });
    for (let i = 0; i < pts.length - 1; i++) {
      const d = `M${pts[i][0]} ${pts[i][1]} L${pts[i+1][0]} ${pts[i+1][1]}`;
      svg.appendChild(el('path', { d, stroke: 'rgba(255,255,255,.35)', 'stroke-width': 1.5, fill: 'none' }));
      if (!reduced) {
        const c = el('circle', { r: 4, fill: '#e2233a' });
        c.appendChild(el('animateMotion', { dur: '3s', repeatCount: 'indefinite', path: d, begin: `${-i * 0.8}s` }));
        svg.appendChild(c);
      }
    }
    pts.forEach(([x, y]) => svg.appendChild(el('circle', { cx: x, cy: y, r: 5, fill: '#fff' })));
    mini.appendChild(svg);
  }

  /* ---------- Parallax del hero + cursor (solo PC) ---------- */
  if (isPC) {
    document.body.classList.add('has-cursor');
    const cursor = $('.cursor');
    const LERP = 0.09; // calibrado: 0.16 se sentía a empujones
    let tx = 0, ty = 0, cx = 0, cy = 0;      // red
    let mx = -100, my = -100, px = -100, py = -100; // cursor
    addEventListener('pointermove', e => {
      const nx = e.clientX / innerWidth - .5, ny = e.clientY / innerHeight - .5;
      tx = nx * -28; ty = ny * -18;
      mx = e.clientX; my = e.clientY;
    }, { passive: true });
    addEventListener('pointerover', e => {
      const t = e.target.closest('a,button,label,.product,.case');
      cursor.classList.toggle('is-link', !!t);
    });
    let heroVisible = true;
    new IntersectionObserver(([en]) => heroVisible = en.isIntersecting, { rootMargin: '35%' }).observe(netHero);
    (function loop() {
      px += (mx - px) * .35; py += (my - py) * .35;
      cursor.style.transform = `translate3d(${px}px,${py}px,0)`;
      if (heroVisible) {
        cx += (tx - cx) * LERP; cy += (ty - cy) * LERP;
        netHero.style.transform = `translate3d(${cx}px,${cy}px,0)`;
      }
      requestAnimationFrame(loop);
    })();
  }

  /* ---------- HUD reloj ---------- */
  const clock = $('#hudClock');
  if (clock && isPC) {
    const tick = () => {
      clock.textContent = new Date().toLocaleTimeString('es-CR', { hour12: false, timeZone: 'America/Costa_Rica' });
    };
    tick(); setInterval(tick, 1000);
  }

  /* ---------- Nav ---------- */
  const nav = $('#nav'), burger = $('#burger');
  const onScroll = () => nav.classList.toggle('is-scrolled', scrollY > 40);
  addEventListener('scroll', onScroll, { passive: true }); onScroll();
  // Bloqueo de scroll compatible con iOS Safari (position:fixed + restaurar scrollY)
  let lockY = 0;
  const setMenu = open => {
    nav.classList.toggle('is-open', open);
    burger.setAttribute('aria-expanded', open);
    burger.setAttribute('aria-label', open ? 'Cerrar menú' : 'Abrir menú');
    if (open) { lockY = scrollY; document.body.classList.add('menu-open'); document.body.style.top = `-${lockY}px`; }
    else { document.body.classList.remove('menu-open'); document.body.style.top = ''; scrollTo({ top: lockY, behavior: 'instant' }); }
  };
  burger.addEventListener('click', () => setMenu(!nav.classList.contains('is-open')));
  $$('.nav__links a').forEach(a => a.addEventListener('click', () => nav.classList.contains('is-open') && setMenu(false)));
  addEventListener('keydown', e => e.key === 'Escape' && nav.classList.contains('is-open') && setMenu(false));

  /* ---------- Carruseles con snap (móvil): indicadores ---------- */
  const isMobile = matchMedia('(max-width:767px)');
  $$('[data-snap]').forEach(track => {
    const items = [...track.children];
    if (items.length < 2) return;
    const dots = document.createElement('div');
    dots.className = 'snap-dots'; dots.setAttribute('aria-hidden', 'true');
    items.forEach(() => dots.appendChild(document.createElement('i')));
    track.after(dots);
    const update = () => {
      if (!isMobile.matches) return;
      const x = track.scrollLeft + track.clientWidth * .3;
      let idx = 0;
      items.forEach((it, i) => { if (it.offsetLeft - track.offsetLeft <= x) idx = i; });
      [...dots.children].forEach((d, i) => d.classList.toggle('on', i === idx));
    };
    track.addEventListener('scroll', update, { passive: true }); update();
  });

  /* ---------- Barra inferior: se esconde al bajar, vuelve al subir ---------- */
  const mbar = $('.mbar');
  if (mbar) {
    let lastY = scrollY, acc = 0;
    addEventListener('scroll', () => {
      const y = scrollY, dy = y - lastY; lastY = y; acc = Math.sign(dy) === Math.sign(acc) ? acc + dy : dy;
      const nearBottom = innerHeight + y >= document.documentElement.scrollHeight - 200;
      if (acc > 80 && !nearBottom) mbar.classList.add('is-hidden');
      else if (acc < -40 || y < 10 || nearBottom) mbar.classList.remove('is-hidden');
    }, { passive: true });
  }

  /* ---------- Barra de progreso ---------- */
  const bar = $('.progress span');
  let lastP = -1;
  addEventListener('scroll', () => {
    const p = scrollY / (document.documentElement.scrollHeight - innerHeight);
    if (Math.abs(p - lastP) > .003) { bar.style.transform = `scaleX(${p})`; lastP = p; }
  }, { passive: true });

  /* ---------- Reveals ---------- */
  const reveals = $$('.reveal');
  if (reduced) reveals.forEach(r => r.classList.add('in'));
  else {
    const io = new IntersectionObserver(entries => entries.forEach(en => {
      if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target); }
    }), { threshold: .15, rootMargin: '0px 0px -8% 0px' });
    reveals.forEach(r => io.observe(r));
  }

  /* ---------- Contadores ---------- */
  const counters = $$('[data-count]');
  if (counters.length) {
    const run = b => {
      const end = +b.dataset.count, t0 = performance.now(), D = 1800;
      const ease = t => 1 - Math.pow(1 - t, 3);
      const step = now => {
        const t = Math.min(1, (now - t0) / D);
        b.textContent = Math.round(end * ease(t));
        if (t < 1) requestAnimationFrame(step);
      };
      reduced ? (b.textContent = end) : requestAnimationFrame(step);
    };
    const io = new IntersectionObserver(entries => entries.forEach(en => {
      if (en.isIntersecting) { run(en.target); io.unobserve(en.target); }
    }), { threshold: .5 });
    counters.forEach(c => io.observe(c));
  }

  /* ---------- Formulario → WhatsApp (prototipo, sin backend) ---------- */
  const form = $('#form');
  form.addEventListener('submit', e => {
    e.preventDefault();
    if (!form.reportValidity()) return;
    const f = new FormData(form);
    const msg = [
      `Hola Crimson, soy ${f.get('nombre')}.`,
      `Tel: ${f.get('telefono')}${f.get('email') ? ` · Email: ${f.get('email')}` : ''}`,
      `Proyecto: ${f.get('tipo')}`,
      f.get('mensaje') ? `\n${f.get('mensaje')}` : ''
    ].join('\n');
    open(`https://wa.me/50688889892?text=${encodeURIComponent(msg)}`, '_blank', 'noopener');
  });

  /* ---------- Pausar marquee/red fuera de pantalla ---------- */
  const track = $('.marquee__track');
  // Duplicar para bucle sin costura (translateX(-50%) exige dos copias)
  [...track.children].forEach(f => { const c = f.cloneNode(true); c.setAttribute('aria-hidden', 'true'); track.appendChild(c); });
  new IntersectionObserver(([en]) => track.style.animationPlayState = en.isIntersecting ? 'running' : 'paused').observe(track);
})();
