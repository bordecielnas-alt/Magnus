/* Orbe animée de l'assistant : un sort vaporeux dessiné sur un <canvas>.
   États : repos, ecoute, reflexion, parole, erreur.
   Écoles (apparence) : arcane, givre, flamme, foudre, soin.
   Utilisation :
     const orbe = creerOrbe(canvas, { ecole: "givre", particules: 80, brume: .75 });
     orbe.etat("reflexion");      // change d'état
     orbe.impulsion(n);           // un morceau de réponse vient d'arriver (fait pulser la parole)
     orbe.reglages({ ecole, particules, brume });
   Option « fond » (couleur CSS) : l'orbe est peinte sur ce fond opaque, ce qui garde toute la
   finesse des dégradés (sur un canvas transparent, les halos très légers n'ont que quelques
   niveaux de couleur et font des bandes). Un tramage temporel très faible casse les marches restantes.
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
  const trace = document.createElement("canvas"), tc = trace.getContext("2d");
  let ctx = mc;
  const reduit = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let W = 0, D = 1;
  function taille() {
    D = Math.min(devicePixelRatio || 1, 2);
    W = Math.max(2, Math.min(1800, Math.round(cv.clientWidth * D)));   // plafond : plein écran fluide
    cv.width = cv.height = trace.width = trace.height = W;
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

  let ecole = ECOLES[opts.ecole] ? opts.ecole : "givre", etat = "repos";
  // valeurs lissées
  const L = { vit: .5, rayon: .78, aspir: 0, eclat: .75, rouge: 0, voix: 0 };
  let cible = Object.assign({}, ETATS.repos);
  let nbPart = opts.particules ?? 80, vapeur = opts.brume ?? .75, activite = 0;
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
    p.dy = 0; p.taille = .4 + Math.random() * 1.1;
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

    const C = couleurs(), E = ECOLES[ecole], cx = W / 2, cy = W / 2, R = W * .3;
    let vacille = etat === "erreur" ? (Math.random() < .08 ? .35 : 1) : 1;
    const souffle = 1 + Math.sin(temps * (etat === "reflexion" ? 5 : 1.6)) * .05 + L.voix * .22;
    const jit = etat === "erreur" ? (Math.random() - .5) * W * .006 : 0;

    // traînées : on assombrit l'image précédente
    // (les particules ont leur propre calque à traînées, la brume est redessinée nette)
    mc.setTransform(1, 0, 0, 1, 0, 0); mc.globalCompositeOperation = "source-over"; mc.clearRect(0, 0, W, W);
    if (fond) { mc.fillStyle = fond; mc.fillRect(0, 0, W, W); }
    tc.setTransform(1, 0, 0, 1, 0, 0); tc.globalCompositeOperation = "destination-out";
    tc.fillStyle = "rgba(0,0,0,0.22)"; tc.fillRect(0, 0, W, W);
    for (const c of [mc, tc]) { c.globalCompositeOperation = "lighter"; c.translate(cx + jit, cy + jit * .6); }
    ctx = mc;

    // halo extérieur
    const hR = Math.min(W * .5, R * 1.45 * souffle);
    let g = ctx.createRadialGradient(0, 0, 0, 0, 0, hR);
    g.addColorStop(0, rgba(C.mi, .09 * L.eclat * vacille));
    g.addColorStop(.45, rgba(C.bord, .05 * L.eclat * vacille));
    g.addColorStop(1, rgba(C.bord, 0));
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(0, 0, hR, 0, 7); ctx.fill();

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
        ctx.save(); ctx.translate(x, y); ctx.rotate(b.th + Math.PI / 2); ctx.scale(b.etire, 1);
        g = ctx.createRadialGradient(0, 0, 0, 0, 0, tR);
        g.addColorStop(0, rgba(b.r < .45 ? C.coeur : C.mi, al));
        g.addColorStop(.5, rgba(C.mi, al * .45));
        g.addColorStop(1, rgba(C.bord, 0));
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(0, 0, tR, 0, 7); ctx.fill();
        ctx.restore();
      }
    }

    // particules en orbite
    ctx = tc;
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
    g = ctx.createRadialGradient(0, 0, 0, 0, 0, cR * 2.2);
    g.addColorStop(0, rgba([255,255,255], .7 * vacille));
    g.addColorStop(.15, rgba(C.coeur, .45 * L.eclat * vacille));
    g.addColorStop(.45, rgba(C.mi, .16 * L.eclat * vacille));
    g.addColorStop(1, rgba(C.bord, 0));
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(0, 0, cR * 2.2, 0, 7); ctx.fill();

    ctx = tc;
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
      ctx.fillStyle = rgba(C.coeur, e.vie * .9 * vacille);
      ctx.beginPath(); ctx.arc(e.x * R * 2, e.y * R * 2, D * 1.6 * e.vie + D * .5, 0, 7); ctx.fill();
    }
    if (etincelles.length > 400) etincelles.splice(0, etincelles.length - 400);

    ctx = mc;
    mc.setTransform(1, 0, 0, 1, 0, 0);
    mc.drawImage(trace, 0, 0);
    if (fond && motif) {
      const ox = (Math.random() * 64) | 0, oy = (Math.random() * 64) | 0;
      mc.globalCompositeOperation = "lighter";
      mc.translate(-ox, -oy); mc.fillStyle = motif; mc.fillRect(0, 0, W + 64, W + 64);
      mc.setTransform(1, 0, 0, 1, 0, 0);
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
    impulsion(n = 1) { activite = Math.min(1.5, activite + .25 + Math.min(n, 40) / 40); },
    reglages(r = {}) {
      if (r.ecole && ECOLES[r.ecole]) { ecole = r.ecole; for (const p of parts) p.dy = 0; }
      if (r.particules != null) { nbPart = Math.max(0, Math.min(400, +r.particules | 0)); ajuster(); }
      if (r.brume != null) vapeur = Math.max(0, Math.min(1, +r.brume));
    },
    redimensionner: taille,
  };
}
creerOrbe.ECOLES = {
  arcane: "Arcane", givre: "Givre", flamme: "Flamme", foudre: "Foudre", soin: "Restauration",
};
window.creerOrbe = creerOrbe;
})();
