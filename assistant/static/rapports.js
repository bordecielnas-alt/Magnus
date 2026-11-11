// Cartes de rapport : « fais-moi un rapport météo », « rapport du CAC 40 », « fais le point sur Docker ».
// Le serveur construit la carte (assistant/rapports.py) ; la page la dessine sous la réponse, dépliée
// (repliée pour une question fermée), toujours repliable d'un toucher sur l'en-tête.
// Modèles : meteo (icônes dessinées ici), bourse (courbe du mois), agenda, docker, actus et libre
// (chiffres clés, points, articles, sources). « Actualiser » redemande la même carte (POST /api/rapport).
(() => {
  const css = `
  .rapports { display:grid; gap:10px; margin-top:.7em; font-size:.95rem; text-shadow:none; }
  .rapport > * { min-width:0; }
  .rapport { background:var(--surface); border:1px solid var(--bord); border-radius:18px; padding:14px 16px; display:grid; gap:12px; max-width:440px; position:relative; overflow:hidden; }
  .rapport.occupee { opacity:.6; }
  .rapports .rapport.repliee > :not(.r-tete) { display:none; }      /* question fermée : la réponse est dite, la carte attend */
  .rapport.repliable > .r-tete { cursor:pointer; }
  .rapport.repliable > .r-tete::after { content:'▾'; color:var(--muted); font-size:.8rem; align-self:center; transition:transform .2s; }
  .rapport.repliable:not(.repliee) > .r-tete::after { transform:rotate(180deg); }
  .rapport > .r-tete { display:flex; align-items:flex-start; gap:10px; }
  .rapport > .r-tete .titre { flex:1; min-width:0; line-height:1.25; }
  .rapport > .r-tete strong { font-family:var(--f-titre); font-weight:600; font-size:1.08rem; }
  .rapport > .r-tete small { display:block; color:var(--muted); font-size:.8rem; margin-top:2px; }
  .rapport .marque { width:30px; height:30px; flex:none; border-radius:9px; display:grid; place-items:center; background:var(--bg); color:var(--accent); }
  .rapport .marque svg { width:18px; height:18px; }
  .rapport .resume { margin:0; line-height:1.45; }
  .rapport h4 { margin:0 0 4px; font-size:.75rem; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); font-weight:600; }
  .rapport .chiffres { display:grid; grid-template-columns:repeat(auto-fit, minmax(118px, 1fr)); gap:8px; }
  .rapport .chiffre { background:var(--bg); border-radius:12px; padding:8px 10px; display:grid; gap:2px; min-width:0; }
  .rapport .chiffre span { color:var(--muted); font-size:.75rem; }
  .rapport .chiffre b { font-variant-numeric:tabular-nums; font-size:1.02rem; overflow-wrap:anywhere; }
  .rapport .hausse b { color:#4cd97b; } .rapport .baisse b { color:var(--rouge); }
  .rapport ul.points { margin:0; padding-left:1.1em; display:grid; gap:4px; line-height:1.4; }
  .rapport .articles { list-style:none; margin:0; padding:0; display:grid; gap:8px; }
  .rapport .articles li { border-left:3px solid var(--accent); padding:2px 0 2px 10px; line-height:1.35; }
  .rapport .articles a, .rapport .articles .t { color:var(--fg); font-weight:600; text-decoration:none; }
  .rapport .articles a:hover { text-decoration:underline; }
  .rapport .articles p { margin:2px 0 0; color:var(--muted); font-size:.86rem; }
  .rapport .r-pied { display:flex; align-items:center; gap:10px; flex-wrap:wrap; color:var(--muted); font-size:.78rem; }
  .rapport .r-pied .sources { flex:1; min-width:0; display:flex; flex-wrap:wrap; gap:4px 10px; }
  .rapport .r-pied a { color:var(--muted); }
  .rapport .r-pied button { background:none; border:1px solid var(--bord); border-radius:99px; padding:3px 10px; color:var(--muted); font-size:.78rem; cursor:pointer; }
  .rapport .r-pied button:hover { color:var(--fg); border-color:var(--fg); }
  .rapport .note.ko { color:var(--rouge); }
  /* météo */
  .rapport.meteo .ciel-actuel > div { min-width:0; }
  .rapport.meteo .ciel-actuel { display:flex; align-items:center; gap:14px; }
  .rapport.meteo .ciel-actuel svg { width:76px; height:76px; flex:none; }
  .rapport.meteo .temp { font-family:var(--f-titre); font-size:2.6rem; line-height:1; font-variant-numeric:tabular-nums; }
  .rapport.meteo .ciel-actuel small { display:block; color:var(--muted); font-size:.82rem; margin-top:4px; }
  .rapport .defile { display:flex; gap:6px; overflow-x:auto; scrollbar-width:none; padding-bottom:2px; }
  .rapport .defile::-webkit-scrollbar { display:none; }
  .rapport .h { flex:none; width:50px; display:grid; justify-items:center; gap:2px; background:var(--bg); border-radius:12px; padding:6px 0; font-size:.78rem; }
  .rapport .h svg { width:26px; height:26px; }
  .rapport .h b { font-variant-numeric:tabular-nums; font-size:.9rem; }
  .rapport .h i, .rapport .j i { color:#6fb6ff; font-style:normal; font-size:.72rem; min-height:1em; }
  .rapport .jours { display:grid; gap:4px; }
  .rapport .j > * { min-width:0; }
  .rapport .j { display:grid; grid-template-columns:86px 26px minmax(0,1fr) 38px; align-items:center; gap:8px; font-size:.86rem; }
  .rapport .j svg { width:24px; height:24px; }
  .rapport .j .barre { position:relative; height:6px; border-radius:99px; background:var(--bg); }
  .rapport .j .barre span { position:absolute; top:0; bottom:0; border-radius:99px; background:linear-gradient(90deg, #6fb6ff, #ffd84d, #ff9f43); }
  .rapport .j .mm { display:flex; justify-content:space-between; font-variant-numeric:tabular-nums; color:var(--muted); font-size:.78rem; margin-top:2px; }
  /* bourse */
  .rapport .courbe { width:100%; height:86px; display:block; }
  /* agenda */
  .rapport .jour-agenda { display:grid; gap:4px; }
  .rapport .evt { display:grid; grid-template-columns:52px 1fr; gap:8px; align-items:baseline; }
  .rapport .evt time { color:var(--accent); font-variant-numeric:tabular-nums; font-size:.84rem; }
  .rapport .evt small { color:var(--muted); }
  /* docker */
  .rapport .conteneurs { list-style:none; margin:0; padding:0; display:grid; gap:3px; max-height:260px; overflow-y:auto; }
  .rapport .conteneurs li { display:flex; align-items:center; gap:8px; font-size:.86rem; }
  .rapport .conteneurs li span { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .rapport .conteneurs small { color:var(--muted); font-size:.75rem; max-width:45%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .rapport .pt { width:9px; height:9px; border-radius:50%; flex:none; background:var(--bord); }
  .rapport .pt.marche { background:#4cd97b; } .rapport .pt.souci { background:var(--rouge); box-shadow:0 0 8px var(--rouge); }
  .rapport .alertes { margin:0; padding-left:1.1em; color:var(--rouge); font-size:.86rem; }
  .rapport .jauge { height:8px; border-radius:99px; background:var(--bg); overflow:hidden; }
  .rapport .jauge span { display:block; height:100%; background:var(--accent); }
  .rapport details summary { cursor:pointer; color:var(--muted); font-size:.82rem; }
  @media (prefers-reduced-motion: no-preference) {
    .rapport .soleil-rayons { transform-origin:32px 32px; animation:rapport-tourne 24s linear infinite; }
    .rapport .goutte { animation:rapport-pluie 1.4s ease-in infinite; }
    .rapport .goutte:nth-of-type(2) { animation-delay:.45s; } .rapport .goutte:nth-of-type(3) { animation-delay:.9s; }
  }
  @keyframes rapport-tourne { to { transform:rotate(360deg); } }
  @keyframes rapport-pluie { 0% { transform:translateY(-3px); opacity:0; } 30% { opacity:1; } 100% { transform:translateY(6px); opacity:0; } }`;
  const style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);

  const echap = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const lien = u => /^https?:\/\//.test(u || '') ? echap(u) : '';

  // --- icônes météo (SVG 64×64, couleurs fixes : lisibles sur fond sombre comme clair) -------------------
  const SOLEIL = (cx = 32, cy = 32, r = 11) => `<g class="soleil-rayons" style="transform-origin:${cx}px ${cy}px">`
    + Array.from({length: 8}, (_, i) => {
      const a = i * Math.PI / 4, x1 = cx + Math.cos(a) * (r + 5), y1 = cy + Math.sin(a) * (r + 5);
      const x2 = cx + Math.cos(a) * (r + 10), y2 = cy + Math.sin(a) * (r + 10);
      return `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="#ffc83d" stroke-width="3" stroke-linecap="round"/>`;
    }).join('') + `</g><circle cx="${cx}" cy="${cy}" r="${r}" fill="#ffd84d"/>`;
  const LUNE = (cx = 30, cy = 28) => `<path d="M${cx + 8} ${cy - 13}a14 14 0 1 0 9 22a12 12 0 0 1-9-22z" fill="#e8e3c8"/>`;
  const NUAGE = (couleur = '#c9d4de', dy = 0) => `<path d="M18 ${48 + dy}h30a10 10 0 0 0 0-20a14 14 0 0 0-27-3a9 9 0 0 0-3 23z" fill="${couleur}"/>`;
  const GOUTTES = (c = '#5aa9ff') => [20, 31, 42].map(x => `<line class="goutte" x1="${x}" y1="52" x2="${x - 3}" y2="59" stroke="${c}" stroke-width="3" stroke-linecap="round"/>`).join('');
  const FLOCONS = () => [20, 32, 44].map(x => `<circle class="goutte" cx="${x}" cy="56" r="2.6" fill="#eef6ff"/>`).join('');
  const ICONES = {
    'soleil': () => SOLEIL(),
    'lune': () => LUNE(32, 32),
    'soleil-nuage': () => SOLEIL(24, 22, 9) + NUAGE('#d8e1e9', 4),
    'lune-nuage': () => LUNE(22, 20) + NUAGE('#b7c3cf', 4),
    'nuage': () => NUAGE('#8e9ba8', -6).replace('M18', 'M26') + NUAGE('#c9d4de', 2),
    'brouillard': () => NUAGE('#9aa6b2', -8) + [44, 50, 56].map(y => `<line x1="14" y1="${y}" x2="50" y2="${y}" stroke="#b9c3cc" stroke-width="3" stroke-linecap="round"/>`).join(''),
    'bruine': () => NUAGE('#a9b6c2', -6) + GOUTTES('#8cc4ff'),
    'pluie': () => NUAGE('#7f8d9b', -6) + GOUTTES(),
    'averse': () => SOLEIL(22, 18, 8) + NUAGE('#9aa8b6', -6) + GOUTTES(),
    'neige': () => NUAGE('#c9d4de', -6) + FLOCONS(),
    'orage': () => NUAGE('#6c7884', -6) + '<path d="M33 42l-7 11h6l-4 10 11-14h-6l4-7z" fill="#ffd84d"/>',
  };
  const icone = (nom, titre) => `<svg viewBox="0 0 64 64" role="img" aria-label="${echap(titre || nom)}">${(ICONES[nom] || ICONES.nuage)()}</svg>`;

  const MARQUES = {
    meteo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="9" cy="9" r="3.5"/><path d="M9 2v1.5M2 9h1.5M4 4l1 1M14 4l-1 1"/><path d="M8 20h9a4 4 0 0 0 0-8 5.5 5.5 0 0 0-10 1.5A3.3 3.3 0 0 0 8 20z"/></svg>',
    bourse: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17l5-6 4 3 8-9"/><path d="M15 5h5v5"/></svg>',
    agenda: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="5" width="18" height="16" rx="3"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>',
    docker: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M12 12l8-4.5M12 12v9M12 12L4 7.5"/></svg>',
    actus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="4" width="15" height="16" rx="2"/><path d="M18 8h3v10a2 2 0 0 1-2 2M7 8h7M7 12h7M7 16h4"/></svg>',
    libre: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 13h7M9 17h5"/></svg>',
  };

  const heureMaj = iso => { try { return new Date(iso).toLocaleTimeString('fr-FR', {hour: '2-digit', minute: '2-digit'}).replace(':', 'h'); } catch { return ''; } };

  function chiffres(liste) {
    if (!liste?.length) return '';
    return '<div class="chiffres">' + liste.map(c => `<div class="chiffre ${echap(c.tendance || '')}"><span>${echap(c.libelle)}</span>`
      + `<b>${c.tendance === 'hausse' ? '▲ ' : c.tendance === 'baisse' ? '▼ ' : ''}${echap(c.valeur)}</b></div>`).join('') + '</div>';
  }
  function points(liste, titre) {
    if (!liste?.length) return '';
    return `<div class="r-bloc">${titre ? `<h4>${titre}</h4>` : ''}<ul class="points">${liste.map(p => `<li>${echap(p)}</li>`).join('')}</ul></div>`;
  }
  function articles(liste) {
    if (!liste?.length) return '';
    return '<ul class="articles">' + liste.map(a => {
      const t = lien(a.url) ? `<a href="${lien(a.url)}" target="_blank" rel="noopener noreferrer">${echap(a.titre)}</a>` : `<span class="t">${echap(a.titre)}</span>`;
      return `<li>${t}${a.texte || a.source ? `<p>${echap(a.texte)}${a.source ? ` <em>— ${echap(a.source)}</em>` : ''}</p>` : ''}</li>`;
    }).join('') + '</ul>';
  }

  function corpsMeteo(c) {
    const m = c.meteo || {};
    const details = [m.ressenti != null && `ressenti ${m.ressenti}°`, m.humidite != null && `humidité ${m.humidite} %`,
      m.vent != null && `vent ${m.vent} km/h`, m.lever && `☀ ${m.lever.replace(':', 'h')} – ${(m.coucher || '').replace(':', 'h')}`].filter(Boolean);
    let h = `<div class="ciel-actuel">${icone(m.icone, m.ciel)}<div><div class="temp">${m.temp ?? '–'}°</div>`
      + `<small>${echap(m.ciel)}${details.length ? ' · ' + echap(details.join(' · ')) : ''}</small></div></div>`;
    if (m.heures?.length) h += '<div class="r-bloc"><h4>Prochaines heures</h4><div class="defile">' + m.heures.map(x =>
      `<div class="h"><span>${echap(x.heure)}</span>${icone(x.icone)}<b>${x.temp ?? '–'}°</b><i>${x.pluie ? x.pluie + ' %' : ''}</i></div>`).join('') + '</div></div>';
    if (m.jours?.length) {
      const mins = m.jours.map(j => j.min).filter(v => v != null), maxs = m.jours.map(j => j.max).filter(v => v != null);
      const bas = Math.min(...mins), haut = Math.max(...maxs), ecart = Math.max(1, haut - bas);
      h += '<div class="r-bloc"><h4>Prochains jours</h4><div class="jours">' + m.jours.map(j => {
        const g = ((j.min ?? bas) - bas) / ecart * 100, d = (haut - (j.max ?? haut)) / ecart * 100;
        return `<div class="j"><span>${echap(j.jour)}</span>${icone(j.icone, j.ciel)}`
          + `<div><div class="barre"><span style="left:${g.toFixed(0)}%;right:${d.toFixed(0)}%"></span></div>`
          + `<div class="mm"><span>${j.min ?? '–'}°</span><span>${j.max ?? '–'}°</span></div></div><i>${j.pluie ? j.pluie + ' %' : ''}</i></div>`;
      }).join('') + '</div></div>';
    }
    return h;
  }

  function courbe(serie, hausse) {
    if (!serie || serie.length < 2) return '';
    const L = 300, H = 86, p = 4, min = Math.min(...serie), max = Math.max(...serie), e = max - min || 1;
    const pts = serie.map((v, i) => [p + i * (L - 2 * p) / (serie.length - 1), p + (max - v) / e * (H - 2 * p)]);
    const d = pts.map((q, i) => (i ? 'L' : 'M') + q[0].toFixed(1) + ' ' + q[1].toFixed(1)).join('');
    const coul = hausse ? '#4cd97b' : '#e0533d', id = 'g' + Math.random().toString(36).slice(2, 8);
    return `<svg class="courbe" viewBox="0 0 ${L} ${H}" preserveAspectRatio="none" role="img" aria-label="Évolution sur un mois">`
      + `<defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${coul}" stop-opacity=".35"/><stop offset="1" stop-color="${coul}" stop-opacity="0"/></linearGradient></defs>`
      + `<path d="${d}L${pts.at(-1)[0].toFixed(1)} ${H}L${p} ${H}Z" fill="url(#${id})"/>`
      + `<path d="${d}" fill="none" stroke="${coul}" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg>`;
  }

  function corpsAgenda(c) {
    if (!c.agenda?.length) return '<p class="resume" style="color:var(--muted)">Rien de prévu.</p>';
    return c.agenda.map(j => `<div class="r-bloc jour-agenda"><h4>${echap(j.jour)}</h4>` + j.evenements.map(e =>
      `<div class="evt"><time>${echap(e.heure || 'journée')}</time><span>${echap(e.titre)}${e.lieu ? ` <small>· ${echap(e.lieu)}</small>` : ''}</span></div>`).join('') + '</div>').join('');
  }

  function corpsDocker(c) {
    const dk = c.docker || {}, s = dk.serveur;
    let h = chiffres(c.chiffres);
    if (s) {
      const pct = s.total_to ? Math.round(100 * (s.utilise_to || 0) / s.total_to) : null;
      h += `<div class="r-bloc"><h4>Serveur${s.array ? ' · ' + echap(s.array.toLowerCase()) : ''}</h4>`
        + (pct != null ? `<div class="jauge" title="${pct} %"><span style="width:${pct}%"></span></div><small style="color:var(--muted)">${s.utilise_to} To utilisés sur ${s.total_to} To (${pct} %)</small>` : '')
        + (s.alertes?.length ? `<ul class="alertes">${s.alertes.map(a => `<li>${echap(a)}</li>`).join('')}</ul>` : '') + '</div>';
    }
    const liste = dk.conteneurs || [];
    const ligne = x => `<li><i class="pt ${echap(x.etat)}"></i><span>${echap(x.nom)}</span><small>${echap(x.statut)}</small></li>`;
    const souci = liste.filter(x => x.etat === 'souci');
    if (souci.length) h += `<div class="r-bloc"><h4>En souci</h4><ul class="conteneurs">${souci.map(ligne).join('')}</ul></div>`;
    const autres = liste.filter(x => x.etat !== 'souci');
    if (autres.length) h += `<details><summary>Tous les conteneurs (${autres.length})</summary><ul class="conteneurs">${autres.map(ligne).join('')}</ul></details>`;
    return h;
  }

  function dessiner(c, conversation) {
    const el = document.createElement('article');
    el.className = 'rapport ' + (c.modele || 'libre');
    el.dataset.modele = c.modele || 'libre';
    let corps = '';
    if (c.modele === 'meteo') corps = corpsMeteo(c);
    else if (c.modele === 'agenda') corps = corpsAgenda(c);
    else if (c.modele === 'docker') corps = corpsDocker(c);
    else {
      if (c.bourse) corps += courbe(c.bourse.serie, (c.bourse.serie?.at(-1) ?? 0) >= (c.bourse.serie?.[0] ?? 0));
      corps += chiffres(c.chiffres) + articles(c.articles) + points(c.points, c.modele === 'bourse' ? 'Ce qui a fait bouger' : 'Points clés');
      if (c.modele !== 'bourse' && c.modele !== 'meteo' && c.resume && !c.points?.length && !c.articles?.length) corps = `<p class="resume">${echap(c.resume)}</p>` + corps;
    }
    const sources = [...(c.source ? [c.source] : []), ...(c.sources || [])].filter(s => lien(s.url));
    el.innerHTML = `<div class="r-tete"><span class="marque" aria-hidden="true">${MARQUES[c.modele] || MARQUES.libre}</span>`
      + `<div class="titre"><strong>${echap(c.titre)}</strong>${c.sous_titre ? `<small>${echap(c.sous_titre)}</small>` : ''}</div></div>`
      + corps
      + `<div class="r-pied"><span class="sources">${sources.map(s => `<a href="${lien(s.url)}" target="_blank" rel="noopener noreferrer">${echap(s.titre || 'source')}</a>`).join('')}</span>`
      + `<span class="note" role="status">${c.maj ? 'à ' + heureMaj(c.maj) : ''}</span>`
      + (c.demande ? '<button type="button">Actualiser</button>' : '') + '</div>';
    const b = el.querySelector('.r-pied button');
    if (b) b.onclick = () => actualiser(el, c, conversation);
    // toujours repliable d'un toucher sur l'en-tête ; repliée dès l'arrivée pour une question fermée
    // (« il pleut demain ? »). Le repli et la disparition automatiques sont dans cartes.js.
    el.classList.add('repliable');
    el.classList.toggle('repliee', !!c.repliee);
    const tete = el.querySelector('.r-tete');
    tete.setAttribute('role', 'button'); tete.tabIndex = 0;
    tete.setAttribute('aria-expanded', String(!c.repliee));
    const basculer = () => tete.setAttribute('aria-expanded', String(!el.classList.toggle('repliee')));
    tete.onclick = e => { if (!e.target.closest('a, button')) basculer(); };
    tete.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); basculer(); } };
    return el;
  }

  async function actualiser(el, c, conversation) {
    el.classList.add('occupee');
    const note = el.querySelector('.r-pied .note');
    note.textContent = 'mise à jour…'; note.classList.remove('ko');
    try {
      const r = await fetch('/api/rapport', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(c.demande)});
      const o = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof o.detail === 'string' ? o.detail : 'HTTP ' + r.status);
      if (!o.cartes?.length) throw new Error(o.texte || 'rien reçu');
      el.replaceWith(dessiner(o.cartes[0], conversation));
    } catch (e) {
      el.classList.remove('occupee');
      note.textContent = 'Pas actualisé : ' + e.message; note.classList.add('ko');
    }
  }

  window.rapportsChat = {
    dessiner,
    afficher(corps, cartes, conversation) {
      if (!Array.isArray(cartes) || !cartes.length) return;
      const zone = document.createElement('div');
      zone.className = 'rapports';
      for (const c of cartes) zone.appendChild(dessiner(c, conversation));
      corps.appendChild(zone);
    },
  };
})();
