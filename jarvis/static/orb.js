/* JARVIS arc-reactor orb — a canvas core whose motion encodes assistant state.
 *
 * States: idle (slow breathe) · listening (amber ripples) · thinking
 * (rings accelerate & counter-rotate) · speaking (nucleus pulses in bursts).
 * One exported controller: Orb.mount(canvas) -> { setState(name) }.
 */
(() => {
  const STATES = {
    idle:      { hue: 195, spin: 0.10, pulse: 0.35, turb: 0.5, ringGlow: 0.7 },
    listening: { hue: 38,  spin: 0.16, pulse: 0.9,  turb: 0.9, ringGlow: 1.0 },
    thinking:  { hue: 205, spin: 0.7,  pulse: 0.6,  turb: 1.4, ringGlow: 1.1 },
    confirming:{ hue: 38,  spin: 0.06, pulse: 0.45, turb: 0.5, ringGlow: 1.15 },
    executing: { hue: 265, spin: 1.1,  pulse: 1.0,  turb: 1.6, ringGlow: 1.3 },
    speaking:  { hue: 190, spin: 0.22, pulse: 1.6,  turb: 1.1, ringGlow: 1.2 },
    offline:   { hue: 210, spin: 0.03, pulse: 0.12, turb: 0.2, ringGlow: 0.25 },
  };

  function mount(canvas) {
    const ctx = canvas.getContext("2d");
    let dpr = Math.min(window.devicePixelRatio || 1, 2);
    let W = 0, H = 0, cx = 0, cy = 0, R = 0;

    let cur = { ...STATES.idle };
    let target = STATES.idle;
    let t = 0;
    const ripples = [];       // listening ripples
    let lastState = "idle";
    const prefersReduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // Three energy rings — tight and concentric, hugging the core so they
    // read as one system rather than scattered arcs.
    const rings = [
      { rad: 1.32, tilt: 0.34, dir: 1,  width: 2.4, gap: 0.10, phase: 0 },
      { rad: 1.55, tilt: -0.5, dir: -1, width: 1.7, gap: 0.14, phase: 1.1 },
      { rad: 1.80, tilt: 0.20, dir: 1,  width: 1.3, gap: 0.20, phase: 2.3 },
    ];
    // Static starfield for depth (drawn faint, parallax-free).
    const motes = Array.from({ length: 90 }, () => ({
      a: Math.random() * Math.PI * 2,
      d: 1.25 + Math.random() * 1.6,
      s: Math.random() * 0.6 + 0.2,
      tw: Math.random() * Math.PI * 2,
    }));

    function resize() {
      const rect = canvas.getBoundingClientRect();
      W = rect.width; H = rect.height;
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = W * dpr; canvas.height = H * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      cx = W / 2; cy = H / 2;
      R = Math.min(W, H) * 0.15;
    }

    function setState(name) {
      target = STATES[name] || STATES.idle;
      if (name === "listening" && lastState !== "listening") {
        ripples.push({ r: R, life: 1 });
      }
      lastState = name;
    }

    function lerp(a, b, k) { return a + (b - a) * k; }

    function draw() {
      t += prefersReduced ? 0.003 : 0.016;
      // ease current params toward target
      for (const k of Object.keys(target)) cur[k] = lerp(cur[k], target[k], 0.05);

      ctx.clearRect(0, 0, W, H);
      const breathe = 1 + Math.sin(t * 1.1) * 0.04 * cur.pulse;
      const burst = cur === STATES.speaking ? 0 : 0;
      const coreR = R * breathe;

      // --- ambient starfield ---
      for (const m of motes) {
        const tw = 0.35 + 0.65 * (0.5 + 0.5 * Math.sin(t * 1.5 + m.tw));
        const x = cx + Math.cos(m.a) * R * m.d;
        const y = cy + Math.sin(m.a) * R * m.d;
        ctx.beginPath();
        ctx.arc(x, y, m.s, 0, Math.PI * 2);
        ctx.fillStyle = `hsla(${cur.hue}, 80%, 70%, ${0.10 * tw})`;
        ctx.fill();
      }

      // --- outer halo glow ---
      const halo = ctx.createRadialGradient(cx, cy, coreR * 0.3, cx, cy, coreR * 4.2);
      halo.addColorStop(0, `hsla(${cur.hue}, 90%, 60%, ${0.16 * cur.ringGlow})`);
      halo.addColorStop(0.5, `hsla(${cur.hue}, 90%, 55%, 0.05)`);
      halo.addColorStop(1, "hsla(0,0%,0%,0)");
      ctx.fillStyle = halo;
      ctx.fillRect(0, 0, W, H);

      // --- listening ripples ---
      for (let i = ripples.length - 1; i >= 0; i--) {
        const rp = ripples[i];
        rp.r += 2.4; rp.life -= 0.012;
        if (rp.life <= 0) { ripples.splice(i, 1); continue; }
        ctx.beginPath();
        ctx.arc(cx, cy, rp.r, 0, Math.PI * 2);
        ctx.strokeStyle = `hsla(38, 95%, 60%, ${rp.life * 0.5})`;
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
      if (target === STATES.listening && Math.sin(t * 3) > 0.98) {
        ripples.push({ r: coreR, life: 1 });
      }

      // --- energy rings (elliptical, tilted, rotating) ---
      rings.forEach((ring, idx) => {
        const rot = t * cur.spin * ring.dir + ring.phase;
        const rx = coreR * ring.rad;
        const ry = rx * (0.34 + Math.abs(Math.sin(ring.tilt + t * 0.1)) * 0.30);
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(rot);
        // dashed arc segments give the "energy" read rather than a solid hoop
        const segs = 60;
        for (let s = 0; s < segs; s++) {
          const a0 = (s / segs) * Math.PI * 2;
          const on = (Math.sin(a0 * 3 + t * 2 + idx) + 1) / 2;
          if (on < ring.gap) continue;
          const a1 = a0 + (Math.PI * 2 / segs) * 0.7;
          ctx.beginPath();
          ctx.ellipse(0, 0, rx, ry, 0, a0, a1);
          ctx.strokeStyle = `hsla(${cur.hue}, 85%, ${55 + on * 20}%, ${0.5 * cur.ringGlow * on})`;
          ctx.lineWidth = ring.width;
          ctx.stroke();
        }
        ctx.restore();
      });

      // --- turbulent inner corona ---
      const lobes = 7;
      ctx.beginPath();
      for (let a = 0; a <= Math.PI * 2 + 0.01; a += 0.08) {
        const wob = 1 + Math.sin(a * lobes + t * 2.2) * 0.06 * cur.turb
                      + Math.sin(a * 3 - t * 1.5) * 0.04 * cur.turb;
        const r = coreR * 1.35 * wob;
        const x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
        a === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.closePath();
      const corona = ctx.createRadialGradient(cx, cy, coreR * 0.5, cx, cy, coreR * 1.6);
      corona.addColorStop(0, `hsla(${cur.hue}, 95%, 65%, 0.30)`);
      corona.addColorStop(1, "hsla(0,0%,0%,0)");
      ctx.fillStyle = corona;
      ctx.fill();

      // --- bright nucleus ---
      const pulse = 1 + Math.sin(t * 6) * 0.06 * (cur.pulse);
      const nucR = coreR * pulse;
      const core = ctx.createRadialGradient(
        cx - coreR * 0.22, cy - coreR * 0.22, coreR * 0.05,
        cx, cy, nucR);
      core.addColorStop(0, "hsla(0,0%,100%,0.98)");
      core.addColorStop(0.32, `hsla(${cur.hue}, 100%, 82%, 0.96)`);
      core.addColorStop(0.75, `hsla(${cur.hue}, 92%, 55%, 0.7)`);
      core.addColorStop(1, `hsla(${cur.hue}, 90%, 42%, 0.1)`);
      ctx.beginPath();
      ctx.arc(cx, cy, nucR, 0, Math.PI * 2);
      ctx.fillStyle = core;
      ctx.fill();

      // --- internal turbulence: drifting plasma cells clipped to the nucleus ---
      ctx.save();
      ctx.beginPath();
      ctx.arc(cx, cy, nucR * 0.98, 0, Math.PI * 2);
      ctx.clip();
      ctx.globalCompositeOperation = "screen";
      for (let c = 0; c < 5; c++) {
        const ca = t * (0.4 + c * 0.22) * (c % 2 ? -1 : 1) + c * 1.7;
        const cd = nucR * (0.2 + 0.32 * Math.abs(Math.sin(t * 0.6 + c)));
        const bx = cx + Math.cos(ca) * cd, by = cy + Math.sin(ca) * cd;
        const br = nucR * (0.28 + 0.14 * Math.sin(t * 1.3 + c * 2));
        const blob = ctx.createRadialGradient(bx, by, 0, bx, by, br);
        const light = 70 + c * 4;
        blob.addColorStop(0, `hsla(${cur.hue - 6}, 100%, ${light}%, ${0.22 * cur.turb})`);
        blob.addColorStop(1, "hsla(0,0%,0%,0)");
        ctx.fillStyle = blob;
        ctx.beginPath(); ctx.arc(bx, by, br, 0, Math.PI * 2); ctx.fill();
      }
      // hot white center kept crisp on top of the churn
      const hot = ctx.createRadialGradient(cx, cy, 0, cx, cy, nucR * 0.4);
      hot.addColorStop(0, "hsla(0,0%,100%,0.9)");
      hot.addColorStop(1, "hsla(0,0%,100%,0)");
      ctx.fillStyle = hot;
      ctx.beginPath(); ctx.arc(cx, cy, nucR * 0.4, 0, Math.PI * 2); ctx.fill();
      ctx.restore();

      requestAnimationFrame(draw);
    }

    resize();
    window.addEventListener("resize", resize);
    requestAnimationFrame(draw);
    return { setState };
  }

  window.Orb = { mount };
})();
