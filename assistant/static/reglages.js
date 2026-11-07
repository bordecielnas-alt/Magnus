/* Configuration de l'assistant, dans le panneau Réglages (⚙) du chat.
   Lit GET /api/reglages, écrit POST /api/reglages (config.yaml), /api/reglages/secret (jeton HA),
   /api/reglages/ollama/telecharger, /api/securite/mot-de-passe et /api/redemarrer.
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
  `;
  document.head.appendChild(css);

  const zone = document.createElement('div');
  zone.id = 'config';
  zone.innerHTML = `
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
      <label class="champ">Mot de passe actuel<input id="cMdpActuel" type="password" autocomplete="current-password"></label>
      <label class="champ">Nouveau mot de passe (10 caractères min.)<input id="cMdpNouveau" type="password" autocomplete="new-password" minlength="10"></label>
      <div class="rangee"><button type="button" class="bouton" id="cMdpBtn">Changer le mot de passe</button><span class="aide" data-msg="mdp"></span></div>
      <p class="aide">Changer le mot de passe déconnecte tous les autres appareils.</p>
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
    etat('eAcces', e.securite.mot_de_passe_defini ? 'mot de passe défini' : 'sans mot de passe', e.securite.mot_de_passe_defini ? 'ok' : 'ko');

    const v = e.voix || {};
    $('cSystemeInfo').textContent = `Version ${e.version} · ${e.conteneur ? 'conteneur Docker' : 'PC'} · transcription sur ${v.appareil || '?'} · voix ${v.moteur || '?'}`;
    etat('eSysteme', 'v' + e.version);
  }

  async function charger() {
    try { afficher(await api('/api/reglages')); } catch (e) { etat('eSysteme', 'réglages indisponibles : ' + e.message, 'ko'); }
  }

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
