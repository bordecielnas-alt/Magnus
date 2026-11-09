/* Vue « Mémoire » : ce que l'assistant sait de la maison (foyer) et de chaque compte (privé).
   Ouverte depuis le menu ⋯ (ligne « Mémoire ») ou depuis Réglages > Mémoire. Chargée par reglages.js.
   API : GET /api/memoire (?corbeille=1), POST /api/memoire, POST /api/memoire/valider-tout,
   POST /api/memoire/{id}/valider|refuser|restaurer, PATCH et DELETE /api/memoire/{id},
   GET /api/memoire/{id}/historique.
   - Les souvenirs proposés par l'assistant sont en tête (« À valider »), avec l'ancien texte barré
     quand il s'agit d'une mise à jour.
   - Un clic sur un souvenir le modifie sur place (Entrée enregistre, Échap annule).
   - Oublier ou refuser se rattrape : « Annuler » pendant quelques secondes, puis la corbeille. */
(function () {
  const $ = id => document.getElementById(id);
  if ($('dlgMemoire')) return;

  const css = document.createElement('style');
  css.textContent = `
    dialog#dlgMemoire { width:min(96vw, 560px); padding:16px 16px 14px; }
    #dlgMemoire h2 { display:flex; align-items:baseline; justify-content:space-between; gap:8px; }
    #dlgMemoire h2:focus { outline:none; }
    #dlgMemoire h2 small { font-family:var(--f-texte); font-size:.78rem; color:var(--muted); font-weight:400; }
    .mem-barre { display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin:0 0 10px; }
    .mem-barre input[type=search] { flex:1 1 180px; min-width:0; background:var(--bg); color:var(--fg); border:1px solid var(--bord);
      border-radius:10px; padding:7px 10px; font-size:.9rem; }
    .mem-filtres { display:flex; flex-wrap:wrap; gap:4px; }
    .mem-filtres button { padding:5px 10px; background:var(--bg); border:1px solid var(--bord); border-radius:999px; font-size:.8rem;
      color:var(--muted); cursor:pointer; }
    .mem-filtres button[aria-pressed="true"] { color:var(--fg); border-color:var(--accent); }
    .mem-filtres button b { font-weight:400; opacity:.7; margin-left:3px; }
    .mem-titre { display:flex; justify-content:space-between; align-items:center; gap:8px; margin:14px 0 6px;
      font-size:.75rem; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); }
    .mem-titre:first-child { margin-top:4px; }
    .mem-titre button { text-transform:none; letter-spacing:0; }
    .mem-liste { list-style:none; margin:0; padding:0; display:grid; gap:6px; }
    .mem-sv { border:1px solid var(--bord); border-radius:10px; padding:8px 10px; background:var(--bg); }
    .mem-sv.propose { border-color:color-mix(in srgb, var(--accent) 55%, var(--bord)); }
    .mem-haut { display:flex; gap:8px; align-items:flex-start; }
    .mem-texte { flex:1; min-width:0; font-size:.92rem; line-height:1.35; overflow-wrap:anywhere; cursor:text; border-radius:6px; }
    .mem-texte:hover { background:rgba(255,255,255,.03); }
    .mem-ancien { display:block; color:var(--muted); text-decoration:line-through; font-size:.82rem; margin-bottom:2px; }
    .mem-meta { margin-top:4px; font-size:.74rem; color:var(--muted); display:flex; flex-wrap:wrap; gap:4px 8px; align-items:center; }
    .mem-tag { padding:0 7px; border-radius:999px; border:1px solid var(--bord); font-size:.7rem; line-height:1.5; }
    .mem-tag.prive { border-color:color-mix(in srgb, var(--givre) 50%, var(--bord)); color:var(--givre); }
    .mem-btn { padding:5px 10px; border-radius:8px; border:1px solid var(--bord); background:transparent; color:var(--fg);
      font-size:.8rem; cursor:pointer; white-space:nowrap; }
    .mem-btn.principal { background:var(--accent); color:var(--accent-fg); border-color:var(--accent); }
    .mem-btn.rouge { color:var(--rouge); }
    .mem-plus { padding:2px 9px; font-size:1rem; line-height:1.2; color:var(--muted); }
    .mem-actions { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
    .mem-menu { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; padding-top:8px; border-top:1px dashed var(--bord); }
    .mem-edit textarea { width:100%; min-height:3.2em; resize:vertical; background:var(--surface); color:var(--fg);
      border:1px solid var(--accent); border-radius:8px; padding:6px 8px; font:inherit; font-size:.92rem; }
    .mem-edit .mem-actions { align-items:center; }
    .mem-edit select, .mem-ajout select { width:auto; padding:5px 6px; font-size:.8rem; }
    .mem-histo { margin:8px 0 0; padding:8px 0 0; border-top:1px dashed var(--bord); list-style:none; font-size:.76rem; color:var(--muted);
      display:grid; gap:4px; }
    .mem-histo i { color:var(--fg); font-style:normal; }
    .mem-vide { margin:10px 0; font-size:.85rem; color:var(--muted); text-align:center; }
    .mem-ajout { display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin-top:14px; padding-top:12px; border-top:1px solid var(--bord); }
    .mem-ajout input { flex:1 1 200px; min-width:0; background:var(--bg); color:var(--fg); border:1px solid var(--bord); border-radius:10px;
      padding:7px 10px; font-size:.9rem; }
    .mem-msg { min-height:1.2em; margin:6px 0 0; font-size:.8rem; color:var(--muted); }
    .mem-msg.ko { color:var(--rouge); }
    .mem-toast { position:sticky; bottom:0; margin-top:10px; display:flex; justify-content:space-between; align-items:center; gap:10px;
      padding:8px 10px; border-radius:10px; background:var(--surface); border:1px solid var(--accent); font-size:.85rem;
      box-shadow:0 4px 18px rgba(0,0,0,.5); }
    .mem-toast[hidden], .mem-ajout[hidden] { display:none; }
    .mem-badge { margin-left:6px; padding:0 7px; border-radius:999px; background:var(--accent); color:var(--accent-fg); font-size:.72rem; }
  `;
  document.head.appendChild(css);

  const dlg = document.createElement('dialog');
  dlg.id = 'dlgMemoire';
  dlg.innerHTML = `
    <form method="dialog">
      <h2 tabindex="-1" autofocus>Mémoire <small id="memResume"></small></h2>
      <p class="note">Ce que l'assistant sait de la maison et de toi. Rien n'est retenu sans ton accord. « Foyer » est partagé avec tous les comptes, « privé » n'est qu'à toi.</p>
      <div class="mem-barre">
        <input type="search" id="memCherche" placeholder="Chercher un souvenir…" aria-label="Chercher un souvenir">
        <div class="mem-filtres" id="memFiltres" role="group" aria-label="Filtrer">
          <button type="button" data-f="tout" aria-pressed="true">Tout<b></b></button>
          <button type="button" data-f="foyer" aria-pressed="false">Foyer<b></b></button>
          <button type="button" data-f="prive" aria-pressed="false">Privé<b></b></button>
          <button type="button" data-f="corbeille" aria-pressed="false">Corbeille<b></b></button>
        </div>
      </div>
      <div id="memContenu"><p class="mem-vide">Chargement…</p></div>
      <div class="mem-ajout" id="memAjout">
        <input id="memNouveau" maxlength="500" placeholder="Ajouter : ex. Les poubelles sortent le mardi soir" aria-label="Nouveau souvenir">
        <select id="memNouveauPortee" aria-label="Portée"><option value="foyer">foyer</option><option value="prive">privé</option></select>
        <button type="button" class="mem-btn principal" id="memAjouter">Ajouter</button>
      </div>
      <p class="mem-msg" id="memMsg" aria-live="polite"></p>
      <div class="mem-toast" id="memToast" hidden><span id="memToastTexte"></span><button type="button" class="mem-btn" id="memToastAnnuler">Annuler</button></div>
      <div class="actions" style="margin-top:12px"><span></span><button value="ok">Fermer</button></div>
    </form>`;
  document.body.appendChild(dlg);

  // ----- état ------------------------------------------------------------------------------------------
  let souvenirs = [], corbeille = [], filtre = 'tout', recherche = '', enEdition = null, ouvert = new Set(), historiques = {};
  const ecouteurs = [];
  let derniere = null, lu = false;          // reglages.js et le menu suivent le nombre « à valider »

  const texte = (balise, contenu, classe) => { const e = document.createElement(balise); if (contenu != null) e.textContent = contenu; if (classe) e.className = classe; return e; };
  const bouton = (libelle, action, classe) => { const b = texte('button', libelle, 'mem-btn' + (classe ? ' ' + classe : '')); b.type = 'button'; b.onclick = action; return b; };
  const quand = d => {
    if (!d) return '';
    const x = new Date(d), j = (Date.now() - x) / 864e5;
    if (j < 1 && x.getDate() === new Date().getDate()) return "aujourd'hui " + x.toLocaleTimeString('fr-FR', {hour: '2-digit', minute: '2-digit'});
    if (j < 2) return 'hier';
    if (j < 7) return 'il y a ' + Math.floor(j) + ' j';
    return x.toLocaleDateString('fr-FR', {day: 'numeric', month: 'short', year: x.getFullYear() === new Date().getFullYear() ? undefined : 'numeric'});
  };
  const sansAccents = s => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  const msg = (t, ok) => { const e = $('memMsg'); e.textContent = t || ''; e.className = 'mem-msg' + (ok === false ? ' ko' : ''); };

  async function api(url, opts) {
    const r = await fetch(url, Object.assign({cache: 'no-store'}, opts || {}));
    let o = {}; try { o = await r.json(); } catch (e) {}
    if (!r.ok) { const err = new Error(typeof o.detail === 'string' ? o.detail : 'erreur ' + r.status); err.status = r.status; throw err; }
    return o;
  }
  const json = (methode, corps) => ({method: methode, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(corps || {})});

  // ----- chargement --------------------------------------------------------------------------------------
  async function charger() {
    let d;
    try { d = await api('/api/memoire'); }
    catch (e) {
      souvenirs = [];
      $('memContenu').replaceChildren(texte('p', e.status === 403 ? "La mémoire n'est pas visible sur un appareil partagé." : 'Mémoire indisponible (' + e.message + ').', 'mem-vide'));
      prevenir(null);
      return null;
    }
    souvenirs = d.souvenirs;
    if (filtre === 'corbeille') await chargerCorbeille(); else afficher();
    prevenir(d);
    return d;
  }
  async function chargerCorbeille() {
    try { corbeille = (await api('/api/memoire?corbeille=1')).souvenirs; }
    catch (e) { corbeille = []; msg(e.message, false); }
    afficher();
  }
  function prevenir(d) {
    const info = d ? {total: d.souvenirs.filter(s => s.statut === 'valide').length, a_valider: d.a_valider} : null;
    derniere = info; lu = true;
    for (const f of ecouteurs) { try { f(info); } catch (e) {} }
  }

  // ----- affichage ---------------------------------------------------------------------------------------
  function correspond(sv) {
    if (filtre === 'foyer' && sv.portee !== 'foyer') return false;
    if (filtre === 'prive' && sv.portee !== 'prive') return false;
    if (!recherche) return true;
    return sansAccents(sv.texte + ' ' + (sv.remplace_texte || '') + ' ' + (sv.auteur || '')).includes(recherche);
  }

  function afficher() {
    const valides = souvenirs.filter(s => s.statut === 'valide'), proposes = souvenirs.filter(s => s.statut === 'propose');
    const compte = p => valides.filter(s => s.portee === p).length;
    const nb = {tout: valides.length, foyer: compte('foyer'), prive: compte('prive'), corbeille: filtre === 'corbeille' ? corbeille.length : null};
    for (const b of $('memFiltres').children) {
      b.setAttribute('aria-pressed', String(b.dataset.f === filtre));
      b.querySelector('b').textContent = nb[b.dataset.f] != null ? nb[b.dataset.f] : '';
    }
    $('memResume').textContent = valides.length + ' souvenir' + (valides.length > 1 ? 's' : '') + (proposes.length ? ' · ' + proposes.length + ' à valider' : '');
    $('memAjout').hidden = filtre === 'corbeille';

    const zone = $('memContenu');
    zone.replaceChildren();
    if (filtre === 'corbeille') {
      const liste = corbeille.filter(correspond);
      zone.appendChild(texte('div', 'Oubliés et refusés', 'mem-titre'));
      if (!liste.length) { zone.appendChild(texte('p', recherche ? 'Rien ne correspond dans la corbeille.' : 'La corbeille est vide.', 'mem-vide')); return; }
      const ul = texte('ul', null, 'mem-liste');
      for (const sv of liste) ul.appendChild(ligneCorbeille(sv));
      zone.appendChild(ul);
      return;
    }

    const props = proposes.filter(correspond);
    if (props.length) {
      const t = texte('div', null, 'mem-titre');
      t.append(texte('span', 'À valider · ' + props.length));
      if (props.length > 1) t.append(bouton('Tout retenir', toutRetenir, 'principal'));
      const ul = texte('ul', null, 'mem-liste');
      for (const sv of props) ul.appendChild(ligne(sv));
      zone.append(t, ul);
    }
    const liste = valides.filter(correspond);
    if (props.length || liste.length) zone.appendChild(texte('div', filtre === 'prive' ? 'Privé' : filtre === 'foyer' ? 'Foyer' : 'Retenu', 'mem-titre'));
    if (!liste.length) {
      zone.appendChild(texte('p', recherche ? 'Aucun souvenir ne correspond à « ' + $('memCherche').value.trim() + ' ».'
        : valides.length ? 'Rien dans cette catégorie.'
        : "Aucun souvenir pour l'instant. Dis par exemple « retiens que les poubelles sortent le mardi soir », ou ajoute-en un ci-dessous.", 'mem-vide'));
      return;
    }
    const ul = texte('ul', null, 'mem-liste');
    for (const sv of liste) ul.appendChild(ligne(sv));
    zone.appendChild(ul);
  }

  function meta(sv) {
    const m = texte('div', null, 'mem-meta');
    m.append(texte('span', sv.portee === 'prive' ? 'privé' : 'foyer', 'mem-tag' + (sv.portee === 'prive' ? ' prive' : '')));
    const qui = sv.origine === 'conversation' ? 'retenu en conversation' + (sv.auteur ? ' avec ' + sv.auteur : '') : 'ajouté' + (sv.auteur ? ' par ' + sv.auteur : '');
    m.append(texte('span', (sv.statut === 'propose' ? 'proposé en conversation' + (sv.auteur ? ' avec ' + sv.auteur : '') : qui) + ' · ' + quand(sv.maj)));
    return m;
  }

  function ligne(sv) {
    const li = texte('li', null, 'mem-sv' + (sv.statut === 'propose' ? ' propose' : ''));
    li.dataset.id = sv.id;
    if (enEdition === sv.id) { li.appendChild(editeur(sv)); return li; }

    const haut = texte('div', null, 'mem-haut');
    const t = texte('div', null, 'mem-texte');
    if (sv.remplace_texte && sv.statut === 'propose') t.append(texte('span', sv.remplace_texte, 'mem-ancien'));
    t.append(document.createTextNode(sv.texte));
    t.title = 'Cliquer pour modifier';
    t.tabIndex = 0;
    t.onclick = () => editer(sv.id);
    t.onkeydown = ev => { if (ev.key === 'Enter') { ev.preventDefault(); editer(sv.id); } };
    haut.append(t);
    if (sv.statut === 'valide') {
      const plus = bouton('⋯', () => { ouvert.has(sv.id) ? ouvert.delete(sv.id) : ouvert.add(sv.id); afficher(); }, 'mem-plus');
      plus.setAttribute('aria-label', 'Plus d\'actions'); plus.setAttribute('aria-expanded', String(ouvert.has(sv.id)));
      haut.append(plus);
    }
    li.append(haut, meta(sv));

    if (sv.statut === 'propose') {
      const a = texte('div', null, 'mem-actions');
      a.append(bouton(sv.remplace_texte ? 'Mettre à jour' : 'Retenir', () => decider(sv, 'valider'), 'principal'),
               bouton('Refuser', () => decider(sv, 'refuser')),
               bouton('Modifier', () => editer(sv.id)));
      li.append(a);
    } else if (ouvert.has(sv.id)) {
      const m = texte('div', null, 'mem-menu');
      m.append(bouton('Modifier', () => editer(sv.id)),
               bouton(sv.portee === 'prive' ? 'Partager avec le foyer' : 'Rendre privé', () => changerPortee(sv)),
               bouton(historiques[sv.id] ? 'Masquer l\'historique' : 'Historique', () => basculerHistorique(sv)),
               bouton('Oublier', () => oublier(sv), 'rouge'));
      li.append(m);
      if (historiques[sv.id]) li.append(listeHistorique(historiques[sv.id]));
    }
    return li;
  }

  function ligneCorbeille(sv) {
    const li = texte('li', null, 'mem-sv');
    const haut = texte('div', null, 'mem-haut');
    haut.append(texte('div', sv.texte, 'mem-texte'), bouton('Restaurer', () => restaurer(sv)));
    const m = meta(sv);
    m.lastChild.textContent = (sv.statut === 'refuse' ? 'refusé' : 'oublié') + ' · ' + quand(sv.maj);
    li.append(haut, m);
    return li;
  }

  const ACTIONS = {proposé: 'proposé', ajouté: 'ajouté', validé: 'retenu', refusé: 'refusé', supprimé: 'oublié', modifié: 'modifié',
                   remplacé: 'remplacé par une mise à jour', restauré: 'restauré', reproposé: 'reproposé'};
  function listeHistorique(h) {
    const ul = texte('ul', null, 'mem-histo');
    if (!h.length) ul.append(texte('li', 'Aucun historique.'));
    for (const x of h) {
      const li = texte('li', quand(x.quand) + ' · ' + (ACTIONS[x.action] || x.action) + (x.compte ? ' par ' + x.compte : ''));
      if ((x.action === 'modifié' || x.action === 'remplacé') && x.avant) {
        li.append(document.createElement('br'), document.createTextNode('avant : '), texte('i', x.avant));
      }
      ul.append(li);
    }
    return ul;
  }

  // ----- édition sur place ---------------------------------------------------------------------------------
  function editer(id) { enEdition = id; afficher(); const z = dlg.querySelector('.mem-edit textarea'); if (z) { z.focus(); z.setSelectionRange(z.value.length, z.value.length); } }
  function editeur(sv) {
    const f = texte('div', null, 'mem-edit');
    const z = texte('textarea'); z.value = sv.texte; z.maxLength = 500; z.setAttribute('aria-label', 'Texte du souvenir');
    const portee = texte('select'); portee.innerHTML = '<option value="foyer">foyer</option><option value="prive">privé</option>'; portee.value = sv.portee;
    const enregistrer = async () => {
      const t = z.value.replace(/\s+/g, ' ').trim();
      if (t.length < 2) { msg('Le souvenir est vide.', false); return; }
      const corps = {};
      if (t !== sv.texte) corps.texte = t;
      if (portee.value !== sv.portee) corps.portee = portee.value;
      enEdition = null;
      if (!Object.keys(corps).length) { afficher(); return; }
      try { await api(`/api/memoire/${sv.id}`, json('PATCH', corps)); msg('Corrigé.', true); delete historiques[sv.id]; }
      catch (e) { msg(e.message, false); }
      charger();
    };
    const annuler = () => { enEdition = null; afficher(); };
    z.onkeydown = ev => {
      if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); enregistrer(); }
      else if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); annuler(); }
    };
    const a = texte('div', null, 'mem-actions');
    a.append(portee, bouton('Enregistrer', enregistrer, 'principal'), bouton('Annuler', annuler));
    f.append(z, a);
    return f;
  }
  // Échap pendant une modification annule la modification, pas toute la fenêtre
  dlg.addEventListener('cancel', ev => { if (enEdition != null) { ev.preventDefault(); enEdition = null; afficher(); } });

  // ----- actions -----------------------------------------------------------------------------------------
  let minuteurToast = null, annulation = null;
  function toast(t, annuler) {
    clearTimeout(minuteurToast);
    annulation = annuler;
    $('memToastTexte').textContent = t;
    $('memToastAnnuler').hidden = !annuler;
    $('memToast').hidden = false;
    minuteurToast = setTimeout(() => { $('memToast').hidden = true; annulation = null; }, 7000);
  }
  $('memToastAnnuler').onclick = async () => {
    const f = annulation; annulation = null; $('memToast').hidden = true; clearTimeout(minuteurToast);
    if (f) await f();
  };
  async function restaurerSilencieux(id, quoi) {
    try { await api(`/api/memoire/${id}/restaurer`, {method: 'POST'}); msg(quoi + ' annulé.', true); }
    catch (e) { msg(e.message, false); }
    charger();
  }

  async function decider(sv, decision) {
    try {
      await api(`/api/memoire/${sv.id}/${decision}`, {method: 'POST'});
      if (decision === 'refuser') toast('Souvenir refusé.', () => restaurerSilencieux(sv.id, 'Refus'));
      else msg(sv.remplace_texte ? 'Mis à jour.' : 'Retenu.', true);
    } catch (e) { msg(e.message, false); }
    charger();
  }
  async function toutRetenir() {
    try { const o = await api('/api/memoire/valider-tout', {method: 'POST'}); msg(o.valides + ' souvenir(s) retenu(s).', true); }
    catch (e) { msg(e.message, false); }
    charger();
  }
  async function oublier(sv) {
    try {
      await api(`/api/memoire/${sv.id}`, {method: 'DELETE'});
      ouvert.delete(sv.id); delete historiques[sv.id];
      toast('« ' + (sv.texte.length > 40 ? sv.texte.slice(0, 40) + '…' : sv.texte) + ' » oublié.', () => restaurerSilencieux(sv.id, 'Oubli'));
      msg('');
    } catch (e) { msg(e.message, false); }
    charger();
  }
  async function changerPortee(sv) {
    const p = sv.portee === 'prive' ? 'foyer' : 'prive';
    try { await api(`/api/memoire/${sv.id}`, json('PATCH', {portee: p})); msg(p === 'prive' ? 'Rendu privé : il n\'est plus visible par les autres comptes.' : 'Partagé avec tout le foyer.', true); delete historiques[sv.id]; }
    catch (e) { msg(e.message, false); }
    charger();
  }
  async function basculerHistorique(sv) {
    if (historiques[sv.id]) { delete historiques[sv.id]; afficher(); return; }
    try { historiques[sv.id] = (await api(`/api/memoire/${sv.id}/historique`)).historique; }
    catch (e) { msg(e.message, false); }
    afficher();
  }
  async function restaurer(sv) {
    try { const o = await api(`/api/memoire/${sv.id}/restaurer`, {method: 'POST'}); msg(o.statut === 'propose' ? 'Remis dans « À valider ».' : 'Restauré.', true); }
    catch (e) { msg(e.message, false); }
    charger();
  }
  async function ajouter() {
    const t = $('memNouveau').value.trim();
    if (t.length < 2) { $('memNouveau').focus(); return; }
    try {
      await api('/api/memoire', json('POST', {texte: t, portee: $('memNouveauPortee').value}));
      $('memNouveau').value = ''; msg('Ajouté.', true);
      if (recherche) { recherche = ''; $('memCherche').value = ''; }
    } catch (e) { msg(e.message, false); }
    charger();
  }
  $('memAjouter').onclick = ajouter;
  $('memNouveau').onkeydown = ev => { if (ev.key === 'Enter') { ev.preventDefault(); ajouter(); } };

  $('memCherche').oninput = () => { recherche = sansAccents($('memCherche').value.trim()); afficher(); };
  $('memCherche').onkeydown = ev => { if (ev.key === 'Enter') ev.preventDefault(); };
  for (const b of $('memFiltres').children) {
    b.onclick = () => {
      filtre = b.dataset.f; enEdition = null; msg('');
      if (filtre === 'corbeille') chargerCorbeille(); else afficher();
    };
  }

  // ----- ouverture : menu ⋯ et Réglages ----------------------------------------------------------------
  function ouvrir() {
    msg(''); enEdition = null;
    if (!dlg.open) { dlg.showModal(); dlg.querySelector('h2').focus(); }
    charger();
  }
  const menu = $('menuOptions');
  if (menu) {
    const b = texte('button', null, 'ligne-menu'); b.type = 'button'; b.id = 'ouvrirMemoire';
    b.append(document.createTextNode('Mémoire'));
    const badge = texte('span', null, 'mem-badge'); badge.hidden = true; b.append(badge);
    b.onclick = () => { if (typeof fermerMenu === 'function') fermerMenu(); ouvrir(); };
    const apres = $('ouvrirAgenda');
    if (apres) apres.after(b); else menu.appendChild(b);
    ecouteurs.push(info => {
      b.hidden = !info;
      badge.hidden = !(info && info.a_valider);
      if (info) badge.textContent = info.a_valider + ' à valider';
    });
  }

  window.memoireAssistant = {ouvrir, charger, suivre: f => { ecouteurs.push(f); if (lu) f(derniere); }};
  // première lecture (badge du menu), sans bruit si la mémoire n'est pas accessible
  charger().catch(() => {});
})();
