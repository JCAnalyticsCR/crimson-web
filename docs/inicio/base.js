/* Crimson · portada nueva: lo comun a todas las partes (entradas al aparecer y configuracion del sitio).
   Cada parte trae su propio parts/N-*.js en una IIFE; aqui solo vive lo compartido. */
(() => {
  'use strict';
  document.documentElement.classList.add('js');
  // #site-config: telefono, WhatsApp, API (un solo lugar). Las partes lo leen con window.NV_CFG.
  try { window.NV_CFG = JSON.parse(document.getElementById('site-config').textContent); }
  catch { window.NV_CFG = { tel: '+50688889892', wa: '50688889892', api: 'https://api-production-f07a.up.railway.app', store: 'crimson' }; }
  // Enlaces a secciones de la portada anterior (videos, correos, mensajes ya enviados): llevan a su equivalente.
  // Se reemplaza el ancla antes de que las partes la lean (#sol-* abre su hoja, #reportar el formulario de fallas).
  const ALIAS = { seguridad: 'sol-seguridad', redes: 'sol-redes', iot: 'sol-iot', ia: 'sol-software', software: 'sol-software',
    servicios: 'soluciones', equipos: 'tienda', destacados: 'tienda', monitoreo: 'proyectos', casos: 'proyectos',
    'caso-residencial': 'proyectos', 'caso-acceso': 'proyectos', 'caso-bodega': 'proyectos', ejemplos: 'soluciones',
    incidentForm: 'reportar', portada: 'inicio', top: 'inicio' };
  const alias = () => {
    const n = ALIAS[location.hash.slice(1)]; if (!n) return false;
    history.replaceState(null, '', '#' + n);
    // las secciones simples se encuadran aqui; #sol-* y #reportar los abre su parte
    if (!/^(sol-|reportar)/.test(n)) document.getElementById(n)?.scrollIntoView();
    return true;
  };
  alias();
  addEventListener('hashchange', () => { if (alias()) dispatchEvent(new HashChangeEvent('hashchange')); });
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const els = [...document.querySelectorAll('[data-nv-in]')];
  if (reduced || !('IntersectionObserver' in window)) { els.forEach(e => e.classList.add('is-in')); return; }
  const io = new IntersectionObserver(es => es.forEach(en => { if (en.isIntersecting) { en.target.classList.add('is-in'); io.unobserve(en.target); } }),
    { threshold: .12, rootMargin: '0px 0px -6% 0px' });
  els.forEach(e => io.observe(e));

  /* ---------- Red de nodos de fondo, en toda la pagina (26/10: "que se note mas movimiento") ----------
     Canvas fijo detras del contenido: nodos que derivan, lineas entre los cercanos, pulsos rojos que viajan por ellas,
     profundidad al hacer scroll y, en PC con mouse, los nodos cerca del puntero se encienden. 30 cuadros/s, DPR max 2,
     se detiene con la pestana oculta y no existe con reduced-motion. */
  const red = (host = null) => {
    const cv = document.createElement('canvas');
    cv.className = host ? 'nv-bgnet nv-bgnet--host' : 'nv-bgnet'; cv.setAttribute('aria-hidden', 'true');
    host ? host.appendChild(cv) : document.body.prepend(cv);
    const ctx = cv.getContext('2d'); if (!ctx) return;
    const movil = matchMedia('(max-width:767px)').matches;
    const fino = matchMedia('(hover:hover) and (pointer:fine)').matches;
    const N = host ? (movil ? 24 : 46) : (movil ? 42 : 96), DIST = movil ? 140 : 190, PASO = 1000 / 30;
    let W = 0, H = 0, nodos = [], pulsos = [], mx = -1e4, my = -1e4, prev = 0, cuenta = 0, raf = 0;
    const medir = () => {
      const dpr = Math.min(2, devicePixelRatio || 1);
      W = host ? host.clientWidth : innerWidth; H = host ? host.clientHeight : innerHeight;
      cv.width = W * dpr; cv.height = H * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (!nodos.length) nodos = Array.from({ length: N }, () => ({ x: Math.random() * W, y: Math.random() * H, vx: (Math.random() - .5) * .25, vy: (Math.random() - .5) * .25, z: .4 + Math.random() * .6, r: Math.random() < .18 }));
    };
    const pos = n => ({ x: n.x, y: host ? n.y : ((n.y - scrollY * .12 * n.z) % H + H) % H });
    const cuadro = ts => {
      raf = requestAnimationFrame(cuadro);
      if (ts - prev < PASO) return;
      const dt = Math.min(3, (ts - prev) / 16.7); prev = ts;
      ctx.clearRect(0, 0, W, H);
      const P = nodos.map(n => {
        n.x += n.vx * dt; n.y += n.vy * dt;
        if (n.x < -20) n.x = W + 20; if (n.x > W + 20) n.x = -20;
        if (host) { if (n.y < -20) n.y = H + 20; if (n.y > H + 20) n.y = -20; }
        return pos(n);
      });
      const pares = [];
      for (let i = 0; i < N; i++) for (let j = i + 1; j < N; j++) {
        const dx = P[i].x - P[j].x, dy = P[i].y - P[j].y, d = Math.hypot(dx, dy);
        if (d > DIST) continue;
        pares.push([i, j]);
        const cerca = fino && Math.hypot((P[i].x + P[j].x) / 2 - mx, (P[i].y + P[j].y) / 2 - my) < 170;
        ctx.strokeStyle = cerca ? `rgba(255,68,88,${.55 * (1 - d / DIST)})` : `rgba(244,241,238,${.26 * (1 - d / DIST)})`;
        ctx.lineWidth = cerca ? 1.2 : 1;
        ctx.beginPath(); ctx.moveTo(P[i].x, P[i].y); ctx.lineTo(P[j].x, P[j].y); ctx.stroke();
      }
      P.forEach((p, i) => {
        const n = nodos[i], cerca = fino && Math.hypot(p.x - mx, p.y - my) < 150;
        ctx.fillStyle = n.r || cerca ? 'rgba(255,68,88,.9)' : 'rgba(244,241,238,.62)';
        ctx.beginPath(); ctx.arc(p.x, p.y, (cerca ? 2.8 : 1.7) * n.z + .4, 0, 6.283); ctx.fill();
      });
      if (++cuenta % (movil ? 14 : 7) === 0 && pares.length && pulsos.length < 18) {
        const [a, b] = pares[(Math.random() * pares.length) | 0];
        pulsos.push({ a, b, t: 0 });
      }
      pulsos = pulsos.filter(q => (q.t += .022 * dt) < 1);
      pulsos.forEach(q => {
        const A = P[q.a], B = P[q.b]; if (Math.hypot(A.x - B.x, A.y - B.y) > DIST) return;
        const x = A.x + (B.x - A.x) * q.t, y = A.y + (B.y - A.y) * q.t;
        const g = ctx.createRadialGradient(x, y, 0, x, y, 10);
        g.addColorStop(0, 'rgba(255,90,110,.95)'); g.addColorStop(1, 'rgba(226,35,58,0)');
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, 10, 0, 6.283); ctx.fill();
      });
    };
    medir();
    addEventListener('resize', medir, { passive: true });
    if (fino) addEventListener('pointermove', e => {
      if (host) { const r = host.getBoundingClientRect(); mx = e.clientX - r.left; my = e.clientY - r.top; } else { mx = e.clientX; my = e.clientY; }
    }, { passive: true });
    let visible = true;
    const correr = () => { cancelAnimationFrame(raf); if (!document.hidden && visible) raf = requestAnimationFrame(cuadro); };
    document.addEventListener('visibilitychange', correr);
    // la de la portada solo corre mientras la portada se ve
    if (host && 'IntersectionObserver' in window) new IntersectionObserver(([en]) => { visible = en.isIntersecting; correr(); }).observe(host);
    correr();
  };

  /* ---------- Tarjetas que responden al puntero (PC con mouse): inclinacion 3D y un brillo que sigue al cursor ---------- */
  const tarjetas = () => {
    if (!matchMedia('(hover:hover) and (pointer:fine)').matches) return;
    document.querySelectorAll('.nvt-p, .nvs-card, .nvp-card, .nvt-q, .nvt-arma').forEach(el => {
      el.classList.add('nv-tilt');
      const brillo = document.createElement('span'); brillo.className = 'nv-shine'; brillo.setAttribute('aria-hidden', 'true');
      el.appendChild(brillo);
      el.addEventListener('pointermove', e => {
        const r = el.getBoundingClientRect(), x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
        el.style.setProperty('--mx', `${x * 100}%`); el.style.setProperty('--my', `${y * 100}%`);
        el.style.transition = 'transform 120ms ease-out';
        el.style.transform = `perspective(900px) translateY(-4px) rotateX(${((.5 - y) * 7).toFixed(2)}deg) rotateY(${((x - .5) * 9).toFixed(2)}deg)`;
      });
      el.addEventListener('pointerleave', () => { el.style.transition = 'transform 600ms cubic-bezier(.16,1,.3,1)'; el.style.transform = ''; });
    });
  };
  red(); red(document.querySelector('.nvh-hero')); tarjetas(); // con reduced-motion el bloque de entradas ya salio antes de llegar aqui
})();
