/* Control de avance del material. El servidor valida de nuevo cada dato,
   esto es solo la experiencia en pantalla. */
(function () {
  const C = window.CURSO;
  if (!C) return;

  const barra = document.getElementById('barra');
  const porcentaje = document.getElementById('porcentaje');
  const botonExamen = document.getElementById('ir-examen');
  const candado = document.getElementById('candado');
  let completo = C.completo;

  function pintarAvance(valor) {
    if (barra) barra.style.width = Math.min(100, valor) + '%';
    if (porcentaje) porcentaje.textContent = Math.floor(valor);
  }

  function desbloquear() {
    if (completo || !botonExamen) return;
    completo = true;
    botonExamen.style.pointerEvents = '';
    botonExamen.style.background = '';
    botonExamen.style.borderColor = '';
    botonExamen.removeAttribute('aria-disabled');
    if (candado) candado.hidden = true;
  }

  async function reportar(datos) {
    try {
      const r = await fetch(C.urlAvance, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': C.csrf },
        body: JSON.stringify(datos)
      });
      const cuerpo = await r.json();
      if (typeof cuerpo.avance === 'number') pintarAvance(cuerpo.avance);
      if (cuerpo.habilitado) desbloquear();
      return cuerpo;
    } catch (e) {
      return null;
    }
  }

  /* ------------------------------------------------------------------ video */
  const video = document.getElementById('material');
  if (video) {
    let maxVisto = C.maxPosicion || 0;
    let ultimoEnvio = 0;

    video.addEventListener('loadedmetadata', () => {
      if (!completo && maxVisto > 2 && maxVisto < video.duration - 2) {
        video.currentTime = maxVisto;
      }
    });

    video.addEventListener('timeupdate', () => {
      const t = video.currentTime;
      if (!completo && t > maxVisto + 1.5) {
        video.currentTime = maxVisto;   // no se permite adelantar
        return;
      }
      if (t > maxVisto) maxVisto = t;
      if (t - ultimoEnvio >= 10) {
        ultimoEnvio = t;
        reportar({ posicion: maxVisto });
      }
      if (!completo && video.duration) {
        pintarAvance(Math.min(100, (maxVisto / video.duration) * 100));
      }
    });

    ['pause', 'ended'].forEach(evento =>
      video.addEventListener(evento, () => reportar({ posicion: maxVisto })));

    window.addEventListener('beforeunload', () => {
      navigator.sendBeacon && navigator.sendBeacon(
        C.urlAvance,
        new Blob([JSON.stringify({ posicion: maxVisto })], { type: 'application/json' })
      );
    });
  }

  /* -------------------------------------------------------------- documento */
  const imagen = document.getElementById('pagina');
  if (imagen) {
    const anterior = document.getElementById('anterior');
    const siguiente = document.getElementById('siguiente');
    const indicador = document.getElementById('indicador');
    const mensaje = document.getElementById('mensaje-visor');
    const base = C.urlPagina.replace(/0\.png$/, '');

    let actual = Math.min(Math.max(1, C.maxPosicion || 1), C.paginas);
    let maxAlcanzada = Math.max(1, C.maxPosicion || 1);
    let cuentaRegresiva = null;

    function mostrar(n) {
      actual = n;
      imagen.src = base + n + '.png';
      indicador.textContent = 'Página ' + n + ' de ' + C.paginas;
      anterior.disabled = n <= 1;
      refrescarSiguiente();
    }

    function refrescarSiguiente() {
      clearInterval(cuentaRegresiva);
      if (actual >= C.paginas) {
        siguiente.disabled = true;
        siguiente.textContent = completo ? 'Content complete' : 'Last page';
        if (completo && mensaje) mensaje.textContent = 'Content complete — you can now take the exam.';
        return;
      }
      siguiente.textContent = 'Next →';
      if (actual < maxAlcanzada) {          // ya la leyó antes
        siguiente.disabled = false;
        if (mensaje) mensaje.textContent = 'You can move freely through the pages you have already read.';
        return;
      }
      let restante = C.segundosPagina;
      siguiente.disabled = true;
      const tic = () => {
        if (restante <= 0) {
          clearInterval(cuentaRegresiva);
          siguiente.disabled = false;
          if (mensaje) mensaje.textContent = 'You can move to the next page.';
          return;
        }
        if (mensaje) mensaje.textContent = 'You can continue in ' + restante + 's.';
        restante -= 1;
      };
      tic();
      cuentaRegresiva = setInterval(tic, 1000);
    }

    anterior.addEventListener('click', () => { if (actual > 1) mostrar(actual - 1); });

    siguiente.addEventListener('click', async () => {
      if (actual >= C.paginas) return;
      const proxima = actual + 1;
      const respuesta = await reportar({ pagina: proxima });
      if (respuesta && respuesta.mensaje && respuesta.max_posicion < proxima) {
        if (mensaje) mensaje.textContent = respuesta.mensaje;
        refrescarSiguiente();
        return;
      }
      maxAlcanzada = Math.max(maxAlcanzada, proxima);
      mostrar(proxima);
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowLeft' && !anterior.disabled) anterior.click();
      if (e.key === 'ArrowRight' && !siguiente.disabled) siguiente.click();
    });

    mostrar(actual);
    reportar({ pagina: actual });
  }
})();
