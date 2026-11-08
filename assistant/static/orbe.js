/* Orbe animée de l'assistant : un sort vaporeux dessiné sur un <canvas>.
   États : repos, ecoute, reflexion, parole, erreur.
   Écoles (apparence) : arcane, givre, flamme, foudre, soin.
   Utilisation :
     const orbe = creerOrbe(canvas, { ecole: "givre", particules: 80, brume: .75 });
     orbe.etat("reflexion");      // change d'état
     orbe.impulsion(n);           // un morceau de réponse vient d'arriver (fait pulser la parole)
     orbe.reglages({ ecole, particules, brume, trainees });   // trainees : 0 (net) à 12
   Option « fond » (couleur CSS) : l'orbe est peinte sur ce fond opaque, ce qui garde toute la
   finesse des dégradés (sur un canvas transparent, les halos très légers n'ont que quelques
   niveaux de couleur et font des bandes).
   Les grands dégradés doux (halo, brume, volutes, cœur) sont calculés par la carte graphique (WebGL)
   en haute précision puis arrondis une seule fois, avec un tramage invisible : plus aucune bande.
   Les particules restent sur le canvas d'origine, posé par-dessus en mode additif.
*/
(function () {
function creerOrbe(cv, opts = {}) {
  const ECOLES = {
    arcane:  { nom: "Arcane",      coeur: [235,225,255], mi: [150,110,255], bord: [70,60,200],  lueur: "#8f6bff", effet: "runes" },
    givre:   { nom: "Givre",       coeur: [240,252,255], mi: [140,220,255], bord: [40,120,210], lueur: "#7fd6ff", effet: "eclats" },
    flamme:  { nom: "Flamme",      coeur: [255,245,210], mi: [255,150,40],  bord: [200,50,10],  lueur: "#ff8a2a", effet: "monte" },
    foudre:  { nom: "Foudre",      coeur: [240,245,255], mi: [120,170,255], bord: [80,60,230],  lueur: "#7aa8ff", effet: "arcs" },
    soin:    { nom: "Restauration",coeur: [255,252,225], mi: [255,215,110], bord: [210,140,30], lueur: "#ffd36b", effet: "rayons" },
  };
  const ROUGE = { coeur: [255,225,215], mi: [255,70,50], bord: [150,10,20] };
  const ETATS = {
    repos:     { nom: "Repos",     vit: .5,  rayon: .78, aspir: 0,   eclat: .75 },
    ecoute:    { nom: "Écoute",    vit: .9,  rayon: 1.0, aspir: 1,   eclat: 1.0 },
    reflexion: { nom: "Réflexion", vit: 2.6, rayon: .62, aspir: .3,  eclat: 1.05 },
    parole:    { nom: "Parole",    vit: 1.1, rayon: .9,  aspir: -1,  eclat: 1.1 },
    erreur:    { nom: "Erreur",    vit: .7,  rayon: .8,  aspir: 0,   eclat: .9,  legende: "En cas d'erreur, l'orbe vire au rouge et vacille." },
  };

  const mc = cv.getContext("2d");
  let ctx = mc;
  const reduit = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let W = 0, D = 1, couche = null;   // couche : la toile WebGL des dégradés, si elle existe
  function taille() {
    D = Math.min(devicePixelRatio || 1, 2);
    W = Math.max(2, Math.min(1800, Math.round(cv.clientWidth * D)));   // plafond : plein écran fluide
    cv.width = cv.height = W;
    if (couche) couche.width = couche.height = W;
  }
  addEventListener("resize", taille); taille();

  // Tramage temporel : on ajoute à chaque image un bruit de 0 à 2 niveaux (sur 255), le même partout
  // quelle que soit la luminosité, et déplacé à chaque image. L'œil en fait la moyenne : les marches
  // d'un niveau des dégradés sombres (anneaux) disparaissent, sans grain visible dans les zones claires.
  const fond = opts.fond || null;
  const bruit = document.createElement("canvas"); bruit.width = bruit.height = 64;
  { const bc = bruit.getContext("2d"), im = bc.createImageData(64, 64);
    for (let i = 0; i < im.data.length; i += 4) { const v = (Math.random() * 3) | 0; im.data[i] = im.data[i + 1] = im.data[i + 2] = v; im.data[i + 3] = 255; }
    bc.putImageData(im, 0, 0); }
  const motif = mc.createPattern(bruit, "repeat");

  // Couche WebGL des dégradés (repli sur le dessin 2D si WebGL n'est pas disponible)
  const NB = 26, gpu = fond ? initGPU() : null;
  function initGPU() {
    try {
      const glc = document.createElement("canvas");
      const gl = glc.getContext("webgl2", { alpha: false, antialias: false, premultipliedAlpha: true }) ||
                 glc.getContext("webgl", { alpha: false, antialias: false, premultipliedAlpha: true });
      if (!gl || gl.getParameter(gl.MAX_FRAGMENT_UNIFORM_VECTORS) < 3 * NB + 16) return null;
      const vs = "attribute vec2 a;void main(){gl_Position=vec4(a,0.,1.);}";
      const fs = `precision highp float;
uniform vec2 uC; uniform float uW;
uniform vec3 uMi, uBord, uCoeur;
uniform vec3 uHalo;            // rayon, alpha centre, alpha à 45 %
uniform vec4 uCore;            // rayon, alpha blanc, alpha cœur, alpha mi
uniform int uNB;
uniform vec4 uB1[${NB}];       // x, y, angle, étirement
uniform vec3 uB2[${NB}];       // rayon, alpha, 1 = couleur cœur
uniform vec4 uV[5];            // angle, écrasement, rayon, alpha
float ign(vec2 v){ return fract(52.9829189 * fract(dot(v, vec2(.06711056, .00583715)))); }
void main(){
  vec2 p = vec2(gl_FragCoord.x, uW - gl_FragCoord.y) - uC;
  vec3 c = vec3(0.);
  float d = length(p) / uHalo.x;
  if (d < 1.) c += d < .45 ? mix(uMi * uHalo.y, uBord * uHalo.z, d / .45) : mix(uBord * uHalo.z, vec3(0.), (d - .45) / .55);
  for (int i = 0; i < ${NB}; i++) {
    if (i >= uNB) break;
    vec4 b = uB1[i]; vec3 b2 = uB2[i];
    vec2 q = p - b.xy; float cs = cos(b.z), sn = sin(b.z);
    vec2 l = vec2(cs * q.x + sn * q.y, -sn * q.x + cs * q.y); l.x /= b.w;
    float e = length(l) / b2.x;
    if (e < 1.) { vec3 c0 = mix(uMi, uCoeur, b2.z) * b2.y, c1 = uMi * b2.y * .45;
      c += e < .5 ? mix(c0, c1, e / .5) : mix(c1, vec3(0.), (e - .5) / .5); }
  }
  for (int i = 0; i < 5; i++) {
    vec4 v = uV[i]; float cs = cos(v.x), sn = sin(v.x);
    vec2 l = vec2(cs * p.x + sn * p.y, -sn * p.x + cs * p.y); l.y /= v.y;
    float e = length(l - vec2(v.z * .3, 0.)) / v.z;
    if (e < 1.) c += mix(uMi * v.w, vec3(0.), e);
  }
  d = length(p) / uCore.x;
  if (d < 1.) c += d < .15 ? mix(vec3(uCore.y), uCoeur * uCore.z, d / .15)
           : d < .45 ? mix(uCoeur * uCore.z, uMi * uCore.w, (d - .15) / .3)
           : mix(uMi * uCore.w, vec3(0.), (d - .45) / .55);
  c = min(c, vec3(1.));
  float f = clamp((.5 * uW - length(gl_FragCoord.xy - .5 * uW)) / (.2 * uW), 0., 1.);
  vec3 o = c * f * 255.;
  // tramage triangulaire fixe d'un niveau, avant l'unique arrondi ; le noir reste noir pur
  float n = ign(gl_FragCoord.xy) + ign(gl_FragCoord.xy + vec2(47., 113.)) - 1.;
  o += n * clamp(o * 4., 0., 1.);
  gl_FragColor = vec4(floor(o + .5) / 255., 1.);
}`;
      const sh = (t, src) => { const x = gl.createShader(t); gl.shaderSource(x, src); gl.compileShader(x);
        if (!gl.getShaderParameter(x, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(x)); return x; };
      const pr = gl.createProgram();
      gl.attachShader(pr, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, fs));
      gl.linkProgram(pr); if (!gl.getProgramParameter(pr, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(pr));
      gl.useProgram(pr);
      gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
      const loc = gl.getAttribLocation(pr, "a"); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
      const u = {}; for (const n of ["uC","uW","uMi","uBord","uCoeur","uHalo","uCore","uNB","uB1","uB2","uV"]) u[n] = gl.getUniformLocation(pr, n);
      // la couche WebGL se place sous le canvas d'origine, qui s'y ajoute en mode additif
      const par = cv.parentNode;
      par.style.display = "grid";
      glc.className = cv.className; glc.setAttribute("aria-hidden", "true");
      glc.style.gridArea = cv.style.gridArea = "1 / 1"; glc.style.pointerEvents = "none";
      par.insertBefore(glc, cv);
      cv.style.mixBlendMode = CSS.supports("mix-blend-mode", "plus-lighter") ? "plus-lighter" : "screen";
      return { gl, glc, u, b1: new Float32Array(NB * 4), b2: new Float32Array(NB * 3), v: new Float32Array(20), nb: 0,
               halo: [1, 0, 0], core: [1, 0, 0, 0] };
    } catch (e) { console.warn("orbe : WebGL indisponible, dessin 2D", e); return null; }
  }
  if (gpu) { couche = gpu.glc; couche.width = couche.height = W; }

  let ecole = ECOLES[opts.ecole] ? opts.ecole : "givre", etat = "repos";
  // valeurs lissées
  const L = { vit: .5, rayon: .78, aspir: 0, eclat: .75, rouge: 0, voix: 0, son: 0 };
  let sonCible = 0;   // niveau du micro (0 à 1), donné par la page à chaque image
  let cible = Object.assign({}, ETATS.repos);
  let nbPart = opts.particules ?? 80, vapeur = opts.brume ?? .75, activite = 0;
  // longueur des traînées : nombre d'images gardées derrière chaque particule (0 = particules nettes)
  let traine = Math.max(0, Math.min(12, Math.round(opts.trainees ?? 0)));
  // Volutes de brume : grandes nappes douces qui tournent autour du cœur
  const brume = [];
  for (let i = 0; i < 26; i++) brume.push({ th: Math.random() * 7, r: .12 + Math.random() * .6, v: (.15 + Math.random() * .35) * (i % 3 ? 1 : -1),
    taille: .32 + Math.random() * .38, ph: Math.random() * 7, etire: 1.4 + Math.random() * 1.2, dy: 0 });

  const mix = (a, b, t) => a + (b - a) * t;
  const mixc = (a, b, t) => [mix(a[0],b[0],t), mix(a[1],b[1],t), mix(a[2],b[2],t)];
  const rgba = (c, a) => `rgba(${c[0]|0},${c[1]|0},${c[2]|0},${a})`;
  function couleurs() {
    const e = ECOLES[ecole];
    return { coeur: mixc(e.coeur, ROUGE.coeur, L.rouge), mi: mixc(e.mi, ROUGE.mi, L.rouge), bord: mixc(e.bord, ROUGE.bord, L.rouge) };
  }

  // Particules en orbite sur des plans inclinés (pseudo 3D)
  const parts = [];
  function nouvelle(p = {}) {
    p.th = Math.random() * Math.PI * 2;
    p.r = .25 + Math.random() * .8;
    p.incl = (Math.random() - .5) * Math.PI;
    p.rot = Math.random() * Math.PI * 2;
    p.v = (.4 + Math.random()) * (Math.random() < .5 ? -1 : 1);
    p.t = .6 + Math.random() * 2.2; p.vie = 0;
    p.dy = 0; p.taille = .4 + Math.random() * 1.1; p.h = [];
    return p;
  }
  function ajuster() { while (parts.length < nbPart) parts.push(nouvelle()); parts.length = nbPart; }
  ajuster();

  // Étincelles éjectées (parole, flamme)
  const etincelles = [];
  function jaillir(n, force) {
    for (let i = 0; i < n; i++) {
      const a = Math.random() * Math.PI * 2, s = (.002 + Math.random() * .005) * force;
      etincelles.push({ x: Math.cos(a) * .2, y: Math.sin(a) * .2, vx: Math.cos(a) * s, vy: Math.sin(a) * s, vie: 1, t: .5 + Math.random() });
    }
  }

  // Enveloppe vocale simulée : syllabes
  let syl = 0, sylCible = 0, sylT = 0;
  function voix(dt) {
    sylT -= dt;
    if (sylT <= 0) { const pause = Math.random() < .18; sylCible = pause ? 0 : .35 + Math.random() * .65; sylT = pause ? .25 + Math.random() * .35 : .08 + Math.random() * .16; }
    syl += (sylCible - syl) * Math.min(1, dt * 22);
    return syl;
  }

  let arcs = [], arcT = 0;
  function eclair(x0, y0, x1, y1, prof) {
    const pts = [[x0, y0]], n = 9;
    for (let i = 1; i < n; i++) {
      const t = i / n, j = (1 - Math.abs(t - .5) * 2) * prof;
      pts.push([mix(x0, x1, t) + (Math.random() - .5) * j, mix(y0, y1, t) + (Math.random() - .5) * j]);
    }
    pts.push([x1, y1]); return pts;
  }

  let t0 = performance.now(), temps = 0;
  function frame(now) {
    let dt = Math.min(.05, (now - t0) / 1000); t0 = now;
    if (reduit) dt *= .35;
    temps += dt;

    // l'activité vocale retombe si plus aucun morceau de texte n'arrive
    activite = Math.max(0, activite - dt * 1.6);

    const k = Math.min(1, dt * 3);
    for (const c of ["vit","rayon","aspir","eclat"]) L[c] += (cible[c] - L[c]) * k;
    L.rouge += ((etat === "erreur" ? 1 : 0) - L.rouge) * k;
    const v = etat === "parole" ? voix(dt) * (.35 + .65 * Math.min(1, activite)) : 0;
    L.voix += (v - L.voix) * Math.min(1, dt * 20);
    // scintillement léger quand le micro capte du son (sans lancer l'animation d'écoute)
    sonCible = Math.max(0, sonCible - dt * 3);
    L.son += ((etat === "parole" ? 0 : sonCible) - L.son) * Math.min(1, dt * 14);

    const C = couleurs(), E = ECOLES[ecole], cx = W / 2, cy = W / 2, R = W * .3;
    let vacille = etat === "erreur" ? (Math.random() < .08 ? .35 : 1) : 1;
    if (L.son > .01) vacille *= 1 + L.son * (.1 + .22 * Math.random());
    const souffle = 1 + Math.sin(temps * (etat === "reflexion" ? 5 : 1.6)) * .05 + L.voix * .22;
    const jit = etat === "erreur" ? (Math.random() - .5) * W * .006 : 0;

    // Image redessinée entièrement à chaque fois : aucune trace ne peut rester.
    // Les traînées des particules et des étincelles sont tracées à partir de leurs dernières positions.
    mc.setTransform(1, 0, 0, 1, 0, 0); mc.globalCompositeOperation = "source-over"; mc.clearRect(0, 0, W, W);
    if (fond && !gpu) { mc.fillStyle = fond; mc.fillRect(0, 0, W, W); }
    if (gpu) gpu.nb = 0;
    mc.globalCompositeOperation = "lighter"; mc.translate(cx + jit, cy + jit * .6);
    ctx = mc;

    // halo extérieur
    const hR = Math.min(W * .5, R * 1.45 * souffle);
    let g;
    if (gpu) gpu.halo = [hR, .09 * L.eclat * vacille, .05 * L.eclat * vacille];
    else {
      g = ctx.createRadialGradient(0, 0, 0, 0, 0, hR);
      g.addColorStop(0, rgba(C.mi, .09 * L.eclat * vacille));
      g.addColorStop(.45, rgba(C.bord, .05 * L.eclat * vacille));
      g.addColorStop(1, rgba(C.bord, 0));
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(0, 0, hR, 0, 7); ctx.fill();
    }

    // anneau de runes (arcane) ou anneau d'aspiration (écoute)
    if (E.effet === "runes" || etat === "reflexion") {
      ctx.save(); ctx.rotate(temps * .3 * L.vit);
      ctx.strokeStyle = rgba(C.mi, .25 * vacille); ctx.lineWidth = D;
      const rr = R * 1.12;
      ctx.beginPath(); ctx.arc(0, 0, rr, 0, 7); ctx.stroke();
      for (let i = 0; i < 24; i++) {
        ctx.save(); ctx.rotate(i / 24 * Math.PI * 2); ctx.translate(rr, 0);
        const h = R * .05, s = (i * 7919) % 5;
        ctx.beginPath();
        if (s === 0) { ctx.moveTo(-h, -h); ctx.lineTo(h, 0); ctx.lineTo(-h, h); }
        else if (s === 1) { ctx.moveTo(-h, 0); ctx.lineTo(h, 0); ctx.moveTo(0, -h); ctx.lineTo(0, h * .4); }
        else if (s === 2) { ctx.moveTo(-h, h); ctx.lineTo(0, -h); ctx.lineTo(h, h); }
        else if (s === 3) { ctx.arc(0, 0, h * .6, 0, 5); }
        else { ctx.moveTo(-h, -h); ctx.lineTo(h, h); ctx.moveTo(h * .2, -h); ctx.lineTo(h, -h * .2); }
        ctx.strokeStyle = rgba(C.mi, .35 * vacille); ctx.stroke(); ctx.restore();
      }
      ctx.restore();
    }
    if (L.aspir > .05) {
      const ph = (temps * .8) % 1;
      for (let i = 0; i < 3; i++) {
        const p = (ph + i / 3) % 1, rr = R * (1.5 - p * 1.1);
        ctx.strokeStyle = rgba(C.mi, .22 * p * L.aspir); ctx.lineWidth = D * 1.5;
        ctx.beginPath(); ctx.arc(0, 0, rr, 0, 7); ctx.stroke();
      }
    }

    // rayons (restauration)
    if (E.effet === "rayons") {
      ctx.save(); ctx.rotate(temps * .15);
      for (let i = 0; i < 12; i++) {
        ctx.rotate(Math.PI / 6);
        const lg = R * (1.2 + .25 * Math.sin(temps * 1.3 + i * 2));
        g = ctx.createLinearGradient(0, 0, lg, 0);
        g.addColorStop(0, rgba(C.mi, .18 * vacille)); g.addColorStop(1, rgba(C.mi, 0));
        ctx.fillStyle = g; ctx.beginPath(); ctx.moveTo(0, -R * .04); ctx.lineTo(lg, 0); ctx.lineTo(0, R * .04); ctx.fill();
      }
      ctx.restore();
    }

    // brume vaporeuse
    if (vapeur > 0) {
      const ext = (L.aspir > 0 ? 1 - .25 * L.aspir : 1) * (1 + L.voix * .35) * (etat === "reflexion" ? .8 : 1);
      for (const b of brume) {
        b.th += b.v * dt * L.vit * 1.2;
        if (E.effet === "monte") { b.dy -= dt * .12; if (b.dy < -.9) b.dy = 0; } else b.dy *= .95;
        const ondule = 1 + .18 * Math.sin(temps * .9 + b.ph);
        const rr = b.r * R * ext * ondule;
        const x = Math.cos(b.th) * rr, y = Math.sin(b.th) * rr * .85 + b.dy * R;
        const tR = R * b.taille * (1 + L.voix * .3);
        const al = .2 * vapeur * L.eclat * vacille * (.7 + .3 * Math.sin(temps * 1.3 + b.ph)) * (b.dy ? 1 + b.dy : 1);
        if (gpu) {
          if (gpu.nb < NB) { const i = gpu.nb++;
            gpu.b1.set([x, y, b.th + Math.PI / 2, b.etire], i * 4); gpu.b2.set([tR, al, b.r < .45 ? 1 : 0], i * 3); }
          continue;
        }
        ctx.save(); ctx.translate(x, y); ctx.rotate(b.th + Math.PI / 2); ctx.scale(b.etire, 1);
        g = ctx.createRadialGradient(0, 0, 0, 0, 0, tR);
        g.addColorStop(0, rgba(b.r < .45 ? C.coeur : C.mi, al));
        g.addColorStop(.5, rgba(C.mi, al * .45));
        g.addColorStop(1, rgba(C.bord, 0));
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(0, 0, tR, 0, 7); ctx.fill();
        ctx.restore();
      }
    }

    // particules en orbite (traînée : les 7 dernières positions, de plus en plus pâles)
    ctx = mc;
    const rayon = R * L.rayon * souffle;
    for (const p of parts) {
      p.vie += dt;
      p.th += p.v * dt * L.vit * (1.5 / (p.r + .3));
      if (L.aspir > 0) p.r -= dt * .35 * L.aspir * p.r;
      if (L.aspir < 0) p.r += dt * .25 * -L.aspir * (.3 + L.voix);
      if (E.effet === "monte") p.dy -= dt * .25;
      if (p.vie > p.t || p.r < .08 || p.r > 1.35) nouvelle(p);
      const x0 = Math.cos(p.th) * p.r, y0 = Math.sin(p.th) * p.r;
      const y1 = y0 * Math.cos(p.incl), z = y0 * Math.sin(p.incl);
      const x = (x0 * Math.cos(p.rot) - y1 * Math.sin(p.rot)) * rayon;
      const y = (x0 * Math.sin(p.rot) + y1 * Math.cos(p.rot)) * rayon + p.dy * R;
      const prof = (z + 1.2) / 2.4;
      const fondu = Math.sin(Math.min(1, p.vie / p.t) * Math.PI);
      const a = fondu * (.2 + .6 * prof) * vacille * .8;
      const s = p.taille * D * (.6 + prof) * (E.effet === "eclats" ? 1.3 : 1);
      const col = p.r < .45 ? C.coeur : C.mi;
      // traînée continue : un filet qui relie les dernières positions et s'efface vers la queue
      const h = p.h || (p.h = []);
      ctx.lineCap = "round";
      let px = x, py = y;
      for (let k = 0; k < h.length; k++) {
        const q = h[k], f = Math.pow(.72, k + 1);
        ctx.strokeStyle = rgba(q[3], q[2] * .8 * f); ctx.lineWidth = s * 1.6 * (1 - k * .1);
        ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(q[0], q[1]); ctx.stroke();
        px = q[0]; py = q[1];
      }
      h.unshift([x, y, a, col]); if (h.length > traine) h.length = traine;
      if (E.effet === "eclats" && p.taille > 1.8) {
        ctx.save(); ctx.translate(x, y); ctx.rotate(temps * 2 + p.rot);
        ctx.fillStyle = rgba(C.coeur, a * .9);
        ctx.beginPath(); ctx.moveTo(0, -s * 2.4); ctx.lineTo(s * .7, 0); ctx.lineTo(0, s * 2.4); ctx.lineTo(-s * .7, 0); ctx.fill();
        ctx.restore();
      } else {
        ctx.fillStyle = rgba(col, a * .9);
        ctx.beginPath(); ctx.arc(x, y, s, 0, 7); ctx.fill();
        ctx.fillStyle = rgba(C.mi, a * .1);
        ctx.beginPath(); ctx.arc(x, y, s * 3, 0, 7); ctx.fill();
      }
    }

    ctx = mc;
    // volutes internes : ellipses tournantes
    for (let i = 0; i < 5; i++) {
      if (gpu) {
        gpu.v.set([temps * (.4 + i * .23) * L.vit * (i % 2 ? -1 : 1) + i, .35 + .15 * Math.sin(temps * .7 + i),
                   R * (.42 + i * .05) * souffle, .07 * L.eclat * vacille], i * 4);
        continue;
      }
      ctx.save();
      ctx.rotate(temps * (.4 + i * .23) * L.vit * (i % 2 ? -1 : 1) + i);
      ctx.scale(1, .35 + .15 * Math.sin(temps * .7 + i));
      const er = R * (.42 + i * .05) * souffle;
      g = ctx.createRadialGradient(er * .3, 0, 0, er * .3, 0, er);
      g.addColorStop(0, rgba(C.mi, .07 * L.eclat * vacille));
      g.addColorStop(1, rgba(C.bord, 0));
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(er * .3, 0, er, 0, 7); ctx.fill();
      ctx.restore();
    }

    // cœur
    const cR = R * .3 * souffle * (etat === "reflexion" ? .85 + .15 * Math.sin(temps * 9) : 1);
    if (gpu) gpu.core = [cR * 2.2, .7 * vacille, .45 * L.eclat * vacille, .16 * L.eclat * vacille];
    else {
      g = ctx.createRadialGradient(0, 0, 0, 0, 0, cR * 2.2);
      g.addColorStop(0, rgba([255,255,255], .7 * vacille));
      g.addColorStop(.15, rgba(C.coeur, .45 * L.eclat * vacille));
      g.addColorStop(.45, rgba(C.mi, .16 * L.eclat * vacille));
      g.addColorStop(1, rgba(C.bord, 0));
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(0, 0, cR * 2.2, 0, 7); ctx.fill();
    }

    ctx = mc;
    // éclairs : seulement pour l'école Foudre (plus d'éclairs en réflexion pour les autres écoles)
    arcT -= dt;
    const freqArc = E.effet === "arcs" ? (etat === "reflexion" ? .04 : .12) : 99;
    if (arcT <= 0) {
      arcT = freqArc * (.5 + Math.random());
      const a = Math.random() * Math.PI * 2, lg = R * (.8 + Math.random() * .6) * (1 + L.voix * .5);
      arcs.push({ pts: eclair(0, 0, Math.cos(a) * lg, Math.sin(a) * lg, R * .35), vie: .12 + Math.random() * .1 });
    }
    arcs = arcs.filter(A => (A.vie -= dt) > 0);
    for (const A of arcs) {
      for (const [w, al, c] of [[6, .15, C.mi], [2, .9, C.coeur]]) {
        ctx.strokeStyle = rgba(c, al * vacille); ctx.lineWidth = w * D; ctx.lineJoin = "round";
        ctx.beginPath(); A.pts.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)); ctx.stroke();
      }
    }

    // étincelles
    if (etat === "parole" && Math.random() < L.voix * .3) jaillir(1, 1 + L.voix);
    if (E.effet === "monte" && Math.random() < .25) { jaillir(1, .5); etincelles[etincelles.length - 1].vy -= .006; }
    for (let i = etincelles.length - 1; i >= 0; i--) {
      const e = etincelles[i];
      e.x += e.vx * 60 * dt; e.y += e.vy * 60 * dt;
      if (E.effet === "monte") e.vy -= .0004 * 60 * dt;
      e.vie -= dt / e.t;
      if (e.vie <= 0) { etincelles.splice(i, 1); continue; }
      const ex = e.x * R * 2, ey = e.y * R * 2, er = D * 1.6 * e.vie + D * .5, eh = e.h || (e.h = []);
      ctx.lineCap = "round";
      let qx = ex, qy = ey;
      for (let k = 0; k < eh.length; k++) {
        ctx.strokeStyle = rgba(C.coeur, eh[k][2] * .8 * vacille * Math.pow(.72, k + 1)); ctx.lineWidth = er * 1.6;
        ctx.beginPath(); ctx.moveTo(qx, qy); ctx.lineTo(eh[k][0], eh[k][1]); ctx.stroke();
        qx = eh[k][0]; qy = eh[k][1];
      }
      eh.unshift([ex, ey, e.vie]); if (eh.length > traine) eh.length = traine;
      ctx.fillStyle = rgba(C.coeur, e.vie * .9 * vacille);
      ctx.beginPath(); ctx.arc(ex, ey, er, 0, 7); ctx.fill();
    }
    if (etincelles.length > 400) etincelles.splice(0, etincelles.length - 400);

    ctx = mc;
    mc.setTransform(1, 0, 0, 1, 0, 0);
    if (gpu) {
      const { gl, u } = gpu, c255 = c => [c[0] / 255, c[1] / 255, c[2] / 255];
      gl.viewport(0, 0, W, W);
      gl.uniform2f(u.uC, cx + jit, cy + jit * .6); gl.uniform1f(u.uW, W);
      gl.uniform3fv(u.uMi, c255(C.mi)); gl.uniform3fv(u.uBord, c255(C.bord)); gl.uniform3fv(u.uCoeur, c255(C.coeur));
      gl.uniform3fv(u.uHalo, gpu.halo); gl.uniform4fv(u.uCore, gpu.core);
      gl.uniform1i(u.uNB, gpu.nb); gl.uniform4fv(u.uB1, gpu.b1); gl.uniform3fv(u.uB2, gpu.b2); gl.uniform4fv(u.uV, gpu.v);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    } else if (fond && motif) {
      // tramage sur tout le halo, jusqu'à son bord : au-delà, le fond reste noir pur (écrans OLED)
      const ox = (Math.random() * 64) | 0, oy = (Math.random() * 64) | 0;
      mc.save();
      mc.beginPath(); mc.arc(cx + jit, cy + jit * .6, hR, 0, 7); mc.clip();
      mc.globalCompositeOperation = "lighter";
      mc.translate(-ox, -oy); mc.fillStyle = motif; mc.fillRect(0, 0, W + 64, W + 64);
      mc.restore();
    }
    // fondu circulaire des bords : aucun bord carré visible
    mc.globalCompositeOperation = "destination-in";
    g = mc.createRadialGradient(W / 2, W / 2, W * .3, W / 2, W / 2, W * .5);
    g.addColorStop(0, "rgba(0,0,0,1)"); g.addColorStop(1, "rgba(0,0,0,0)");
    mc.fillStyle = g; mc.fillRect(0, 0, W, W);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);


  return {
    etat(e) {
      if (!ETATS[e]) return;
      if (e === "parole" && etat !== "parole") jaillir(8, 1.5);
      etat = e; cible = Object.assign({}, ETATS[e]);
    },
    get etatCourant() { return etat; },
    son(n = 0) { sonCible = Math.max(sonCible, Math.max(0, Math.min(1, +n || 0))); },
    impulsion(n = 1) { activite = Math.min(1.5, activite + .25 + Math.min(n, 40) / 40); },
    reglages(r = {}) {
      if (r.ecole && ECOLES[r.ecole]) { ecole = r.ecole; for (const p of parts) p.dy = 0; }
      if (r.particules != null) { nbPart = Math.max(0, Math.min(400, +r.particules | 0)); ajuster(); }
      if (r.brume != null) vapeur = Math.max(0, Math.min(1, +r.brume));
      if (r.trainees != null) traine = Math.max(0, Math.min(12, Math.round(+r.trainees)));
    },
    redimensionner: taille,
  };
}
creerOrbe.ECOLES = {
  arcane: "Arcane", givre: "Givre", flamme: "Flamme", foudre: "Foudre", soin: "Restauration",
};
window.creerOrbe = creerOrbe;
})();
