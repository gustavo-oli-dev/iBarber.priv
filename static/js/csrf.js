/* Proteção CSRF do lado do cliente.
 *
 * Lê o token de <meta name="csrf-token"> e o injeta em toda requisição
 * same-origin que altera estado. Requisições autenticadas por
 * "Authorization: Bearer ..." não usam cookie e portanto não são forjáveis
 * por CSRF — essas ficam de fora.
 *
 * Precisa ser carregado ANTES de qualquer script que faça fetch().
 */
(function () {
  var meta = document.querySelector('meta[name="csrf-token"]');
  var TOKEN = meta ? meta.getAttribute('content') : '';
  if (!TOKEN) return;

  var SEGUROS = { GET: 1, HEAD: 1, OPTIONS: 1, TRACE: 1 };

  // 1) fetch()
  var _fetch = window.fetch;
  window.fetch = function (entrada, init) {
    init = init || {};
    var metodo = (init.method || (entrada && entrada.method) || 'GET').toUpperCase();
    var url = typeof entrada === 'string' ? entrada : (entrada && entrada.url) || '';
    var externa = /^https?:\/\//i.test(url) && url.indexOf(window.location.origin) !== 0;
    if (!SEGUROS[metodo] && !externa) {
      var h = new Headers(init.headers || (entrada && entrada.headers) || {});
      if (!h.has('Authorization')) h.set('X-CSRF-Token', TOKEN);
      init.headers = h;
    }
    return _fetch.call(this, entrada, init);
  };

  // 2) XMLHttpRequest (usado por uploads antigos)
  var _open = XMLHttpRequest.prototype.open;
  var _send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (metodo, url) {
    this._csrfMetodo = String(metodo || 'GET').toUpperCase();
    this._csrfUrl = String(url || '');
    return _open.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function () {
    var externa = /^https?:\/\//i.test(this._csrfUrl) &&
                  this._csrfUrl.indexOf(window.location.origin) !== 0;
    if (!SEGUROS[this._csrfMetodo] && !externa) {
      try { this.setRequestHeader('X-CSRF-Token', TOKEN); } catch (e) { /* já enviado */ }
    }
    return _send.apply(this, arguments);
  };

  // 3) <form method="post"> sem campo _csrf — insere na submissão
  document.addEventListener('submit', function (ev) {
    var f = ev.target;
    if (!f || f.tagName !== 'FORM') return;
    if (SEGUROS[(f.method || 'GET').toUpperCase()]) return;
    if (f.querySelector('input[name="_csrf"]')) return;
    var i = document.createElement('input');
    i.type = 'hidden';
    i.name = '_csrf';
    i.value = TOKEN;
    f.appendChild(i);
  }, true);
})();
