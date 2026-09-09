/* Crimson Consulting · "Sala de monitoreo"
   Toda la coreografía vive aquí. Sin dependencias. */
(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const isMobile = matchMedia('(max-width:767px)');
  const isPC = matchMedia('(min-width:1024px) and (hover:hover) and (prefers-reduced-motion: no-preference)').matches;
  const hasView = CSS.supports('animation-timeline: view()');
  const CFG = (() => { try { return JSON.parse($('#site-config').textContent); } catch { return { tel: '+50688889892', wa: '50688889892', waLink: 'https://wa.me/message/5YQKXZFUPHWLA1' }; } })();
  const scrollFns = []; // un solo listener de scroll con rAF
  const SVG = 'http://www.w3.org/2000/svg';
  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElementNS(SVG, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    kids.forEach(k => n.appendChild(k));
    return n;
  };

  /* ---------- Red de nodos: horizontal (PC), vertical (móvil), convergente (contacto) ---------- */
  const NODES = [[120,140],[380,90],[700,160],[1050,110],[1320,190],[200,420],[520,470],[900,430],[1250,470],[80,680],[420,700],[760,660],[1120,690],[1380,640]];
  const LINKS = [[0,1],[1,2],[2,3],[3,4],[0,5],[1,6],[2,6],[3,7],[4,8],[5,6],[6,7],[7,8],[5,9],[6,10],[7,11],[8,12],[8,13],[10,11],[11,12]];
  const NODES_M = [[120,160],[520,110],[700,340],[90,520],[380,600],[720,760],[160,980],[560,1080],[300,1330]];
  const LINKS_M = [[0,1],[1,2],[0,3],[2,4],[3,4],[4,5],[3,6],[5,7],[6,8],[7,8],[4,7]];
  const PULSE = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--pulse')) || 6;

  function buildNet({ dark = false, seed = 0, portrait = false, converge = false, density = 1 } = {}) {
    const N = portrait ? NODES_M : NODES;
    let L = portrait ? LINKS_M : LINKS;
    if (converge) { const c = portrait ? 4 : 6; L = L.map(([a, b]) => (a === c || b === c) ? [a, b] : [a, c]); }
    const svg = el('svg', { viewBox: portrait ? '0 0 810 1440' : '0 0 1440 810', preserveAspectRatio: 'xMidYMid slice' });
    const line = dark ? 'rgba(255,255,255,.12)' : 'rgba(21,19,26,.14)';
    const node = dark ? '#f4f1ee' : '#15131a';
    L.forEach(([a, b], i) => {
      const [x1, y1] = N[a], [x2, y2] = N[b];
      const d = `M${x1} ${y1} L${x2} ${y2}`;
      svg.appendChild(el('path', { d, class: 'net-line', stroke: line }));
      if (i % Math.round(1 / density) === 0) {
        const dash = el('path', { d, class: 'net-dash' });
        dash.style.animationDelay = `${-i * 0.7}s`;
        svg.appendChild(dash);
      }
      if (!reduced && i % 2 === seed && (density === 1 || i % 4 === seed)) {
        const c = el('circle', { r: 4, fill: '#e2233a' });
        c.appendChild(el('animateMotion', { dur: `${converge ? 4 : PULSE + (i % 3)}s`, repeatCount: 'indefinite', path: d, begin: `${-i * 1.3}s` }));
        svg.appendChild(c);
      }
    });
    N.forEach(([x, y], i) => {
      const g = el('circle', { cx: x, cy: y, r: 14, class: 'net-glow' });
      g.style.animationDuration = `${3 + (i % 4)}s`;
      g.style.animationDelay = `${i * 0.4}s`;
      if (density < 1 && i % 3) g.style.animation = 'none';
      svg.appendChild(g);
      svg.appendChild(el('circle', { cx: x, cy: y, r: 5, fill: node, class: 'net-node' }));
    });
    return svg;
  }
  const netHero = $('#netHero'), netContact = $('#netContact');
  const heroSvg = buildNet({ portrait: isMobile.matches, density: isMobile.matches ? .5 : 1 });
  netHero && netHero.appendChild(heroSvg);
  netContact && netContact.appendChild(buildNet({ dark: true, seed: 1, converge: true, portrait: isMobile.matches, density: isMobile.matches ? .5 : 1 }));

  // Toque sobre la red (móvil): los nodos cercanos al dedo se encienden
  if (isMobile.matches && !reduced && netHero) {
    $('#hero').addEventListener('pointerdown', e => {
      const pt = heroSvg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
      let p; try { p = pt.matrixTransform(heroSvg.getScreenCTM().inverse()); } catch { return; }
      $$('.net-node', heroSvg).forEach(n => {
        const d = Math.hypot(n.cx.baseVal.value - p.x, n.cy.baseVal.value - p.y);
        if (d < 260) { n.classList.add('hit'); n.previousElementSibling?.classList.add('hit'); setTimeout(() => { n.classList.remove('hit'); n.previousElementSibling?.classList.remove('hit'); }, 600 + d); }
      });
    }, { passive: true });
  }

  // Pausar TODO lo que anima cuando su sección no se ve (CSS + SMIL)
  if (!reduced) {
    const io = new IntersectionObserver(entries => entries.forEach(en => {
      en.target.classList.toggle('is-offscreen', !en.isIntersecting);
      en.target.querySelectorAll('svg').forEach(s => en.isIntersecting ? s.unpauseAnimations() : s.pauseAnimations());
    }), { rootMargin: '20%' });
    $$('section, footer').forEach(s => io.observe(s));
  }

  /* ---------- Consola: cámara activa, tono ambiente, theme-color ---------- */
  const themeMeta = $('#themeColor'), hudCam = $('#hudCam'), hudTl = $('.hud--tl');
  const TC = { cream: '#f6f2ee', bone: '#efe9e3', ink: '#15131a' };
  const setScene = sec => {
    const tone = sec.dataset.tone || 'cream';
    document.body.dataset.scene = sec.id || 'footer';
    if (document.body.dataset.tone !== tone) {
      document.body.dataset.tone = tone;
      if (themeMeta && !document.body.classList.contains('menu-open')) themeMeta.content = TC[tone];
    }
    if (hudCam && sec.dataset.cam) {
      const txt = `REC · CAM ${sec.dataset.cam} · ${sec.dataset.label || ''}`;
      if (hudCam.textContent !== txt) { hudCam.textContent = txt; hudTl.classList.add('is-switching'); setTimeout(() => hudTl.classList.remove('is-switching'), 280); }
    }
  };
  const sceneIO = new IntersectionObserver(entries => entries.forEach(en => en.isIntersecting && setScene(en.target)), { rootMargin: '-45% 0px -45% 0px' });
  $$('section[data-tone]').forEach(s => sceneIO.observe(s));
  const footer = $('footer'); if (footer) { footer.dataset.tone = 'ink'; sceneIO.observe(footer); }

  /* ---------- Reloj (HUD + feeds) ---------- */
  const clocks = [$('#hudClock'), ...$$('.feed__clock')].filter(Boolean);
  if (clocks.length) {
    const tick = () => { const t = new Date().toLocaleTimeString('es-CR', { hour12: false, timeZone: 'America/Costa_Rica' }); clocks.forEach(c => c.textContent = t); };
    tick(); setInterval(tick, 1000);
  }

  /* ---------- PC: parallax de la red, cursor con estados, nodo más cercano ---------- */
  if (isPC) {
    document.body.classList.add('has-cursor');
    const cursor = $('.cursor'), label = $('.cursor__label');
    const LERP = 0.09;
    let tx = 0, ty = 0, cx = 0, cy = 0, mx = -100, my = -100, px = -100, py = -100, raf = 0, lastNear = -1;
    const nodeEls = $$('.net-node', heroSvg), glowEls = $$('.net-glow', heroSvg);
    let heroVisible = true;
    new IntersectionObserver(([en]) => heroVisible = en.isIntersecting, { rootMargin: '35%' }).observe(netHero);
    const nearest = () => {
      const r = netHero.getBoundingClientRect(), sx = 1440 / r.width, sy = 810 / r.height;
      let best = -1, bd = 150 * 150;
      NODES.forEach(([x, y], i) => { const dx = (mx - r.left) * sx - x, dy = (my - r.top) * sy - y; const d = dx * dx + dy * dy; if (d < bd) { bd = d; best = i; } });
      return best;
    };
    function loop() {
      px += (mx - px) * .35; py += (my - py) * .35;
      cursor.style.transform = `translate3d(${px}px,${py}px,0)`;
      if (heroVisible) {
        cx += (tx - cx) * LERP; cy += (ty - cy) * LERP;
        netHero.style.transform = `translate3d(${cx}px,${cy}px,0)`;
        const n = nearest();
        if (n !== lastNear) { nodeEls.forEach((c, i) => c.classList.toggle('near', i === n)); glowEls.forEach((g, i) => g.classList.toggle('hit', i === n)); lastNear = n; }
      }
      const idle = Math.abs(mx - px) + Math.abs(my - py) + (heroVisible ? Math.abs(tx - cx) + Math.abs(ty - cy) : 0) < .1;
      raf = idle ? 0 : requestAnimationFrame(loop);
    }
    addEventListener('pointermove', e => {
      tx = (e.clientX / innerWidth - .5) * -28; ty = (e.clientY / innerHeight - .5) * -18;
      mx = e.clientX; my = e.clientY;
      if (!raf) raf = requestAnimationFrame(loop);
    }, { passive: true });
    addEventListener('pointerover', e => {
      const t = e.target.closest('[data-cursor],a,button,label,.feed');
      const isFrame = !!e.target.closest('.feed');
      cursor.classList.toggle('is-link', !!t && !isFrame);
      cursor.classList.toggle('is-frame', isFrame);
      const txt = t?.dataset.cursor || '';
      label.textContent = txt; cursor.classList.toggle('has-label', !!txt);
      cursor.classList.toggle('is-hidden', !!e.target.closest('input,textarea,select'));
    });
    // Botones: el relleno barre desde el punto de entrada; imán en los grandes
    $$('.btn').forEach(b => {
      b.addEventListener('pointermove', e => {
        const r = b.getBoundingClientRect();
        b.style.setProperty('--x', `${e.clientX - r.left}px`); b.style.setProperty('--y', `${e.clientY - r.top}px`);
        if (b.classList.contains('btn--lg')) b.style.transform = `translate(${(e.clientX - r.left - r.width / 2) * .12}px,${(e.clientY - r.top - r.height / 2) * .12}px)`;
      }, { passive: true });
      b.addEventListener('pointerleave', () => b.style.transform = '');
    });
    // HUD: el timecode reacciona al scroll rápido y al foco sobre CTAs
    const hudState = $('#hudState');
    let ly = scrollY;
    scrollFns.push(() => { const v = Math.abs(scrollY - ly); ly = scrollY; if (hudState) hudState.textContent = v > 40 ? '›› AVANCE' : '14 NODOS'; });
    $$('.btn').forEach(b => { b.addEventListener('pointerenter', () => hudState && (hudState.textContent = '● ENFOCANDO')); b.addEventListener('pointerleave', () => hudState && (hudState.textContent = '14 NODOS')); });
  }

  /* ---------- Móvil: giroscopio con permiso (iOS) o parallax por scroll ---------- */
  if (isMobile.matches && !reduced && netHero) {
    let tx = 0, ty = 0, cx = 0, cy = 0, raf = 0, hv = true, tilt = false;
    new IntersectionObserver(([e]) => hv = e.isIntersecting).observe(netHero);
    const loop = () => { cx += (tx - cx) * .08; cy += (ty - cy) * .08; netHero.style.transform = `translate3d(${cx}px,${cy}px,0)`; raf = (Math.abs(tx - cx) + Math.abs(ty - cy) > .05) ? requestAnimationFrame(loop) : 0; };
    const onTilt = e => { if (!hv) return; tilt = true; tx = Math.max(-14, Math.min(14, (e.gamma || 0) * .5)); ty = Math.max(-10, Math.min(10, ((e.beta || 0) - 45) * .3)); if (!raf) raf = requestAnimationFrame(loop); };
    const arm = () => addEventListener('deviceorientation', onTilt, { passive: true });
    const DOE = window.DeviceOrientationEvent;
    if (DOE?.requestPermission) $('#hero').addEventListener('touchstart', () => DOE.requestPermission().then(s => s === 'granted' && arm()).catch(() => {}), { once: true, passive: true });
    else if (DOE) arm();
    scrollFns.push(() => { if (hv && !tilt && !raf) netHero.style.transform = `translate3d(0,${scrollY * -.15}px,0)`; });
  }

  /* ---------- Nav ---------- */
  const nav = $('#nav'), burger = $('#burger');
  const onScroll = () => { if (document.body.classList.contains('menu-open')) return; nav.classList.toggle('is-scrolled', scrollY > 40); };
  onScroll();
  let lockY = 0;
  const setMenu = open => {
    nav.classList.toggle('is-open', open);
    burger.setAttribute('aria-expanded', open);
    burger.setAttribute('aria-label', open ? 'Cerrar menú' : 'Abrir menú');
    if (open) { lockY = scrollY; document.body.classList.add('menu-open'); document.body.style.top = `-${lockY}px`; themeMeta && (themeMeta.content = TC.cream); }
    else { document.body.classList.remove('menu-open'); document.body.style.top = ''; scrollTo({ top: lockY, behavior: 'instant' }); themeMeta && (themeMeta.content = TC[document.body.dataset.tone] || TC.cream); }
    $$('main, footer, .wa').forEach(e => { e.inert = open; e.setAttribute('aria-hidden', open); });
    if (open) setTimeout(() => $('.nav__links a').focus(), 400); else burger.focus();
  };
  burger.addEventListener('click', () => setMenu(!nav.classList.contains('is-open')));
  $$('.nav__links a').forEach(a => a.addEventListener('click', () => nav.classList.contains('is-open') && setMenu(false)));
  addEventListener('keydown', e => e.key === 'Escape' && nav.classList.contains('is-open') && setMenu(false));

  /* ---------- Un solo listener de scroll ---------- */
  scrollFns.push(onScroll);
  let ticking = false;
  addEventListener('scroll', () => { if (ticking) return; ticking = true; requestAnimationFrame(() => { scrollFns.forEach(f => f()); ticking = false; }); }, { passive: true });

  /* ---------- Reveals (fallback IO; en móvil con view() el CSS manda) ---------- */
  const useNativeReveal = hasView && isMobile.matches && !reduced;
  const reveals = $$('.reveal').filter(r => !r.closest('[data-snap]') && !(useNativeReveal && !r.classList.contains('lines') && !r.classList.contains('sec-idx')));
  $$('.steps').forEach(s => reveals.push(s));
  if (reduced) reveals.forEach(r => r.classList.add('in'));
  else {
    const io = new IntersectionObserver(entries => entries.forEach(en => { if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target); } }), { threshold: .12, rootMargin: '0px 0px -8% 0px' });
    reveals.forEach(r => io.observe(r));
  }

  /* ---------- REC se enciende cuando llega Videovigilancia ---------- */
  const firstCard = $('.card');
  if (firstCard) {
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setTimeout(() => { $('#servicios').classList.add('is-rec'); navigator.vibrate?.(8); }, 300); io.disconnect(); } }, { threshold: .5 });
    io.observe(firstCard);
  }

  /* ---------- Carruseles (móvil): barra + contador 01/06 ---------- */
  $$('[data-snap]').forEach(track => {
    const items = [...track.children]; if (items.length < 2) return;
    const ui = document.createElement('div'); ui.className = 'snap-ui meta'; ui.setAttribute('aria-hidden', 'true');
    const num = document.createElement('span'), bar = document.createElement('span'); bar.className = 'snap-ui__bar'; bar.appendChild(document.createElement('i'));
    ui.append(num, bar); track.after(ui);
    const n = items.length, pad = x => String(x).padStart(2, '0');
    bar.style.setProperty('--w', `${100 / n}%`); num.textContent = `${pad(1)} / ${pad(n)}`;
    let last = 0, tick = false;
    track.addEventListener('scroll', () => {
      if (!isMobile.matches || tick) return; tick = true;
      requestAnimationFrame(() => {
        tick = false;
        const p = track.scrollLeft / Math.max(1, track.scrollWidth - track.clientWidth);
        bar.style.setProperty('--x', `${p * (n - 1) * 100}%`);
        const i = Math.round(p * (n - 1));
        if (i !== last) { num.textContent = `${pad(i + 1)} / ${pad(n)}`; navigator.vibrate?.(4); last = i; }
      });
    }, { passive: true });
  });

  /* ---------- Muro de monitores: cambio de feed cada 5 s (PC), scanline en el activo ---------- */
  const wall = $('#wall');
  if (wall && !reduced) {
    const feeds = $$('.feed', wall);
    const pool = ['foto-camaras-comunidad','foto-rack-h','foto-camara-residencia-h','foto-antena-camara-h','foto-patch-panel','foto-camaras-poste-h','foto-tecnico-rack-h','foto-instalacion-torre','foto-tecnico-poste-h'];
    let k = 0, timer = 0;
    const swap = () => {
      feeds.forEach(f => f.classList.remove('is-active'));
      const f = feeds[k % feeds.length]; f.classList.add('is-switch', 'is-active');
      setTimeout(() => {
        if (!isMobile.matches) {
          const img = f.querySelector('img');
          const shown = feeds.map(x => (x.querySelector('img').src.match(/img\/(.+?)-\d+\.webp/) || [])[1]);
          const free = pool.filter(n => !shown.includes(n));
          const name = free[(k * 7) % free.length];
          img.src = `assets/img/${name}-800.webp`; img.srcset = `assets/img/${name}-480.webp 480w, assets/img/${name}-800.webp 800w`;
        }
        f.classList.remove('is-switch');
      }, 140);
      k++;
    };
    new IntersectionObserver(([en]) => { if (en.isIntersecting) { swap(); timer = setInterval(swap, 5000); } else clearInterval(timer); }).observe(wall);
  }

  /* ---------- Formulario → WhatsApp ---------- */
  const form = $('#form');
  form?.addEventListener('submit', e => {
    e.preventDefault();
    if (!form.reportValidity()) return;
    const f = new FormData(form);
    const msg = [`Hola Crimson, soy ${f.get('nombre')}.`, `Tel: ${f.get('telefono')}${f.get('email') ? ` · Email: ${f.get('email')}` : ''}`, `Proyecto: ${f.get('tipo')}`, f.get('mensaje') ? `\n${f.get('mensaje')}` : ''].join('\n');
    const a = Object.assign(document.createElement('a'), { href: `https://wa.me/${CFG.wa}?text=${encodeURIComponent(msg)}`, target: '_blank', rel: 'noopener' });
    document.body.append(a); a.click(); a.remove();
  });
  // Feedback antes del salto a WhatsApp (en iOS tarda ~1 s)
  const waBtn = $('#waBtn');
  waBtn?.addEventListener('click', () => { const s = waBtn.querySelector('span'); if (s) s.textContent = 'Abriendo WhatsApp…'; setTimeout(() => s && (s.textContent = 'Escribinos por WhatsApp'), 2500); });
})();
