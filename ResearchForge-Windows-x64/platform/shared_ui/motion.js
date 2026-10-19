/* Motion engine shared by every workspace page.
   - Illustrated hero scenes built from mask layers, with spring-smoothed pointer and scroll parallax.
   - Headings that ink in character by character; reveal-on-scroll; keyed entrances for lists that
     polling re-renders (only genuinely new items animate).
   - Micro-interactions: pointer light on cards, magnetic primary actions, sliding
     segment indicators, number tickers and a seal stamp for confirmations.
   Everything degrades to static content when motion is off or the system asks for less motion. */
(function () {
  'use strict';
  var root = document.documentElement;
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  var finePointer = window.matchMedia('(hover: hover) and (pointer: fine)');

  function mode() {
    var m = root.getAttribute('data-rf-motion');
    if (m === 'full' || m === 'calm' || m === 'off') return m;
    return reduced.matches ? 'off' : 'full';
  }
  function active() { return mode() !== 'off'; }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  /* ---------------------------------------------------------------- Springs */
  // Critically-damped-ish spring stepping; returns true while still moving.
  function Spring(value, stiffness, damping) {
    this.value = value; this.target = value; this.velocity = 0;
    this.k = stiffness || 120; this.c = damping || 18;
  }
  Spring.prototype.step = function (dt) {
    var force = -this.k * (this.value - this.target) - this.c * this.velocity;
    this.velocity += force * dt;
    this.value += this.velocity * dt;
    if (Math.abs(this.velocity) < 0.01 && Math.abs(this.value - this.target) < 0.01) { this.value = this.target; this.velocity = 0; return false; }
    return true;
  };

  /* ---------------------------------------------------------------- Split headings */
  function split(el) {
    if (!el || el.dataset.rfSplitDone) return;
    var text = el.textContent;
    el.dataset.rfSplitDone = '1';
    if (!active() || root.classList.contains('rf-entered')) return;  // the heading inks in on a first visit only
    el.setAttribute('aria-label', text);
    var frag = document.createDocumentFragment(), i = 0;
    Array.from(text).forEach(function (ch) {
      var span = document.createElement('span');
      span.className = 'rf-char';
      span.setAttribute('aria-hidden', 'true');
      span.style.setProperty('--ci', String(i++));
      span.textContent = ch;
      frag.appendChild(span);
    });
    el.replaceChildren(frag);
    requestAnimationFrame(function () { el.classList.add('rf-split-ready'); });
  }

  /* ---------------------------------------------------------------- Reveal on scroll */
  var revealObserver = 'IntersectionObserver' in window ? new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      entry.target.classList.add('is-in');
      revealObserver.unobserve(entry.target);
    });
  }, { rootMargin: '0px 0px -8% 0px', threshold: 0.06 }) : null;
  function reveal(el, index) {
    if (el.dataset.rfRevealed) return;
    el.dataset.rfRevealed = '1';
    if (index != null) el.style.setProperty('--i', String(index));
    if (!active() || !revealObserver) { el.classList.add('is-in'); return; }
    // After an in-app page switch, what is already on screen stays put; only sections below still reveal on scroll.
    if (root.classList.contains('rf-entered') && el.getBoundingClientRect().top < window.innerHeight) { el.classList.add('is-in'); return; }
    el.classList.add('rf-reveal');
    revealObserver.observe(el);
  }
  function revealGroup(container) {
    Array.from(container.children).forEach(function (child, i) { reveal(child, i); });
  }

  /* ---------------------------------------------------------------- Keyed list entrances */
  // Lists re-rendered by polling animate only items whose key has not been seen before.
  var seen = new WeakMap();
  function keyOf(node) {
    var d = node.dataset || {};
    return d.rfKey || d.taskId || d.job || d.id || d.paper || d.uid || d.k || null;
  }
  function watchList(container) {
    if (!container || seen.has(container)) return;
    var keys = new Set(), settled = false, policy = container.getAttribute('data-rf-animate') || 'new';
    seen.set(container, keys);
    var mark = function (nodes, initial) {
      var index = 0;
      nodes.forEach(function (node) {
        if (node.nodeType !== 1 || node.matches('.rf-skeleton-list, script, template')) return;
        var key = keyOf(node);
        var fresh = key ? !keys.has(key) : (!settled || policy === 'always');
        if (key) keys.add(key);
        if (!fresh || !active()) return;
        if (initial && root.classList.contains('rf-arrived') && policy !== 'always') return;
        node.style.setProperty('--i', String(index++));
        node.classList.remove('rf-unfold');
        void node.offsetWidth;
        node.classList.add('rf-unfold');
        node.addEventListener('animationend', function done(event) {
          if (event.target !== node) return;
          node.classList.remove('rf-unfold');
          node.removeEventListener('animationend', done);
        });
      });
    };
    mark(Array.from(container.children), true);
    if (container.children.length && !container.querySelector('.rf-skeleton-list')) setTimeout(function () { settled = true; }, 1200);
    new MutationObserver(function (records) {
      var added = [];
      records.forEach(function (record) { record.addedNodes.forEach(function (n) { if (n.parentNode === container) added.push(n); }); });
      if (!added.length) return;
      var firstFill = !settled;
      mark(added, false);
      if (firstFill) setTimeout(function () { settled = true; }, 1200);
    }).observe(container, { childList: true });
  }

  /* ---------------------------------------------------------------- Pointer light, magnetism, ripples */
  var GLOW = '.rf-card, .idea-card, .top-item, .paper-row, .reference-card, .ready-item, .memory-paper, .prompt-card, .library-result, .guide-part, .memory-stat, .rf-stat, .knowledge-section, .bridge-candidate, .upload-card, .research-card';
  var glowTarget = null;
  document.addEventListener('pointermove', function (event) {
    if (!finePointer.matches || !active()) return;
    var el = event.target.closest && event.target.closest(GLOW);
    if (el !== glowTarget) glowTarget = el;
    if (!el) return;
    if (!el.hasAttribute('data-rf-glow')) el.setAttribute('data-rf-glow', '');
    var r = el.getBoundingClientRect();
    el.style.setProperty('--mx', (event.clientX - r.left) + 'px');
    el.style.setProperty('--my', (event.clientY - r.top) + 'px');
  }, { passive: true });

  var magnets = new Map();
  function magnet(el) {
    if (magnets.has(el)) return;
    var sx = new Spring(0, 170, 16), sy = new Spring(0, 170, 16), raf = 0, last = 0;
    var tick = function (now) {
      var dt = Math.min(0.033, (now - last) / 1000 || 0.016); last = now;
      var moving = sx.step(dt) | sy.step(dt);
      el.style.translate = sx.value.toFixed(2) + 'px ' + sy.value.toFixed(2) + 'px';
      raf = moving ? requestAnimationFrame(tick) : 0;
    };
    var kick = function () { if (!raf) { last = performance.now(); raf = requestAnimationFrame(tick); } };
    el.addEventListener('pointermove', function (event) {
      if (!finePointer.matches || mode() !== 'full' || el.disabled) return;
      var r = el.getBoundingClientRect();
      sx.target = clamp((event.clientX - (r.left + r.width / 2)) * 0.22, -8, 8);
      sy.target = clamp((event.clientY - (r.top + r.height / 2)) * 0.3, -5, 5);
      kick();
    });
    el.addEventListener('pointerleave', function () { sx.target = 0; sy.target = 0; kick(); });
    magnets.set(el, true);
  }

  // No press ripple: a light flash on every click read as flicker. Buttons give feedback by a slight press (CSS :active).

  /* ---------------------------------------------------------------- Segmented indicator */
  var SELECTED = '[aria-pressed="true"], [aria-selected="true"], [aria-current="true"], [aria-current="page"], .active, .selected';
  function segmented(container) {
    if (container.querySelector(':scope > .rf-seg-ink')) return;
    var ink = document.createElement('span');
    ink.className = 'rf-seg-ink';
    ink.setAttribute('aria-hidden', 'true');
    container.prepend(ink);
    container.classList.add('rf-seg-ready');
    var first = true;
    var place = function () {
      var current = container.querySelector(':scope > :is(button, a):is(' + SELECTED + ')');
      if (!current || !current.offsetWidth) { ink.style.opacity = '0'; return; }
      var transition = ink.style.transition;
      if (first) ink.style.transition = 'none';
      ink.style.opacity = '1';
      ink.style.width = current.offsetWidth + 'px';
      ink.style.transform = 'translateX(' + current.offsetLeft + 'px)';
      if (first) { void ink.offsetWidth; ink.style.transition = transition; first = false; }
    };
    new MutationObserver(place).observe(container, { subtree: true, attributes: true, attributeFilter: ['aria-pressed', 'aria-selected', 'aria-current', 'class'] });
    if ('ResizeObserver' in window) new ResizeObserver(place).observe(container);
    place();
  }

  /* ---------------------------------------------------------------- Number ticker */
  function countTo(el, value, options) {
    options = options || {};
    var format = options.format || function (n) { return Math.round(n).toLocaleString('zh-CN'); };
    var first = el.dataset.rfValue === undefined, start = Number(el.dataset.rfValue || 0);
    el.dataset.rfValue = String(value);
    // After an in-app page switch the figures are simply shown; later changes still tick.
    if (!active() || start === value || (first && root.classList.contains('rf-entered'))) { el.textContent = format(value); return; }
    var t0 = performance.now(), duration = options.duration || 1100;
    var step = function (now) {
      var t = Math.min(1, (now - t0) / duration), e = 1 - Math.pow(1 - t, 4);
      el.textContent = format(start + (value - start) * e);
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  /* ---------------------------------------------------------------- Seal stamp */
  function stamp(text, x, y) {
    if (!active()) return;
    var el = document.createElement('span');
    el.className = 'rf-stamp';
    el.textContent = text || '已存';
    el.style.left = x + 'px';
    el.style.top = y + 'px';
    document.body.appendChild(el);
    el.addEventListener('animationend', function () { el.remove(); });
  }
  function stampAt(element, text) {
    if (!element) return;
    var r = element.getBoundingClientRect();
    stamp(text, r.left + r.width / 2, r.top + r.height / 2);
  }

  /* ---------------------------------------------------------------- Scenes */
  var ART = '/ui/art/';
  // [layer, colour token, depth, behaviour, opacity]
  var SCENES = {
    dawn: { sun: [76, 30, 104], birds: true, layers: [['far', 'far', 0.1], ['mist', 'mist', 0.18, 'mist', 0.95], ['mid', 'mid', 0.28], ['near', 'near', 0.5]] },
    lake: { sun: [33, 30, 82], water: 62, ripples: true, moon: true,
      layers: [['far', 'far', 0.08], ['mist', 'mist', 0.14, 'mist', 0.9], ['near', 'mid', 0.2], ['reflection', 'water', 0.2, null, 0.85], ['waves', 'water', 0.26, null, 0.8], ['reeds', 'near', 0.62, 'sway']] },
    isles: { sun: [84, 24, 68], data: 'isles.json',
      layers: [['reflection', 'water', 0.16, null, 0.8], ['waves', 'water', 0.18, null, 0.75], ['islands', 'mid', 0.22], ['mist', 'mist', 0.34, 'mist', 0.9]] },
    plum: { data: 'plum.json', petals: true, contain: true, layers: [['branch', 'ink', 0.22, null, 0.92]] }
  };
  var scenes = [];
  function token(name) { return 'var(--rf-art-' + name + ')'; }
  function svgEl(name, attrs) {
    var el = document.createElementNS('http://www.w3.org/2000/svg', name);
    for (var k in attrs) el.setAttribute(k, attrs[k]);
    return el;
  }
  function layerEl(scene, spec, k) {
    var el = document.createElement('i');
    el.className = 'rf-layer' + (spec[3] === 'mist' ? ' is-mist' : '');
    el.style.setProperty('--m', 'url("' + ART + scene + '-' + spec[0] + '.webp")');
    el.style.setProperty('--c', token(spec[1]));
    el.style.setProperty('--d', String(spec[2]));
    el.style.setProperty('--k', String(k));
    if (spec[4] != null) el.style.setProperty('--o', String(spec[4]));
    return el;
  }
  function buildScene(host) {
    var name = host.getAttribute('data-scene'), spec = SCENES[name];
    if (!spec || host.dataset.rfBuilt) return;
    host.dataset.rfBuilt = '1';
    host.setAttribute('aria-hidden', 'true');
    host.classList.add('rf-scene', 'rf-scene-' + name);
    var sky = document.createElement('i'); sky.className = 'rf-scene-sky'; host.appendChild(sky);
    if (spec.sun) {
      var sun = document.createElement('i'); sun.className = 'rf-scene-sun';
      host.style.setProperty('--sun-x', spec.sun[0] + '%');
      host.style.setProperty('--sun-y', spec.sun[1] + '%');
      host.style.setProperty('--sun-size', spec.sun[2] + 'px');
      host.appendChild(sun);
    }
    if (spec.water) host.style.setProperty('--water', spec.water + '%');
    if (spec.birds) {
      var birds = svgEl('svg', { class: 'rf-birds', viewBox: '0 0 120 50' });
      ['M8 22 q6 -7 12 0 q6 -7 12 0', 'M44 12 q5 -6 10 0 q5 -6 10 0', 'M70 30 q4 -5 8 0 q4 -5 8 0'].forEach(function (d) { birds.appendChild(svgEl('path', { d: d })); });
      host.appendChild(birds);
    }
    spec.layers.forEach(function (layer, k) {
      if (layer[3] === 'sway') {
        // Two halves sway around their own roots, so reeds bend instead of the frame tilting.
        [['inset(0 50% 0 0)', '18% 100%'], ['inset(0 0 0 50%)', '86% 100%']].forEach(function (half, j) {
          var el = layerEl(name, layer, k);
          el.classList.add('is-sway');
          el.style.clipPath = half[0];
          el.style.transformOrigin = half[1];
          el.style.animationDelay = (k * 140 + 120) + 'ms, ' + (-j * 2.7) + 's';
          host.appendChild(el);
        });
        return;
      }
      var el = layerEl(name, layer, k);
      if (spec.contain) {
        el.style.setProperty('--inset', '-4% -3% -6% 34%');
        el.style.webkitMaskSize = el.style.maskSize = 'contain';
        el.style.webkitMaskPosition = el.style.maskPosition = 'right bottom';
      }
      host.appendChild(el);
    });
    if (spec.moon) { var glint = document.createElement('i'); glint.className = 'rf-moon-reflection'; host.appendChild(glint); }
    if (spec.ripples) {
      var ripples = document.createElement('div'); ripples.className = 'rf-ripples';
      for (var i = 0; i < 7; i++) {
        var line = document.createElement('i');
        line.style.left = (18 + Math.random() * 56) + '%';
        line.style.top = (6 + Math.random() * 60) + '%';
        line.style.width = (40 + Math.random() * 90) + 'px';
        line.style.animationDelay = (-Math.random() * 7) + 's';
        ripples.appendChild(line);
      }
      host.appendChild(ripples);
    }
    if (spec.petals) {
      for (var p = 0; p < 6; p++) {
        var petal = document.createElement('i'); petal.className = 'rf-petal-fall';
        petal.style.left = (52 + Math.random() * 44) + '%';
        petal.style.animationDelay = (p * 2.3 + Math.random() * 2) + 's';
        petal.style.animationDuration = (12 + Math.random() * 6) + 's';
        host.appendChild(petal);
      }
    }
    if (spec.data) {
      fetch(ART + spec.data, { cache: 'force-cache' }).then(function (r) { return r.ok ? r.json() : null; }).then(function (data) {
        if (!data) return;
        if (name === 'isles') drawRoutes(host, data);
        if (name === 'plum') drawBlossoms(host, data);
      }).catch(function () {});
    }
    var state = { host: host, vars: host.closest('.rf-hero') || host, visible: true, px: new Spring(0, 60, 14), py: new Spring(0, 60, 14) };
    scenes.push(state);
    if (sceneObserver) sceneObserver.observe(host);
    kickParallax();
  }
  function drawRoutes(host, data) {
    var svg = svgEl('svg', { class: 'rf-scene-svg', viewBox: '0 0 ' + data.width + ' ' + data.height, preserveAspectRatio: 'xMidYMax slice' });
    svg.style.setProperty('--d', '0.22');
    var defs = svgEl('defs', {}); svg.appendChild(defs);
    var pt = function (i) { return [data.isles[i].x * data.width, data.isles[i].y * data.height]; };
    var longest = null;
    data.routes.forEach(function (route, k) {
      var a = pt(route[0]), b = pt(route[1]);
      var lift = Math.min(140, Math.abs(a[0] - b[0]) * 0.25 + 40);
      var d = 'M' + a[0].toFixed(1) + ' ' + a[1].toFixed(1) + ' Q' + ((a[0] + b[0]) / 2).toFixed(1) + ' ' + (Math.min(a[1], b[1]) - lift).toFixed(1) + ' ' + b[0].toFixed(1) + ' ' + b[1].toFixed(1);
      var mask = svgEl('mask', { id: 'rf-route-mask-' + k, maskUnits: 'userSpaceOnUse' });
      var reveal = svgEl('path', { d: d, class: 'rf-route-draw', fill: 'none', stroke: '#fff', 'stroke-width': '12' });
      reveal.style.setProperty('--k', String(k));
      mask.appendChild(reveal); defs.appendChild(mask);
      var path = svgEl('path', { d: d, class: 'rf-route', mask: 'url(#rf-route-mask-' + k + ')' });
      if (k === 0) path.id = 'rf-route-0';
      svg.appendChild(path);
      if (!longest) longest = d;
    });
    data.isles.forEach(function (isle, k) {
      var c = svgEl('circle', { cx: (isle.x * data.width).toFixed(1), cy: (isle.y * data.height).toFixed(1), r: '11', class: 'rf-isle-dot' });
      c.style.setProperty('--k', String(k));
      svg.appendChild(c);
    });
    // A small boat travels the first crossing and back.
    var chain = data.routes.slice(0, 5).map(function (route) { return [pt(route[0]), pt(route[1])]; });
    var dChain = 'M' + chain[0][0].join(' ') + chain.map(function (seg) { return ' L' + seg[1][0].toFixed(1) + ' ' + (seg[1][1] + 26).toFixed(1); }).join('');
    var track = svgEl('path', { id: 'rf-boat-track', d: dChain, fill: 'none', stroke: 'none' });
    svg.appendChild(track);
    var boat = svgEl('g', { class: 'rf-boat', transform: 'scale(1.8)' });
    boat.appendChild(svgEl('path', { d: 'M-16 0 L16 0 L11 6 L-11 6 Z' }));
    boat.appendChild(svgEl('path', { d: 'M-1 -1 L-1 -22 L12 -3 Z', opacity: '.75' }));
    var motion = svgEl('animateMotion', { dur: '90s', repeatCount: 'indefinite', keyPoints: '0;1;0', keyTimes: '0;0.5;1', calcMode: 'linear' });
    motion.appendChild(svgEl('mpath', { href: '#rf-boat-track' }));
    boat.appendChild(motion);
    svg.appendChild(boat);
    host.appendChild(svg);
    svg.querySelectorAll('.rf-route-draw').forEach(function (p) { p.style.setProperty('--len', String(Math.ceil(p.getTotalLength()))); });
    if (!active()) svg.querySelectorAll('.rf-route-draw').forEach(function (p) { p.style.strokeDasharray = 'none'; });
  }
  function drawBlossoms(host, data) {
    var svg = svgEl('svg', { class: 'rf-scene-svg rf-blossoms', viewBox: '0 0 ' + data.width + ' ' + data.height, preserveAspectRatio: 'xMaxYMax meet' });
    svg.style.setProperty('--d', '0.24');
    data.blossoms.forEach(function (f, k) {
      var size = f.s * data.width;
      var g = svgEl('g', { class: 'rf-blossom' + (f.state === 'bud' ? ' bud' : ''), transform: 'translate(' + (f.x * data.width).toFixed(1) + ' ' + (f.y * data.height).toFixed(1) + ') rotate(' + f.r + ')' });
      g.style.setProperty('--k', String(k));
      if (f.state === 'bud') {
        g.appendChild(svgEl('circle', { class: 'petal', r: (size * 0.26).toFixed(1) }));
      } else {
        for (var i = 0; i < 5; i++) {
          var a = i * Math.PI * 2 / 5;
          g.appendChild(svgEl('circle', { class: 'petal', cx: (Math.cos(a) * size * 0.32).toFixed(1), cy: (Math.sin(a) * size * 0.32).toFixed(1), r: (size * 0.27).toFixed(1) }));
        }
        g.appendChild(svgEl('circle', { class: 'core', r: (size * 0.12).toFixed(1) }));
      }
      svg.appendChild(g);
    });
    host.appendChild(svg);
  }

  var sceneObserver = 'IntersectionObserver' in window ? new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      var s = scenes.find(function (x) { return x.host === entry.target; });
      if (s) s.visible = entry.isIntersecting;
    });
    kickParallax();
  }) : null;

  var parallaxRaf = 0, parallaxLast = 0, pointer = { x: 0, y: 0 };
  window.addEventListener('pointermove', function (event) {
    pointer.x = event.clientX / window.innerWidth * 2 - 1;
    pointer.y = event.clientY / window.innerHeight * 2 - 1;
    kickParallax();
  }, { passive: true });
  function kickParallax() {
    if (!parallaxRaf && scenes.length) { parallaxLast = performance.now(); parallaxRaf = requestAnimationFrame(parallax); }
  }
  function parallax(now) {
    parallaxRaf = 0;
    var dt = Math.min(0.033, (now - parallaxLast) / 1000); parallaxLast = now;
    var m = mode(), amp = m === 'full' ? 1 : m === 'calm' ? 0.4 : 0, moving = false;
    var sy = m === 'off' ? 0 : window.scrollY;
    scenes.forEach(function (s) {
      if (!s.visible) return;
      s.px.target = finePointer.matches ? -pointer.x * 34 * amp : 0;
      s.py.target = finePointer.matches ? -pointer.y * 14 * amp : 0;
      moving = s.px.step(dt) || moving;
      moving = s.py.step(dt) || moving;
      s.vars.style.setProperty('--px', s.px.value.toFixed(2));
      s.vars.style.setProperty('--py', s.py.value.toFixed(2));
      s.vars.style.setProperty('--sy', String(Math.min(sy, 900)));
    });
    if (moving) parallaxRaf = requestAnimationFrame(parallax);
  }

  /* ---------------------------------------------------------------- Scroll state */
  var scrollRaf = 0, progressBar = null;
  function onScroll() {
    if (scrollRaf) return;
    scrollRaf = requestAnimationFrame(function () {
      scrollRaf = 0;
      var y = window.scrollY;
      if (y > 6) root.setAttribute('data-rf-scrolled', ''); else root.removeAttribute('data-rf-scrolled');
      if (progressBar) {
        var max = document.documentElement.scrollHeight - window.innerHeight;
        progressBar.style.setProperty('--p', max > 0 ? clamp(y / max, 0, 1).toFixed(4) : '0');
      }
      kickParallax();
    });
  }
  function progress(enabled) {
    if (enabled && !progressBar) {
      progressBar = document.createElement('div');
      progressBar.className = 'rf-scroll-progress';
      progressBar.setAttribute('aria-hidden', 'true');
      document.body.appendChild(progressBar);
      onScroll();
    } else if (!enabled && progressBar) { progressBar.remove(); progressBar = null; }
  }
  window.addEventListener('scroll', onScroll, { passive: true });

  /* ---------------------------------------------------------------- Wiring */
  function enhance(scope) {
    scope = scope || document;
    scope.querySelectorAll('[data-scene]').forEach(buildScene);
    scope.querySelectorAll('[data-rf-split]').forEach(split);
    scope.querySelectorAll('[data-rf-reveal]').forEach(function (el) { reveal(el, Number(el.dataset.rfReveal) || 0); });
    scope.querySelectorAll('[data-rf-stagger]').forEach(function (el) { if (!el.dataset.rfStaggered) { el.dataset.rfStaggered = '1'; revealGroup(el); } });
    scope.querySelectorAll('[data-rf-animate]').forEach(watchList);
    scope.querySelectorAll('[data-rf-segmented]').forEach(segmented);
    scope.querySelectorAll('[data-rf-magnetic]').forEach(magnet);
  }
  // Arriving from another workspace page (boot.js): what the page first shows is already in place, so the
  // one-off entrances its first render would play (cards unfolding, folders rising, art inking in, a segment
  // indicator sliding to its tab) are finished before they are painted. Ambient loops, the page cross-fade
  // and anything that changes after the first moments animate as usual.
  function settleArrival() {
    if (!root.classList.contains('rf-entered') || !document.getAnimations) return;
    var until = performance.now() + 1200;
    (function settle(now) {
      document.getAnimations().forEach(function (animation) {
        var effect = animation.effect;
        if (!effect || animation.playState === 'finished' || effect.getTiming().iterations === Infinity) return;
        if ((effect.pseudoElement || '').indexOf('view-transition') >= 0) return;
        try { animation.finish(); } catch (e) { /* An animation that cannot finish keeps playing. */ }
      });
      if (now < until) requestAnimationFrame(settle);
    })(performance.now());
  }
  function init() {
    settleArrival();
    enhance(document);
    onScroll();
    // Re-enhance content inserted later (dialogs filled on open, rendered panels).
    new MutationObserver(function (records) {
      records.forEach(function (record) {
        record.addedNodes.forEach(function (node) {
          if (node.nodeType !== 1) return;
          if (node.matches('[data-scene], [data-rf-segmented], [data-rf-animate], [data-rf-magnetic], [data-rf-split]') || node.querySelector('[data-scene], [data-rf-segmented], [data-rf-animate], [data-rf-magnetic], [data-rf-split]')) enhance(node.parentNode || document);
        });
      });
    }).observe(document.body, { childList: true, subtree: true });
    setTimeout(function () { root.classList.remove('rf-arrived'); }, 1500);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();

  window.RFMotion = { mode: mode, active: active, split: split, reveal: reveal, revealGroup: revealGroup, watchList: watchList,
    segmented: segmented, countTo: countTo, stamp: stamp, stampAt: stampAt, progress: progress, enhance: enhance, Spring: Spring };
})();
