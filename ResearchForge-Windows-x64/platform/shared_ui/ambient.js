/* Ambient watercolour on the desk: slow, domain-warped washes rendered at low resolution.
   Time comes from the wall clock, so the wash continues seamlessly across page loads.
   It never blocks the page: no WebGL means the CSS gradient fallback stays. */
(function () {
  'use strict';
  var root = document.documentElement;
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  var darkQuery = window.matchMedia('(prefers-color-scheme: dark)');

  function mode() {
    var m = root.getAttribute('data-rf-motion');
    if (m === 'full' || m === 'calm' || m === 'off') return m;
    return reduced.matches ? 'off' : 'full';
  }

  var VERT = 'attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}';
  var FRAG = [
    '#ifdef GL_FRAGMENT_PRECISION_HIGH', 'precision highp float;', '#else', 'precision mediump float;', '#endif',
    'uniform vec2 uRes;uniform float uTime;uniform vec2 uPointer;uniform vec3 uDesk;uniform vec3 uC1;uniform vec3 uC2;uniform vec3 uC3;',
    'float hash(vec2 p){p=fract(p*vec2(123.34,456.21));p+=dot(p,p+45.32);return fract(p.x*p.y);}',
    'float noise(vec2 p){vec2 i=floor(p),f=fract(p);vec2 u=f*f*(3.-2.*f);',
    'return mix(mix(hash(i),hash(i+vec2(1.,0.)),u.x),mix(hash(i+vec2(0.,1.)),hash(i+vec2(1.,1.)),u.x),u.y);}',
    'float fbm(vec2 p){float v=0.,a=.5;mat2 m=mat2(1.6,1.2,-1.2,1.6);for(int i=0;i<5;i++){v+=a*noise(p);p=m*p;a*=.5;}return v;}',
    'void main(){',
    ' vec2 uv=gl_FragCoord.xy/uRes;vec2 p=uv*vec2(uRes.x/uRes.y,1.)*1.35;',
    ' float t=uTime*.016;',
    ' vec2 toP=uv-uPointer;p+=toP*.18*exp(-dot(toP,toP)*6.);',
    ' vec2 q=vec2(fbm(p+vec2(0.,t)),fbm(p+vec2(5.2,1.3)-t*.7));',
    ' vec2 r=vec2(fbm(p+2.6*q+vec2(1.7,9.2)+t*.45),fbm(p+2.6*q+vec2(8.3,2.8)-t*.38));',
    ' float f=fbm(p+2.2*r);',
    ' float b1=smoothstep(.42,.86,f);float b2=smoothstep(.5,.95,length(q)*.9);float b3=smoothstep(.55,.92,r.y);',
    ' vec3 col=uDesk;col=mix(col,uC1,b1*.85);col=mix(col,uC2,b2*.7);col=mix(col,uC3,b3*.55);',
    ' float rim=smoothstep(.035,0.,abs(f-.6))*.05+smoothstep(.03,0.,abs(length(q)*.9-.62))*.035;',
    ' col=mix(col,col*.9,rim);',
    ' float grain=(hash(gl_FragCoord.xy+fract(uTime))-.5)*.012;',
    ' gl_FragColor=vec4(col+grain,1.);}'
  ].join('\n');

  var canvas, gl, program, uniforms = {}, colors = null, target = null, colorStart = 0, from = null;
  var pointer = [0.5, 0.5], pointerTarget = [0.5, 0.5], raf = 0, last = 0, running = false;

  function parseColor(value) {
    var probe = parseColor.ctx || (parseColor.ctx = document.createElement('canvas').getContext('2d'));
    probe.fillStyle = '#000';
    probe.fillStyle = value.trim() || '#000';
    var c = probe.fillStyle;
    if (c[0] === '#') return [parseInt(c.slice(1, 3), 16) / 255, parseInt(c.slice(3, 5), 16) / 255, parseInt(c.slice(5, 7), 16) / 255];
    var m = c.match(/[\d.]+/g) || [0, 0, 0];
    return [m[0] / 255, m[1] / 255, m[2] / 255];
  }
  function readColors() {
    var style = getComputedStyle(root);
    return ['--rf-desk', '--rf-wash-1', '--rf-wash-2', '--rf-wash-3'].map(function (name) { return parseColor(style.getPropertyValue(name)); });
  }
  function compile(type, source) {
    var shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
    return shader;
  }
  function setup() {
    canvas = document.querySelector('canvas.rf-ambient');
    if (!canvas) {
      canvas = document.createElement('canvas');
      canvas.className = 'rf-ambient';
      canvas.setAttribute('aria-hidden', 'true');
      document.body.insertBefore(canvas, document.body.firstChild);
    }
    gl = canvas.getContext('webgl', { alpha: false, antialias: false, depth: false, stencil: false, powerPreference: 'low-power', preserveDrawingBuffer: false });
    if (!gl) { canvas.remove(); return false; }
    program = gl.createProgram();
    gl.attachShader(program, compile(gl.VERTEX_SHADER, VERT));
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
    gl.useProgram(program);
    var buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    var loc = gl.getAttribLocation(program, 'p');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    ['uRes', 'uTime', 'uPointer', 'uDesk', 'uC1', 'uC2', 'uC3'].forEach(function (name) { uniforms[name] = gl.getUniformLocation(program, name); });
    canvas.addEventListener('webglcontextlost', function (event) { event.preventDefault(); stop(); }, false);
    colors = readColors();
    resize();
    // The first frame is drawn synchronously above, so the wash appears continuous across pages.
    root.classList.add('rf-ambient-on');
    return true;
  }
  function resize() {
    if (!canvas) return;
    // The wash is soft by nature; a small buffer keeps it cheap on any GPU.
    var scale = Math.min(1, 560 / Math.max(window.innerWidth, window.innerHeight));
    var w = Math.max(64, Math.round(window.innerWidth * scale)), h = Math.max(64, Math.round(window.innerHeight * scale));
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    gl.viewport(0, 0, w, h);
    draw(performance.now());
  }
  function mixColor(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]; }
  function draw(now) {
    if (!gl) return;
    if (target) {
      var t = Math.min(1, (now - colorStart) / 900), e = 1 - Math.pow(1 - t, 3);
      colors = from.map(function (c, i) { return mixColor(c, target[i], e); });
      if (t >= 1) target = null;
    }
    pointer[0] += (pointerTarget[0] - pointer[0]) * 0.035;
    pointer[1] += (pointerTarget[1] - pointer[1]) * 0.035;
    gl.uniform2f(uniforms.uRes, canvas.width, canvas.height);
    gl.uniform1f(uniforms.uTime, (Date.now() % 21600000) / 1000);
    gl.uniform2f(uniforms.uPointer, pointer[0], pointer[1]);
    gl.uniform3fv(uniforms.uDesk, colors[0]);
    gl.uniform3fv(uniforms.uC1, colors[1]);
    gl.uniform3fv(uniforms.uC2, colors[2]);
    gl.uniform3fv(uniforms.uC3, colors[3]);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }
  function frame(now) {
    raf = 0;
    if (!running) return;
    // ~30 fps is plenty for drifting pigment and halves GPU work.
    if (now - last >= 33 || target) { last = now; draw(now); }
    raf = requestAnimationFrame(frame);
  }
  function start() {
    if (running || !gl) return;
    running = true;
    raf = requestAnimationFrame(frame);
  }
  function stop() {
    running = false;
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
  }
  function sync() {
    if (!gl) return;
    var m = mode();
    if (m === 'full' && document.visibilityState === 'visible') start();
    else { stop(); draw(performance.now()); }
  }
  function refreshColors() {
    if (!gl) return;
    if (!running) {
      // Calm and off modes switch palette at once: nothing waits on animation frames.
      colors = readColors();
      target = null;
      draw(performance.now());
      return;
    }
    from = colors;
    target = readColors();
    colorStart = performance.now();
  }

  function init() {
    try { if (!setup()) return; }
    catch (error) { if (canvas) canvas.remove(); gl = null; return; }
    window.addEventListener('resize', function () { clearTimeout(resize.timer); resize.timer = setTimeout(resize, 120); });
    window.addEventListener('pointermove', function (event) {
      pointerTarget[0] = event.clientX / window.innerWidth;
      pointerTarget[1] = 1 - event.clientY / window.innerHeight;
    }, { passive: true });
    document.addEventListener('visibilitychange', sync);
    reduced.addEventListener('change', sync);
    darkQuery.addEventListener('change', refreshColors);
    new MutationObserver(function (records) {
      records.forEach(function (record) {
        if (record.attributeName === 'data-theme') refreshColors();
        if (record.attributeName === 'data-rf-motion') sync();
      });
    }).observe(root, { attributes: true, attributeFilter: ['data-theme', 'data-rf-tone', 'data-rf-motion'] });
    sync();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
  window.RFAmbient = { refresh: refreshColors };
})();
