/**
 * iBarber Forms — validação e formatação global
 * Inclua em base_gestao.html e páginas standalone.
 */
(function (w) {
  'use strict';

  // ── Máscaras ────────────────────────────────────────────────
  function mascaraTel(e) {
    let v = (typeof e === 'string' ? e : e.target.value).replace(/\D/g, '').slice(0, 11);
    if (v.length > 10)     v = v.replace(/^(\d{2})(\d{5})(\d{4})$/, '($1) $2-$3');
    else if (v.length > 6) v = v.replace(/^(\d{2})(\d{4,5})(\d{0,4})/, '($1) $2-$3');
    else if (v.length > 2) v = v.replace(/^(\d{2})(\d+)/, '($1) $2');
    else if (v.length > 0) v = '(' + v;
    if (typeof e !== 'string') e.target.value = v;
    return v;
  }

  function mascaraMoeda(e) {
    const inp = typeof e === 'string' ? null : e.target;
    const raw = inp ? inp.value : e;
    let v = raw.toString().replace(/[^\d,\.]/g, '').replace(',', '.');
    const n = parseFloat(v);
    const fmt = isNaN(n) ? '' : n.toFixed(2).replace('.', ',');
    if (inp) inp.value = fmt;
    return fmt;
  }

  // ── Erro inline ─────────────────────────────────────────────
  function erroInline(input, msg) {
    limparErro(input);
    const err = document.createElement('div');
    err.className = 'ib-erro';
    err.textContent = msg;
    input.parentNode.insertBefore(err, input.nextSibling);
    input.classList.add('ib-invalido');
  }

  function limparErro(input) {
    const next = input.nextElementSibling;
    if (next && next.classList.contains('ib-erro')) next.remove();
    input.classList.remove('ib-invalido');
  }

  // ── Validação de campo ──────────────────────────────────────
  function validarCampo(input) {
    const tipo = input.dataset.validate || '';
    const val  = input.value.trim();
    const req  = input.required || input.dataset.required === '1';

    if (req && !val) { erroInline(input, 'Campo obrigatório'); return false; }
    if (!val)        { limparErro(input); return true; }

    if (tipo === 'tel' || input.type === 'tel') {
      if (val.replace(/\D/g,'').length < 10) { erroInline(input, 'Telefone inválido — mínimo 10 dígitos'); return false; }
    }
    if (tipo === 'email' || input.type === 'email') {
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(val)) { erroInline(input, 'E-mail inválido'); return false; }
    }
    if (tipo === 'nome') {
      if (val.length < 3) { erroInline(input, 'Mínimo 3 caracteres'); return false; }
    }
    if (tipo === 'senha') {
      if (val.length < 8) { erroInline(input, 'Mínimo 8 caracteres'); return false; }
    }
    if (tipo === 'moeda' || input.dataset.mask === 'moeda') {
      const n = parseFloat(val.replace(',', '.'));
      if (isNaN(n) || n < 0) { erroInline(input, 'Valor inválido'); return false; }
    }
    if (input.type === 'number' || tipo === 'number') {
      const n   = parseFloat(val.replace(',', '.'));
      const min = input.min !== '' ? parseFloat(input.min) : null;
      const max = input.max !== '' ? parseFloat(input.max) : null;
      if (isNaN(n))               { erroInline(input, 'Número inválido'); return false; }
      if (min !== null && n < min) { erroInline(input, `Valor mínimo: ${min}`); return false; }
      if (max !== null && n > max) { erroInline(input, `Valor máximo: ${max}`); return false; }
    }
    if (tipo === 'url') {
      try { new URL(val); } catch { erroInline(input, 'URL inválida'); return false; }
    }
    if (tipo === 'horario') {
      if (!/^\d{2}:\d{2}$/.test(val)) { erroInline(input, 'Formato HH:MM'); return false; }
    }

    limparErro(input);
    return true;
  }

  // ── Validação de formulário completo ─────────────────────────
  function validarForm(el) {
    const campos = el.querySelectorAll(
      'input[required], input[data-validate], input[data-required="1"], textarea[required]'
    );
    let ok = true;
    campos.forEach(inp => { if (!validarCampo(inp)) ok = false; });
    // Cross: abertura < fechamento
    const ab = el.querySelector('[data-crossval="abertura"]');
    const fe = el.querySelector('[data-crossval="fechamento"]');
    if (ab && fe && ab.value && fe.value && ab.value >= fe.value) {
      erroInline(fe, 'Fechamento deve ser após abertura');
      ok = false;
    }
    return ok;
  }

  // ── Capitalizar nome ─────────────────────────────────────────
  function capitalizarNome(e) {
    const inp = e.target;
    const pos = inp.selectionStart;
    inp.value = inp.value.replace(/\b\w/g, c => c.toUpperCase());
    inp.setSelectionRange(pos, pos);
  }

  // ── Auto-bind ao carregar ─────────────────────────────────────
  function bind() {
    // Máscaras
    document.querySelectorAll('[data-mask="tel"]').forEach(inp => {
      inp.setAttribute('maxlength', '15');
      inp.setAttribute('inputmode', 'tel');
      inp.addEventListener('input', mascaraTel);
    });
    document.querySelectorAll('[data-mask="moeda"]').forEach(inp => {
      inp.setAttribute('inputmode', 'decimal');
      inp.addEventListener('blur', mascaraMoeda);
    });

    // Capitalização
    document.querySelectorAll('[data-validate="nome"]').forEach(inp => {
      inp.addEventListener('blur', capitalizarNome);
    });

    // Validação on-blur para todos os campos marcados
    document.querySelectorAll('[data-validate], [required]').forEach(inp => {
      inp.addEventListener('blur', () => validarCampo(inp));
      inp.addEventListener('input', () => {
        if (inp.classList.contains('ib-invalido')) validarCampo(inp);
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }

  // API pública
  w.ibForms = { mascaraTel, mascaraMoeda, erroInline, limparErro, validarCampo, validarForm, bind };
})(window);
