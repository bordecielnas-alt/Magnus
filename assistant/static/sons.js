// Sons d'éveil de l'orbe : joués juste avant qu'il prenne la parole.
// Tous fabriqués à la volée avec Web Audio (aucun fichier). Le son se choisit dans Réglages.
// Chaque son est repéré par son numéro (le même que sur la page de démonstration) : ne pas renuméroter.
(() => {
'use strict';
const enveloppe = (g, t, pic, monte, tient, fin) => {
  g.gain.setValueAtTime(.0001, t);
  g.gain.exponentialRampToValueAtTime(pic, t + monte);
  g.gain.setValueAtTime(pic, t + monte + tient);
  // extinction : d'abord douce et lente, le son s'entend jusqu'au bout
  g.gain.setTargetAtTime(.0001, t + monte + tient, (fin - monte - tient) / 3.2);
  g.gain.setValueAtTime(.0001, t + fin);
};
const bruit = (c, duree, brun) => {
  const n = Math.floor(c.sampleRate * duree), b = c.createBuffer(1, n, c.sampleRate), d = b.getChannelData(0);
  let x = 0;
  for (let i = 0; i < n; i++) { const r = Math.random() * 2 - 1; if (brun) { x = (x + .02 * r) / 1.02; d[i] = x * 3.5; } else d[i] = r; }
  const s = c.createBufferSource(); s.buffer = b; return s;
};
// réverbération de grande salle de pierre, fabriquée avec du bruit qui décroît
const salle = (c, out, duree = 3.5, humide = .55, niveau = 1) => {
  const sortie = c.createGain(); sortie.gain.value = niveau; sortie.connect(out); out = sortie;
  const n = Math.floor(c.sampleRate * duree), ir = c.createBuffer(2, n, c.sampleRate);
  for (let ch = 0; ch < 2; ch++) { const d = ir.getChannelData(ch); for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / n, 2.6); }
  const conv = c.createConvolver(); conv.buffer = ir;
  const entree = c.createGain(), sec = c.createGain(), mouil = c.createGain();
  sec.gain.value = 1 - humide * .5; mouil.gain.value = humide;
  entree.connect(sec).connect(out); entree.connect(conv).connect(mouil).connect(out);
  return entree;
};
const osc = (c, type, f, t, fin, dest, g) => { const o = c.createOscillator(); o.type = type; o.frequency.setValueAtTime(f, t); o.connect(g).connect(dest); o.start(t); o.stop(t + fin + .05); return o; };

// Chaque son dure 5 s : montée lente, tenue, puis extinction douce.
const D = 5;
const SONS = [
  { nom: 'Souffle', duree: D,
    texte: 'Une longue inspiration douce, sans note : un souffle d\'air qui gonfle pendant deux secondes, tient, puis retombe.',
    jouer(c, out, t) {
      const s = bruit(c, D + .1), bp = c.createBiquadFilter(), g = c.createGain();
      bp.type = 'bandpass'; bp.Q.value = .8;
      bp.frequency.setValueAtTime(350, t); bp.frequency.exponentialRampToValueAtTime(1300, t + 2.2); bp.frequency.exponentialRampToValueAtTime(500, t + D);
      enveloppe(g, t, .3, 2.2, .6, D); s.connect(bp).connect(g).connect(out); s.start(t); s.stop(t + D + .1);
    } },
  { nom: 'Bourdon chaud', duree: D,
    texte: 'Un accord grave de synthétiseur (do et sol) dont le timbre s\'ouvre lentement, comme une machine qui se met sous tension.',
    jouer(c, out, t) {
      const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.Q.value = 2;
      lp.frequency.setValueAtTime(110, t); lp.frequency.exponentialRampToValueAtTime(900, t + 2.6); lp.frequency.exponentialRampToValueAtTime(180, t + D);
      lp.connect(out);
      for (const [f, v] of [[65.4, .09], [98, .06], [130.8, .04]]) for (const desaccord of [-4, 4]) {
        const g = c.createGain(); enveloppe(g, t, v, 2.2, .9, D);
        const o = osc(c, 'sawtooth', f, t, D, lp, g); o.detune.value = desaccord;
      }
    } },
  { nom: 'Bol chantant', duree: D,
    texte: 'Un bol de méditation effleuré : un son grave et métallique qui vibre doucement et s\'éteint très longuement.',
    jouer(c, out, t) {
      for (const [f, v, d] of [[164, .16, D], [164 * 2.71, .05, 3.2], [164 * 5.15, .02, 1.6], [166.5, .1, D]]) {
        const g = c.createGain(); enveloppe(g, t, v, .06, .2, d); osc(c, 'sine', f, t, d, out, g);
      }
      const s = bruit(c, .12), bp = c.createBiquadFilter(), g = c.createGain();
      bp.type = 'bandpass'; bp.frequency.value = 900; bp.Q.value = 3; enveloppe(g, t, .05, .01, 0, .12);
      s.connect(bp).connect(g).connect(out); s.start(t); s.stop(t + .15);
    } },
  { nom: 'Mise en route', duree: D,
    texte: 'Un son grave qui monte lentement en hauteur pendant trois secondes, puis se pose et s\'efface : l\'orbe qui s\'allume.',
    jouer(c, out, t) {
      const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = 700; lp.connect(out);
      for (const [type, f0, f1, v] of [['sine', 55, 196, .22], ['triangle', 110, 392, .05]]) {
        const g = c.createGain(); enveloppe(g, t, v, 2.2, 1, D);
        const o = osc(c, type, f0, t, D, lp, g); o.frequency.exponentialRampToValueAtTime(f1, t + 3);
      }
    } },
  { nom: 'Notes lentes', duree: D,
    texte: 'Trois notes basses et rondes, espacées, comme un marimba en bois (la, mi, la), qui résonnent jusqu\'au bout.',
    jouer(c, out, t) {
      for (const [f, dep] of [[110, 0], [164.8, .9], [220, 1.8]]) {
        for (const [mult, v, d] of [[1, .2, D - dep], [4, .025, .4], [2, .05, 1.2]]) {
          const g = c.createGain(); enveloppe(g, t + dep, v, .008, 0, d); osc(c, 'sine', f * mult, t + dep, d, out, g);
        }
      }
    } },
  { nom: 'Vague', duree: D,
    texte: 'Une vague grave qui enfle lentement et se retire, mélange de souffle sourd et d\'une note tenue très basse.',
    jouer(c, out, t) {
      const s = bruit(c, D + .1, true), lp = c.createBiquadFilter(), g = c.createGain();
      lp.type = 'lowpass'; lp.frequency.setValueAtTime(140, t); lp.frequency.exponentialRampToValueAtTime(700, t + 2.5); lp.frequency.exponentialRampToValueAtTime(160, t + D);
      enveloppe(g, t, .35, 2.4, .5, D); s.connect(lp).connect(g).connect(out); s.start(t); s.stop(t + D + .1);
      const g2 = c.createGain(); enveloppe(g2, t, .1, 2.4, .5, D); osc(c, 'sine', 55, t, D, out, g2);
      const g3 = c.createGain(); enveloppe(g3, t, .04, 2.4, .5, D); osc(c, 'sine', 110, t, D, out, g3);
    } },
  // ----- nouvelles variantes, dans l'esprit du bourdon (2) et du bol (3) -----
  { nom: 'Bol grave', duree: D, nouveau: true,
    texte: 'Un bol plus grand et plus grave, frappé une fois au feutre : la note est plus basse et le battement plus lent.',
    jouer(c, out, t) {
      for (const [f, v, d] of [[110, .2, D], [111.2, .12, D], [110 * 2.76, .05, 3.6], [110 * 5.4, .015, 1.8]]) {
        const g = c.createGain(); enveloppe(g, t, v, .09, .3, d); osc(c, 'sine', f, t, d, out, g);
      }
      const s = bruit(c, .15), bp = c.createBiquadFilter(), g = c.createGain();
      bp.type = 'bandpass'; bp.frequency.value = 500; bp.Q.value = 2; enveloppe(g, t, .04, .02, 0, .15);
      s.connect(bp).connect(g).connect(out); s.start(t); s.stop(t + .18);
    } },
  { nom: 'Bol frotté', duree: D, nouveau: true,
    texte: 'Le bol n\'est pas frappé mais frotté : le son naît du silence, enfle pendant deux secondes en chantant, puis s\'éteint.',
    jouer(c, out, t) {
      for (const [f, v] of [[146.8, .16], [148.3, .1], [146.8 * 2.71, .035], [146.8 * 5.2, .01]]) {
        const g = c.createGain(); enveloppe(g, t, v, 2, .6, D); osc(c, 'sine', f, t, D, out, g);
      }
    } },
  { nom: 'Bourdon et bol', duree: D, nouveau: true,
    texte: 'Le bourdon chaud qui monte, et le bol qui sonne par-dessus au moment où il s\'ouvre. Un mélange des deux.',
    jouer(c, out, t) {
      const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.Q.value = 1.5;
      lp.frequency.setValueAtTime(100, t); lp.frequency.exponentialRampToValueAtTime(650, t + 2.2); lp.frequency.exponentialRampToValueAtTime(160, t + D);
      lp.connect(out);
      for (const [f, v] of [[65.4, .07], [98, .045]]) for (const de of [-5, 5]) {
        const g = c.createGain(); enveloppe(g, t, v, 2, .8, D); const o = osc(c, 'sawtooth', f, t, D, lp, g); o.detune.value = de;
      }
      const t2 = t + 1.4;
      for (const [f, v, d] of [[196, .1, D - 1.4], [198, .06, D - 1.4], [196 * 2.71, .025, 2.4]]) {
        const g = c.createGain(); enveloppe(g, t2, v, .06, .2, d); osc(c, 'sine', f, t2, d, out, g);
      }
    } },
  { nom: 'Bourdon qui respire', duree: D, nouveau: true,
    texte: 'Le bourdon chaud, plus doux et plus rond, avec un timbre qui s\'ouvre et se ferme lentement, comme une respiration.',
    jouer(c, out, t) {
      const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.Q.value = 3; lp.frequency.value = 380; lp.connect(out);
      const lfo = c.createOscillator(), lg = c.createGain(); lfo.frequency.value = .7; lg.gain.value = 260;
      lfo.connect(lg).connect(lp.frequency); lfo.start(t); lfo.stop(t + D);
      for (const [f, v] of [[55, .1], [82.4, .06], [110, .04]]) for (const de of [-6, 6]) {
        const g = c.createGain(); enveloppe(g, t, v, 1.8, 1, D); const o = osc(c, 'sawtooth', f, t, D, lp, g); o.detune.value = de;
      }
    } },
  { nom: 'Orgue lointain', duree: D, nouveau: true,
    texte: 'Un accord grave et pur, comme un orgue entendu de loin dans une grande salle : sans attaque, très enveloppant.',
    jouer(c, out, t) {
      const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = 1200; lp.connect(out);
      for (const [f, v] of [[65.4, .1], [130.8, .07], [196, .05], [261.6, .025]]) for (const de of [-3, 3]) {
        const g = c.createGain(); enveloppe(g, t, v, 1.6, 1.2, D); const o = osc(c, 'triangle', f, t, D, lp, g); o.detune.value = de;
      }
    } },
  { nom: 'Gong doux', duree: D, nouveau: true,
    texte: 'Un petit gong grave effleuré : une note sourde qui s\'élargit et vibre longtemps, plus sombre que le bol.',
    jouer(c, out, t) {
      for (const [f, v, d, mont] of [[73.4, .18, D, .15], [73.4 * 1.48, .07, 4, .5], [73.4 * 2.09, .05, 3.6, .8], [73.4 * 2.8, .025, 2.6, 1.1]]) {
        const g = c.createGain(); enveloppe(g, t, v, mont, .3, d); osc(c, 'sine', f, t, d, out, g);
      }
      const s = bruit(c, D, true), lp = c.createBiquadFilter(), g = c.createGain();
      lp.type = 'lowpass'; lp.frequency.value = 300; enveloppe(g, t, .08, 1, .5, D);
      s.connect(lp).connect(g).connect(out); s.start(t); s.stop(t + D);
    } },
  // ----- troisième série, toujours autour du bourdon et du bol -----
  { nom: 'Deux bols', duree: D, nouveau: true,
    texte: 'Deux bols accordés à la quinte, effleurés l\'un après l\'autre : leurs vibrations se mêlent et battent doucement.',
    jouer(c, out, t) {
      for (const [f0, dep] of [[130.8, 0], [196, .8]]) for (const [m, v, d] of [[1, .14, D - dep], [1.008, .08, D - dep], [2.71, .03, 2.6], [5.2, .01, 1.2]]) {
        const g = c.createGain(); enveloppe(g, t + dep, v, .07, .2, d); osc(c, 'sine', f0 * m, t + dep, d, out, g);
      }
    } },
  { nom: 'Violoncelle', duree: D, nouveau: true,
    texte: 'Une note grave tenue à l\'archet (ré), avec un léger vibrato qui arrive peu à peu. Chaud et humain.',
    jouer(c, out, t) {
      const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.Q.value = 1;
      lp.frequency.setValueAtTime(250, t); lp.frequency.linearRampToValueAtTime(1100, t + 1.8); lp.connect(out);
      const vib = c.createOscillator(), vg = c.createGain(); vib.frequency.value = 5;
      vg.gain.setValueAtTime(0, t); vg.gain.linearRampToValueAtTime(9, t + 2.2); vib.connect(vg); vib.start(t); vib.stop(t + D);
      for (const [f, v] of [[73.4, .2], [146.8, .05]]) {
        const g = c.createGain(); enveloppe(g, t, v, 1.4, 1.4, D); const o = osc(c, 'sawtooth', f, t, D, lp, g); vg.connect(o.detune);
      }
    } },
  { nom: 'Nappe sombre', duree: D, nouveau: true,
    texte: 'Un accord mineur très grave (la mineur) en nappe de synthétiseur, qui s\'éclaire puis s\'assombrit. Plus mystérieux.',
    jouer(c, out, t) {
      const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.Q.value = 1.2;
      lp.frequency.setValueAtTime(150, t); lp.frequency.exponentialRampToValueAtTime(800, t + 2.4); lp.frequency.exponentialRampToValueAtTime(150, t + D);
      lp.connect(out);
      for (const [f, v] of [[55, .09], [110, .05], [130.8, .04], [164.8, .035]]) for (const de of [-8, 0, 8]) {
        const g = c.createGain(); enveloppe(g, t, v * 1.1, 2, .8, D); const o = osc(c, 'triangle', f, t, D, lp, g); o.detune.value = de;
      }
    } },
  { nom: 'Bol d\'eau', duree: D, nouveau: true,
    texte: 'Un bol rempli d\'un peu d\'eau : la note grave ondule lentement, comme si elle flottait.',
    jouer(c, out, t) {
      const ond = c.createOscillator(), og = c.createGain(); ond.frequency.value = 1.6; og.gain.value = 14; ond.connect(og); ond.start(t); ond.stop(t + D);
      for (const [f, v, d] of [[155.6, .17, D], [157, .08, D], [155.6 * 2.7, .035, 3], [155.6 * 5.1, .012, 1.4]]) {
        const g = c.createGain(); enveloppe(g, t, v, .08, .3, d); const o = osc(c, 'sine', f, t, d, out, g); og.connect(o.detune);
      }
    } },
  { nom: 'Harmoniques', duree: D, nouveau: true,
    texte: 'Un bourdon grave sur lequel les harmoniques apparaissent une à une, comme un chant diphonique qui s\'élève.',
    jouer(c, out, t) {
      const f = 65.4;
      const g0 = c.createGain(); enveloppe(g0, t, .16, 1.2, 1.6, D); osc(c, 'sine', f, t, D, out, g0);
      const g1 = c.createGain(); enveloppe(g1, t, .06, 1.2, 1.6, D); osc(c, 'sine', f * 2, t, D, out, g1);
      [3, 4, 5, 6, 8].forEach((h, i) => {
        const dep = t + .8 + i * .45, g = c.createGain(); enveloppe(g, dep, .028 / (1 + i * .3), .5, .4, D - (dep - t));
        osc(c, 'sine', f * h, dep, D - (dep - t), out, g);
      });
    } },
  { nom: 'Souffle et bol', duree: D, nouveau: true,
    texte: 'Un souffle grave qui monte, et le bol frotté qui chante dedans. Doux et ample.',
    jouer(c, out, t) {
      const s = bruit(c, D, true), lp = c.createBiquadFilter(), g = c.createGain();
      lp.type = 'lowpass'; lp.frequency.setValueAtTime(140, t); lp.frequency.exponentialRampToValueAtTime(520, t + 2.2); lp.frequency.exponentialRampToValueAtTime(140, t + D);
      enveloppe(g, t, .22, 2, .6, D); s.connect(lp).connect(g).connect(out); s.start(t); s.stop(t + D);
      for (const [f, v] of [[174.6, .1], [176, .06], [174.6 * 2.71, .02]]) {
        const g2 = c.createGain(); enveloppe(g2, t + .4, v, 1.8, .6, D - .4); osc(c, 'sine', f, t + .4, D - .4, out, g2);
      }
    } },
  { nom: 'Battement lent', duree: D, nouveau: true,
    texte: 'Deux notes très graves presque identiques : leur battement fait pulser le son lentement, comme un cœur qui se réveille.',
    jouer(c, out, t) {
      for (const [f, v] of [[58, .15], [58.9, .15], [116, .04], [117.8, .04]]) {
        const g = c.createGain(); enveloppe(g, t, v, 1.5, 1.4, D); osc(c, 'sine', f, t, D, out, g);
      }
    } },
  { nom: 'Bourdon montant', duree: D, nouveau: true,
    texte: 'Le bourdon chaud qui monte lentement d\'une quinte (de do à sol) en s\'ouvrant, puis se pose. Plus solennel.',
    jouer(c, out, t) {
      const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.Q.value = 1.8;
      lp.frequency.setValueAtTime(110, t); lp.frequency.exponentialRampToValueAtTime(800, t + 2.8); lp.frequency.exponentialRampToValueAtTime(200, t + D);
      lp.connect(out);
      for (const [f, v] of [[65.4, .09], [130.8, .04]]) for (const de of [-5, 5]) {
        const g = c.createGain(); enveloppe(g, t, v, 2, 1, D);
        const o = osc(c, 'sawtooth', f, t, D, lp, g); o.detune.value = de; o.frequency.setValueAtTime(f, t + .6); o.frequency.exponentialRampToValueAtTime(f * 1.498, t + 3);
      }
    } },
  // ----- quatrième série : plus lumineuse et rassurante (accords majeurs, registre un peu plus haut) -----
  { nom: 'Bourdon majeur', duree: D, nouveau: true,
    texte: 'Le bourdon chaud, mais sur un accord majeur (do, mi, sol) : il s\'ouvre comme un sourire au lieu d\'un grondement.',
    jouer(c, out, t) {
      const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.Q.value = .9;
      lp.frequency.setValueAtTime(220, t); lp.frequency.exponentialRampToValueAtTime(1500, t + 2.2); lp.frequency.exponentialRampToValueAtTime(450, t + D);
      lp.connect(out);
      for (const [f, v] of [[130.8, .07], [164.8, .05], [196, .05], [261.6, .03]]) for (const de of [-5, 5]) {
        const g = c.createGain(); enveloppe(g, t, v, 1.6, 1, D); const o = osc(c, 'sawtooth', f, t, D, lp, g); o.detune.value = de;
      }
    } },
  { nom: 'Bol clair', duree: D, nouveau: true,
    texte: 'Un petit bol plus clair (do du milieu), effleuré doucement : apaisant, sans battement sombre.',
    jouer(c, out, t) {
      for (const [f, v, d] of [[261.6, .16, D], [262.2, .05, D], [261.6 * 2.72, .03, 2.6], [261.6 * 5.2, .008, 1.2]]) {
        const g = c.createGain(); enveloppe(g, t, v, .1, .3, d); osc(c, 'sine', f, t, d, out, g);
      }
    } },
  { nom: 'Trois bols en accord', duree: D, nouveau: true,
    texte: 'Trois bols accordés en accord majeur (do, mi, sol), effleurés l\'un après l\'autre. Doux et harmonieux.',
    jouer(c, out, t) {
      [[130.8, 0], [164.8, .6], [196, 1.2]].forEach(([f, dep]) => {
        for (const [m, v, d] of [[1, .11, D - dep], [1.004, .04, D - dep], [2.72, .02, 2.2]]) {
          const g = c.createGain(); enveloppe(g, t + dep, v, .08, .2, d); osc(c, 'sine', f * m, t + dep, d, out, g);
        }
      });
    } },
  { nom: 'Lever de soleil', duree: D, nouveau: true,
    texte: 'Une nappe douce qui part du grave et s\'éclaire peu à peu jusqu\'à un accord majeur lumineux.',
    jouer(c, out, t) {
      const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.Q.value = .7;
      lp.frequency.setValueAtTime(180, t); lp.frequency.exponentialRampToValueAtTime(2600, t + 3); lp.connect(out);
      [[98, 0, .08], [130.8, .3, .07], [164.8, .9, .05], [196, 1.4, .045], [261.6, 1.9, .03], [329.6, 2.4, .02]].forEach(([f, dep, v]) => {
        for (const de of [-4, 4]) { const g = c.createGain(); enveloppe(g, t + dep, v, 1.4, .6, D - dep); const o = osc(c, 'triangle', f, t + dep, D - dep, lp, g); o.detune.value = de; }
      });
    } },
  { nom: 'Accord tiède', duree: D, nouveau: true,
    texte: 'Un accord rond et feutré, comme un piano électrique qu\'on tiendrait longtemps. Rassurant et familier.',
    jouer(c, out, t) {
      for (const [f, v] of [[130.8, .1], [196, .07], [261.6, .06], [329.6, .045]]) {
        const g = c.createGain(); enveloppe(g, t, v, .9, 1.4, D); osc(c, 'sine', f, t, D, out, g);
        const g2 = c.createGain(); enveloppe(g2, t, v * .2, .05, .1, 1.2); osc(c, 'sine', f * 3, t, 1.2, out, g2);
      }
    } },
  { nom: 'Bourdon qui se résout', duree: D, nouveau: true,
    texte: 'Le bourdon commence en suspens puis se pose sur un accord majeur, comme une question qui trouve sa réponse.',
    jouer(c, out, t) {
      const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.Q.value = 1;
      lp.frequency.setValueAtTime(200, t); lp.frequency.exponentialRampToValueAtTime(1300, t + 2.4); lp.frequency.exponentialRampToValueAtTime(400, t + D);
      lp.connect(out);
      for (const [f, v] of [[130.8, .07], [196, .05], [261.6, .03]]) for (const de of [-5, 5]) {
        const g = c.createGain(); enveloppe(g, t, v, 1.4, 1, D); const o = osc(c, 'sawtooth', f, t, D, lp, g); o.detune.value = de;
      }
      // la quarte (fa) glisse vers la tierce (mi)
      for (const de of [-5, 5]) {
        const g = c.createGain(); enveloppe(g, t, .05, 1.4, 1, D);
        const o = osc(c, 'sawtooth', 174.6, t, D, lp, g); o.detune.value = de;
        o.frequency.setValueAtTime(174.6, t + 1.8); o.frequency.exponentialRampToValueAtTime(164.8, t + 2.5);
      }
    } },
  { nom: 'Bois chaud', duree: D, nouveau: true,
    texte: 'Un arpège lent de marimba grave en accord majeur (do, mi, sol, do), chaleureux, qui résonne jusqu\'au bout.',
    jouer(c, out, t) {
      [[130.8, 0], [164.8, .5], [196, 1], [261.6, 1.5]].forEach(([f, dep]) => {
        for (const [m, v, d] of [[1, .15, D - dep], [4, .02, .3], [2, .04, 1]]) {
          const g = c.createGain(); enveloppe(g, t + dep, v, .01, 0, d); osc(c, 'sine', f * m, t + dep, d, out, g);
        }
      });
    } },
  { nom: 'Souffle tiède', duree: D, nouveau: true,
    texte: 'Un souffle léger avec un accord majeur très doux en dessous : une inspiration calme avant de parler.',
    jouer(c, out, t) {
      const s = bruit(c, D), bp = c.createBiquadFilter(), g = c.createGain();
      bp.type = 'bandpass'; bp.Q.value = .7; bp.frequency.setValueAtTime(500, t); bp.frequency.exponentialRampToValueAtTime(1600, t + 2.4); bp.frequency.exponentialRampToValueAtTime(700, t + D);
      enveloppe(g, t, .12, 2.2, .5, D); s.connect(bp).connect(g).connect(out); s.start(t); s.stop(t + D);
      for (const [f, v] of [[130.8, .07], [164.8, .05], [196, .05]]) {
        const g2 = c.createGain(); enveloppe(g2, t, v, 2.2, .6, D); osc(c, 'sine', f, t, D, out, g2);
      }
    } },
  // ----- série ciblée : grave, apparition progressive depuis le silence, sans attaque, consonant -----
  { nom: 'Apparition', duree: D, cible: true,
    texte: 'Une seule note grave (do) qui sort du silence très lentement, s\'arrondit, puis s\'efface. Le plus simple.',
    jouer(c, out, t) {
      const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.Q.value = .5;
      lp.frequency.setValueAtTime(140, t); lp.frequency.exponentialRampToValueAtTime(700, t + 3); lp.connect(out);
      for (const [type, f, v] of [['sine', 65.4, .24], ['triangle', 130.8, .08], ['sine', 196.2, .025]]) {
        const g = c.createGain(); enveloppe(g, t, v, 2.8, .6, D); osc(c, type, f, t, D, lp, g);
      }
    } },
  { nom: 'Note qui s\'éclaire', duree: D, cible: true,
    texte: 'La note grave apparaît seule, puis ses harmoniques douces (octave, quinte, tierce) s\'allument une à une au-dessus.',
    jouer(c, out, t) {
      [[65.4, 0, .22], [130.8, .7, .07], [196.2, 1.3, .045], [261.6, 1.9, .03], [327, 2.4, .02]].forEach(([f, dep, v]) => {
        const g = c.createGain(); enveloppe(g, t + dep, v, 1.8, .4, D - dep); osc(c, 'sine', f, t + dep, D - dep, out, g);
      });
    } },
  { nom: 'Brume grave', duree: D, cible: true,
    texte: 'Un accord majeur grave, très doux et voilé, qui monte comme une brume avec un souffle sourd en dessous.',
    jouer(c, out, t) {
      const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.Q.value = .6;
      lp.frequency.setValueAtTime(120, t); lp.frequency.exponentialRampToValueAtTime(600, t + 3); lp.frequency.exponentialRampToValueAtTime(250, t + D);
      lp.connect(out);
      for (const [f, v] of [[65.4, .1], [98, .06], [130.8, .05], [164.8, .03]]) for (const de of [-6, 6]) {
        const g = c.createGain(); enveloppe(g, t, v, 2.6, .6, D); const o = osc(c, 'triangle', f, t, D, lp, g); o.detune.value = de;
      }
      const s = bruit(c, D, true), lp2 = c.createBiquadFilter(), g = c.createGain();
      lp2.type = 'lowpass'; lp2.frequency.value = 260; enveloppe(g, t, .12, 2.6, .6, D);
      s.connect(lp2).connect(g).connect(out); s.start(t); s.stop(t + D);
    } },
  { nom: 'Bol qui apparaît', duree: D, cible: true,
    texte: 'Un grand bol grave frotté si doucement qu\'on ne l\'entend pas commencer : il apparaît, chante, puis se retire.',
    jouer(c, out, t) {
      for (const [f, v] of [[98, .2], [98.5, .07], [98 * 2.72, .035], [98 * 5.2, .008]]) {
        const g = c.createGain(); enveloppe(g, t, v, 2.6, .8, D); osc(c, 'sine', f, t, D, out, g);
      }
    } },
  { nom: 'Accord qui se lève', duree: D, cible: true,
    texte: 'Un accord majeur grave qui se construit doucement de bas en haut (do, sol, do, mi), chaque note apparaissant en fondu.',
    jouer(c, out, t) {
      const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = 900; lp.connect(out);
      [[65.4, 0, .16], [98, .7, .08], [130.8, 1.4, .06], [164.8, 2.1, .045]].forEach(([f, dep, v]) => {
        for (const de of [-3, 3]) { const g = c.createGain(); enveloppe(g, t + dep, v, 1.5, .4, D - dep); const o = osc(c, 'triangle', f, t + dep, D - dep, lp, g); o.detune.value = de; }
      });
    } },
  { nom: 'Lueur', duree: D, cible: true,
    texte: 'Un bourdon grave et rond, sans grésillement, qui gonfle lentement comme une lumière qui s\'allume, avec une quinte qui le rejoint.',
    jouer(c, out, t) {
      const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.Q.value = .8;
      lp.frequency.setValueAtTime(110, t); lp.frequency.exponentialRampToValueAtTime(520, t + 3.2); lp.frequency.exponentialRampToValueAtTime(220, t + D);
      lp.connect(out);
      for (const [f, v, dep] of [[55, .18, 0], [110, .07, 0], [82.4, .08, 1.2], [164.8, .03, 1.2]]) for (const de of [-4, 4]) {
        const g = c.createGain(); enveloppe(g, t + dep, v * .6, 2.4 - dep * .5, .5, D - dep); const o = osc(c, 'triangle', f, t + dep, D - dep, lp, g); o.detune.value = de;
      }
    } },
  // ----- apparition grave qui finit plus aiguë -----
  { nom: 'Montée douce', duree: D, groupe: 'monte',
    texte: 'Une note grave sort du silence et glisse lentement d\'une quinte vers le haut (de do à sol) en s\'éclaircissant.',
    jouer(c, out, t) {
      const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.Q.value = .5;
      lp.frequency.setValueAtTime(150, t); lp.frequency.exponentialRampToValueAtTime(1100, t + 3.4); lp.connect(out);
      for (const [type, m, v] of [['sine', 1, .22], ['triangle', 2, .07], ['sine', 3, .02]]) {
        const g = c.createGain(); enveloppe(g, t, v, 2.6, .8, D);
        const o = osc(c, type, 65.4 * m, t, D, lp, g); o.frequency.setValueAtTime(65.4 * m, t + .4); o.frequency.exponentialRampToValueAtTime(98 * m, t + 3.4);
      }
    } },
  { nom: 'Note qui s\'envole', duree: D, groupe: 'monte',
    texte: 'Une note grave apparaît, puis une seconde note naît une octave plus haut et prend le relais pendant que la grave s\'efface.',
    jouer(c, out, t) {
      const g1 = c.createGain(); enveloppe(g1, t, .22, 1.8, .3, 3.6); osc(c, 'sine', 65.4, t, 3.6, out, g1);
      const g2 = c.createGain(); enveloppe(g2, t, .05, 1.8, .3, 3.6); osc(c, 'triangle', 130.8, t, 3.6, out, g2);
      const g3 = c.createGain(); enveloppe(g3, t + 1.6, .13, 1.6, .6, D - 1.6); osc(c, 'sine', 130.8, t + 1.6, D - 1.6, out, g3);
      const g4 = c.createGain(); enveloppe(g4, t + 2.2, .06, 1.4, .4, D - 2.2); osc(c, 'sine', 196, t + 2.2, D - 2.2, out, g4);
    } },
  { nom: 'Accord qui s\'élève', duree: D, groupe: 'monte',
    texte: 'L\'accord majeur se construit de bas en haut, chaque note un peu plus aiguë que la précédente, et finit sur une note claire.',
    jouer(c, out, t) {
      const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.setValueAtTime(500, t); lp.frequency.exponentialRampToValueAtTime(2200, t + 3.4); lp.connect(out);
      [[65.4, 0, .15], [98, .6, .08], [130.8, 1.2, .06], [164.8, 1.8, .045], [196, 2.4, .04], [261.6, 3, .03]].forEach(([f, dep, v]) => {
        const g = c.createGain(); enveloppe(g, t + dep, v, 1.2, .3, D - dep); osc(c, 'triangle', f, t + dep, D - dep, lp, g);
      });
    } },
  { nom: 'Bol qui monte', duree: D, groupe: 'monte',
    texte: 'Le grand bol grave apparaît, et un deuxième bol, plus petit et plus aigu, se met à chanter par-dessus.',
    jouer(c, out, t) {
      for (const [f, v] of [[98, .17], [98.5, .06], [98 * 2.72, .03]]) { const g = c.createGain(); enveloppe(g, t, v, 2.2, .4, D); osc(c, 'sine', f, t, D, out, g); }
      for (const [f, v] of [[196, .09], [197, .04], [196 * 2.72, .015]]) { const g = c.createGain(); enveloppe(g, t + 1.8, v, 1.6, .5, D - 1.8); osc(c, 'sine', f, t + 1.8, D - 1.8, out, g); }
    } },
  // ----- hors du cadre -----
  { nom: 'Chœur', duree: D, groupe: 'hors',
    texte: 'Un « ah » de chœur grave, sans paroles, qui s\'ouvre et monte doucement, comme des voix qui se réveillent.',
    jouer(c, out, t) {
      const voix = c.createGain(); voix.gain.value = 1.7;
      const formants = [[700, .9], [1150, .5], [2600, .2]].map(([f, v]) => { const b = c.createBiquadFilter(); b.type = 'bandpass'; b.frequency.value = f; b.Q.value = 8; const g = c.createGain(); g.gain.value = v; voix.connect(b).connect(g).connect(out); return b; });
      formants[0].frequency.setValueAtTime(400, t); formants[0].frequency.linearRampToValueAtTime(750, t + 2.6);
      for (const [f, v] of [[98, .3], [146.8, .2], [196, .14]]) for (const de of [-9, 0, 9]) {
        const g = c.createGain(); enveloppe(g, t, v, 2.4, .7, D);
        const o = osc(c, 'sawtooth', f, t, D, voix, g); o.detune.value = de; o.frequency.setValueAtTime(f, t + .5); o.frequency.exponentialRampToValueAtTime(f * 1.122, t + 3.2);
      }
    } },
  { nom: 'Bouteille', duree: D, groupe: 'hors',
    texte: 'Comme souffler doucement dans une grande bouteille : un son d\'air qui prend une note grave, puis monte à mesure qu\'elle se « remplit ».',
    jouer(c, out, t) {
      const s = bruit(c, D), bp = c.createBiquadFilter(), g = c.createGain();
      bp.type = 'bandpass'; bp.Q.value = 30; bp.frequency.setValueAtTime(110, t); bp.frequency.exponentialRampToValueAtTime(260, t + 4);
      enveloppe(g, t, 4.5, 2, .8, D); s.connect(bp).connect(g).connect(out); s.start(t); s.stop(t + D);
      const o2 = c.createGain(); enveloppe(o2, t, .1, 2.2, .6, D);
      const o = osc(c, 'sine', 110, t, D, out, o2); o.frequency.exponentialRampToValueAtTime(260, t + 4);
    } },
  { nom: 'Verre chantant', duree: D, groupe: 'hors',
    texte: 'Un doigt mouillé sur le bord d\'un verre, mais en grave : un son pur et cristallin qui apparaît et monte d\'un ton.',
    jouer(c, out, t) {
      for (const [m, v, i] of [[1, .22, 1.5], [2, .05, .6]]) {
        const car = c.createOscillator(), mod = c.createOscillator(), mg = c.createGain(), g = c.createGain();
        car.frequency.setValueAtTime(130.8 * m, t); car.frequency.exponentialRampToValueAtTime(146.8 * m, t + 3.5);
        mod.frequency.setValueAtTime(130.8 * m * 2.0, t); mod.frequency.exponentialRampToValueAtTime(146.8 * m * 2.0, t + 3.5);
        mg.gain.setValueAtTime(0, t); mg.gain.linearRampToValueAtTime(130.8 * i, t + 2.5);
        mod.connect(mg).connect(car.frequency); enveloppe(g, t, v, 2.4, .6, D); car.connect(g).connect(out);
        car.start(t); mod.start(t); car.stop(t + D + .05); mod.stop(t + D + .05);
      }
    } },
  { nom: 'Bande à l\'envers', duree: D, groupe: 'hors',
    texte: 'Un accord de piano joué à l\'envers : il enfle du silence jusqu\'à un sommet, puis se pose sur une note claire qui s\'efface.',
    jouer(c, out, t) {
      for (const f of [65.4, 98, 130.8, 164.8]) for (const [m, v] of [[1, .07], [2, .025], [3, .01]]) {
        const g = c.createGain(); g.gain.setValueAtTime(.0001, t); g.gain.exponentialRampToValueAtTime(v, t + 3.2); g.gain.linearRampToValueAtTime(.0001, t + 3.45);
        osc(c, 'sine', f * m, t, 3.5, out, g);
      }
      const g = c.createGain(); enveloppe(g, t + 3.2, .12, .03, .1, D - 3.2); osc(c, 'sine', 261.6, t + 3.2, D - 3.2, out, g);
    } },
  { nom: 'Escalier infini', duree: D, groupe: 'hors',
    texte: 'Une illusion sonore qui semble monter sans fin, très douce et grave : l\'impression que quelque chose s\'élève vers toi.',
    jouer(c, out, t) {
      const base = 32.7, centre = Math.log2(130), larg = 2.2;
      for (let k = 0; k < 6; k++) {
        const o = c.createOscillator(), g = c.createGain(), g2 = c.createGain(); o.type = 'sine';
        const f0 = base * 2 ** k;
        o.frequency.setValueAtTime(f0, t); o.frequency.exponentialRampToValueAtTime(f0 * 2, t + D);
        const pts = 40;
        for (let i = 0; i <= pts; i++) {
          const tt = t + D * i / pts, f = f0 * 2 ** (i / pts);
          g.gain.setValueAtTime(.1 * Math.exp(-((Math.log2(f) - centre) ** 2) / (2 * larg * larg / 4)), tt);
        }
        enveloppe(g2, t, 1, 1.8, 1.6, D); o.connect(g).connect(g2).connect(out); o.start(t); o.stop(t + D + .05);
      }
    } },
  { nom: 'Gouttes graves', duree: D, groupe: 'hors',
    texte: 'Quelques gouttes rondes et graves qui tombent dans l\'eau, de plus en plus aiguës, sur un fond doux qui apparaît.',
    jouer(c, out, t) {
      const g0 = c.createGain(); enveloppe(g0, t, .12, 2.2, .6, D); osc(c, 'sine', 65.4, t, D, out, g0);
      [[98, .4], [110, 1.2], [130.8, 1.9], [146.8, 2.5], [164.8, 3], [196, 3.4]].forEach(([f, dep]) => {
        const o = c.createOscillator(), g = c.createGain(); o.type = 'sine';
        o.frequency.setValueAtTime(f * .7, t + dep); o.frequency.exponentialRampToValueAtTime(f * 1.25, t + dep + .12);
        g.gain.setValueAtTime(.0001, t + dep); g.gain.exponentialRampToValueAtTime(.18, t + dep + .02); g.gain.exponentialRampToValueAtTime(.0001, t + dep + .7);
        o.connect(g).connect(out); o.start(t + dep); o.stop(t + dep + .75);
      });
    } },
  // ----- magie : esprit fantasy nordique, sons originaux -----
  { nom: 'Sort qui se charge', duree: D, groupe: 'magie',
    texte: 'Une énergie grave qui tourbillonne dans les mains et monte en puissance, avec un souffle qui tourne autour.',
    jouer(c, out, t) {
      const r = salle(c, out, 3, .5, 1.6);
      const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.Q.value = 6;
      lp.frequency.setValueAtTime(150, t); lp.frequency.exponentialRampToValueAtTime(1400, t + 3.6); lp.connect(r);
      const tour = c.createOscillator(), tg = c.createGain(); tour.frequency.setValueAtTime(1.5, t); tour.frequency.linearRampToValueAtTime(6, t + 3.6);
      tg.gain.value = 180; tour.connect(tg).connect(lp.frequency); tour.start(t); tour.stop(t + D);
      for (const [f, v] of [[55, .08], [82.4, .05], [110, .04]]) for (const de of [-10, 10]) {
        const g = c.createGain(); enveloppe(g, t, v, 3, .3, D); const o = osc(c, 'sawtooth', f, t, D, lp, g); o.detune.value = de;
        o.frequency.setValueAtTime(f, t + .5); o.frequency.exponentialRampToValueAtTime(f * 1.5, t + 3.6);
      }
      const s = bruit(c, D), bp = c.createBiquadFilter(), g = c.createGain();
      bp.type = 'bandpass'; bp.Q.value = 4; bp.frequency.setValueAtTime(300, t); bp.frequency.exponentialRampToValueAtTime(2000, t + 3.6);
      enveloppe(g, t, .1, 3, .3, D); s.connect(bp).connect(g).connect(r); s.start(t); s.stop(t + D);
    } },
  { nom: 'Salle des runes', duree: D, groupe: 'magie',
    texte: 'Des pierres gravées qui s\'éveillent dans une grande salle : une résonance grave, profonde, qui s\'allume et se répercute.',
    jouer(c, out, t) {
      const r = salle(c, out, 4.5, .75);
      [[65.4, 0, .16], [98, .5, .09], [130.8, 1.1, .06], [196, 1.8, .035]].forEach(([f, dep, v]) => {
        for (const [m, vv] of [[1, v], [2.01, v * .25], [3.02, v * .1]]) {
          const g = c.createGain(); enveloppe(g, t + dep, vv, 1.4, .4, D - dep); osc(c, 'sine', f * m, t + dep, D - dep, r, g);
        }
      });
    } },
  { nom: 'Chant du nord', duree: D, groupe: 'magie',
    texte: 'Des voix graves lointaines sur une quinte ouverte, comme un chœur dans une montagne enneigée, avec un vent léger.',
    jouer(c, out, t) {
      const r = salle(c, out, 4, .7, 1.4);
      const voix = c.createGain(); voix.gain.value = 1.8;
      for (const [f, v] of [[600, .9], [1040, .45], [2500, .15]]) { const b = c.createBiquadFilter(); b.type = 'bandpass'; b.frequency.value = f; b.Q.value = 9; const g = c.createGain(); g.gain.value = v; voix.connect(b).connect(g).connect(r); }
      for (const [f, v] of [[73.4, .3], [110, .22], [146.8, .14]]) for (const de of [-12, 0, 12]) {
        const g = c.createGain(); enveloppe(g, t, v, 2.6, .8, D); const o = osc(c, 'sawtooth', f, t, D, voix, g); o.detune.value = de;
      }
      const s = bruit(c, D), hp = c.createBiquadFilter(), g = c.createGain();
      hp.type = 'bandpass'; hp.frequency.setValueAtTime(500, t); hp.frequency.exponentialRampToValueAtTime(1200, t + 3); hp.Q.value = .8;
      enveloppe(g, t, .05, 2.5, .8, D); s.connect(hp).connect(g).connect(r); s.start(t); s.stop(t + D);
    } },
  { nom: 'Givre', duree: D, groupe: 'magie',
    texte: 'Un bourdon grave et froid sur lequel se forme lentement une brume de cristal, douce et lointaine, pas de tintement.',
    jouer(c, out, t) {
      const r = salle(c, out, 4, .7, 2);
      const g0 = c.createGain(); enveloppe(g0, t, .16, 2.4, .8, D); osc(c, 'sine', 61.7, t, D, r, g0);
      const g1 = c.createGain(); enveloppe(g1, t, .05, 2.4, .8, D); osc(c, 'triangle', 123.5, t, D, r, g1);
      for (const [f, de] of [[1233, -7], [1480, 5], [1850, -3], [2470, 8]]) {
        const g = c.createGain(); enveloppe(g, t + 1, .008, 2.4, .6, D - 1); const o = osc(c, 'sine', f, t + 1, D - 1, r, g); o.detune.value = de;
      }
    } },
  { nom: 'Portail', duree: D, groupe: 'magie',
    texte: 'Un passage qui s\'ouvre : un tourbillon d\'air grave qui s\'élargit en tournant, puis une note pleine qui s\'installe.',
    jouer(c, out, t) {
      const r = salle(c, out, 3.5, .6);
      const s = bruit(c, D, true), lp = c.createBiquadFilter(), g = c.createGain();
      lp.type = 'lowpass'; lp.Q.value = 8; lp.frequency.setValueAtTime(120, t); lp.frequency.exponentialRampToValueAtTime(900, t + 2.6); lp.frequency.exponentialRampToValueAtTime(300, t + D);
      const tour = c.createOscillator(), tg = c.createGain(); tour.frequency.value = 3; tg.gain.value = 120; tour.connect(tg).connect(lp.frequency); tour.start(t); tour.stop(t + D);
      enveloppe(g, t, .5, 2.4, .5, D); s.connect(lp).connect(g).connect(r); s.start(t); s.stop(t + D);
      for (const [f, v] of [[65.4, .14], [98, .07], [130.8, .05]]) { const g2 = c.createGain(); enveloppe(g2, t + 1.8, v, 1.4, .6, D - 1.8); osc(c, 'sine', f, t + 1.8, D - 1.8, r, g2); }
    } },
  { nom: 'Pierre de lumière', duree: D, groupe: 'magie',
    texte: 'Une pierre magique qui s\'illumine : une note grave pure, puis un halo harmonique qui monte doucement et emplit la salle.',
    jouer(c, out, t) {
      const r = salle(c, out, 5, .8, 2);
      const g0 = c.createGain(); enveloppe(g0, t, .18, 1.8, 1, D); osc(c, 'sine', 73.4, t, D, r, g0);
      [[146.8, .8, .06], [220, 1.4, .04], [293.7, 2, .03], [370, 2.6, .02]].forEach(([f, dep, v]) => {
        const g = c.createGain(); enveloppe(g, t + dep, v, 1.2, .5, D - dep); const o = osc(c, 'triangle', f, t + dep, D - dep, r, g);
      });
    } },
  // ----- magie, deuxième série -----
  { nom: 'Âme qui s\'éveille', duree: D, groupe: 'magie2',
    texte: 'Un « oh » de voix grave, doux et lointain, qui monte d\'une quinte dans une grande salle, comme un esprit qui s\'éveille.',
    jouer(c, out, t) {
      const r = salle(c, out, 4.5, .75, 1.6);
      const voix = c.createGain(); voix.gain.value = 1.8;
      for (const [f, v] of [[400, 1], [800, .4], [2400, .1]]) { const b = c.createBiquadFilter(); b.type = 'bandpass'; b.frequency.value = f; b.Q.value = 10; const g = c.createGain(); g.gain.value = v; voix.connect(b).connect(g).connect(r); }
      for (const de of [-10, 0, 10]) {
        const g = c.createGain(); enveloppe(g, t, .3, 2.4, .8, D); const o = osc(c, 'sawtooth', 98, t, D, voix, g); o.detune.value = de;
        o.frequency.setValueAtTime(98, t + .8); o.frequency.exponentialRampToValueAtTime(146.8, t + 3.2);
      }
    } },
  { nom: 'Cors lointains', duree: D, groupe: 'magie2',
    texte: 'Deux cors graves qui se répondent au loin, sur une quinte ouverte, comme un appel venu des montagnes.',
    jouer(c, out, t) {
      const r = salle(c, out, 4.5, .7, 1.5);
      [[73.4, 0], [110, 1.6]].forEach(([f, dep]) => {
        const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.Q.value = 1.5;
        lp.frequency.setValueAtTime(200, t + dep); lp.frequency.exponentialRampToValueAtTime(900, t + dep + 1.2); lp.frequency.exponentialRampToValueAtTime(300, t + D);
        lp.connect(r);
        for (const de of [-6, 6]) { const g = c.createGain(); enveloppe(g, t + dep, .12, 1.2, .8, D - dep); const o = osc(c, 'sawtooth', f, t + dep, D - dep, lp, g); o.detune.value = de; }
      });
    } },
  { nom: 'Aurore', duree: D, groupe: 'magie2',
    texte: 'Une nappe qui ondule lentement comme une aurore boréale, partant du grave pour s\'élever vers une lumière douce.',
    jouer(c, out, t) {
      const r = salle(c, out, 4, .6, 1.4);
      const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.Q.value = 3;
      lp.frequency.setValueAtTime(200, t); lp.frequency.exponentialRampToValueAtTime(1600, t + 3.4); lp.connect(r);
      const ond = c.createOscillator(), og = c.createGain(); ond.frequency.value = .9; og.gain.value = 300; ond.connect(og).connect(lp.frequency); ond.start(t); ond.stop(t + D);
      for (const [f, v] of [[65.4, .07], [98, .05], [130.8, .045], [164.8, .035], [196, .03]]) for (const de of [-12, 0, 12]) {
        const g = c.createGain(); enveloppe(g, t, v, 2.4, .8, D); const o = osc(c, 'sawtooth', f, t, D, lp, g); o.detune.value = de;
      }
    } },
  { nom: 'Cristal de mana', duree: D, groupe: 'magie2',
    texte: 'Un cristal grave qui se met à vibrer : un son de cloche feutrée qui enfle au lieu de frapper, puis résonne dans la salle.',
    jouer(c, out, t) {
      const r = salle(c, out, 5, .8, 1.6);
      for (const [f, v, ratio] of [[98, .2, 1.4], [196, .06, 2.1]]) {
        const car = c.createOscillator(), mod = c.createOscillator(), mg = c.createGain(), g = c.createGain();
        car.frequency.value = f; mod.frequency.value = f * ratio;
        mg.gain.setValueAtTime(0, t); mg.gain.linearRampToValueAtTime(f * 1.2, t + 2.2); mg.gain.linearRampToValueAtTime(f * .2, t + D);
        mod.connect(mg).connect(car.frequency); enveloppe(g, t, v, 2, .8, D); car.connect(g).connect(r);
        car.start(t); mod.start(t); car.stop(t + D + .05); mod.stop(t + D + .05);
      }
    } },
  { nom: 'Sanctuaire', duree: D, groupe: 'magie2',
    texte: 'Un orgue doux dans un temple de pierre : un accord majeur grave qui s\'élève jusqu\'à l\'octave et emplit l\'espace.',
    jouer(c, out, t) {
      const r = salle(c, out, 5, .8, 1.4);
      [[65.4, 0, .1], [98, .4, .07], [130.8, .9, .06], [164.8, 1.4, .045], [196, 1.9, .04], [261.6, 2.5, .03]].forEach(([f, dep, v]) => {
        for (const [m, vv] of [[1, v], [2, v * .4], [4, v * .12]]) { const g = c.createGain(); enveloppe(g, t + dep, vv, 1.3, .5, D - dep); osc(c, 'sine', f * m, t + dep, D - dep, r, g); }
      });
    } },
  { nom: 'Flamme bleue', duree: D, groupe: 'magie2',
    texte: 'Une flamme magique qui s\'allume sans bruit de feu : un souffle chaud qui gonfle et une note grave qui brille au cœur.',
    jouer(c, out, t) {
      const r = salle(c, out, 3, .5, 1.3);
      const s = bruit(c, D), bp = c.createBiquadFilter(), g = c.createGain();
      bp.type = 'bandpass'; bp.Q.value = 1.2; bp.frequency.setValueAtTime(250, t); bp.frequency.exponentialRampToValueAtTime(1100, t + 2.4); bp.frequency.exponentialRampToValueAtTime(400, t + D);
      enveloppe(g, t, .2, 2.2, .6, D); s.connect(bp).connect(g).connect(r); s.start(t); s.stop(t + D);
      for (const [f, v] of [[82.4, .16], [123.5, .06], [164.8, .04]]) { const g2 = c.createGain(); enveloppe(g2, t + .6, v, 2, .6, D - .6); osc(c, 'sine', f, t + .6, D - .6, r, g2); }
    } },
  { nom: 'Mot ancien', duree: D, groupe: 'magie2',
    texte: 'Une syllabe grave murmurée par une voix ancienne, qui s\'ouvre de « mmm » vers « ah » et se perd en écho.',
    jouer(c, out, t) {
      const r = salle(c, out, 4.5, .75, 1.6);
      const voix = c.createGain(); voix.gain.value = 2;
      const f1 = c.createBiquadFilter(); f1.type = 'bandpass'; f1.Q.value = 9; f1.frequency.setValueAtTime(250, t); f1.frequency.linearRampToValueAtTime(700, t + 2.6);
      const f2 = c.createBiquadFilter(); f2.type = 'bandpass'; f2.Q.value = 9; f2.frequency.setValueAtTime(900, t); f2.frequency.linearRampToValueAtTime(1150, t + 2.6);
      const g2 = c.createGain(); g2.gain.value = .5; voix.connect(f1).connect(r); voix.connect(f2).connect(g2).connect(r);
      for (const de of [-8, 8]) { const g = c.createGain(); enveloppe(g, t, .3, 2.2, .6, D); const o = osc(c, 'sawtooth', 87.3, t, D, voix, g); o.detune.value = de; }
    } },
  { nom: 'Étoile qui naît', duree: D, groupe: 'magie2',
    texte: 'Un grondement très grave et doux qui se condense en une note claire au centre, comme une étoile qui s\'allume.',
    jouer(c, out, t) {
      const r = salle(c, out, 4, .7, 1.5);
      const s = bruit(c, D, true), lp = c.createBiquadFilter(), g = c.createGain();
      lp.type = 'lowpass'; lp.frequency.setValueAtTime(100, t); lp.frequency.exponentialRampToValueAtTime(400, t + 2.5);
      g.gain.setValueAtTime(.0001, t); g.gain.exponentialRampToValueAtTime(.3, t + 2.2); g.gain.exponentialRampToValueAtTime(.0001, t + 3.6);
      s.connect(lp).connect(g).connect(r); s.start(t); s.stop(t + D);
      const g0 = c.createGain(); enveloppe(g0, t, .1, 2.2, .4, 3.8); osc(c, 'sine', 55, t, 3.8, r, g0);
      for (const [f, v] of [[220, .1], [330, .04], [440, .025]]) { const g2 = c.createGain(); enveloppe(g2, t + 1.8, v, 1.2, .6, D - 1.8); osc(c, 'sine', f, t + 1.8, D - 1.8, r, g2); }
    } },
  // ----- autour d'« Étoile qui naît », moins grave -----
  // modèle commun : un souffle qui gonfle puis se retire pendant qu'une note claire apparaît au centre
  ...(() => {
    const etoile = (c, out, t, o) => {
      const r = salle(c, out, o.salle || 4, o.humide || .7, o.niveau || 2.1);
      const s = bruit(c, D, !o.clair), f = c.createBiquadFilter(), g = c.createGain();
      f.type = o.clair ? 'bandpass' : 'lowpass'; if (o.clair) f.Q.value = .9;
      f.frequency.setValueAtTime(o.souffle[0], t); f.frequency.exponentialRampToValueAtTime(o.souffle[1], t + 2.5);
      g.gain.setValueAtTime(.0001, t); g.gain.exponentialRampToValueAtTime(o.vSouffle, t + 2.2); g.gain.exponentialRampToValueAtTime(.0001, t + 3.6);
      s.connect(f).connect(g).connect(r); s.start(t); s.stop(t + D);
      if (o.fond) { const g0 = c.createGain(); enveloppe(g0, t, o.fond[1], 2.2, .4, 3.8); osc(c, 'sine', o.fond[0], t, 3.8, r, g0); }
      for (const [fr, v, dep = 1.8, glisse] of o.notes) {
        const g2 = c.createGain(); enveloppe(g2, t + dep, v, 1.2, .6, D - dep);
        const n = osc(c, o.forme || 'sine', glisse ? fr * glisse : fr, t + dep, D - dep, r, g2);
        if (glisse) n.frequency.exponentialRampToValueAtTime(fr, t + dep + 1.4);
      }
    };
    return [
      { nom: 'Étoile douce', duree: D, groupe: 'favoris',
        texte: 'La même étoile, une octave plus haut : le souffle est plus léger et la note claire arrive plus tôt.',
        jouer: (c, out, t) => etoile(c, out, t, { souffle: [200, 700], vSouffle: .22, fond: [110, .07], notes: [[330, .1, 1.5], [440, .05, 1.5], [660, .02, 1.5]] }) },
      { nom: 'Étoile majeure', duree: D, groupe: 'favoris',
        texte: 'Le souffle se condense en un accord majeur clair (do, mi, sol) au lieu d\'une seule note. Plus chaleureux.',
        jouer: (c, out, t) => etoile(c, out, t, { souffle: [180, 650], vSouffle: .22, fond: [98, .06], notes: [[261.6, .08], [329.6, .06, 2], [392, .05, 2.2]] }) },
      { nom: 'Étoile qui monte', duree: D, groupe: 'etoile',
        texte: 'La note claire n\'apparaît pas d\'un coup : elle glisse doucement d\'en dessous jusqu\'à sa place.',
        jouer: (c, out, t) => etoile(c, out, t, { souffle: [180, 650], vSouffle: .2, fond: [98, .06], notes: [[293.7, .1, 1.6, .75], [440, .04, 1.6, .75]] }) },
      { nom: 'Étoile lointaine', duree: D, groupe: 'etoile',
        texte: 'Plus d\'air et plus d\'espace : le souffle est clair, la note naît loin dans une grande salle. Le plus apaisé.',
        jouer: (c, out, t) => etoile(c, out, t, { clair: true, souffle: [400, 1400], vSouffle: .14, salle: 5, humide: .9, niveau: 2.8, notes: [[349.2, .09, 1.8], [523.3, .035, 1.9]] }) },
      { nom: 'Deux étoiles', duree: D, groupe: 'favoris',
        texte: 'Deux notes claires qui naissent l\'une après l\'autre (sol puis ré), comme deux étoiles qui s\'allument.',
        jouer: (c, out, t) => etoile(c, out, t, { souffle: [180, 650], vSouffle: .2, fond: [98, .05], notes: [[196, .1, 1.4], [293.7, .08, 2.4], [392, .025, 2.4]] }) },
      { nom: 'Étoile chaude', duree: D, groupe: 'etoile',
        texte: 'Une étoile plus ronde : un souffle tiède à la place du grondement, et une note feutrée au timbre de bois.',
        jouer: (c, out, t) => etoile(c, out, t, { clair: true, souffle: [300, 1000], vSouffle: .16, forme: 'triangle', fond: [130.8, .05], notes: [[261.6, .12, 1.7], [392, .04, 1.9]] }) },
      // ----- dans le registre des favoris, un peu plus aigu -----
      { nom: 'Étoile claire', duree: D, groupe: 'etoile2',
        texte: 'Comme « Étoile douce » mais une quinte plus haut : un souffle léger qui se condense en une note claire (sol).',
        jouer: (c, out, t) => etoile(c, out, t, { souffle: [300, 1000], vSouffle: .18, fond: [146.8, .05], notes: [[392, .1, 1.5], [587.3, .04, 1.5], [784, .015, 1.6]] }) },
      { nom: 'Accord d\'étoiles', duree: D, groupe: 'etoile2',
        texte: 'Les notes d\'un accord majeur (ré, fa dièse, la) s\'allument l\'une après l\'autre : le mélange de 60 et 63, plus haut.',
        jouer: (c, out, t) => etoile(c, out, t, { souffle: [250, 900], vSouffle: .18, fond: [146.8, .05], notes: [[293.7, .09, 1.5], [370, .07, 2.1], [440, .06, 2.7]] }) },
      { nom: 'Deux étoiles hautes', duree: D, groupe: 'etoile2',
        texte: 'Comme « Deux étoiles », une quarte plus haut : ré puis la, avec une octave légère au-dessus de la seconde.',
        jouer: (c, out, t) => etoile(c, out, t, { souffle: [250, 900], vSouffle: .18, fond: [146.8, .045], notes: [[293.7, .1, 1.4], [440, .08, 2.4], [587.3, .025, 2.4]] }) },
      { nom: 'Trois étoiles', duree: D, groupe: 'etoile2',
        texte: 'Trois notes d\'un accord majeur (do, mi, sol) qui s\'allument tour à tour, puis l\'octave qui les rejoint doucement.',
        jouer: (c, out, t) => etoile(c, out, t, { souffle: [220, 800], vSouffle: .18, fond: [130.8, .05], notes: [[261.6, .09, 1.3], [329.6, .07, 1.9], [392, .06, 2.5], [523.3, .025, 2.9]] }) },
      { nom: 'Aube', duree: D, groupe: 'etoile2',
        texte: 'Un souffle clair, sans grondement, qui laisse apparaître un accord majeur lumineux (fa, la, do). Le plus léger.',
        jouer: (c, out, t) => etoile(c, out, t, { clair: true, souffle: [500, 1600], vSouffle: .12, notes: [[349.2, .09, 1.5], [440, .07, 1.8], [523.3, .05, 2.1]] }) },
      { nom: 'Constellation', duree: D, groupe: 'etoile2',
        texte: 'Quatre notes douces qui s\'allument en montant (sol, si, ré, sol), sans tinter : elles apparaissent en fondu, comme des étoiles.',
        jouer: (c, out, t) => etoile(c, out, t, { souffle: [250, 900], vSouffle: .16, fond: [196, .04], notes: [[392, .08, 1.3], [493.9, .06, 1.8], [587.3, .05, 2.3], [784, .025, 2.8]] }) },
    ];
  })(),
];

const GROUPES = {favoris: 'Favoris', etoile2: 'Étoiles, plus aigu', etoile: 'Étoiles', magie2: 'Magie (2)', magie: 'Magie',
                 monte: 'Du grave vers l\'aigu', hors: 'Hors du cadre'};
const ORDRE = ['Favoris', 'Étoiles, plus aigu', 'Étoiles', 'Magie (2)', 'Magie', 'Du grave vers l\'aigu', 'Hors du cadre',
               'Apparition grave', 'Lumineux', 'Bourdon et bol', 'Premiers essais'];
const liste = SONS.map((s, i) => ({
  numero: i + 1, nom: s.nom, texte: s.texte, duree: s.duree,
  groupe: GROUPES[s.groupe] || (s.cible ? 'Apparition grave' : i < 6 ? 'Premiers essais' : i < 20 ? 'Bourdon et bol' : 'Lumineux'),
}));
window.sonsEveil = {
  liste, ordreGroupes: ORDRE, defaut: 60,
  // joue le son numéro n dans le contexte audio c, vers la sortie out, à l'instant t ; renvoie sa durée
  jouer(n, c, out, t) {
    const s = SONS[n - 1] || SONS[59];
    s.jouer(c, out, t ?? c.currentTime + .03);
    return s.duree;
  },
};
})();
