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
  function aplicar(control) {
    var bloque = document.getElementById(control.dataset.controla);
    if (!bloque) return;
    var activo = control.type === 'checkbox'
      ? !control.checked
      : control.value === control.dataset.valorActivo;
    bloque.dataset.apagado = activo ? '0' : '1';
    bloque.querySelectorAll('input, select, textarea').forEach(function (campo) {
      campo.disabled = !activo;
    });
  }

  document.querySelectorAll('[data-controla]').forEach(function (control) {
    aplicar(control);
    control.addEventListener('change', function () { aplicar(control); });
  });
})();
