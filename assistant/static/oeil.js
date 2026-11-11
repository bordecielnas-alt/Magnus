/* Thème « HAL 9000 » : un œil-lentille rouge dessiné sur un <canvas>, et ses sons.
   Interprétation originale (aucun son, voix ni image du film) : une lentille noire dans une monture
   métallique, une lueur rouge au cœur presque doré, et une couronne de graduations dans le verre.
   Même interface que l'orbe :
     const oeil = creerOeil(canvas, { style: "classique", couronne: true });
     style : « classique » (lentille fish-eye proche du film, animation très calme)
             ou « moderne » (lueur qui pulse, couronne de graduations).
     oeil.etat("reflexion");   // repos, ecoute, reflexion, parole, erreur
     oeil.impulsion(n);        // un morceau de réponse vient d'arriver (fait pulser la parole)
     oeil.son(n);              // niveau du micro, 0 à 1
     oeil.reglages({ style, couronne }); oeil.pause(true);
   Sons : sonsHal.eveil(nom, ctx, sortie, t) avec nom = "cristal" ou "aube" ; sonsHal.ecoute(ctx, sortie, t).
   Tous fabriqués à la volée avec Web Audio, dans les aigus et en gamme majeure (clairs, pas inquiétants).
*/
(function () {
'use strict';
function creerOeil(cv, opts = {}) {
  const ETATS = {
    repos:     { eclat: .32, taille: .55, respire: .10, vitesse: .35, couronne: 0,   balayage: 0 },
    ecoute:    { eclat: .9,  taille: .95, respire: .05, vitesse: 1.0, couronne: .6,  balayage: 0 },
    reflexion: { eclat: .7,  taille: .8,  respire: .03, vitesse: 2.2, couronne: .35, balayage: 1 },
    parole:    { eclat: .8,  taille: .92, respire: .02, vitesse: 1.2, couronne: .5,  balayage: 0 },
    erreur:    { eclat: .45, taille: .6,  respire: .02, vitesse: .7,  couronne: .15, balayage: 0 },
  };
  const g = cv.getContext('2d');
  const reduit = matchMedia('(prefers-reduced-motion: reduce)').matches;
  let W = 0, couronne = opts.couronne !== false, enPause = !!opts.pause, style = opts.style === 'moderne' ? 'moderne' : 'classique';
  function taille() {
    const D = Math.min(devicePixelRatio || 1, 2);
    W = Math.max(2, Math.min(1800, Math.round(cv.clientWidth * D)));
    cv.width = cv.height = W;
  }
  addEventListener('resize', taille); taille();

  let etat = 'repos';
  const vu = Object.assign({}, ETATS.repos);
  let flash = 0, syllabe = 0, activite = 0, micro = 0, microCible = 0, angle = 0, t0 = performance.now();

  function frame(t) {
    if (enPause) { t0 = t; requestAnimationFrame(frame); return; }
    if (cv.clientWidth && Math.abs(cv.clientWidth * Math.min(devicePixelRatio || 1, 2) - W) > 2) taille();
    const dt = Math.min(.05, (t - t0) / 1000); t0 = t;
    const c = ETATS[etat], k = reduit ? .2 : .06;
    for (const cle in vu) vu[cle] += (c[cle] - vu[cle]) * k;
    flash *= .965;
    // parole : l'activité (morceaux de réponse) devient des syllabes qui pulsent
    activite = Math.max(0, activite - dt * .9);
    const cibleS = etat === 'parole' ? Math.min(1, activite) * (.55 + .45 * Math.abs(Math.sin(t / 95))) : 0;
    syllabe += (cibleS - syllabe) * (cibleS > syllabe ? .35 : .12);
    micro += (microCible - micro) * .2; microCible *= .9;

    if (style === 'classique') { classique(t); requestAnimationFrame(frame); return; }
    const cx = W / 2, cy = W / 2, R = W * .36;
    g.clearRect(0, 0, W, W);
    const respire = reduit ? 0 : Math.sin(t / 1000 * vu.vitesse * 1.6) * vu.respire;
    const fremit = reduit ? 0 : (Math.sin(t / 53) + Math.sin(t / 37)) * .006;
    const vacille = etat === 'erreur' && !reduit ? (Math.random() < .08 ? -.25 : 0) : 0;
    const vie = Math.max(0, vu.eclat + respire + fremit + vacille + syllabe * .35 + micro * .3 + flash * .5);

    // halo rouge autour de l'objet
    const halo = g.createRadialGradient(cx, cy, R * .8, cx, cy, R * 1.35);
    halo.addColorStop(0, `rgba(255,40,30,${.18 * vie})`); halo.addColorStop(1, 'rgba(255,40,30,0)');
    g.fillStyle = halo; g.beginPath(); g.arc(cx, cy, R * 1.35, 0, 7); g.fill();

    // monture métallique
    const met = g.createLinearGradient(cx, cy - R, cx, cy + R);
    met.addColorStop(0, '#8d8a8c'); met.addColorStop(.35, '#3b393b'); met.addColorStop(.65, '#1b1a1c'); met.addColorStop(1, '#555255');
    g.fillStyle = met; g.beginPath(); g.arc(cx, cy, R, 0, 7); g.fill();
    const met2 = g.createLinearGradient(cx, cy - R * .9, cx, cy + R * .9);
    met2.addColorStop(0, '#141315'); met2.addColorStop(1, '#4a474a');
    g.fillStyle = met2; g.beginPath(); g.arc(cx, cy, R * .93, 0, 7); g.fill();
    g.strokeStyle = 'rgba(255,255,255,.18)'; g.lineWidth = Math.max(1, W * .002);
    g.beginPath(); g.arc(cx, cy, R * .995, Math.PI * 1.1, Math.PI * 1.9); g.stroke();

    // verre noir
    const Rl = R * .86;
    const verre = g.createRadialGradient(cx, cy, 0, cx, cy, Rl);
    verre.addColorStop(0, '#120406'); verre.addColorStop(.7, '#070203'); verre.addColorStop(1, '#000');
    g.fillStyle = verre; g.beginPath(); g.arc(cx, cy, Rl, 0, 7); g.fill();

    // couronne de graduations : s'allume à l'éveil, ondule à l'écoute, comète pendant la réflexion
    angle += dt * .9 * vu.vitesse * (reduit ? .2 : 1);
    if (couronne) {
      const N = 60, r1 = Rl * .88, r2 = Rl * .95;
      g.lineCap = 'round'; g.lineWidth = Math.max(1, W * .0045);
      for (let i = 0; i < N; i++) {
        const a = i / N * Math.PI * 2 - Math.PI / 2;
        let lum = vu.couronne * .25 * vie;
        lum += micro * .9 * (Math.sin(a * 3 + t / 180) * .5 + .5);
        lum += syllabe * .5 * (Math.cos(a + Math.PI / 2) * .5 + .5);
        if (vu.balayage > .01) {
          let d = ((a + Math.PI / 2) / (Math.PI * 2) - angle) % 1; if (d < 0) d += 1;
          lum += vu.balayage * Math.pow(1 - d, 6) * 1.1;
        }
        lum += flash * .5;
        if (lum < .02) continue;
        const l = Math.min(1, lum);
        g.strokeStyle = `rgba(255,${90 + 100 * l},${70 + 60 * l},${l})`;
        g.beginPath(); g.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1); g.lineTo(cx + Math.cos(a) * r2, cy + Math.sin(a) * r2); g.stroke();
      }
    }

    // la lueur : couches additives, du rouge profond au cœur blanc doré
    g.save(); g.globalCompositeOperation = 'lighter';
    const ray = Rl * (.25 + .5 * vu.taille) * (1 + syllabe * .12 + micro * .1 + flash * .25);
    const v = Math.min(1.3, vie);
    const l1 = g.createRadialGradient(cx, cy, 0, cx, cy, ray * 1.5);
    l1.addColorStop(0, `rgba(200,10,15,${.55 * v})`); l1.addColorStop(.5, `rgba(140,0,10,${.3 * v})`); l1.addColorStop(1, 'rgba(80,0,0,0)');
    g.fillStyle = l1; g.beginPath(); g.arc(cx, cy, Math.min(Rl, ray * 1.5), 0, 7); g.fill();
    const l2 = g.createRadialGradient(cx, cy, 0, cx, cy, ray * .75);
    l2.addColorStop(0, `rgba(255,70,40,${.9 * v})`); l2.addColorStop(.45, `rgba(240,30,20,${.55 * v})`); l2.addColorStop(1, 'rgba(200,0,0,0)');
    g.fillStyle = l2; g.beginPath(); g.arc(cx, cy, ray * .75, 0, 7); g.fill();
    const rc = ray * (.16 + .06 * syllabe);
    const l3 = g.createRadialGradient(cx, cy, 0, cx, cy, rc * 2);
    l3.addColorStop(0, `rgba(255,248,225,${Math.min(1, .95 * v)})`); l3.addColorStop(.35, `rgba(255,200,120,${.8 * v})`); l3.addColorStop(1, 'rgba(255,90,40,0)');
    g.fillStyle = l3; g.beginPath(); g.arc(cx, cy, rc * 2, 0, 7); g.fill();
    g.restore();

    // reflets sur le verre bombé
    g.save(); g.beginPath(); g.arc(cx, cy, Rl, 0, 7); g.clip();
    const ref = g.createRadialGradient(cx - Rl * .35, cy - Rl * .5, 0, cx - Rl * .35, cy - Rl * .5, Rl * .7);
    ref.addColorStop(0, 'rgba(255,255,255,.13)'); ref.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = ref; g.beginPath(); g.ellipse(cx - Rl * .3, cy - Rl * .48, Rl * .55, Rl * .3, -.5, 0, 7); g.fill();
    g.strokeStyle = 'rgba(255,255,255,.10)'; g.lineWidth = W * .006;
    g.beginPath(); g.arc(cx, cy, Rl * .8, Math.PI * 1.15, Math.PI * 1.42); g.stroke();
    g.fillStyle = 'rgba(255,255,255,.05)';
    g.beginPath(); g.ellipse(cx + Rl * .42, cy + Rl * .55, Rl * .12, Rl * .05, -.6, 0, 7); g.fill();
    g.restore();
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  // ----- Style « classique » : proche de l'œil du film, il bouge très peu, seule la lumière varie -----
  const ECLAT = { repos: .55, ecoute: .95, reflexion: .75, parole: .85, erreur: .4 };
  let eclatC = .55;
  function classique(t) {
    eclatC += (ECLAT[etat] - eclatC) * (reduit ? .2 : .03);   // transitions lentes
    let vie = eclatC + syllabe * .22 + micro * .15 + flash * .15;
    if (!reduit) {
      vie += Math.sin(t / 1000 * 1.1) * .025;                         // respiration à peine visible
      if (etat === 'reflexion') vie += Math.sin(t / 1000 * 2.4) * .14; // lente baisse et remontée
      if (etat === 'erreur' && Math.random() < .08) vie -= .25;        // vacille
      vie += (Math.sin(t / 61) + Math.sin(t / 43)) * .004;
    }
    const lueur = 1.35, reflets = 1.5, anneaux = .7;
    const cx = W / 2, cy = W / 2, R = W * .36, TOUR = Math.PI * 2;
    g.clearRect(0, 0, W, W);
    // halo très doux autour
    const h = g.createRadialGradient(cx, cy, R * .85, cx, cy, R * 1.25);
    h.addColorStop(0, `rgba(255,30,20,${.10 * vie * lueur})`); h.addColorStop(1, 'rgba(255,30,20,0)');
    g.fillStyle = h; g.beginPath(); g.arc(cx, cy, R * 1.25, 0, TOUR); g.fill();
    // collier noir, puis anneau chromé brossé
    g.fillStyle = '#060606'; g.beginPath(); g.arc(cx, cy, R, 0, TOUR); g.fill();
    const rc1 = R * .965, rc2 = R * .86;
    let ch;
    if (g.createConicGradient) {
      ch = g.createConicGradient(-Math.PI * .75, cx, cy);
      for (const [o, c] of [[0,'#f2f2f2'],[.07,'#8a8a8c'],[.16,'#2a2a2c'],[.27,'#bdbdbf'],[.36,'#4a4a4c'],[.5,'#e6e6e8'],[.6,'#3a3a3c'],[.72,'#a8a8aa'],[.84,'#262628'],[.93,'#9a9a9c'],[1,'#f2f2f2']]) ch.addColorStop(o, c);
    } else { ch = g.createLinearGradient(cx - R, cy - R, cx + R, cy + R); ch.addColorStop(0, '#eee'); ch.addColorStop(.5, '#333'); ch.addColorStop(1, '#aaa'); }
    g.save(); g.globalAlpha = .93;
    g.fillStyle = ch; g.beginPath(); g.arc(cx, cy, rc1, 0, TOUR); g.moveTo(cx + rc2, cy); g.arc(cx, cy, rc2, 0, TOUR); g.fill('evenodd'); g.restore();
    g.strokeStyle = 'rgba(0,0,0,.18)'; g.lineWidth = Math.max(.5, W * .0012);
    for (let r = rc2; r < rc1; r += (rc1 - rc2) / 7) { g.beginPath(); g.arc(cx, cy, r, 0, TOUR); g.stroke(); }
    g.fillStyle = '#020202'; g.beginPath(); g.arc(cx, cy, rc2, 0, TOUR); g.fill();
    // verre bombé
    const Rl = R * .82;
    const verre = g.createRadialGradient(cx, cy, 0, cx, cy, Rl);
    verre.addColorStop(0, '#1a0304'); verre.addColorStop(.55, '#0b0102'); verre.addColorStop(1, '#000');
    g.fillStyle = verre; g.beginPath(); g.arc(cx, cy, Rl, 0, TOUR); g.fill();
    // éléments internes de l'objectif : anneaux sombres concentriques
    g.save(); g.beginPath(); g.arc(cx, cy, Rl, 0, TOUR); g.clip();
    for (const [k, a] of [[.86, .5], [.68, .6], [.5, .55], [.34, .45]]) {
      const r = Rl * k;
      g.lineWidth = Rl * .035; g.strokeStyle = `rgba(0,0,0,${.55 * anneaux})`; g.beginPath(); g.arc(cx, cy, r, 0, TOUR); g.stroke();
      g.lineWidth = Math.max(1, Rl * .008); g.strokeStyle = `rgba(255,60,40,${.10 * a * vie * anneaux})`; g.beginPath(); g.arc(cx, cy, r - Rl * .02, 0, TOUR); g.stroke();
    }
    g.restore();
    // la lueur : rouge large, rouge vif, cœur jaune-blanc
    g.save(); g.globalCompositeOperation = 'lighter';
    const L = Math.min(1.4, vie * lueur);
    const l1 = g.createRadialGradient(cx, cy, 0, cx, cy, Rl * .85);
    l1.addColorStop(0, `rgba(230,20,15,${.55 * L})`); l1.addColorStop(.35, `rgba(170,5,10,${.35 * L})`); l1.addColorStop(1, 'rgba(90,0,0,0)');
    g.fillStyle = l1; g.beginPath(); g.arc(cx, cy, Rl * .85, 0, TOUR); g.fill();
    const l2 = g.createRadialGradient(cx, cy, 0, cx, cy, Rl * .32);
    l2.addColorStop(0, `rgba(255,80,40,${.85 * L})`); l2.addColorStop(.5, `rgba(250,30,20,${.5 * L})`); l2.addColorStop(1, 'rgba(220,0,0,0)');
    g.fillStyle = l2; g.beginPath(); g.arc(cx, cy, Rl * .32, 0, TOUR); g.fill();
    const rc = Rl * .075;
    const l3 = g.createRadialGradient(cx, cy, 0, cx, cy, rc);
    l3.addColorStop(0, `rgba(255,250,215,${Math.min(1, L)})`); l3.addColorStop(.4, `rgba(255,215,110,${.85 * L})`); l3.addColorStop(1, 'rgba(255,110,40,0)');
    g.fillStyle = l3; g.beginPath(); g.arc(cx, cy, rc, 0, TOUR); g.fill();
    g.restore();
    // reflets de lampes sur le verre
    g.save(); g.beginPath(); g.arc(cx, cy, Rl, 0, TOUR); g.clip();
    g.lineCap = 'round';
    const arc = (r, a1, a2, larg, alpha) => { g.strokeStyle = `rgba(255,255,255,${alpha * reflets})`; g.lineWidth = larg; g.beginPath(); g.arc(cx, cy, r, a1, a2); g.stroke(); };
    arc(Rl * .80, Math.PI * 1.08, Math.PI * 1.40, Rl * .06, .10);
    arc(Rl * .70, Math.PI * 1.12, Math.PI * 1.33, Rl * .025, .16);
    arc(Rl * .58, Math.PI * 1.18, Math.PI * 1.30, Rl * .018, .10);
    arc(Rl * .80, Math.PI * .12, Math.PI * .30, Rl * .03, .06);
    for (let i = 0; i < 5; i++) {
      const a = Math.PI * (1.16 + i * .045), x = cx + Math.cos(a) * Rl * .64, y = cy + Math.sin(a) * Rl * .64;
      const p = g.createRadialGradient(x, y, 0, x, y, Rl * .035);
      p.addColorStop(0, `rgba(255,255,255,${.45 * reflets})`); p.addColorStop(1, 'rgba(255,255,255,0)');
      g.fillStyle = p; g.beginPath(); g.arc(x, y, Rl * .035, 0, TOUR); g.fill();
    }
    const voile = g.createLinearGradient(cx, cy - Rl, cx, cy);
    voile.addColorStop(0, `rgba(255,255,255,${.06 * reflets})`); voile.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = voile; g.beginPath(); g.ellipse(cx, cy - Rl * .45, Rl * .8, Rl * .5, 0, 0, TOUR); g.fill();
    g.restore();
  }

  return {
    etat(e) {
      if (!ETATS[e]) return;
      if (e === 'ecoute' && etat === 'repos') flash = 1;   // l'œil s'allume
      etat = e;
    },
    get etatCourant() { return etat; },
    son(n = 0) { microCible = Math.max(microCible, Math.max(0, Math.min(1, +n || 0))); },
    impulsion(n = 1) { activite = Math.min(1.5, activite + .25 + Math.min(n, 40) / 40); },
    eclair() { flash = 1; },
    reglages(r = {}) {
      if (r.couronne != null) couronne = !!r.couronne;
      if (r.style) style = r.style === 'moderne' ? 'moderne' : 'classique';
    },
    pause(b) { enPause = !!b; },
    redimensionner: taille,
  };
}

/* ----- Sons ----- */
let reverbs = new WeakMap();
// réverbération douce (2,8 s), une seule par contexte audio et par sortie
function reverb(c, out) {
  let m = reverbs.get(out);
  if (m) return m;
  const n = Math.floor(c.sampleRate * 2.8), ir = c.createBuffer(2, n, c.sampleRate);
  for (let ch = 0; ch < 2; ch++) { const d = ir.getChannelData(ch); for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / n, 3.2); }
  const conv = c.createConvolver(); conv.buffer = ir;
  const retour = c.createGain(); retour.gain.value = .55;
  conv.connect(retour).connect(out);
  reverbs.set(out, conv);
  return conv;
}
function note(c, out, t, f, debut, duree, o = {}) {
  const t0 = t + debut, osc = c.createOscillator(), env = c.createGain();
  osc.type = o.type || 'sine';
  osc.frequency.setValueAtTime(f, t0);
  if (o.vers) osc.frequency.exponentialRampToValueAtTime(o.vers, t0 + (o.glisse || duree));
  env.gain.setValueAtTime(.0001, t0);
  env.gain.exponentialRampToValueAtTime(o.vol ?? .15, t0 + (o.attaque ?? .008));
  env.gain.exponentialRampToValueAtTime(.0001, t0 + duree);
  osc.connect(env); env.connect(out);
  const envoi = c.createGain(); envoi.gain.value = o.reverb ?? .6; env.connect(envoi).connect(reverb(c, out));
  if (o.vibrato) {
    const lfo = c.createOscillator(), p = c.createGain();
    lfo.frequency.value = o.vibrato; p.gain.value = f * .004;
    lfo.connect(p).connect(osc.frequency); lfo.start(t0); lfo.stop(t0 + duree + .05);
  }
  osc.start(t0); osc.stop(t0 + duree + .05);
}
function souffle(c, out, t, debut, duree, de, vers, vol) {
  const t0 = t + debut, n = Math.floor(c.sampleRate * duree), b = c.createBuffer(1, n, c.sampleRate), d = b.getChannelData(0);
  for (let i = 0; i < n; i++) d[i] = Math.random() * 2 - 1;
  const s = c.createBufferSource(); s.buffer = b;
  const f = c.createBiquadFilter(); f.type = 'bandpass'; f.Q.value = 6;
  f.frequency.setValueAtTime(de, t0); f.frequency.exponentialRampToValueAtTime(vers, t0 + duree);
  const env = c.createGain();
  env.gain.setValueAtTime(.0001, t0); env.gain.exponentialRampToValueAtTime(vol, t0 + duree * .6); env.gain.exponentialRampToValueAtTime(.0001, t0 + duree);
  s.connect(f).connect(env); env.connect(out);
  const envoi = c.createGain(); envoi.gain.value = .8; env.connect(envoi).connect(reverb(c, out));
  s.start(t0); s.stop(t0 + duree);
}
const EVEILS = {
  cristal: { nom: 'Lentille de cristal', texte: 'Une note qui monte doucement, puis une pluie d\'étincelles claires.',
    jouer(c, out, t) {
      note(c, out, t, 660, 0, .9, { vers: 1320, glisse: .55, vol: .10, attaque: .12, vibrato: 5 });
      note(c, out, t, 990, .05, .8, { vers: 1980, glisse: .55, vol: .04, attaque: .15, type: 'triangle' });
      [1319, 1661, 1976, 2637, 3322].forEach((f, i) => note(c, out, t, f, .42 + i * .075, 1.1 - i * .1, { vol: .09 - i * .01, reverb: .9 }));
    } },
  aube: { nom: 'Aube rouge', texte: 'Un accord clair qui gonfle avec un souffle, puis une cloche haute.',
    jouer(c, out, t) {
      souffle(c, out, t, 0, 1.0, 700, 5000, .045);
      [659, 988, 1319].forEach((f, i) => note(c, out, t, f, .05 + i * .06, 1.5, { vol: .06, attaque: .55, vibrato: 4.5 }));
      note(c, out, t, 2637, .72, 1.4, { vol: .08, reverb: 1 });
      note(c, out, t, 3951, .74, .9, { vol: .025, reverb: 1 });
    } },
};
window.sonsHal = {
  eveils: EVEILS,
  defaut: 'cristal',
  eveil(nom, c, out, t) { (EVEILS[nom] || EVEILS.cristal).jouer(c, out, t); },
  // deux petites notes qui montent : « je t'écoute » (mot de réveil entendu)
  ecoute(c, out, t) { note(c, out, t, 1175, 0, .22, { vol: .11 }); note(c, out, t, 1568, .09, .35, { vol: .11 }); },
};
window.creerOeil = creerOeil;
})();
