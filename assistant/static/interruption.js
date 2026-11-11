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
  //   'verifier': ça dure assez pour être toi ; l'appelant baisse la voix de l'assistant pendant
  //               ~0,4 s (voir plus bas) et continue à donner les images
  //   'coupe'   : c'est bien toi (ta voix ne baisse pas avec la sienne), l'assistant doit se taire
  //   'echo'    : c'était son propre écho (il a baissé avec sa voix) ; l'appelant remet le volume
  //   ''        : rien de neuf
  //
  // Pourquoi la vérification : le navigateur annule l'écho du haut-parleur (echoCancellation), si bien
  // que l'écho appris est minuscule (gain 0,01 à 0,03)… mais l'annulation laisse parfois passer des
  // bouffées de sa voix, plus fortes que ta voix normale. Au volume seul, impossible de les distinguer
  // de toi, et les longues phrases étaient coupées. Le test physique tranche : on baisse sa voix ;
  // son écho baisse d'autant, ta voix non.
  function creerDetecteur(o) {
    o = o || {};
    const SEUIL = o.seuil || 0.012;          // ≈ -38 dBFS : en dessous, trop faible pour être une voix proche
    const MARGE = o.marge || 1.6;            // ta voix doit dépasser l'écho attendu (déjà pris au plus haut) de ce facteur
    const DUREE = o.duree || 350;            // ms de voix cumulée avant de couper
    const SECOURS = o.secours || 0.25;       // crête sans enveloppe connue (son d'éveil, décodage raté)
    const ECHAUFFEMENT = o.echauffement || 800;   // ms de sa voix pour apprendre son écho, à chaque lecture
    const VERIFIER = o.verifier !== false;   // false : coupe directement (ancien comportement)
    const VERIF = o.verif || 400;            // ms de voix baissée pour trancher
    const VERIF_ATTENTE = o.verifAttente || 130;   // ms ignorées au début (son déjà parti, réverbération)
    const PART_TOI = o.partToi || 0.35;      // ta voix garde au moins cette part de son niveau ; l'écho tombe à ~10 %
    const d = {
      gain: o.gain > 0 ? o.gain : 0.5,       // part du son de l'assistant qui revient dans le micro
      bruit: 0.004,                          // bruit de fond du micro quand personne ne parle
      score: 0, candidat: false, debut: 0, dernier: 0, traine: 0, appris: 0,
      raison: '', verif: 0, mesures: [], niveau: 0, nNiveau: 0, traineCand: 0, nConnu: 0,
      reinit() { d.score = 0; d.candidat = false; d.debut = 0; d.dernier = 0; d.traine = 0; d.appris = 0; d.verif = 0; },
      enVerification() { return d.verif > 0; },
      image(rms, pic, sortie, t) {
        const dt = d.dernier ? Math.min(100, t - d.dernier) : 16;
        d.dernier = t;
        if (d.verif) return d._verifier(rms, t);
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
        // niveau moyen de ce qu'on croit être toi, et de son écho attendu au même moment
        const connu = !(sortie === null || sortie === undefined);
        if (!d.candidat && voix) {
          d.candidat = true; d.debut = t; d.score = dt;
          d.niveau = rms; d.nNiveau = 1; d.traineCand = connu ? d.traine : 0; d.nConnu = connu ? 1 : 0;
          return 'debut';
        }
        if (d.candidat && voix) { d.niveau += rms; d.nNiveau++; if (connu) { d.traineCand += d.traine; d.nConnu++; } }
        if (d.candidat && d.score <= 0) { d.candidat = false; d.score = 0; return 'abandon'; }
        if (d.candidat && d.score >= DUREE) {
          d.score = 0;
          if (!VERIFIER) { d.candidat = false; return 'coupe'; }
          d.verif = t; d.mesures = [];
          return 'verifier';
        }
        return '';
      },
      // Pendant que sa voix est baissée : on mesure le micro, puis on tranche.
      _verifier(rms, t) {
        const ecoule = t - d.verif;
        if (ecoule >= VERIF_ATTENTE) d.mesures.push(rms);
        if (ecoule < VERIF) return '';
        const m = d.mesures.slice().sort((a, b) => a - b);
        const haut = m.length ? m[Math.min(m.length - 1, Math.floor(m.length * 0.75))] : 0;
        const avant = d.nNiveau ? d.niveau / d.nNiveau : 0;
        d.verif = 0; d.candidat = false; d.score = 0;
        if (haut > SEUIL && haut >= PART_TOI * avant) {
          d.raison += ' · confirmé voix baissée : micro ' + haut.toFixed(3) + ' (avant ' + avant.toFixed(3) + ')';
          return 'coupe';
        }
        // C'était son écho : on retient qu'il peut revenir aussi fort, pour ne pas revérifier sans cesse.
        const traine = d.nConnu ? d.traineCand / d.nConnu : 0;
        if (traine > 0.02) d.gain = Math.min(4, Math.max(d.gain, 1.1 * avant / traine / MARGE));
        d.raison = 'écho : micro ' + avant.toFixed(3) + ' puis ' + haut.toFixed(3) + ' voix baissée (gain d\'écho ' + d.gain.toFixed(2) + ')';
        return 'echo';
      }};
    return d;
  }

  (typeof window !== 'undefined' ? window : globalThis).Interruption = {PAS, enveloppe, enveloppeBlob, niveauA, creerDetecteur};
})();
