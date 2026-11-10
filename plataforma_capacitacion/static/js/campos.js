/* Habilita o deshabilita un bloque de campos según una casilla o un select.
 *
 * El control lleva data-controla="id-del-bloque". Una casilla marcada apaga el
 * bloque (es el caso de "one time", que bloquea la validez); un select lo
 * enciende cuando su valor coincide con data-valor-activo (certificado firmado).
 *
 * Los campos apagados se envían con disabled, así que el servidor no los recibe
 * y no hay forma de guardar una validez en un curso one time por accidente.
 */
(function () {
  function conmutar(bloque, activo) {
    if (!bloque) return;
    bloque.dataset.apagado = activo ? '0' : '1';
    if (bloque.dataset.ocultar === '1') bloque.hidden = !activo;
    bloque.querySelectorAll('input, select, textarea').forEach(function (campo) {
      campo.disabled = !activo;
    });
  }

  function aplicar(control) {
    var activo = control.type === 'checkbox'
      ? !control.checked
      : control.value === control.dataset.valorActivo;
    conmutar(document.getElementById(control.dataset.controla), activo);
    // Bloque que se enciende justo cuando el otro se apaga (SMTP vs Graph).
    if (control.dataset.controlaAlterno) {
      conmutar(document.getElementById(control.dataset.controlaAlterno), !activo);
    }
  }

  document.querySelectorAll('[data-controla]').forEach(function (control) {
    aplicar(control);
    control.addEventListener('change', function () { aplicar(control); });
  });
})();

// Un campo inválido dentro de un panel plegado y cerrado no se puede enfocar:
// el navegador cancela el envío sin decir nada y el botón de guardar parece
// roto. Al primer campo inválido se abren los paneles que lo contienen.
(function () {
  document.addEventListener('invalid', function (evento) {
    var nodo = evento.target.closest('details:not([open])');
    while (nodo) {
      nodo.open = true;
      nodo = nodo.parentElement && nodo.parentElement.closest('details:not([open])');
    }
  }, true);
})();

// Un <details> abierto se queda abierto aunque uno haga clic en otro lado.
// Para un menú eso estorba: se cierran los demás al abrir uno, y todos al
// hacer clic fuera o con Escape.
(function () {
  function cerrarTodos(menos) {
    document.querySelectorAll('details.menu[open]').forEach(function (m) {
      if (m !== menos) m.open = false;
    });
  }
  document.addEventListener('click', function (evento) {
    var dentro = evento.target.closest('details.menu');
    // Al elegir una opción el menú también se cierra: si no, tapa el
    // formulario que acaba de abrir (renombrar, reemplazar archivo).
    if (dentro && evento.target.closest('.menu-lista button, .menu-lista a')) {
      dentro.open = false;
      dentro = null;
    }
    cerrarTodos(dentro);
  });
  document.addEventListener('keydown', function (evento) {
    if (evento.key === 'Escape') cerrarTodos(null);
  });
})();
