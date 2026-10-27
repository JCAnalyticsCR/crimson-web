/* Crimson · portada nueva: lo comun a todas las partes (entradas al aparecer y configuracion del sitio).
   Cada parte trae su propio parts/N-*.js en una IIFE; aqui solo vive lo compartido. */
(() => {
  'use strict';
  document.documentElement.classList.add('js');
  // #site-config: telefono, WhatsApp, API (un solo lugar). Las partes lo leen con window.NV_CFG.
  try { window.NV_CFG = JSON.parse(document.getElementById('site-config').textContent); }
  catch { window.NV_CFG = { tel: '+50688889892', wa: '50688889892', api: 'https://api-production-f07a.up.railway.app', store: 'crimson' }; }
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const els = [...document.querySelectorAll('[data-nv-in]')];
  if (reduced || !('IntersectionObserver' in window)) { els.forEach(e => e.classList.add('is-in')); return; }
  const io = new IntersectionObserver(es => es.forEach(en => { if (en.isIntersecting) { en.target.classList.add('is-in'); io.unobserve(en.target); } }),
    { threshold: .12, rootMargin: '0px 0px -6% 0px' });
  els.forEach(e => io.observe(e));
})();
