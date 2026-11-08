/* Configuration de l'assistant, dans le panneau Réglages (⚙) du chat.
   Lit GET /api/reglages, écrit POST /api/reglages (config.yaml), /api/reglages/secret (jeton HA),
   /api/reglages/ollama/telecharger, /api/securite/mot-de-passe et /api/redemarrer ;
   comptes : /api/securite, /api/comptes, /api/compte/secours, /api/audit (administrateurs). 
   Aucun secret n'est jamais renvoyé par le serveur : le champ du jeton reste vide. */
(function () {
  const $ = id => document.getElementById(id);
  const dlg = $('dlgOrbe');
  if (!dlg) return;

  const css = document.createElement('style');
  css.textContent = `
    dialog#dlgOrbe { width:min(94vw, 460px); }
    #config details { border-top:1px solid var(--bord); padding:10px 0; }
    #config details:first-child { border-top:0; }
    #config summary { cursor:pointer; list-style:none; display:flex; justify-content:space-between; align-items:center;
                      font-size:.95rem; color:var(--fg); }
    #config summary::-webkit-details-marker { display:none; }
    #config summary .etat { font-size:.78rem; color:var(--muted); }
    #config summary .etat.ok { color:var(--givre); }
    #config summary .etat.ko { color:var(--rouge); }
    #config .champ { display:grid; gap:4px; margin:10px 0 0; font-size:.85rem; color:var(--muted); }
    #config .champ input, #config .champ select { width:100%; background:var(--bg); color:var(--fg);
                      border:1px solid var(--bord); border-radius:8px; padding:7px 9px; font:inherit; font-size:.9rem; }
    #config .rangee { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-top:10px; }
    #config .bouton { padding:7px 12px; border-radius:9px; cursor:pointer; border:1px solid var(--bord);
                      background:transparent; color:var(--fg); font:inherit; font-size:.85rem; }
    #config .bouton.principal { background:var(--accent); color:var(--accent-fg); border-color:var(--accent); }
    #config .bouton:disabled { opacity:.5; cursor:default; }
    #config .aide { margin:8px 0 0; font-size:.78rem; color:var(--muted); line-height:1.45; }
    #config code { font-family:var(--f-mono, monospace); font-size:.78rem; background:var(--bg);
                   border:1px solid var(--bord); border-radius:5px; padding:1px 5px; user-select:all; }
    #config .msg-ok { color:var(--givre); } #config .msg-ko { color:var(--rouge); }
    #bandeauRedemarrage { margin:0 0 12px; padding:8px 10px; border:1px solid var(--accent); border-radius:10px;
                          font-size:.82rem; display:flex; gap:8px; align-items:center; justify-content:space-between; }
    #bandeauRedemarrage[hidden] { display:none; }
    #config .compte { display:flex; gap:8px; align-items:center; justify-content:space-between; padding:8px 0;
                      border-bottom:1px dashed var(--bord); font-size:.85rem; flex-wrap:wrap; }
    #config .compte small { color:var(--muted); display:block; font-size:.75rem; }
    #config .codes { display:grid; grid-template-columns:1fr 1fr; gap:4px 10px; font-family:var(--f-mono, monospace);
                     font-size:.85rem; user-select:all; margin-top:8px; }
    #config .journal { max-height:240px; overflow:auto; margin-top:8px; font-size:.75rem; color:var(--muted); }
    #config .journal div { padding:3px 0; border-bottom:1px dashed var(--bord); }
    #config .journal b { color:var(--fg); font-weight:500; }
    #config .lienInv { word-break:break-all; user-select:all; }
  `;
  document.head.appendChild(css);

  const zone = document.createElement('div');
  zone.id = 'config';
  zone.innerHTML = `
    <p class="aide" id="cVersionHaut" style="margin:0 0 8px;text-align:right"></p>
    <div id="bandeauRedemarrage" hidden><span>Certains changements s'appliqueront au redémarrage.</span>
      <button type="button" class="bouton principal" id="cfgRedemarrer">Redémarrer</button></div>
    <details id="cfgAssistant"><summary>Assistant <span class="etat" id="eAssistant"></span></summary>
      <label class="champ">Nom<input id="cNom" maxlength="40"></label>
      <label class="champ">Mot de réveil (vide = le nom)<input id="cWake" maxlength="40"></label>
      <div class="rangee"><button type="button" class="bouton principal" data-enr="assistant">Enregistrer</button><span class="aide" data-msg="assistant"></span></div>
    </details>
    <details id="cfgModeles"><summary>Modèles <span class="etat" id="eModeles"></span></summary>
      <label class="champ">Réponses rapides<select id="cRoleRapide"></select></label>
      <label class="champ">Raisonnement<select id="cRoleRaison"></select></label>
      <label class="champ">Secours (si le premier ne répond pas)<select id="cRoleSecours"></select></label>
      <label class="champ">Modèle Claude<select id="cClaudeModele"><option value="sonnet">Sonnet (équilibré)</option><option value="opus">Opus (le plus fort, plus lent)</option><option value="haiku">Haiku (le plus rapide)</option></select></label>
      <label class="champ">Modèle Ollama<select id="cOllamaModele"></select></label>
      <label class="champ">Adresse d'Ollama<input id="cOllamaUrl" placeholder="http://127.0.0.1:11434/v1"></label>
      <div class="rangee"><button type="button" class="bouton principal" data-enr="modeles">Enregistrer</button><span class="aide" data-msg="modeles"></span></div>
      <div class="rangee"><input id="cOllamaTirer" placeholder="autre modèle Ollama, ex. qwen2.5:7b" style="flex:1;min-width:0;background:var(--bg);color:var(--fg);border:1px solid var(--bord);border-radius:8px;padding:7px 9px;font:inherit;font-size:.85rem">
        <button type="button" class="bouton" id="cOllamaTirerBtn">Télécharger</button></div>
      <p class="aide" id="cOllamaAvancement"></p>
    </details>
    <details id="cfgClaude"><summary>Claude <span class="etat" id="eClaude"></span></summary>
      <p class="aide" id="cClaudeAide"></p>
    </details>
    <details id="cfgHA"><summary>Home Assistant <span class="etat" id="eHA"></span></summary>
      <label class="champ">Adresse<input id="cHaUrl" placeholder="https://home.example.net"></label>
      <label class="champ">Jeton longue durée<input id="cHaJeton" type="password" autocomplete="off" placeholder="laisser vide pour garder l'actuel"></label>
      <p class="aide">Dans Home Assistant : ton profil, onglet Sécurité, « Jetons d'accès longue durée ». Le jeton n'est jamais réaffiché.</p>
      <div class="rangee"><button type="button" class="bouton principal" data-enr="ha">Enregistrer</button><span class="aide" data-msg="ha"></span></div>
    </details>
    <details id="cfgAcces"><summary>Accès <span class="etat" id="eAcces"></span></summary>
      <label class="champ">Proxy de confiance (Nginx Proxy Manager), séparés par des virgules<input id="cProxies" placeholder="ex. 172.17.0.0/16"></label>
      <label class="champ">Réseaux autorisés, séparés par des virgules<input id="cReseaux"></label>
      <label class="champ">Durée d'une connexion (jours)<input id="cSession" type="number" min="1" max="365"></label>
      <div class="rangee"><button type="button" class="bouton principal" data-enr="acces">Enregistrer</button><span class="aide" data-msg="acces"></span></div>
    </details>
    <details id="cfgMonCompte"><summary>Mon compte <span class="etat" id="eMonCompte"></span></summary>
      <label class="champ">Ton des réponses<select id="cTon"><option value="concis">Concis</option><option value="normal">Normal</option><option value="chaleureux">Chaleureux</option></select></label>
      <label class="champ">Ma pièce (par défaut quand je n'en précise pas)<input id="cPiece" maxlength="40" placeholder="ex. Salon"></label>
      <label class="champ">Mes préférences (consigne pour l'assistant)<input id="cConsigne" maxlength="500" placeholder="ex. tutoie-moi, températures en °C"></label>
      <div class="rangee"><button type="button" class="bouton principal" id="cProfilBtn">Enregistrer mon profil</button><span class="aide" data-msg="profil"></span></div>
      <label class="aide" style="display:flex;gap:8px;align-items:center;margin-top:12px"><input type="checkbox" id="cPartage"> Cet appareil est partagé (tablette commune) : l'assistant en dit moins</label>
      <p class="aide" id="cContexte"></p>
      <label class="champ">Mot de passe actuel<input id="cMdpActuel" type="password" autocomplete="current-password"></label>
      <label class="champ">Nouveau mot de passe (10 caractères min.)<input id="cMdpNouveau" type="password" autocomplete="new-password" minlength="10"></label>
      <div class="rangee"><button type="button" class="bouton" id="cMdpBtn">Changer le mot de passe</button><span class="aide" data-msg="mdp"></span></div>
      <p class="aide">Changer le mot de passe déconnecte tous tes autres appareils.</p>
      <div class="rangee"><button type="button" class="bouton" id="cSecoursBtn">Nouveaux codes de secours</button>
        <button type="button" class="bouton" id="cDeconnexion">Se déconnecter</button><span class="aide" data-msg="compte"></span></div>
      <div class="codes" id="cCodes" hidden></div>
    </details>
    <details id="cfgComptes" hidden><summary>Comptes <span class="etat" id="eComptes"></span></summary>
      <div id="cListeComptes"></div>
      <div class="rangee"><input id="cNouveauNom" placeholder="prénom du nouveau compte" maxlength="40" style="flex:1;min-width:0;background:var(--bg);color:var(--fg);border:1px solid var(--bord);border-radius:8px;padding:7px 9px;font:inherit;font-size:.85rem">
        <label class="aide" style="margin:0"><input type="checkbox" id="cNouveauAdmin"> admin</label>
        <button type="button" class="bouton principal" id="cCreerCompte">Inviter</button></div>
      <p class="aide" data-msg="comptes"></p>
      <p class="aide">L'invitation est un lien à usage unique, valable 7 jours : la personne y choisit son mot de passe puis configure le code de son téléphone.</p>
    </details>
    <details id="cfgJournal" hidden><summary>Journal <span class="etat" id="eJournal"></span></summary>
      <div class="journal" id="cJournal"></div>
    </details>
    <details id="cfgSysteme"><summary>Système <span class="etat" id="eSysteme"></span></summary>
      <p class="aide" id="cSystemeInfo"></p>
      <div class="rangee"><button type="button" class="bouton" id="cfgRedemarrer2">Redémarrer l'assistant</button><span class="aide" data-msg="systeme"></span></div>
    </details>`;
  const actions = dlg.querySelector('.actions');
  actions.parentNode.insertBefore(zone, actions);

  let donnees = null;
  const msg = (cle, texte, ok) => { const e = zone.querySelector(`[data-msg="${cle}"]`); if (e) { e.textContent = texte; e.className = 'aide ' + (ok ? 'msg-ok' : 'msg-ko'); } };
  const etat = (id, texte, classe) => { const e = $(id); e.textContent = texte; e.className = 'etat ' + (classe || ''); };
  const liste = v => v.split(',').map(x => x.trim()).filter(Boolean);

  async function api(url, corps) {
    const r = await fetch(url, corps === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(corps)});
    let o = {}; try { o = await r.json(); } catch (e) {}
    if (!r.ok) throw new Error(typeof o.detail === 'string' ? o.detail : 'erreur ' + r.status);
    return o;
  }

  function options(select, valeurs, actuelle, libelles) {
    select.innerHTML = '';
    for (const v of valeurs) {
      const o = document.createElement('option');
      o.value = v; o.textContent = (libelles && libelles[v]) || v;
      if (v === actuelle) o.selected = true;
      select.appendChild(o);
    }
  }

  function afficher(d) {
    donnees = d;
    const r = d.reglages, e = d.etat;
    $('bandeauRedemarrage').hidden = !e.redemarrage_necessaire;
    $('cNom').value = r.identite.nom; $('cWake').value = r.identite.wake_word || '';
    etat('eAssistant', r.identite.nom + ' · « ' + r.identite.wake_word_effectif + ' »');

    const ids = Object.keys(r.fournisseurs);
    const libelles = Object.fromEntries(ids.map(i => [i, i + ' (' + r.fournisseurs[i].modele + ')']));
    options($('cRoleRapide'), ids, r.roles.rapide, libelles);
    options($('cRoleRaison'), ids, r.roles.raisonnement, libelles);
    options($('cRoleSecours'), ids, r.roles.secours, libelles);
    const idClaude = ids.find(i => r.fournisseurs[i].type === 'claude_code');
    const idOllama = ids.find(i => r.fournisseurs[i].type === 'openai' && (r.fournisseurs[i].url || '').includes('11434'));
    if (idClaude) $('cClaudeModele').value = r.fournisseurs[idClaude].modele;
    const o = e.ollama || {};
    const modelesOllama = [...new Set([...(o.modeles || []), idOllama ? r.fournisseurs[idOllama].modele : ''].filter(Boolean))];
    options($('cOllamaModele'), modelesOllama, idOllama ? r.fournisseurs[idOllama].modele : '');
    $('cOllamaUrl').value = idOllama ? r.fournisseurs[idOllama].url : '';
    etat('eModeles', o.joignable ? 'Ollama : ' + (o.modeles || []).length + ' modèle(s)' : (o.configure ? 'Ollama injoignable' : ''), o.joignable ? 'ok' : 'ko');
    const t = o.telechargement;
    $('cOllamaAvancement').textContent = t ? `${t.modele} : ${t.statut}${t.en_cours && t.pourcent ? ' (' + t.pourcent + ' %)' : ''}` : '';

    const c = e.claude || {};
    if (!c.configure) { etat('eClaude', 'non configuré'); $('cClaudeAide').textContent = ''; }
    else if (!c.installe) { etat('eClaude', 'commande absente', 'ko'); $('cClaudeAide').textContent = 'La commande claude est introuvable.'; }
    else if (c.connecte) { etat('eClaude', 'connecté · ' + c.modele, 'ok'); $('cClaudeAide').textContent = 'Claude répond via ton abonnement.'; }
    else {
      etat('eClaude', 'pas connecté', 'ko');
      $('cClaudeAide').innerHTML = e.conteneur
        ? 'Dans Unraid : icône du conteneur, <b>Console</b>, puis <code>claude auth login</code>. Ouvre le lien affiché, connecte-toi au compte Max et recolle le code.'
        : 'Sur le PC : tableau de suivi, onglet Outils, carte Claude, « Se connecter ».';
    }

    $('cHaUrl').value = r.home_assistant.url || '';
    const h = e.home_assistant || {};
    etat('eHA', h.connecte ? 'connecté' + (h.version ? ' · ' + h.version : '') : (!r.home_assistant.jeton_defini ? 'jeton manquant' : (h.message || 'non connecté')), h.connecte ? 'ok' : 'ko');

    $('cProxies').value = (r.securite.proxies_de_confiance || []).join(', ');
    $('cReseaux').value = (r.securite.reseaux_autorises || []).join(', ');
    $('cSession').value = r.securite.session_jours;
    etat('eAcces', e.securite.mot_de_passe_defini ? 'protégé' : 'sans compte', e.securite.mot_de_passe_defini ? 'ok' : 'ko');

    const v = e.voix || {};
    $('cSystemeInfo').textContent = `Version ${e.version} · ${e.conteneur ? 'conteneur Docker' : 'PC'} · transcription sur ${v.appareil || '?'} · voix ${v.moteur || '?'}`;
    etat('eSysteme', 'v' + e.version);
    $('cVersionHaut').textContent = 'Version ' + e.version;
  }

  async function charger() {
    try { afficher(await api('/api/reglages')); } catch (e) { etat('eSysteme', 'réglages indisponibles : ' + e.message, 'ko'); }
    chargerComptes();
  }

  // --- comptes ---------------------------------------------------------------------------------
  const texte = (balise, contenu) => Object.assign(document.createElement(balise), {textContent: contenu});
  const quand = d => d ? new Date(d).toLocaleString('fr-FR', {dateStyle: 'short', timeStyle: 'short'}) : 'jamais';
  const LIBELLES = {connexion: 'connexion', 'déconnexion': 'déconnexion', action_maison: 'maison', 'réglages': 'réglages',
    secret_modifié: 'secret modifié', 'redémarrage': 'redémarrage', compte_créé: 'compte créé', compte_supprimé: 'compte supprimé',
    compte_réinitialisé: 'compte réinitialisé', invitation_acceptée: 'invitation acceptée', totp_activé: 'code téléphone activé',
    mot_de_passe_changé: 'mot de passe changé', codes_secours_regénérés: 'codes de secours', alias_oublié: 'alias oublié',
    modèle_téléchargé: 'modèle téléchargé', profil_modifié: 'profil modifié', appareil_partagé: 'appareil marqué partagé', appareil_personnel: 'appareil marqué personnel', totp_activation: 'code téléphone', invitation: 'invitation'};

  async function chargerComptes() {
    let s;
    try { s = await api('/api/securite'); } catch (e) { return; }
    const machine = s.compte === 'machine';
    $('cfgMonCompte').hidden = machine || !s.compte;
    if (!machine && s.compte) {
      etat('eMonCompte', s.compte_nom + (s.admin ? ' · admin' : '') + (s.totp ? ' · code téléphone' : ''), s.totp ? 'ok' : '');
      try {
        const {profil} = await api('/api/compte/profil');
        $('cTon').value = profil.ton; $('cPiece').value = profil.piece || ''; $('cConsigne').value = profil.consigne || '';
      } catch (e) {}
      afficherContexte();
    }
    $('cfgComptes').hidden = $('cfgJournal').hidden = !s.admin;
    if (!s.admin) return;
    try {
      const {comptes, moi} = await api('/api/comptes');
      etat('eComptes', comptes.length + ' compte' + (comptes.length > 1 ? 's' : ''));
      const liste = $('cListeComptes');
      liste.replaceChildren();
      for (const c of comptes) {
        const ligne = document.createElement('div'); ligne.className = 'compte';
        const nom = texte('div', c.nom + (c.admin ? ' · admin' : ''));
        nom.appendChild(texte('small', !c.actif ? 'invitation en attente' : (c.totp ? 'code téléphone actif' : 'code téléphone à configurer') +
          ' · dernière connexion : ' + quand(c.derniere_connexion) + (c.totp ? ' · ' + c.secours_restants + ' codes de secours' : '')));
        const boutons = document.createElement('div'); boutons.className = 'rangee'; boutons.style.marginTop = '0';
        const reinv = texte('button', c.actif ? 'Réinitialiser' : 'Nouveau lien'); reinv.type = 'button'; reinv.className = 'bouton';
        reinv.title = 'Nouveau lien d\'invitation : nouveau mot de passe et nouveau code téléphone ; ses appareils sont déconnectés';
        reinv.onclick = async () => {
          if (c.actif && !confirm(`Réinitialiser ${c.nom} ? Ses appareils seront déconnectés et il faudra lui envoyer le nouveau lien.`)) return;
          try { montrerLien(c.nom, (await api(`/api/comptes/${encodeURIComponent(c.id)}/invitation`, {})).lien); chargerComptes(); }
          catch (e) { msg('comptes', e.message, false); }
        };
        boutons.appendChild(reinv);
        const type = document.createElement('select'); type.className = 'bouton'; type.title = 'Type de compte';
        for (const t of ['adulte', 'enfant']) type.appendChild(Object.assign(document.createElement('option'), {value: t, textContent: t}));
        type.value = c.type || 'adulte';
        type.onchange = async () => {
          try { await api(`/api/comptes/${encodeURIComponent(c.id)}/profil`, {type: type.value}); msg('comptes', `${c.nom} : compte ${type.value}.`, true); }
          catch (e) { msg('comptes', e.message, false); }
        };
        boutons.appendChild(type);
        if (c.id !== moi) {
          const suppr = texte('button', 'Supprimer'); suppr.type = 'button'; suppr.className = 'bouton';
          suppr.onclick = async () => {
            if (!confirm(`Supprimer le compte ${c.nom} ? Ses appareils seront déconnectés.`)) return;
            try {
              const r = await fetch(`/api/comptes/${encodeURIComponent(c.id)}`, {method: 'DELETE'});
              if (!r.ok) throw new Error((await r.json()).detail || 'erreur ' + r.status);
              msg('comptes', c.nom + ' supprimé.', true); chargerComptes();
            } catch (e) { msg('comptes', e.message, false); }
          };
          boutons.appendChild(suppr);
        }
        ligne.append(nom, boutons);
        liste.appendChild(ligne);
      }
      const {evenements} = await api('/api/audit?n=60');
      etat('eJournal', evenements.length ? 'dernier : ' + quand(evenements[0].date) : 'vide');
      $('cJournal').replaceChildren(...evenements.map(ev => {
        const d = document.createElement('div');
        d.appendChild(texte('b', LIBELLES[ev.type] || ev.type));
        const precision = ev.resultat === 'refusé' ? ' refusée (' + (ev.raison || '') + ')'
          : ev.type === 'action_maison' ? ' : ' + (ev.demande || ev.action || '') + (ev.resultat ? ' → ' + ev.resultat : '')
          : ev.cible ? ' : ' + ev.cible : ev.sections ? ' : ' + ev.sections.join(', ') : ev.nom ? ' : ' + ev.nom : '';
        d.append(precision + ' · ' + (ev.compte || ev.identifiant || '?') + ' · ' + (ev.ip || '') + ' · ' + quand(ev.date));
        return d;
      }));
    } catch (e) { msg('comptes', e.message, false); }
  }

  function montrerLien(nom, lien) {
    const p = zone.querySelector('[data-msg="comptes"]');
    p.className = 'aide msg-ok';
    p.replaceChildren(`Lien d'invitation pour ${nom} (à lui envoyer, usage unique) : `, Object.assign(texte('span', lien), {className: 'lienInv'}));
    if (navigator.clipboard) navigator.clipboard.writeText(lien).then(() => p.append(' · copié'), () => {});
  }

  $('cCreerCompte').onclick = async () => {
    const nom = $('cNouveauNom').value.trim();
    if (!nom) { msg('comptes', 'Indique un prénom.', false); return; }
    try {
      const o = await api('/api/comptes', {nom, admin: $('cNouveauAdmin').checked});
      $('cNouveauNom').value = ''; $('cNouveauAdmin').checked = false;
      montrerLien(nom, o.lien); chargerComptes();
    } catch (e) { msg('comptes', e.message, false); }
  };

  const NOMS_MASQUES = {etat_sensible: 'état du chauffage, du portail…', historique: 'historique des actions', personnel: 'tes préférences'};
  async function afficherContexte() {
    try {
      const c = await api('/api/appareil');
      $('cPartage').checked = c.partage;
      const ou = [c.distant ? 'hors de la maison' : 'à la maison', c.partage ? 'appareil partagé' : 'appareil personnel']
        .concat(c.enfant ? ['compte enfant'] : []).join(' · ');
      $('cContexte').textContent = 'Ici : ' + ou + (c.masques.length ? ' — non révélé : ' + c.masques.map(m => NOMS_MASQUES[m] || m).join(', ') : ' — tout est accessible');
    } catch (e) {}
  }
  $('cPartage').onchange = async () => {
    try { await api('/api/appareil', {partage: $('cPartage').checked}); afficherContexte(); }
    catch (e) { $('cContexte').textContent = e.message; }
  };

  $('cProfilBtn').onclick = async () => {
    try {
      await api('/api/compte/profil', {ton: $('cTon').value, piece: $('cPiece').value.trim(), consigne: $('cConsigne').value.trim()});
      msg('profil', 'Profil enregistré.', true);
    } catch (e) { msg('profil', e.message, false); }
  };

  $('cSecoursBtn').onclick = async () => {
    if (!confirm('Créer 10 nouveaux codes de secours ? Les anciens ne marcheront plus.')) return;
    try {
      const o = await api('/api/compte/secours', {});
      $('cCodes').replaceChildren(...o.codes_de_secours.map(c => texte('span', c)));
      $('cCodes').hidden = false;
      msg('compte', 'Note-les en lieu sûr : ils ne seront plus affichés.', true);
    } catch (e) { msg('compte', e.message, false); }
  };

  $('cDeconnexion').onclick = async () => {
    try { await api('/api/deconnexion', {}); } catch (e) {}
    location.href = '/connexion';
  };

  async function enregistrer(cle, changements, apres) {
    try {
      const o = await api('/api/reglages', changements);
      if (apres) await apres();
      msg(cle, o.redemarrage_necessaire ? 'Enregistré : redémarre pour appliquer.' : 'Enregistré.', true);
    } catch (e) { msg(cle, e.message, false); }
    charger();
  }

  zone.addEventListener('click', ev => {
    const b = ev.target.closest('[data-enr]');
    if (!b || !donnees) return;
    const r = donnees.reglages, ids = Object.keys(r.fournisseurs);
    const idClaude = ids.find(i => r.fournisseurs[i].type === 'claude_code');
    const idOllama = ids.find(i => r.fournisseurs[i].type === 'openai' && (r.fournisseurs[i].url || '').includes('11434'));
    if (b.dataset.enr === 'assistant') {
      enregistrer('assistant', {identite: {nom: $('cNom').value.trim(), wake_word: $('cWake').value.trim()}});
    } else if (b.dataset.enr === 'modeles') {
      const f = {};
      if (idClaude) f[idClaude] = {modele: $('cClaudeModele').value};
      if (idOllama) f[idOllama] = {modele: $('cOllamaModele').value, url: $('cOllamaUrl').value.trim()};
      enregistrer('modeles', {roles: {rapide: $('cRoleRapide').value, raisonnement: $('cRoleRaison').value, secours: $('cRoleSecours').value}, fournisseurs: f});
    } else if (b.dataset.enr === 'ha') {
      const jeton = $('cHaJeton').value.trim();
      enregistrer('ha', {home_assistant: {url: $('cHaUrl').value.trim()}},
        jeton ? async () => { await api('/api/reglages/secret', {nom: 'HA_JETON', valeur: jeton}); $('cHaJeton').value = ''; } : null);
    } else if (b.dataset.enr === 'acces') {
      enregistrer('acces', {securite: {proxies_de_confiance: liste($('cProxies').value), reseaux_autorises: liste($('cReseaux').value),
                                        session_jours: +$('cSession').value}});
    }
  });

  $('cOllamaTirerBtn').onclick = async () => {
    const m = $('cOllamaTirer').value.trim();
    if (!m) return;
    try { await api('/api/reglages/ollama/telecharger', {modele: m}); $('cOllamaAvancement').textContent = m + ' : démarrage'; suivre(); }
    catch (e) { $('cOllamaAvancement').textContent = e.message; }
  };
  let suivi = null;
  function suivre() {
    clearInterval(suivi);
    suivi = setInterval(async () => {
      await charger();
      const t = donnees && donnees.etat.ollama && donnees.etat.ollama.telechargement;
      if (!t || !t.en_cours) clearInterval(suivi);
    }, 2000);
  }

  $('cMdpBtn').onclick = async () => {
    try {
      await api('/api/securite/mot-de-passe', {nouveau: $('cMdpNouveau').value, actuel: $('cMdpActuel').value});
      $('cMdpNouveau').value = $('cMdpActuel').value = '';
      msg('mdp', 'Mot de passe changé.', true);
    } catch (e) { msg('mdp', e.message, false); }
  };

  async function redemarrer(cle) {
    try {
      await api('/api/redemarrer', {});
      msg(cle, 'Redémarrage…', true);
      etat('eSysteme', 'redémarrage…');
      const debut = Date.now();
      const attendre = setInterval(async () => {
        try { const r = await fetch('/health'); if (r.ok && Date.now() - debut > 3000) { clearInterval(attendre); location.reload(); } } catch (e) {}
      }, 1500);
    } catch (e) { msg(cle, e.message, false); $('bandeauRedemarrage').querySelector('span').textContent = e.message; }
  }
  $('cfgRedemarrer').onclick = () => redemarrer('systeme');
  $('cfgRedemarrer2').onclick = () => redemarrer('systeme');

  // chargement à chaque ouverture du panneau
  new MutationObserver(() => { if (dlg.open) charger(); }).observe(dlg, {attributes: true, attributeFilter: ['open']});
  if (dlg.open) charger();
})();
