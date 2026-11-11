// Cartes du chat : l'état réel d'un appareil, sous la réponse, avec ses réglages.
// Le serveur construit les cartes (ligne {"cartes": [...]} du flux /api/chat) ; un geste appelle
// POST /api/maison/carte, sans passer par un modèle, et la carte est redessinée avec l'état renvoyé.
// Les cartes arrivent repliées (un toucher sur l'en-tête les déplie). Sur écran tactile, les réglages
// sont verrouillés pour ne pas gêner le défilement : un appui long sur la carte les active, et ils se
// verrouillent d'eux-mêmes après DUREE_ACTIF ms sans toucher la carte.
(() => {
  const css = `
  .cartes { display:grid; gap:10px; margin-top:.7em; font-size:.95rem; text-shadow:none; }
  .carte { background:var(--surface); border:1px solid var(--bord); border-radius:16px; padding:12px 14px; display:grid; gap:10px; max-width:380px; }
  .carte.occupee { opacity:.75; }
  .cartes > .carte { -webkit-user-select:none; user-select:none; -webkit-touch-callout:none; transition:border-color .2s, box-shadow .2s; }
  .cartes > .carte > .carte-tete { cursor:pointer; }
  .cartes > .carte.repliee > :not(.carte-tete) { display:none; }
  .cartes > .carte.repliee > .carte-tete .inter { display:none; }
  .carte-tete .chevron { flex:none; color:var(--muted); font-size:.8rem; transition:transform .2s; }
  .carte:not(.repliee) > .carte-tete .chevron { transform:rotate(180deg); }
  .carte-tete .teinte { width:10px; height:10px; border-radius:50%; flex:none; background:var(--bord); }
  .cartes.verrouillee .carte input, .cartes.verrouillee .carte .pastilles button,
  .cartes.verrouillee .carte .pied-carte button { pointer-events:none; }
  .cartes.verrouillee .carte input[type=range] { touch-action:pan-y; }
  .cartes.verrouillee .carte .ligne, .cartes.verrouillee .carte .inter, .cartes.verrouillee .pied-carte button { opacity:.5; }
  .cartes .indice { color:var(--muted); font-size:.78rem; }
  .cartes .indice[hidden] { display:none; }
  .cartes.active > .carte:not(.repliee) { border-color:var(--accent); box-shadow:0 0 0 1px var(--accent); }
  .carte-tete { display:flex; align-items:center; gap:10px; }
  .carte-tete .titre { flex:1; min-width:0; line-height:1.25; }
  .carte-tete .titre small { display:block; color:var(--muted); font-size:.8rem; }
  .carte .halo { height:6px; border-radius:99px; background:var(--bord); transition:background .25s, box-shadow .25s; }
  .carte .ligne { display:flex; align-items:center; gap:10px; }
  .carte .ligne > span { width:68px; flex:none; color:var(--muted); font-size:.82rem; }
  .carte .ligne output { width:52px; text-align:right; font-variant-numeric:tabular-nums; font-size:.82rem; color:var(--muted); }
  .carte input[type=range] { -webkit-appearance:none; appearance:none; flex:1; min-width:0; height:28px; background:transparent; cursor:pointer; margin:0; touch-action:none; }
  .carte input[type=range]::-webkit-slider-runnable-track { height:8px; border-radius:99px; background:var(--piste, var(--bord)); }
  .carte input[type=range]::-moz-range-track { height:8px; border-radius:99px; background:var(--piste, var(--bord)); }
  .carte input[type=range]::-webkit-slider-thumb { -webkit-appearance:none; width:22px; height:22px; margin-top:-7px; border-radius:50%; background:var(--fg); border:3px solid var(--bg); }
  .carte input[type=range]::-moz-range-thumb { width:16px; height:16px; border-radius:50%; background:var(--fg); border:3px solid var(--bg); }
  .carte input[type=range]:disabled { opacity:.4; cursor:default; }
  .carte .r-int { --piste:linear-gradient(90deg, #1b2229, #f5e6c4); }
  .carte .r-blanc { --piste:linear-gradient(90deg, #ffb46b, #fff4e0 50%, #cfe6ff); }
  .carte .pastilles { display:flex; gap:7px; flex-wrap:wrap; }
  .carte .pastilles button { width:28px; height:28px; border-radius:50%; border:2px solid var(--bord); background:var(--c); cursor:pointer; padding:0; }
  .carte .pastilles button:hover { border-color:var(--fg); }
  .carte .pastilles button.roue-bouton { background:conic-gradient(hsl(0 100% 55%), hsl(60 100% 55%), hsl(120 100% 55%), hsl(180 100% 55%), hsl(240 100% 55%), hsl(300 100% 55%), hsl(360 100% 55%)); }
  .carte .pastilles button[aria-expanded=true] { border-color:var(--fg); box-shadow:0 0 0 2px var(--accent); }
  .carte .roue-zone { display:flex; align-items:center; gap:14px; padding-left:78px; }
  .carte .roue-zone[hidden] { display:none; }
  .carte .roue { position:relative; width:150px; height:150px; flex:none; border-radius:50%; cursor:crosshair; touch-action:none;
    background:radial-gradient(circle closest-side, #fff, rgba(255,255,255,0)), conic-gradient(hsl(0 100% 50%), hsl(30 100% 50%), hsl(60 100% 50%),
      hsl(90 100% 50%), hsl(120 100% 50%), hsl(150 100% 50%), hsl(180 100% 50%), hsl(210 100% 50%), hsl(240 100% 50%),
      hsl(270 100% 50%), hsl(300 100% 50%), hsl(330 100% 50%), hsl(360 100% 50%)); box-shadow:0 0 0 1px var(--bord); }
  .carte .roue .repere { position:absolute; width:18px; height:18px; margin:-9px 0 0 -9px; border-radius:50%; border:3px solid #fff;
    box-shadow:0 0 0 1px rgba(0,0,0,.6), 0 1px 4px rgba(0,0,0,.5); pointer-events:none; }
  .carte .roue[aria-disabled=true] { opacity:.4; cursor:default; }
  .carte .roue-zone output { color:var(--muted); font-size:.82rem; }
  .cartes.verrouillee .carte .roue { pointer-events:none; opacity:.5; }
  .carte .tous { display:grid; gap:8px; padding:8px 10px; border:1px dashed var(--bord); border-radius:12px; }
  .carte .tous > strong { font-size:.85rem; font-weight:600; }
  .carte .choix { display:flex; flex-wrap:wrap; gap:6px; }
  .carte .choix button { background:none; border:1px solid var(--bord); border-radius:99px; padding:3px 10px; font-size:.8rem; color:var(--muted); cursor:pointer; }
  .carte .choix button[aria-pressed=true] { border-color:var(--accent); color:var(--fg); background:color-mix(in srgb, var(--accent) 18%, transparent); }
  .cartes.verrouillee .carte .choix button { pointer-events:none; opacity:.5; }
  @media (max-width:420px) { .carte .roue-zone { padding-left:0; justify-content:center; } }
  .inter { position:relative; width:46px; height:26px; flex:none; display:inline-block; }
  .inter input { position:absolute; inset:0; opacity:0; margin:0; cursor:pointer; width:100%; height:100%; }
  .inter span { position:absolute; inset:0; border-radius:99px; background:var(--bord); transition:background .2s; pointer-events:none; }
  .inter span::after { content:""; position:absolute; top:3px; left:3px; width:20px; height:20px; border-radius:50%; background:var(--fg); transition:transform .2s; }
  .inter input:checked + span { background:var(--accent); }
  .inter input:checked + span::after { transform:translateX(20px); background:var(--accent-fg); }
  .inter input:focus-visible + span { outline:2px solid var(--accent); outline-offset:2px; }
  .inter input:disabled + span { opacity:.4; }
  .carte .pied-carte { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
  .carte .pied-carte button { background:none; border:1px solid var(--bord); border-radius:99px; padding:3px 10px; color:var(--muted); font-size:.8rem; cursor:pointer; }
  .carte .note { color:var(--muted); font-size:.8rem; flex:1; min-width:0; }
  .carte .note.ko { color:var(--rouge); }
  .carte .rangs { list-style:none; margin:0; padding:0; display:grid; gap:4px; }
  .carte .rangs > li { display:grid; gap:8px; }
  .carte .rang { display:flex; align-items:center; gap:10px; }
  .carte .rang button.nom { flex:1; min-width:0; text-align:left; background:none; border:0; padding:4px 0; cursor:pointer; color:var(--fg); }
  .carte .rang small { color:var(--muted); font-size:.8rem; font-variant-numeric:tabular-nums; }
  .carte .point { width:9px; height:9px; border-radius:50%; flex:none; background:var(--bord); }
  .carte .point.on { background:#f5d58a; box-shadow:0 0 8px #f5d58a; }
  .carte[hidden] { display:none; }
  .carte .rangs .carte { border-radius:12px; background:var(--bg); padding:8px 12px; }
  .rangs .carte > .carte-tete, .rangs .carte > .pied-carte { display:none; }
  .carte .rang span.nom { flex:1; min-width:0; padding:4px 0; }
  @media (prefers-reduced-motion: reduce) { .carte *, .inter * { transition:none !important; } }`;
  const style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);

  const echap = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  let n = 0;

  function kelvinEnCouleur(k) {
    const t = Math.max(0, Math.min(1, (k - 2200) / (6500 - 2200)));
    const m = (a, b) => Math.round(a + (b - a) * t);
    return t < .5 ? `rgb(255,${m(180, 250)},${m(107, 230)})` : `rgb(${m(255, 207)},${m(250, 230)},255)`;
  }
  function teinte(c) {
    if (!c.allume) return null;
    if (c.rgb) return `rgb(${c.rgb.join(',')})`;
    if (c.kelvin) return kelvinEnCouleur(c.kelvin);
    return '#f5e6c4';
  }
  // Cercle chromatique : 12 teintes en pastilles, et la roue pour toutes les autres (teinte + saturation)
  const TEINTES = [[0, 'rouge'], [30, 'orange'], [50, 'jaune'], [85, 'vert anis'], [120, 'vert'], [155, "vert d'eau"],
    [185, 'cyan'], [210, 'bleu ciel'], [235, 'bleu'], [265, 'violet'], [295, 'magenta'], [330, 'rose']];
  function nomTeinte(t, sat = 100) {
    const ecart = x => Math.min(Math.abs(t - x), 360 - Math.abs(t - x));
    const nom = TEINTES.reduce((a, b) => ecart(b[0]) < ecart(a[0]) ? b : a)[1];
    return sat < 12 ? 'blanc' : sat < 45 ? nom + ' pâle' : nom;
  }
  const hsl = (t, sat = 100) => `hsl(${Math.round(t)} ${Math.round(sat)}% ${Math.round(100 - sat / 2)}%)`;
  function rgbEnHs([r, g, b]) {
    r /= 255; g /= 255; b /= 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
    let h = 0;
    if (d) h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return [(h * 60 + 360) % 360, max ? d / max * 100 : 0];
  }

  // Ligne « Couleur » : pastilles + bouton roue ; envoi({teinte, saturation}) au choix
  function selecteurCouleur(palette, actuel, ok, envoi) {
    const frag = document.createDocumentFragment();
    const l = document.createElement('div');
    l.className = 'ligne';
    l.innerHTML = '<span>Couleur</span><div class="pastilles"></div>';
    const pastilles = l.lastChild;
    for (const p of palette) {
      const b = document.createElement('button');
      b.type = 'button'; b.style.setProperty('--c', hsl(p.teinte)); b.title = p.nom; b.setAttribute('aria-label', p.nom); b.disabled = !ok;
      b.onclick = () => envoi({teinte: p.teinte, saturation: 100});
      pastilles.appendChild(b);
    }
    const bRoue = document.createElement('button');
    bRoue.type = 'button'; bRoue.className = 'roue-bouton'; bRoue.disabled = !ok;
    bRoue.title = 'Toutes les couleurs'; bRoue.setAttribute('aria-label', 'Toutes les couleurs (cercle chromatique)');
    bRoue.setAttribute('aria-expanded', 'false');
    pastilles.appendChild(bRoue);
    const zone = document.createElement('div');
    zone.className = 'roue-zone'; zone.hidden = true;
    zone.innerHTML = '<div class="roue" role="slider" aria-label="Cercle chromatique" aria-valuemin="0" aria-valuemax="359" tabindex="0">'
      + '<span class="repere"></span></div><output></output>';
    const roue = zone.firstChild, repere = roue.firstChild, out = zone.lastChild;
    let [t, sat] = actuel || [30, 0];
    const placer = () => {
      const a = t * Math.PI / 180, r = sat / 100 * 50;
      repere.style.left = (50 + r * Math.sin(a)) + '%'; repere.style.top = (50 - r * Math.cos(a)) + '%';
      repere.style.background = hsl(t, sat);
      out.textContent = actuel || sat ? nomTeinte(t, sat) : '';
      roue.setAttribute('aria-valuenow', String(Math.round(t))); roue.setAttribute('aria-valuetext', nomTeinte(t, sat));
    };
    const lire = e => {
      const b = roue.getBoundingClientRect(), x = e.clientX - b.left - b.width / 2, y = e.clientY - b.top - b.height / 2;
      t = (Math.atan2(x, -y) * 180 / Math.PI + 360) % 360;
      sat = Math.min(100, Math.hypot(x, y) / (b.width / 2) * 100);
      placer();
    };
    let tire = false;
    if (ok) {
      roue.addEventListener('pointerdown', e => { tire = true; roue.setPointerCapture(e.pointerId); lire(e); e.preventDefault(); });
      roue.addEventListener('pointermove', e => { if (tire) lire(e); });
      roue.addEventListener('pointerup', e => { if (!tire) return; tire = false; lire(e); envoi({teinte: Math.round(t), saturation: Math.round(sat)}); });
      roue.addEventListener('pointercancel', () => { tire = false; });
      roue.addEventListener('keydown', e => {
        const pas = {ArrowRight: [15, 0], ArrowLeft: [-15, 0], ArrowUp: [0, 10], ArrowDown: [0, -10]}[e.key];
        if (pas) { e.preventDefault(); t = (t + pas[0] + 360) % 360; sat = Math.max(0, Math.min(100, (sat || 100) + pas[1])); placer(); }
        else if (e.key === 'Enter') { e.preventDefault(); envoi({teinte: Math.round(t), saturation: Math.round(sat || 100)}); }
      });
    } else roue.setAttribute('aria-disabled', 'true');
    bRoue.onclick = () => {
      zone.hidden = !zone.hidden;
      bRoue.setAttribute('aria-expanded', String(!zone.hidden));
    };
    placer();
    frag.append(l, zone);
    return frag;
  }

  function resume(c) {
    if (!c.disponible) return 'injoignable';
    if (c.type === 'volet') return c.position != null ? c.position + ' %' : (c.ouvert ? 'ouvert' : 'fermé');
    if (!c.allume) return 'éteint';
    return c.intensite != null ? c.intensite + ' %' : 'allumé';
  }

  // Appel au serveur, carte « occupée » pendant l'envoi, puis redessinée avec l'état renvoyé
  async function envoyer(el, conv, corpsJson) {
    el.classList.add('occupee');
    // carte détaillée d'un groupe : le message va au pied du groupe
    const note = (el.parentElement?.closest('.carte-groupe') || el).querySelector(':scope > .pied-carte .note');
    try {
      const r = await fetch('/api/maison/carte', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({conversation: conv, ...corpsJson})});
      const o = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof o.detail === 'string' ? o.detail : 'HTTP ' + r.status);
      if (o.carte) {
        const neuve = dessiner(o.carte, conv);
        if (el.parentElement?.classList.contains('cartes')) habiller(neuve, el.classList.contains('repliee'));
        el.replaceWith(neuve);
        const n2 = (neuve.parentElement?.closest('.carte-groupe') || neuve).querySelector(':scope > .pied-carte .note');
        n2.textContent = o.texte || ''; n2.classList.remove('ko');
        if (neuve.closest('.carte-groupe')) majRang(neuve, o.carte);
      }
    } catch (e) {
      el.classList.remove('occupee');
      if (note) { note.textContent = 'Pas fait : ' + e.message; note.classList.add('ko'); }
    }
  }

  async function annuler(el, conv) {
    const note = el.querySelector(':scope > .pied-carte .note');
    try {
      const r = await fetch('/api/maison/carte/annuler', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({conversation: conv})});
      const o = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof o.detail === 'string' ? o.detail : 'HTTP ' + r.status);
      // redessine toutes les cartes de la réponse touchées par l'annulation
      const zone = el.closest('.cartes');
      for (const c of o.cartes?.[0]?.type === 'groupe' ? o.cartes[0].elements : (o.cartes || [])) {
        zone?.querySelectorAll(`.carte[data-id="${CSS.escape(c.entity_id)}"]`).forEach(x => {
          const neuve = dessiner(c, conv);
          if (x.parentElement === zone) habiller(neuve, x.classList.contains('repliee'));
          x.replaceWith(neuve); majRang(null, c, zone);
        });
      }
      const n2 = zone?.querySelector(':scope > .carte > .pied-carte .note') || note;
      if (n2) { n2.textContent = o.texte || ''; n2.classList.toggle('ko', !o.ok); }
    } catch (e) {
      if (note) { note.textContent = 'Annulation impossible : ' + e.message; note.classList.add('ko'); }
    }
  }

  function piedCarte(el, conv) {
    const p = document.createElement('div');
    p.className = 'pied-carte';
    p.innerHTML = '<span class="note" role="status"></span><button type="button">Annuler</button>';
    p.querySelector('button').onclick = () => annuler(el, conv);
    return p;
  }

  // Curseur : la valeur s'affiche pendant le glissé ; l'envoi part au relâchement
  function curseur(el, classe, libelle, min, max, pas, valeur, format, envoi, actif) {
    const id = 'carte-' + (++n);
    const l = document.createElement('div');
    l.className = 'ligne';
    l.innerHTML = `<span id="${id}-l">${libelle}</span><input type="range" class="${classe}" id="${id}" aria-labelledby="${id}-l"`
      + ` min="${min}" max="${max}" step="${pas}" value="${valeur}"${actif ? '' : ' disabled'}><output for="${id}">${format(valeur)}</output>`;
    const r = l.querySelector('input'), out = l.querySelector('output');
    r.addEventListener('input', () => { out.textContent = format(+r.value); });
    r.addEventListener('change', () => envoi(+r.value));
    return l;
  }

  function dessiner(c, conv) {
    if (c.type === 'groupe') return dessinerGroupe(c, conv);
    const el = document.createElement('div');
    el.className = 'carte';
    el.dataset.id = c.entity_id;
    const ok = c.disponible;
    const inter = c.type === 'volet' ? c.ouvert : c.allume;
    el.innerHTML = `<div class="carte-tete"><div class="titre"><strong>${echap(c.nom)}</strong>`
      + `<small>${echap([c.piece, resume(c)].filter(Boolean).join(' · '))}</small></div>`
      + `<label class="inter"><input type="checkbox" aria-label="${c.type === 'volet' ? 'Ouvrir' : 'Allumer'} ${echap(c.nom)}"`
      + `${inter ? ' checked' : ''}${ok ? '' : ' disabled'}><span></span></label></div>`;
    el.querySelector('.inter input').onchange = e => envoyer(el, conv, {entity_id: c.entity_id, allume: e.target.checked});
    const reg = c.reglages || {};
    if (c.type === 'lumiere') {
      const halo = document.createElement('div');
      halo.className = 'halo';
      const t = teinte(c);
      if (t) { halo.style.background = t; halo.style.boxShadow = `0 0 ${6 + (c.intensite ?? 60) / 5}px ${t}`; }
      el.appendChild(halo);
      if (reg.intensite) el.appendChild(curseur(el, 'r-int', 'Intensité', 1, 100, 1, c.intensite ?? 0,
        v => c.allume || v ? v + ' %' : '–', v => envoyer(el, conv, {entity_id: c.entity_id, intensite: v}), ok));
      if (reg.blanc) el.appendChild(curseur(el, 'r-blanc', 'Blanc', reg.blanc[0], reg.blanc[1], 100,
        c.kelvin ?? Math.round((reg.blanc[0] + reg.blanc[1]) / 200) * 100, v => v + ' K',
        v => envoyer(el, conv, {entity_id: c.entity_id, kelvin: v}), ok));
      if (reg.couleurs) el.appendChild(selecteurCouleur(reg.couleurs, c.hs || (c.rgb ? rgbEnHs(c.rgb) : null), ok,
        hs => envoyer(el, conv, {entity_id: c.entity_id, ...hs})));
    } else if (c.type === 'volet' && reg.position) {
      el.appendChild(curseur(el, 'r-pos', 'Ouverture', 0, 100, 5, c.position ?? 0, v => v + ' %',
        v => envoyer(el, conv, {entity_id: c.entity_id, position: v}), ok));
    }
    el.appendChild(piedCarte(el, conv));
    return el;
  }

  const detaillable = c => c.disponible && (c.type === 'lumiere' ? Object.values(c.reglages || {}).some(Boolean)
    : c.type === 'volet' && c.reglages?.position);

  // Plusieurs appareils : une ligne chacun (interrupteur) ; toucher le nom déplie sa carte détaillée
  function dessinerGroupe(g, conv) {
    const el = document.createElement('div');
    el.className = 'carte carte-groupe';
    const allumes = g.elements.filter(c => c.allume || c.ouvert).length;
    el.innerHTML = `<div class="carte-tete"><div class="titre"><strong>${echap(g.titre)}</strong>`
      + `<small>${g.elements.length} appareils · <span class="compte">${allumes}</span> allumés</small></div></div><ul class="rangs"></ul>`;
    const tous = sectionTous(g, el, conv);
    if (tous) el.querySelector('.rangs').before(tous);
    for (const c of g.elements) {
      const li = document.createElement('li');
      li.dataset.rang = c.entity_id;
      li.innerHTML = `<div class="rang"><span class="point${c.allume || c.ouvert ? ' on' : ''}"></span>`
        + (detaillable(c) ? `<button type="button" class="nom" aria-expanded="false">${echap(c.nom)}</button>` : `<span class="nom">${echap(c.nom)}</span>`)
        + `<small>${echap(resume(c))}</small>`
        + `<label class="inter"><input type="checkbox" aria-label="${echap(c.nom)}"${c.allume || c.ouvert ? ' checked' : ''}`
        + `${c.disponible ? '' : ' disabled'}><span></span></label></div>`;
      const detail = dessiner(c, conv);
      detail.hidden = true;
      li.appendChild(detail);
      li.querySelector('.inter input').onchange = e => {
        envoyer(li.querySelector(':scope > .carte'), conv, {entity_id: c.entity_id, allume: e.target.checked});
      };
      const bNom = li.querySelector('button.nom');
      if (bNom) bNom.onclick = e => {
        const d = li.querySelector(':scope > .carte');
        d.hidden = !d.hidden;
        e.currentTarget.setAttribute('aria-expanded', String(!d.hidden));
      };
      el.querySelector('.rangs').appendChild(li);
    }
    el.appendChild(piedCarte(el, conv));
    return el;
  }

  // Carte « groupe » : régler plusieurs lampes d'un coup (couleur, blanc, intensité). On choisit lesquelles
  // (toutes par défaut) ; le serveur ne règle que celles qui le peuvent et dit lesquelles il a laissées.
  function sectionTous(g, el, conv) {
    const lampes = g.elements.filter(c => c.type === 'lumiere' && c.disponible && Object.values(c.reglages || {}).some(Boolean));
    if (lampes.length < 2) return null;
    const sec = document.createElement('div');
    sec.className = 'tous';
    sec.innerHTML = '<strong>Régler plusieurs lampes</strong><div class="choix" role="group" aria-label="Lampes à régler"></div>';
    const choisis = new Set(lampes.map(c => c.entity_id));
    for (const c of lampes) {
      const b = document.createElement('button');
      b.type = 'button'; b.textContent = c.nom; b.setAttribute('aria-pressed', 'true');
      b.onclick = () => {
        const on = !choisis.has(c.entity_id);
        on ? choisis.add(c.entity_id) : choisis.delete(c.entity_id);
        b.setAttribute('aria-pressed', String(on));
      };
      sec.lastChild.appendChild(b);
    }
    const envoi = champs => envoyerGroupe(el, conv, [...choisis], champs);
    const avec = k => lampes.filter(c => c.reglages[k]);
    if (avec('intensite').length) sec.appendChild(curseur(el, 'r-int', 'Intensité', 1, 100, 1, 60, v => v + ' %', v => envoi({intensite: v}), true));
    const blancs = avec('blanc');
    if (blancs.length) {
      const kmin = Math.min(...blancs.map(c => c.reglages.blanc[0])), kmax = Math.max(...blancs.map(c => c.reglages.blanc[1]));
      sec.appendChild(curseur(el, 'r-blanc', 'Blanc', kmin, kmax, 100, Math.round((kmin + kmax) / 200) * 100, v => v + ' K', v => envoi({kelvin: v}), true));
    }
    const couleurs = avec('couleurs');
    if (couleurs.length) sec.appendChild(selecteurCouleur(couleurs[0].reglages.couleurs, null, true, envoi));
    return sec;
  }

  async function envoyerGroupe(el, conv, ids, champs) {
    const note = el.querySelector(':scope > .pied-carte .note');
    if (!ids.length) { note.textContent = 'Choisissez au moins une lampe.'; note.classList.add('ko'); return; }
    el.classList.add('occupee');
    try {
      const r = await fetch('/api/maison/carte/groupe', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({conversation: conv, entity_ids: ids, ...champs})});
      const o = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof o.detail === 'string' ? o.detail : 'HTTP ' + r.status);
      for (const c of o.cartes || []) {
        el.querySelectorAll(`li[data-rang="${CSS.escape(c.entity_id)}"] > .carte`).forEach(x => {
          const neuve = dessiner(c, conv);
          x.replaceWith(neuve); majRang(neuve, c);
        });
      }
      const noms = (o.ecartes || []).map(id => el.querySelector(`li[data-rang="${CSS.escape(id)}"] .nom`)?.textContent).filter(Boolean);
      note.textContent = (o.texte || '') + (noms.length ? ` (pas ${noms.join(', ')} : réglage impossible)` : '');
      note.classList.remove('ko');
    } catch (e) {
      note.textContent = 'Pas fait : ' + e.message; note.classList.add('ko');
    } finally { el.classList.remove('occupee'); }
  }

  // Dans un groupe, la ligne résumée suit la carte détaillée
  function majRang(detail, c, zone) {
    const li = detail ? detail.closest('li[data-rang]') : zone?.querySelector(`li[data-rang="${CSS.escape(c.entity_id)}"]`);
    if (!li) return;
    const on = !!(c.allume || c.ouvert);
    li.querySelector('.point').classList.toggle('on', on);
    li.querySelector('.rang small').textContent = resume(c);
    li.querySelector('.rang .inter input').checked = on;
    const d = li.querySelector(':scope > .carte');
    if (d && li.querySelector('.nom').getAttribute('aria-expanded') === 'true') d.hidden = false;
    else if (d) d.hidden = true;
    const g = li.closest('.carte-groupe');
    if (g) g.querySelector('.compte').textContent = g.querySelectorAll('.rang .point.on').length;
  }

  // Carte du premier niveau : repliable d'un toucher sur l'en-tête (hors interrupteur), chevron, teinte
  function habiller(el, replie) {
    el.classList.toggle('repliee', replie);
    const tete = el.querySelector(':scope > .carte-tete');
    if (!el.classList.contains('carte-groupe')) {
      const t = el.querySelector(':scope > .halo')?.style.background;
      const p = document.createElement('span');
      p.className = 'teinte';
      if (t) { p.style.background = t; p.style.boxShadow = `0 0 6px ${t}`; }
      tete.prepend(p);
    }
    const ch = document.createElement('span');
    ch.className = 'chevron'; ch.setAttribute('aria-hidden', 'true'); ch.textContent = '▾';
    tete.appendChild(ch);
    tete.setAttribute('role', 'button'); tete.tabIndex = 0;
    tete.setAttribute('aria-expanded', String(!replie));
    const basculer = () => {
      const r = !el.classList.contains('repliee');
      el.classList.toggle('repliee', r);
      tete.setAttribute('aria-expanded', String(!r));
      el.closest('.cartes')?.dispatchEvent(new Event('carte-basculee'));
    };
    tete.addEventListener('click', e => {
      if (e.target.closest('.inter') || el.closest('.cartes')?.dataset.ignorerClic) return;
      basculer();
    });
    tete.addEventListener('keydown', e => {
      if ((e.key === 'Enter' || e.key === ' ') && !e.target.closest('.inter')) { e.preventDefault(); basculer(); }
    });
    return el;
  }

  // Écran tactile : réglages verrouillés, appui long pour les activer
  const DUREE_APPUI = 450, DUREE_ACTIF = 20000;
  function verrou(zone) {
    const indice = document.createElement('div');
    indice.className = 'indice'; indice.setAttribute('role', 'status');
    zone.appendChild(indice);
    let minuteur = null, relock = null, x0 = 0, y0 = 0, enAttente = false;
    const majIndice = () => {
      const ouverte = zone.querySelector(':scope > .carte:not(.repliee)');
      indice.hidden = !ouverte;
      indice.textContent = zone.classList.contains('active') ? 'Réglages actifs' : 'Appui long sur la carte pour régler';
    };
    const verrouiller = () => { zone.classList.add('verrouillee'); zone.classList.remove('active'); majIndice(); };
    const prolonger = () => { clearTimeout(relock); relock = setTimeout(verrouiller, DUREE_ACTIF); };
    const activer = carte => {
      // les réglages restent inertes jusqu'à ce que le doigt se lève : l'appui long ne règle rien
      enAttente = true; zone.classList.add('active');
      if (carte?.classList.contains('repliee')) carte.querySelector(':scope > .carte-tete').click();
      navigator.vibrate?.(15);
      // le toucher qui a servi à l'appui long ne doit ni replier la carte ni actionner un réglage
      zone.dataset.ignorerClic = '1';
      setTimeout(() => { delete zone.dataset.ignorerClic; }, 500);
      majIndice(); prolonger();
    };
    zone.addEventListener('pointerdown', e => {
      if (!zone.classList.contains('verrouillee')) { prolonger(); return; }
      const carte = e.target.closest('.cartes > .carte');
      if (!carte) return;
      x0 = e.clientX; y0 = e.clientY;
      clearTimeout(minuteur);
      minuteur = setTimeout(() => activer(carte), DUREE_APPUI);
    });
    zone.addEventListener('pointermove', e => {
      if (minuteur && Math.hypot(e.clientX - x0, e.clientY - y0) > 10) { clearTimeout(minuteur); minuteur = null; }
    });
    const lacher = () => {
      clearTimeout(minuteur); minuteur = null;
      if (enAttente) { enAttente = false; setTimeout(() => zone.classList.remove('verrouillee'), 50); }
    };
    for (const t of ['pointerup', 'pointercancel']) zone.addEventListener(t, lacher);
    document.addEventListener('pointerup', lacher);   // doigt levé hors de la carte
    zone.addEventListener('pointerleave', () => { clearTimeout(minuteur); minuteur = null; });
    zone.addEventListener('contextmenu', e => e.preventDefault());   // appui long : pas de menu du navigateur
    zone.addEventListener('input', prolonger);
    zone.addEventListener('carte-basculee', majIndice);
    zone.addEventListener('click', e => { if (zone.dataset.ignorerClic) { e.preventDefault(); e.stopPropagation(); } }, true);
    verrouiller();
  }

  const tactile = () => matchMedia('(pointer: coarse)').matches;

  window.cartesChat = {
    afficher(corps, cartes, conversation) {
      if (!Array.isArray(cartes) || !cartes.length) return;
      // cartes de rapport (météo, bourse…) : dessinées par rapports.js, dépliées, sans réglages à verrouiller
      const rapports = cartes.filter(c => c.type === 'rapport');
      if (rapports.length && window.rapportsChat) rapportsChat.afficher(corps, rapports, conversation);
      cartes = cartes.filter(c => c.type !== 'rapport');
      if (!cartes.length) return;
      const zone = document.createElement('div');
      zone.className = 'cartes';
      for (const c of cartes) zone.appendChild(habiller(dessiner(c, conversation), true));
      corps.appendChild(zone);
      if (tactile()) verrou(zone);
    },
  };
})();
