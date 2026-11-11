// Interruption de la voix (« barge-in ») : quand tu parles pendant que l'assistant parle, il se tait.
//
// La difficulté : le micro entend aussi l'assistant (le haut-parleur du téléphone est tout près).
// Un simple seuil de volume se déclenche sur sa propre voix, ou ne se déclenche jamais si on le
// monte trop. On compare donc ce que le micro entend à ce que l'assistant est en train de dire :
// on connaît le son de chaque phrase (son « enveloppe », volume toutes les 10 ms), et on apprend
// en cours de route quelle part de ce son revient dans le micro (le « gain d'écho »). Ce qui dépasse
// nettement cet écho attendu, assez longtemps, c'est toi.
(function () {
  'use strict';
  const PAS = 0.01;   // une valeur d'enveloppe toutes les 10 ms

  // Volume (moyenne quadratique) toutes les 10 ms d'un son décodé.
  function enveloppe(donnees, frequence) {
    const n = Math.max(1, Math.round(frequence * PAS)), env = new Float32Array(Math.ceil(donnees.length / n));
    for (let k = 0; k < env.length; k++) {
      let q = 0; const fin = Math.min(donnees.length, (k + 1) * n);
      for (let i = k * n; i < fin; i++) q += donnees[i] * donnees[i];
      env[k] = Math.sqrt(q / Math.max(1, fin - k * n));
    }
    return env;
  }

  // Enveloppe d'un fichier son (blob) : décodé à part, sans toucher au lecteur ni au son qui sort.
  async function enveloppeBlob(blob) {
    const Ctx = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    if (!Ctx) return null;
    const c = new Ctx(1, 1, 22050), donnees = await blob.arrayBuffer();
    const tampon = await new Promise((ok, ko) => {
      const p = c.decodeAudioData(donnees, ok, ko);
      if (p && p.then) p.then(ok, ko);
    }).catch(() => null);
    return tampon ? enveloppe(tampon.getChannelData(0), tampon.sampleRate) : null;
  }

  // Niveau de l'assistant au moment t (s) de la phrase : le maximum sur les 350 ms d'avant
  // (le son met un peu de temps à ressortir du haut-parleur et à revenir dans le micro).
  function niveauA(env, t) {
    if (!env || !env.length) return 0;
    const i0 = Math.max(0, Math.floor((t - 0.35) / PAS)), i1 = Math.min(env.length - 1, Math.ceil((t + 0.05) / PAS));
    let m = 0; for (let i = i0; i <= i1; i++) if (env[i] > m) m = env[i];
    return m;
  }

  // Détecteur : on lui donne, à chaque image (~16 ms), le volume du micro, sa crête, et le niveau
  // attendu de l'assistant (null si on ne le connaît pas). Il répond :
  //   'debut'   : quelque chose qui ressemble à ta voix commence (on peut déjà enregistrer)
  //   'abandon' : fausse alerte, ce n'était pas toi (bruit bref)
  //   'coupe'   : c'est bien toi, l'assistant doit se taire
  //   ''        : rien de neuf
  function creerDetecteur(o) {
    o = o || {};
    const SEUIL = o.seuil || 0.012;          // ≈ -38 dBFS : en dessous, trop faible pour être une voix proche
    const MARGE = o.marge || 1.6;            // ta voix doit dépasser l'écho attendu (déjà pris au plus haut) de ce facteur
    const DUREE = o.duree || 350;            // ms de voix cumulée avant de couper
    const SECOURS = o.secours || 0.25;       // crête sans enveloppe connue (son d'éveil, décodage raté)
    const ECHAUFFEMENT = o.echauffement || 800;   // ms de sa voix pour apprendre son écho, à chaque lecture
    const d = {
      gain: o.gain > 0 ? o.gain : 0.5,       // part du son de l'assistant qui revient dans le micro
      bruit: 0.004,                          // bruit de fond du micro quand personne ne parle
      score: 0, candidat: false, debut: 0, dernier: 0, traine: 0, appris: 0,
      raison: '',
      reinit() { d.score = 0; d.candidat = false; d.debut = 0; d.dernier = 0; d.traine = 0; d.appris = 0; },
      image(rms, pic, sortie, t) {
        const dt = d.dernier ? Math.min(100, t - d.dernier) : 16;
        d.dernier = t;
        let voix;
        if (sortie === null || sortie === undefined) {
          // on ne sait pas ce que l'assistant joue : seulement une voix forte et franche
          voix = pic > SECOURS && rms > SEUIL * 2;
          d.raison = 'crête ' + pic.toFixed(2);
        } else {
          // l'écho s'éteint doucement (fin de phrase, réverbération de la pièce)
          d.traine = Math.max(sortie, d.traine * Math.exp(-dt / 150));
          const echo = d.gain * d.traine;
          voix = rms > SEUIL && rms > 3 * d.bruit && rms > MARGE * echo;
          d.raison = 'micro ' + rms.toFixed(3) + ' / écho attendu ' + echo.toFixed(3);
          // Apprentissage du gain d'écho. Pendant la première seconde où il parle (l'échauffement),
          // on apprend sur toutes les images et on ne coupe pas : on suppose que tu le laisses
          // commencer. Ensuite on n'apprend plus que sur les images qui ressemblent à son écho
          // (pas plus de 25 % au-dessus de l'attendu), jamais sur ta voix.
          const echauffement = d.appris < ECHAUFFEMENT;
          if (d.traine > 0.02 && (echauffement || (!d.candidat && rms < 1.25 * echo))) {
            const r = rms / d.traine;
            d.appris += dt;
            // on monte vite (un écho sous-estimé fait couper à tort), on redescend lentement
            d.gain += (r - d.gain) * (r > d.gain ? (echauffement ? 0.25 : 0.03) : (echauffement ? 0.08 : 0.01));
            d.gain = Math.min(4, Math.max(0.01, d.gain));
            if (echauffement) voix = false;
          }
          if (d.traine < 0.005 && !voix) d.bruit += (Math.min(rms, 0.05) - d.bruit) * 0.02;
        }
        if (voix) d.score += dt; else d.score = d.candidat ? d.score - dt * 0.3 : 0;   // les petits blancs entre tes syllabes sont tolérés
        if (!d.candidat && voix) { d.candidat = true; d.debut = t; d.score = dt; return 'debut'; }
        if (d.candidat && d.score <= 0) { d.candidat = false; d.score = 0; return 'abandon'; }
        if (d.candidat && d.score >= DUREE) { d.candidat = false; d.score = 0; return 'coupe'; }
        return '';
      }};
    return d;
  }

  (typeof window !== 'undefined' ? window : globalThis).Interruption = {PAS, enveloppe, enveloppeBlob, niveauA, creerDetecteur};
})();
