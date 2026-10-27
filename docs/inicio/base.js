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
})();
