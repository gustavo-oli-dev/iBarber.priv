const overlay   = document.getElementById('modalOverlay');
const fecharBtn = document.getElementById('modalClose');
function abrirModal() {
  if (!overlay) return;
  overlay.classList.add('open');
  document.body.style.overflow = 'hidden';
  setTimeout(() => overlay.querySelector('.field-input')?.focus(), 50);
}
function fecharModal() {
  if (!overlay) return;
  overlay.classList.remove('open');
  document.body.style.overflow = '';
}
if (fecharBtn) fecharBtn.addEventListener('click', fecharModal);
if (overlay) {
  overlay.addEventListener('click', e => {
    if (e.target === overlay) fecharModal();
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && overlay.classList.contains('open')) fecharModal();
  });
}
const dotsBtn    = document.getElementById('navDotsBtn');
const dropdown   = document.getElementById('navDropdown');
if (dotsBtn && dropdown) {
  dotsBtn.addEventListener('click', e => {
    e.stopPropagation();
    dropdown.classList.toggle('aberto');
  });
  document.addEventListener('click', () => dropdown.classList.remove('aberto'));
}
const toggle = document.getElementById('navToggle');
const drawer = document.getElementById('navDrawer');
function fecharDrawer() {
  if (!drawer) return;
  drawer.classList.remove('open');
  if (toggle) toggle.setAttribute('aria-expanded', 'false');
  document.body.style.overflow = '';
}
if (toggle && drawer) {
  toggle.addEventListener('click', () => {
    const open = drawer.classList.toggle('open');
    toggle.setAttribute('aria-expanded', open);
    document.body.style.overflow = open ? 'hidden' : '';
  });
  drawer.querySelectorAll('a').forEach(link => {
    link.addEventListener('click', fecharDrawer);
  });
  const drawerFechar = document.getElementById('drawerFechar');
  if (drawerFechar) drawerFechar.addEventListener('click', fecharDrawer);
}
const registerForm = document.querySelector('form[action*="register"]');
if (registerForm) {
  const DOMINIOS = ['gmail.com','hotmail.com','outlook.com','yahoo.com',
                    'icloud.com','live.com','msn.com','bol.com.br',
                    'uol.com.br','terra.com.br','globo.com','protonmail.com'];
  const emailInput = registerForm.querySelector('#email');
  const hint = document.createElement('span');
  hint.className = 'field-hint-error';
  hint.style.cssText = 'color:var(--error);font-size:0.78rem;display:none;';
  emailInput?.parentElement?.appendChild(hint);
  emailInput?.addEventListener('blur', () => {
    const dominio = emailInput.value.split('@')[1] || '';
    if (emailInput.value && !DOMINIOS.includes(dominio)) {
      hint.textContent = 'Domínio inválido. Use @gmail.com, @hotmail.com, etc.';
      hint.style.display = 'block';
      emailInput.style.borderColor = 'var(--error)';
    } else {
      hint.style.display = 'none';
      emailInput.style.borderColor = '';
    }
  });
  registerForm.addEventListener('submit', e => {
    const dominio = emailInput?.value.split('@')[1] || '';
    if (emailInput?.value && !DOMINIOS.includes(dominio)) {
      e.preventDefault();
      hint.style.display = 'block';
      emailInput.focus();
    }
  });
}
const EYE_OPEN = `<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>`;
const EYE_OFF  = `<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>`;
document.querySelectorAll('.toggle-pw:not(.toggle-pw--display)').forEach(btn => {
  btn.addEventListener('click', () => {
    const input = document.getElementById(btn.dataset.target);
    if (!input) return;
    const showing = input.type === 'password';
    input.type = showing ? 'text' : 'password';
    btn.querySelector('svg').innerHTML = showing ? EYE_OFF : EYE_OPEN;
    btn.setAttribute('aria-label', showing ? 'Ocultar senha' : 'Mostrar senha');
  });
});
document.querySelectorAll('.toggle-pw--display').forEach(btn => {
  btn.addEventListener('click', () => {
    const el = document.getElementById(btn.dataset.target);
    if (!el) return;
    const oculto = el.dataset.oculto === 'true';
    const senha = el.dataset.senha;
    el.textContent = oculto ? senha : '••••••••';
    el.style.color = oculto ? 'var(--text)' : 'var(--text-muted)';
    el.dataset.oculto = oculto ? 'false' : 'true';
    btn.querySelector('svg').innerHTML = oculto ? EYE_OFF : EYE_OPEN;
    btn.setAttribute('aria-label', oculto ? 'Ocultar senha' : 'Mostrar senha');
  });
});
const contactInput = document.getElementById('contact');
if (contactInput) {
  contactInput.addEventListener('input', e => {
    let v = e.target.value.replace(/\D/g, '').slice(0, 11);
    if (v.length > 6) {
      v = `(${v.slice(0,2)}) ${v.slice(2,7)}-${v.slice(7)}`;
    } else if (v.length > 2) {
      v = `(${v.slice(0,2)}) ${v.slice(2)}`;
    } else if (v.length > 0) {
      v = `(${v}`;
    }
    e.target.value = v;
  });
}
document.querySelectorAll('.flash').forEach(el => {
  setTimeout(() => el.remove(), 5000);
});
document.querySelectorAll('input[type="email"]').forEach(el => {
  el.addEventListener('input', function() { this.value = this.value.toLowerCase(); });
});
