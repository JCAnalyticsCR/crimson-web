/* 5 · CIERRE: hoja del reporte de fallas (POST {api}/public/incidents, mismo contrato que la portada actual),
   pulsos de la red del banner (solo en pantalla) y barra fija movil (aparece cuando la portada ya paso). */
(() => {
  'use strict';
  const cfg = window.NV_CFG || {};
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const tienda = String(cfg.tienda || 'https://tienda.crimsoncr.com').replace(/\/$/, '');
  const waNum = String(cfg.wa || '50688889892').replace(/\D/g, '');
  const waUrl = (msg) => `https://wa.me/${waNum}?text=${encodeURIComponent(msg)}`;

  // enlaces desde la configuracion (un solo lugar): WhatsApp con mensaje armado, telefono y tienda
  $$('[data-nvc-wa]').forEach((a) => { a.href = waUrl(a.dataset.nvcWa); });
  if (cfg.tel) $$('[data-nvc-tel]').forEach((a) => { a.href = `tel:${String(cfg.tel).replace(/[^\d+]/g, '')}`; });
  $$('[data-nvc-tienda]').forEach((a) => { a.href = tienda + a.dataset.nvcTienda; });

  /* ---------- pulsos de la red del banner: corren solo con el banner en pantalla ---------- */
  const sop = $('.nvc-sop');
  if (sop && !reduced && 'IntersectionObserver' in window) {
    new IntersectionObserver(([en]) => sop.classList.toggle('is-vis', en.isIntersecting), { rootMargin: '10% 0px' }).observe(sop);
  }

  /* ---------- hoja del reporte ---------- */
  const dlg = $('#nvc-reporte');
  const form = $('#nvc-form');
  const ok = $('#nvc-ok');
  const status = $('#nvc-status');
  const btn = form?.querySelector('button[type=submit]');
  const label = btn?.querySelector('span');

  const open = () => {
    if (!dlg || dlg.open) return;
    if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
    // el foco inicial lo pone el navegador (boton cerrar): asi no salta el teclado en el celular.
    // Si quedo abierta la confirmacion de un envio, el foco va a ese titulo.
    if (ok && !ok.hidden) requestAnimationFrame(() => ok.querySelector('.nvc-ok__t')?.focus({ preventScroll: true }));
  };
  const close = () => {
    if (!dlg?.open) return;
    if (typeof dlg.close === 'function') dlg.close(); else dlg.removeAttribute('open');
  };
  $$('[data-nvc-open]').forEach((b) => b.addEventListener('click', open));
  dlg?.addEventListener('click', (e) => {
    if (e.target.closest('[data-nvc-close]')) return close();
    if (e.target === dlg) close(); // toque en el fondo (fuera de la hoja)
  });
  // si alguien llega con #reportar (p. ej. desde otra pagina), se abre directo
  if (/^#(reportar|reporte)$/.test(location.hash)) open();

  // arrastrar la hoja hacia abajo para cerrarla (solo movil, desde el encabezado)
  const head = $('[data-nvc-drag]', dlg || document);
  if (dlg && head && 'PointerEvent' in window) {
    let y0 = null, dy = 0;
    head.addEventListener('pointerdown', (e) => {
      if (innerWidth >= 768 || e.target.closest('button')) return;
      y0 = e.clientY; dy = 0; head.setPointerCapture(e.pointerId); dlg.classList.add('is-drag');
    });
    head.addEventListener('pointermove', (e) => {
      if (y0 === null) return;
      dy = Math.max(0, e.clientY - y0);
      dlg.style.transform = `translateY(${dy}px)`;
    });
    const end = () => {
      if (y0 === null) return;
      y0 = null; dlg.classList.remove('is-drag'); dlg.style.transform = '';
      if (dy > 110) close();
    };
    head.addEventListener('pointerup', end);
    head.addEventListener('pointercancel', end);
  }

  // envio: mismas reglas y mensajes que la portada actual; el API valida todo de nuevo
  if (form && status && btn && label) {
    const local = /^(localhost|127\.0\.0\.1)$/.test(location.hostname);
    // en localhost se puede apuntar a otro puerto con ?api=http://127.0.0.1:8010 (solo desarrollo)
    const apiDev = local && new URLSearchParams(location.search).get('api');
    const api = String((local && (apiDev || cfg.apiLocal)) || cfg.api || '').replace(/\/$/, '');
    const waUrg = waUrl('Hola Crimson, tengo una falla urgente en mi sistema.');
    const esc = (t) => String(t).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const say = (html, cls) => { status.className = 'nvc-status' + (cls ? ' ' + cls : ''); status.innerHTML = html; };
    const marca = (el) => { if (el) { el.setAttribute('aria-invalid', 'true'); el.focus(); } };
    const aviso = (el, msg) => { marca(el); say(esc(msg), 'is-bad'); };
    const falla = (msg) => say(`${esc(msg)} Podés reportarlo por <a href="${esc(waUrg)}" target="_blank" rel="noopener">WhatsApp</a>.`, 'is-bad');

    form.addEventListener('input', (e) => {
      const t = e.target;
      if (t.getAttribute?.('aria-invalid') === 'true') t.removeAttribute('aria-invalid');
      if (t.name === 'phone' || t.name === 'email') { form.elements.phone.removeAttribute('aria-invalid'); form.elements.email.removeAttribute('aria-invalid'); }
    });

    let enviando = false;
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (enviando) return;
      const f = new FormData(form);
      const v = (k) => String(f.get(k) || '').trim();
      const body = {
        name: v('name'), company: v('company') || null, phone: v('phone') || null, email: v('email') || null,
        kind: v('kind') || 'falla', severity: v('severity') || 'media', description: v('description'),
        location: v('location') || null, website: v('website') || null,
      };
      const el = form.elements;
      if (body.name.length < 2) return aviso(el.name, 'Falta tu nombre.');
      if (!body.phone && !body.email) return aviso(el.phone, 'Dejanos un teléfono o un correo para contactarte.');
      if (body.email && !el.email.checkValidity()) return aviso(el.email, 'El correo no parece válido.');
      if (body.description.length < 10) return aviso(el.description, 'Contanos un poco más de lo que pasa (mínimo 10 caracteres).');
      if (!api) return falla('El reporte en línea no está disponible en este momento.');

      enviando = true; btn.disabled = true; label.textContent = 'Enviando…'; say('Enviando el reporte…');
      const ctrl = new AbortController(); const t = setTimeout(() => ctrl.abort(), 15000);
      try {
        const r = await fetch(`${api}/public/incidents`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: ctrl.signal, credentials: 'omit',
        });
        const data = await r.json().catch(() => ({}));
        if (r.ok) {
          form.reset(); say('');
          const num = ok?.querySelector('[data-nvc-num]');
          if (num) { num.hidden = !data.number; const b = num.querySelector('b'); if (b) b.textContent = data.number || ''; }
          if (ok) {
            form.hidden = true; ok.hidden = false;
            ok.querySelector('.nvc-ok__t')?.focus({ preventScroll: true });
            ok.parentElement?.scrollTo?.(0, 0);
          } else {
            say(data.number ? `Recibimos tu reporte. Tu número de ticket es <b>${esc(data.number)}</b>.` : 'Recibimos tu reporte. Te contactamos pronto.');
          }
        } else if (r.status === 429) {
          falla('Recibimos varios reportes seguidos desde tu conexión. Esperá unos minutos.');
        } else if (r.status === 422) {
          const campos = ((data.detail && data.detail.errors) || []).map((x) => x.campo).filter(Boolean).join(', ');
          falla(`Revisá los datos del formulario${campos ? ` (${campos})` : ''}.`);
        } else {
          falla('No pudimos registrar el reporte.');
        }
      } catch {
        falla('No hay conexión con el sistema de soporte.');
      } finally {
        clearTimeout(t); enviando = false; btn.disabled = false; label.textContent = 'Enviar reporte';
      }
    });

    // "Reportar otra falla": vuelve al formulario limpio
    ok?.querySelector('[data-nvc-again]')?.addEventListener('click', () => {
      ok.hidden = true; form.hidden = false; say('');
      form.elements.name?.focus({ preventScroll: true });
    });
  }

  /* ---------- barra fija movil ---------- */
  const dock = $('[data-nvc-dock]');
  if (dock) {
    const set = (on) => dock.classList.toggle('is-on', on);
    const hero = $('.nvh-hero');
    if (hero && 'IntersectionObserver' in window) {
      // visible solo cuando la portada quedo arriba (no antes de llegar a ella)
      new IntersectionObserver(([en]) => set(!en.isIntersecting && en.boundingClientRect.top < 0)).observe(hero);
    } else {
      let raf = 0;
      const check = () => { raf = 0; set(scrollY > innerHeight * 0.8); };
      addEventListener('scroll', () => { if (!raf) raf = requestAnimationFrame(check); }, { passive: true });
      check();
    }
  }
})();
