/* Jaay — interactions locales, aucune librairie externe. */
(function () {
  'use strict';

  function csrf() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) return meta.content;
    const m = document.cookie.match(/(?:^|;\s*)jaay_csrf=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  async function api(url, options = {}) {
    const opts = Object.assign({ method: 'GET', headers: {} }, options);
    opts.headers = Object.assign({ 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() }, opts.headers);
    if (opts.body && typeof opts.body !== 'string') opts.body = JSON.stringify(opts.body);
    const res = await fetch(url, opts);
    let data = {};
    try { data = await res.json(); } catch (_) { /* corps vide */ }
    if (!res.ok) throw new Error(data.detail || data.message || ('Erreur ' + res.status));
    return data;
  }
  window.jaayApi = api;

  // Copie de lien (boutique, produit, reference de commande)
  document.addEventListener('click', async function (e) {
    const btn = e.target.closest('[data-copy]');
    if (!btn) return;
    e.preventDefault();
    const value = btn.getAttribute('data-copy');
    try {
      await navigator.clipboard.writeText(value);
      const old = btn.textContent;
      btn.textContent = '✓ Copié';
      setTimeout(() => { btn.textContent = old; }, 1800);
    } catch (_) {
      window.prompt('Copiez ce lien :', value);
    }
  });

  // Partage natif si disponible
  document.addEventListener('click', async function (e) {
    const btn = e.target.closest('[data-share-url]');
    if (!btn || !navigator.share) return;
    e.preventDefault();
    try { await navigator.share({ title: btn.dataset.shareTitle || 'Jaay', url: btn.dataset.shareUrl }); } catch (_) {}
  });

  // Galerie produit
  document.querySelectorAll('.gallery-thumbs img').forEach(function (thumb) {
    thumb.addEventListener('click', function () {
      const main = document.querySelector('.gallery-main img');
      if (main) main.src = thumb.src;
    });
  });

  // Aperçu de la commission sur le formulaire de déclaration
  const amountInput = document.getElementById('decl-montant');
  const feeOut = document.getElementById('decl-fee');
  if (amountInput && feeOut) {
    const rate = Number(feeOut.dataset.rate || 1000);
    const update = function () {
      const n = Math.max(0, parseInt(amountInput.value || '0', 10) || 0);
      feeOut.textContent = Math.round((n * rate + 5000) / 10000).toLocaleString('fr-FR').replace(/\u202f/g, ' ') + ' F CFA';
    };
    amountInput.addEventListener('input', update);
    update();
  }

  // Aperçu image avant envoi
  const fileInput = document.getElementById('image-input');
  if (fileInput) {
    fileInput.addEventListener('change', function () {
      const f = fileInput.files && fileInput.files[0];
      const box = document.getElementById('image-preview');
      if (f && box) {
        const r = new FileReader();
        r.onload = function () { box.innerHTML = '<img src="' + r.result + '" alt="Aperçu" style="max-width:160px;border-radius:10px">'; };
        r.readAsDataURL(f);
      }
    });
  }

  // Assistant IA vendeur
  const aiForm = document.getElementById('ai-form');
  if (aiForm) {
    aiForm.addEventListener('submit', async function (e) {
      e.preventDefault();
      const out = document.getElementById('ai-result');
      const btn = aiForm.querySelector('button[type="submit"]');
      out.hidden = false;
      out.textContent = 'Génération en cours…';
      btn.disabled = true;
      try {
        const data = await api('/api/ai/assist', {
          method: 'POST',
          body: {
            title: document.getElementById('ai-title').value,
            keywords: document.getElementById('ai-keywords').value,
            category: document.getElementById('ai-category').value,
            language: document.getElementById('ai-language').value
          }
        });
        document.getElementById('ai-out-title').value = data.title || '';
        document.getElementById('ai-out-desc').value = data.description || '';
        document.getElementById('ai-out-tags').value = (data.tags || []).join(', ');
        out.innerHTML = '<strong>Mode : ' + data.mode + '</strong>' +
          (data.price_hint && data.price_hint.available
            ? ' · Prix médian observé : ' + data.price_hint.median_xof + ' F (' + data.price_hint.sample_size + ' produits)'
            : '') + '<br><span class="muted small">Relisez et corrigez toujours avant publication.</span>';
      } catch (err) {
        out.textContent = 'Erreur : ' + err.message;
      } finally { btn.disabled = false; }
    });
  }

  // Confirmations explicites sur les actions sensibles
  document.addEventListener('submit', function (e) {
    const form = e.target;
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) e.preventDefault();
  });

  // Disparition des messages flash
  setTimeout(function () {
    document.querySelectorAll('.flash').forEach(function (f) { f.style.opacity = '.55'; });
  }, 7000);
})();
