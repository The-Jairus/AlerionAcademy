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
