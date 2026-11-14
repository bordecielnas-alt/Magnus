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
    #config .compte .droits { display:flex; flex-wrap:wrap; gap:4px 10px; margin-top:4px; font-size:.75rem; color:var(--muted); }
    #config .compte .droits small { display:inline; }
    #config .compte .droits label { display:inline-flex; gap:3px; align-items:center; }
    #config .codes { display:grid; grid-template-columns:1fr 1fr; gap:4px 10px; font-family:var(--f-mono, monospace);
                     font-size:.85rem; user-select:all; margin-top:8px; }
    #config .journal { max-height:240px; overflow:auto; margin-top:8px; font-size:.75rem; color:var(--muted); }
    #config .journal div { padding:3px 0; border-bottom:1px dashed var(--bord); }
    #config .journal b { color:var(--fg); font-weight:500; }
    #config .lienInv { word-break:break-all; user-select:all; }
    #rubriques { display:flex; flex-wrap:wrap; gap:6px; margin:0 0 10px; }
    #rubriques[hidden] { display:none; }
    #rubriques button { border:1px solid var(--bord); background:transparent; color:var(--fg); border-radius:999px;
                        padding:5px 12px; font:inherit; font-size:.82rem; cursor:pointer; }
    #rubriques button[aria-pressed="true"] { background:var(--accent); color:var(--accent-fg); border-color:var(--accent); }
    #config details[data-cache], #dlgOrbe section.rubrique[data-cache] { display:none; }
    #rubriques { position:sticky; top:0; z-index:1; background:var(--bg, #000); padding:6px 0; }
  `;
  document.head.appendChild(css);

  const zone = document.createElement('div');
  zone.id = 'config';
  zone.innerHTML = `
    <p class="aide" id="cVersionHaut" style="margin:0 0 8px;text-align:right"></p>
    <div id="rubriques" role="tablist" hidden></div>
    <div id="bandeauRedemarrage" hidden><span>Certains changements s'appliqueront au redémarrage.</span>
      <button type="button" class="bouton principal" id="cfgRedemarrer">Redémarrer</button></div>
    <details id="cfgMonCompte"><summary>Mon compte <span class="etat" id="eMonCompte"></span></summary>
      <div class="rangee"><input id="cMonNom" maxlength="40" placeholder="Mon prénom" style="flex:1;min-width:0;background:var(--bg);color:var(--fg);border:1px solid var(--bord);border-radius:8px;padding:7px 9px;font:inherit;font-size:.85rem">
        <button type="button" class="bouton" id="cMonNomBtn">Renommer</button><span class="aide" data-msg="nom"></span></div>
      <label class="champ">Ton des réponses<select id="cTon"><option value="concis">Concis</option><option value="normal">Normal</option><option value="chaleureux">Chaleureux</option></select></label>
      <label class="champ">Ma pièce (par défaut quand je n'en précise pas)<input id="cPiece" maxlength="40" placeholder="ex. Salon"></label>
      <label class="champ">Mes préférences (consigne pour l'assistant)<input id="cConsigne" maxlength="500" placeholder="ex. tutoie-moi, températures en °C"></label>
      <div class="rangee"><button type="button" class="bouton principal" id="cProfilBtn">Enregistrer mon profil</button><span class="aide" data-msg="profil"></span></div>
      <p class="aide" style="margin-top:12px">Ma voix : <span id="cVoixEtat">…</span></p>
      <p class="aide" id="cVoixPhrase" hidden></p>
      <div class="rangee"><button type="button" class="bouton" id="cVoixBtn">Enregistrer une phrase (5 s)</button>
        <button type="button" class="bouton" id="cVoixOublier">Oublier ma voix</button><span class="aide" data-msg="voix"></span></div>
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
    <details id="cfgMesOutils" hidden><summary>Mon agenda et mon Telegram <span class="etat" id="eMesOutils"></span></summary>
      <div id="cMonAgendaBloc">
        <p class="aide">Ton agenda à toi (CalDAV : Radicale, Nextcloud, iCloud…). L'assistant y lit et y ajoute tes rendez-vous, et te les rappelle, à toi seulement.</p>
        <p class="aide" id="cMonAgendaFamille"></p>
        <label class="champ">Adresse du calendrier<input id="cMonAgendaUrl" placeholder="https://…/moi/agenda/"></label>
        <label class="champ">Identifiant<input id="cMonAgendaUser" autocomplete="off"></label>
        <label class="champ">Mot de passe<input id="cMonAgendaMdp" type="password" autocomplete="off" placeholder="laisser vide pour garder l'actuel"></label>
        <div class="rangee"><button type="button" class="bouton principal" id="cMonAgendaBtn">Enregistrer</button>
          <button type="button" class="bouton" id="cMonAgendaOff">Débrancher</button><span class="aide" data-msg="monagenda"></span></div>
      </div>
      <div id="cMonTgBloc" style="margin-top:14px">
        <p class="aide">Mon Telegram : <span id="cMonTgEtat">…</span></p>
        <div class="rangee"><button type="button" class="bouton" id="cMonTgCode">Relier mon Telegram</button>
          <button type="button" class="bouton" id="cMonTgVerif" hidden>J'ai envoyé le code</button>
          <button type="button" class="bouton" id="cMonTgOff" hidden>Délier</button></div>
        <p class="aide" data-msg="montg"></p>
      </div>
    </details>
    <details id="cfgMemoire"><summary>Mémoire <span class="etat" id="eMemoire"></span></summary>
      <p class="aide">Ce que l'assistant sait de la maison et de toi : souvenirs à valider, recherche, modification, corbeille. Aussi dans le menu ⋯ &gt; Mémoire.</p>
      <div class="rangee"><button type="button" class="bouton principal" id="cMemoireOuvrir">Ouvrir la mémoire</button></div>
      <p class="aide" data-msg="memoire"></p>
    </details>
    <details id="cfgHabitudes"><summary>Routines, habitudes et automatisations <span class="etat" id="eHabitudes"></span></summary>
      <p class="aide">Habitudes repérées dans l'historique des actions : « Automatiser » crée l'automatisation dans Home Assistant (administrateur).</p>
      <div id="cHabitudesListe"></div>
      <p class="aide" style="margin-top:12px">Routines : dis par exemple « crée une routine mode cinéma qui ferme les volets du salon et met la lumière à 20 % », puis « mode cinéma ».</p>
      <div id="cRoutinesListe"></div>
      <p class="aide" style="margin-top:12px" id="cAutosTitre" hidden>Automatisations créées dans Home Assistant à partir d'une habitude :</p>
      <div id="cAutosListe"></div>
      <p class="aide" data-msg="habitudes"></p>
    </details>
    <details id="cfgRegles" hidden><summary>Règles « si… alors… » <span class="etat" id="eRegles"></span></summary>
      <p class="aide">Dis par exemple « si la porte du garage reste ouverte plus de 10 minutes, préviens-moi » ou « quand tout le monde part, éteins les lumières ». L'assistant relit la règle et ne l'enregistre qu'après ton « oui ». Une règle en pause ne se déclenche plus, sans être perdue.</p>
      <div id="cReglesListe"></div>
      <p class="aide" data-msg="regles"></p>
    </details>
    <details id="cfgAssistant"><summary>Nom et mot de réveil <span class="etat" id="eAssistant"></span></summary>
      <label class="champ">Nom<input id="cNom" maxlength="40"></label>
      <label class="champ">Mot de réveil (vide = le nom)<input id="cWake" maxlength="40"></label>
      <div class="rangee"><button type="button" class="bouton principal" data-enr="assistant">Enregistrer</button><span class="aide" data-msg="assistant"></span></div>
    </details>
    <details id="cfgDefauts"><summary>Valeurs par défaut de la maison <span class="etat" id="eDefauts"></span></summary>
      <p class="aide">Ce qu'ont les personnes qui n'ont rien choisi elles-mêmes (dans Affichage, Voix et écoute), et les appareils sans compte.</p>
      <label class="champ">Thème<select id="cThemeDefaut"><option value="orbe">Orbe</option><option value="hal">HAL 9000</option></select></label>
      <label class="champ">Voix<select id="cVoixDefaut"></select></label>
      <label class="champ">Écoute après le mot de réveil (secondes)<input id="cFenetreDefaut" type="number" min="0" max="3600" step="5"></label>
      <label class="aide" style="display:flex;gap:8px;align-items:center;margin-top:10px"><input type="checkbox" id="cToujoursDefaut"> Toujours à l'écoute, sans mot de réveil</label>
      <div class="rangee"><button type="button" class="bouton principal" data-enr="defauts">Enregistrer</button><span class="aide" data-msg="defauts"></span></div>
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
    <details id="cfgComptes" hidden><summary>Personnes et droits <span class="etat" id="eComptes"></span></summary>
      <p class="aide">Inviter quelqu'un et choisir son groupe (Admin, Foyer, Extérieur, ou un groupe créé), avec quelques exceptions si besoin. Les groupes et leurs droits se règlent sur la même page.</p>
      <div class="rangee"><a class="bouton principal" href="/admin" style="text-decoration:none">Ouvrir la page Personnes et droits</a></div>
      <div id="cListeComptes"></div>
      <p class="aide" data-msg="comptes"></p>
    </details>
    <details id="cfgJournal" hidden><summary>Journal <span class="etat" id="eJournal"></span></summary>
      <div class="journal" id="cJournal"></div>
    </details>
    <details id="cfgServices"><summary>Annonces et météo <span class="etat" id="eServices"></span></summary>
      <label class="aide" style="display:flex;gap:8px;align-items:center;margin-top:10px"><input type="checkbox" id="cProActif"> L'assistant peut prendre la parole de lui-même</label>
      <label class="champ">Heures calmes (seules les urgences sont dites)<input id="cCalme" placeholder="22:00-07:30"></label>
      <label class="champ">Briefing du matin (heure, vide = jamais)<input id="cBriefing" placeholder="07:30"></label>
      <label class="champ">Ville par défaut pour la météo<input id="cVille" placeholder="ex. Douai"></label>
      <div class="rangee"><button type="button" class="bouton principal" data-enr="services">Enregistrer</button><span class="aide" data-msg="services"></span></div>
    </details>
    <details id="cfgAgenda"><summary>Agenda <span class="etat" id="eAgenda"></span></summary>
      <label class="champ">Agenda CalDAV : adresse du calendrier<input id="cAgendaUrl" placeholder="http://192.168.1.2:5232/benjamin/agenda/"></label>
      <label class="champ">Agenda : identifiant<input id="cAgendaUser" autocomplete="off"></label>
      <label class="champ">Agenda : mot de passe<input id="cAgendaMdp" type="password" autocomplete="off" placeholder="laisser vide pour garder l'actuel"></label>
      <label class="champ">Annoncer un rendez-vous (minutes avant)<input id="cAgendaAvant" type="number" min="0" max="240"></label>
      <div class="rangee"><button type="button" class="bouton principal" data-enr="agenda">Enregistrer</button><span class="aide" data-msg="agenda"></span></div>
    </details>
    <details id="cfgServeur"><summary>Serveur Unraid et Docker <span class="etat" id="eServeur"></span></summary>
      <label class="champ">Docker via l'API Unraid : adresse<input id="cUnraidUrl" placeholder="http://192.168.1.2/graphql"></label>
      <label class="champ">API Unraid : clé (rôle lecture seule)<input id="cUnraidCle" type="password" autocomplete="off" placeholder="laisser vide pour garder l'actuelle"></label>
      <label class="champ">API Unraid : clé pour redémarrer ou mettre à jour un conteneur (droits Docker seulement, facultatif)<input id="cUnraidCleActions" type="password" autocomplete="off" placeholder="laisser vide pour garder l'actuelle"></label>
      <label class="aide" style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="cMaj"> Prévenir quand une image Docker a une mise à jour (boutons « Mettre à jour / Ignorer »)</label>
      <div class="rangee"><label class="champ" style="flex:1">Disque plein à (%)<input id="cPlein" type="number" min="0" max="100"></label>
        <label class="champ" style="flex:1">Array : libre minimum (%)<input id="cLibreMin" type="number" min="0" max="100"></label>
        <label class="champ" style="flex:1">Parité : contrôle max (jours)<input id="cPariteJours" type="number" min="0" max="365"></label></div>
      <input id="cDockerUrl" type="hidden">
      <div class="rangee"><button type="button" class="bouton principal" data-enr="serveur">Enregistrer</button><span class="aide" data-msg="serveur"></span></div>
    </details>
    <details id="cfgTelegram"><summary>Telegram <span class="etat" id="eTelegram"></span></summary>
      <label class="champ">Telegram : jeton du bot (donné par @BotFather)<input id="cTgJeton" type="password" autocomplete="off" placeholder="laisser vide pour garder l'actuel"></label>
      <p class="aide">Alertes envoyées sur Telegram :</p>
      <div class="rangee"><label class="aide" style="margin:0"><input type="checkbox" id="cTgDocker"> conteneurs</label>
        <label class="aide" style="margin:0"><input type="checkbox" id="cTgDisques"> disques</label>
        <label class="aide" style="margin:0"><input type="checkbox" id="cTgAgenda"> rendez-vous</label>
        <label class="aide" style="margin:0"><input type="checkbox" id="cTgMaj"> mises à jour</label></div>
      <div class="rangee"><button type="button" class="bouton" id="cTgTrouver">Trouver mon chat</button>
        <button type="button" class="bouton" id="cTgTest">Message de test</button><span class="aide" data-msg="telegram"></span></div>
      <p class="aide">Telegram : crée un bot avec @BotFather, colle son jeton, enregistre et redémarre ; envoie « /start » à ton bot, puis « Trouver mon chat ». Les rappels d'une personne n'y partent que si elle a le droit « recevoir sur Telegram ».</p>
      <div class="rangee"><button type="button" class="bouton principal" data-enr="telegram">Enregistrer</button><span class="aide" data-msg="telegram-enr"></span></div>
    </details>
    <details id="cfgSysteme"><summary>Système <span class="etat" id="eSysteme"></span></summary>
      <p class="aide" id="cSystemeInfo"></p>
      <div class="rangee"><button type="button" class="bouton" id="cfgRedemarrer2">Redémarrer l'assistant</button><span class="aide" data-msg="systeme"></span></div>
    </details>`;
  const actions = dlg.querySelector('.actions');
  actions.parentNode.insertBefore(zone, actions);
  // --- rubriques (onglets) en haut de la fenêtre ------------------------------------------------------------
  // Affichage, Voix et écoute, Moi : à chaque personne (gardés dans son compte ou ce navigateur).
  // Les suivantes règlent toute la maison : administrateurs seulement.
  const RUBRIQUES = {
    affichage: ['Affichage', ['@affichage']],
    voix: ['Voix et écoute', ['@voix']],
    moi: ['Moi', ['cfgMonCompte', 'cfgMesOutils', 'cfgMemoire', 'cfgHabitudes', 'cfgRegles']],
    maison: ['Maison', ['cfgHA']],
    assistant: ['Assistant', ['cfgAssistant', 'cfgDefauts', 'cfgModeles', 'cfgClaude']],
    services: ['Services', ['cfgServices', 'cfgAgenda', 'cfgTelegram']],
    serveur: ['Serveur', ['cfgServeur']],
    securite: ['Sécurité', ['cfgComptes', 'cfgAcces', 'cfgJournal']],
    systeme: ['Système', ['cfgSysteme']],
  };
  const POUR_TOUS = ['affichage', 'voix', 'moi'];
  const titre = dlg.querySelector('h2');
  if (titre) titre.after($('rubriques')); else dlg.querySelector('form').prepend($('rubriques'));
  $('rubriques').hidden = false;
  let rubrique = 'affichage', estAdmin = false;
  try { rubrique = localStorage.getItem('reglages.rubrique') || 'affichage'; } catch (e) {}
  for (const [cle, [nom]] of Object.entries(RUBRIQUES)) {
    const b = Object.assign(document.createElement('button'), {type: 'button', textContent: nom});
    b.dataset.rubrique = cle;
    b.onclick = () => choisirRubrique(cle);
    $('rubriques').appendChild(b);
  }
  const blocs = ids => ids.map(id => id.startsWith('@') ? dlg.querySelector(`section.rubrique[data-rubrique="${id.slice(1)}"]`) : $(id));
  function choisirRubrique(cle) {
    if (!estAdmin && !POUR_TOUS.includes(cle)) cle = 'affichage';
    rubrique = cle;
    try { localStorage.setItem('reglages.rubrique', cle); } catch (e) {}
    for (const b of $('rubriques').children) {
      b.setAttribute('aria-pressed', String(b.dataset.rubrique === cle));
      b.hidden = !estAdmin && !POUR_TOUS.includes(b.dataset.rubrique);
    }
    for (const [r, [, ids]] of Object.entries(RUBRIQUES))
      for (const d of blocs(ids)) if (d) d.toggleAttribute('data-cache', r !== cle);
    $('cVersionHaut').hidden = cle !== 'systeme' && cle !== 'affichage';
  }
  window.choisirRubriqueReglages = choisirRubrique;
  function appliquerRole(admin) {
    estAdmin = admin;
    choisirRubrique(rubrique);
  }
  choisirRubrique(rubrique);

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
    const v_ = r.voix || {};
    $('cThemeDefaut').value = r.identite.theme || 'orbe';
    options($('cVoixDefaut'), (d.voix_disponibles || []).map(x => x.id), v_.voix,
            Object.fromEntries((d.voix_disponibles || []).map(x => [x.id, x.nom || x.id])));
    $('cFenetreDefaut').value = v_.fenetre_s ?? 30; $('cToujoursDefaut').checked = !!v_.toujours_a_l_ecoute;
    etat('eDefauts', (r.identite.theme === 'hal' ? 'HAL 9000' : 'Orbe') + ' · écoute ' + (v_.toujours_a_l_ecoute ? 'permanente' : (v_.fenetre_s ?? 30) + ' s'));
    etat('eAssistant', (r.identite.nom_effectif || r.identite.nom) + ' · « ' + r.identite.wake_word_effectif + ' »');

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

    const pa = r.proactif || {}, ag = r.agenda || {}, dk = r.docker || {};
    $('cProActif').checked = !!pa.active; $('cCalme').value = pa.heures_calmes || ''; $('cBriefing').value = pa.briefing_heure || '';
    $('cVille').value = pa.ville || ''; $('cAgendaUrl').value = ag.url || ''; $('cAgendaUser').value = ag.utilisateur || '';
    const tg = r.telegram || {};
    $('cTgDocker').checked = tg.docker !== false; $('cTgDisques').checked = tg.disques !== false; $('cTgAgenda').checked = tg.agenda !== false;
    $('cTgMaj').checked = !!tg.mises_a_jour; $('cMaj').checked = dk.mises_a_jour !== false;
    $('cPlein').value = dk.plein_pct ?? 95; $('cLibreMin').value = dk.libre_min_pct ?? 10; $('cPariteJours').value = dk.parite_max_jours ?? 45;
    $('cTgJeton').placeholder = tg.jeton_defini ? 'jeton enregistré — laisser vide pour le garder' : 'jeton du bot';
    $('cAgendaAvant').value = ag.annonce_avant_min ?? 15; $('cDockerUrl').value = dk.url || ''; $('cUnraidUrl').value = dk.unraid_url || '';
    etat('eServices', [pa.active ? 'parle de lui-même' : 'muet', pa.briefing_heure ? 'briefing ' + pa.briefing_heure : '', pa.ville || ''].filter(Boolean).join(' · '));
    etat('eAgenda', ag.url ? 'branché' : 'non branché', ag.url ? 'ok' : '');
    etat('eServeur', dk.unraid_url ? 'API Unraid' : (dk.url ? 'Docker' : 'non branché'), (dk.unraid_url || dk.url) ? 'ok' : '');
    etat('eTelegram', tg.jeton_defini ? (tg.chat_id ? 'relié' : 'chat à trouver') : 'non branché', tg.jeton_defini && tg.chat_id ? 'ok' : '');
    const v = e.voix || {};
    $('cSystemeInfo').textContent = `Version ${e.version} · ${e.conteneur ? 'conteneur Docker' : 'PC'} · transcription sur ${v.appareil || '?'} · voix ${v.moteur || '?'}`;
    etat('eSysteme', 'v' + e.version);
    $('cVersionHaut').textContent = 'Version ' + e.version;
  }

  async function charger() {
    let s = null;
    try { s = await api('/api/securite'); } catch (e) {}
    appliquerRole(!!(s && s.admin));
    if (s && s.admin) {
      try { afficher(await api('/api/reglages')); } catch (e) { etat('eSysteme', 'réglages indisponibles : ' + e.message, 'ko'); }
    }
    chargerComptes();
    chargerMemoire();
    chargerHabitudes();
    chargerRegles();
  }

  // --- comptes ---------------------------------------------------------------------------------
  const texte = (balise, contenu) => Object.assign(document.createElement(balise), {textContent: contenu});
  const DROITS = [['lumieres', 'lumières'], ['prises', 'prises'], ['volets', 'volets'], ['chauffage', 'chauffage'],
    ['medias', 'télé et musique'], ['ventilateurs', 'ventilateurs']];
  const quand = d => d ? new Date(d).toLocaleString('fr-FR', {dateStyle: 'short', timeStyle: 'short'}) : 'jamais';
  const LIBELLES = {connexion: 'connexion', 'déconnexion': 'déconnexion', action_maison: 'maison', 'réglages': 'réglages',
    secret_modifié: 'secret modifié', 'redémarrage': 'redémarrage', compte_créé: 'compte créé', compte_supprimé: 'compte supprimé',
    compte_réinitialisé: 'compte réinitialisé', invitation_acceptée: 'invitation acceptée', totp_activé: 'code téléphone activé',
    mot_de_passe_changé: 'mot de passe changé', codes_secours_regénérés: 'codes de secours', alias_oublié: 'alias oublié',
    modèle_téléchargé: 'modèle téléchargé', profil_modifié: 'profil modifié', appareil_partagé: 'appareil marqué partagé', appareil_personnel: 'appareil marqué personnel', voix_enregistrée: 'voix enregistrée', souvenir_ajouté: 'souvenir ajouté', souvenir_validé: 'souvenir validé',
    souvenir_refusé: 'souvenir refusé', souvenir_modifié: 'souvenir modifié', souvenir_supprimé: 'souvenir oublié', souvenir_restauré: 'souvenir restauré', compte_renommé: 'compte renommé', conteneur_redémarré: 'conteneur redémarré',
    automatisation_créée: 'automatisation créée', routine_créée: 'routine créée', routine_supprimée: 'routine supprimée', voix_oubliée: 'voix oubliée', totp_activation: 'code téléphone', invitation: 'invitation'};

  // --- mémoire --------------------------------------------------------------------------------------
  // L'écran complet est dans memoire.js (fenêtre « Mémoire ») ; ici, le résumé et un bouton pour l'ouvrir.
  function suivreMemoire(info) {
    $('cfgMemoire').hidden = !info;
    if (info) etat('eMemoire', info.a_valider ? info.a_valider + ' à valider' : info.total + ' souvenir(s)', info.a_valider ? 'ko' : '');
  }
  function chargerMemoire() { if (window.memoireAssistant) window.memoireAssistant.charger(); }
  (function chargerScriptMemoire() {
    const moi = document.currentScript && document.currentScript.src;
    const sc = document.createElement('script');
    sc.src = '/static/memoire.js' + (moi && moi.includes('?') ? moi.slice(moi.indexOf('?')) : '');
    sc.onload = () => { if (window.memoireAssistant) window.memoireAssistant.suivre(suivreMemoire); };
    document.body.appendChild(sc);
  })();
  $('cMemoireOuvrir').onclick = () => { if (window.memoireAssistant) window.memoireAssistant.ouvrir(); };
  const VERBES_R = {allumer: 'allumer', eteindre: 'éteindre', ouvrir: 'ouvrir', fermer: 'fermer', regler: 'régler'};
  async function chargerHabitudes() {
    let h = {habitudes: []}, r;
    try { r = await api('/api/routines'); }
    catch (e) { $('cfgHabitudes').hidden = true; return; }
    try { h = await api('/api/habitudes'); } catch (e) {}     // tirées de l'historique : selon les droits
    let au = {automatisations: []};
    try { au = await api('/api/automatisations'); } catch (e) {}
    $('cAutosTitre').hidden = !au.automatisations.length;
    $('cAutosListe').replaceChildren(...au.automatisations.map(a => {
      const ligne = document.createElement('div'); ligne.className = 'compte';
      const t = texte('div', a.alias);
      t.appendChild(texte('small', 'créée le ' + quand(a.cree) + (a.auteur ? ' par ' + a.auteur : '')));
      const bt = texte('button', 'Supprimer'); bt.type = 'button'; bt.className = 'bouton';
      bt.onclick = async () => {
        if (!confirm(`Supprimer de Home Assistant : ${a.alias} ?`)) return;
        const rep = await fetch(`/api/automatisations/${encodeURIComponent(a.id)}`, {method: 'DELETE'});
        msg('habitudes', rep.ok ? 'Automatisation supprimée.' : 'Suppression impossible.', rep.ok);
        chargerHabitudes();
      };
      ligne.append(t, bt);
      return ligne;
    }));
    $('cfgHabitudes').hidden = false;
    etat('eHabitudes', `${h.habitudes.length} habitude(s) · ${r.routines.length} routine(s)`);
    const lh = $('cHabitudesListe'); lh.replaceChildren();
    if (!h.habitudes.length) lh.appendChild(texte('p', 'Rien de régulier pour l\'instant.')).className = 'aide';
    for (const x of h.habitudes) {
      const ligne = document.createElement('div'); ligne.className = 'compte';
      const b = document.createElement('div'); b.className = 'rangee'; b.style.marginTop = '0';
      for (const [lib, dec] of [['Automatiser', 'accepter'], ['Ignorer', 'ignorer']]) {
        const bt = texte('button', lib); bt.type = 'button'; bt.className = 'bouton' + (dec === 'accepter' ? ' principal' : '');
        bt.onclick = async () => {
          if (dec === 'accepter' && !confirm(`Créer dans Home Assistant : ${x.texte} ?`)) return;
          try { const o = await api(`/api/habitudes/${dec}`, {id: x.id}); msg('habitudes', dec === 'accepter' ? 'Automatisation créée : ' + o.automatisation : 'Ignorée.', true); }
          catch (e) { msg('habitudes', e.message, false); }
          chargerHabitudes();
        };
        b.appendChild(bt);
      }
      ligne.append(texte('div', x.texte), b); lh.appendChild(ligne);
    }
    const lr = $('cRoutinesListe'); lr.replaceChildren();
    for (const rt of r.routines) {
      const ligne = document.createElement('div'); ligne.className = 'compte';
      const t = texte('div', rt.nom);
      t.appendChild(texte('small', (rt.auteur ? 'de ' + rt.auteur + ' · ' : '') + rt.etapes.map(e => `${VERBES_R[e.action] || e.action} ${e.entity_ids.join(', ')}${e.valeur != null ? ' ' + e.valeur : ''}${e.couleur ? ' ' + e.couleur : ''}`).join(' · ')));
      const bt = texte('button', 'Supprimer'); bt.type = 'button'; bt.className = 'bouton';
      bt.onclick = async () => {
        if (!confirm(`Supprimer la routine « ${rt.nom} » ?`)) return;
        const rep = await fetch(`/api/routines/${encodeURIComponent(rt.nom)}`, {method: 'DELETE'});
        msg('habitudes', rep.ok ? 'Routine supprimée.' : 'Suppression impossible.', rep.ok);
        chargerHabitudes();
      };
      ligne.append(t, bt); lr.appendChild(ligne);
    }
  }


  async function chargerRegles() {
    let r;
    try { r = await api('/api/regles'); } catch (e) { $('cfgRegles').hidden = true; return; }
    $('cfgRegles').hidden = false;
    const actives = r.regles.filter(x => x.active).length;
    etat('eRegles', r.regles.length ? `${actives} active(s)` + (actives < r.regles.length ? ` · ${r.regles.length - actives} en pause` : '') : 'aucune');
    const l = $('cReglesListe'); l.replaceChildren();
    if (!r.regles.length) l.appendChild(texte('p', 'Aucune règle pour l\'instant.')).className = 'aide';
    for (const x of r.regles) {
      const ligne = document.createElement('div'); ligne.className = 'compte';
      const t = texte('div', x.texte);
      if (!x.active) t.style.opacity = '.55';
      t.appendChild(texte('small', (x.auteur ? 'de ' + x.auteur + ' · ' : '') + (x.active ? '' : 'En pause · ') + 'Dernier déclenchement : ' + quand(x.derniere)
        + (x.resultat ? ' (' + x.resultat + ')' : '')));
      const b = document.createElement('div'); b.className = 'rangee'; b.style.marginTop = '0';
      const pause = texte('button', x.active ? 'Mettre en pause' : 'Reprendre'); pause.type = 'button';
      pause.className = 'bouton' + (x.active ? '' : ' principal');
      pause.onclick = async () => {
        try { await api(`/api/regles/${encodeURIComponent(x.id)}`, {active: !x.active}); msg('regles', x.active ? 'Règle en pause.' : 'Règle reprise.', true); }
        catch (e) { msg('regles', e.message, false); }
        chargerRegles();
      };
      const sup = texte('button', 'Supprimer'); sup.type = 'button'; sup.className = 'bouton';
      sup.onclick = async () => {
        if (!confirm(`Supprimer la règle « ${x.texte} » ?`)) return;
        const rep = await fetch(`/api/regles/${encodeURIComponent(x.id)}`, {method: 'DELETE'});
        msg('regles', rep.ok ? 'Règle supprimée.' : 'Suppression impossible.', rep.ok);
        chargerRegles();
      };
      b.append(pause, sup);
      ligne.append(t, b); l.appendChild(ligne);
    }
  }

  async function chargerComptes() {
    let s;
    try { s = await api('/api/securite'); } catch (e) { return; }
    const machine = s.compte === 'machine';
    $('cfgMonCompte').hidden = machine || !s.compte;
    if (!machine && s.compte) {
      etat('eMonCompte', s.compte_nom + (s.admin ? ' · admin' : '') + (s.totp ? ' · code téléphone' : ''), s.totp ? 'ok' : '');
      if (document.activeElement !== $('cMonNom')) $('cMonNom').value = s.compte_nom || '';
      try {
        const {profil} = await api('/api/compte/profil');
        $('cTon').value = profil.ton; $('cPiece').value = profil.piece || ''; $('cConsigne').value = profil.consigne || '';
      } catch (e) {}
      afficherContexte();
      afficherVoix();
      afficherMesOutils();
    }
    $('cfgComptes').hidden = $('cfgJournal').hidden = !s.admin;
    if (!s.admin) return;
    try {
      // Résumé ; la gestion complète (groupe de chacun, exceptions, groupes et leurs droits) est sur la page /admin
      const {comptes} = await api('/api/comptes');
      etat('eComptes', comptes.length + ' compte' + (comptes.length > 1 ? 's' : ''));
      $('cListeComptes').replaceChildren(...comptes.map(c => {
        const ligne = document.createElement('div'); ligne.className = 'compte';
        const nom = texte('div', c.nom + ' · ' + ({admin: 'Admin', foyer: 'Foyer', exterieur: 'Extérieur'}[c.groupe] || c.groupe));
        nom.appendChild(texte('small', (!c.actif ? 'invitation en attente' : 'dernière connexion : ' + quand(c.derniere_connexion))
          + (c.admin ? '' : ' · ' + c.effectives.length + ' droit(s)')));
        ligne.append(nom);
        return ligne;
      }));
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

  // --- son agenda et son Telegram (selon ses droits) --------------------------------------------------
  async function afficherMesOutils() {
    let o;
    try { o = await api('/api/compte/outils'); } catch (e) { $('cfgMesOutils').hidden = true; return; }
    $('cfgMesOutils').hidden = !(o.agenda.permis || o.telegram.permis);
    $('cMonAgendaBloc').hidden = !o.agenda.permis;
    $('cMonTgBloc').hidden = !o.telegram.permis;
    if (document.activeElement !== $('cMonAgendaUrl')) $('cMonAgendaUrl').value = o.agenda.url;
    if (document.activeElement !== $('cMonAgendaUser')) $('cMonAgendaUser').value = o.agenda.utilisateur;
    $('cMonAgendaMdp').placeholder = o.agenda.mdp_defini ? 'enregistré — laisser vide pour le garder' : 'mot de passe';
    // Ici : TON agenda et TON Telegram. Ceux de la maison (Réglages › Services) sont rappelés pour ne pas confondre.
    $('cMonAgendaFamille').textContent = o.agenda.famille ? "Tu lis aussi l'agenda familial (réglé dans Services) : rien à faire ici pour lui." : '';
    $('cMonTgEtat').textContent = !o.telegram.bot ? "pas de bot Telegram (un administrateur doit le brancher)"
      : o.telegram.relie ? 'ton Telegram est relié (' + o.telegram.relie + ')'
      : o.telegram.maison ? "tu reçois sur le Telegram de la maison (réglé dans Services) ; relie le tien pour l'avoir à part"
      : 'pas relié';
    $('cMonTgCode').hidden = !o.telegram.bot || !!o.telegram.relie;
    $('cMonTgOff').hidden = !o.telegram.relie;
    etat('eMesOutils', [o.agenda.permis ? (o.agenda.url ? 'ton agenda branché' : (o.agenda.famille ? 'agenda familial seulement' : "pas d'agenda")) : '',
      o.telegram.permis ? (o.telegram.relie ? 'ton Telegram relié' : (o.telegram.maison ? 'Telegram de la maison' : 'Telegram pas relié')) : ''].filter(Boolean).join(' · '));
  }
  $('cMonAgendaBtn').onclick = async () => {
    try {
      const o = await api('/api/compte/agenda', {url: $('cMonAgendaUrl').value.trim(), utilisateur: $('cMonAgendaUser').value.trim(),
                                                  mot_de_passe: $('cMonAgendaMdp').value});
      $('cMonAgendaMdp').value = '';
      msg('monagenda', o.branche ? `Branché : ${o.evenements_7_jours} rendez-vous dans les 7 jours.` : 'Débranché.', true);
    } catch (e) { msg('monagenda', e.message, false); }
    afficherMesOutils();
  };
  $('cMonAgendaOff').onclick = async () => {
    if (!confirm('Débrancher ton agenda ? Les rendez-vous restent dans ton calendrier.')) return;
    $('cMonAgendaUrl').value = '';
    $('cMonAgendaBtn').click();
  };
  $('cMonTgCode').onclick = async () => {
    try {
      const o = await api('/api/compte/telegram/code', {});
      msg('montg', `Dans Telegram, envoie « ${o.code} » à ${o.bot ? '@' + o.bot : 'ton bot'} (valable ${o.expire_min} min), puis clique « J'ai envoyé le code ».`, true);
      $('cMonTgVerif').hidden = false;
    } catch (e) { msg('montg', e.message, false); }
  };
  $('cMonTgVerif').onclick = async () => {
    try {
      const o = await api('/api/compte/telegram/relier', {});
      msg('montg', `Relié à ${o.nom || 'ton Telegram'} : un message vient de t'y être envoyé.`, true);
      $('cMonTgVerif').hidden = true;
    } catch (e) { msg('montg', e.message, false); }
    afficherMesOutils();
  };
  $('cMonTgOff').onclick = async () => {
    try { const r = await fetch('/api/compte/telegram', {method: 'DELETE'}); if (!r.ok) throw new Error('erreur ' + r.status); msg('montg', 'Délié.', true); }
    catch (e) { msg('montg', e.message, false); }
    afficherMesOutils();
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

  // --- empreinte vocale : quelques phrases lues à voix haute -----------------------------------------
  const PHRASES = ['Le soleil se lève sur la maison et la journée commence doucement.',
    'Allume la lumière du salon et baisse un peu les volets de la chambre.',
    'Quel temps fera-t-il demain matin, et faut-il prendre un parapluie ?',
    'Je rentre dans une heure, pense à mettre un peu de musique.',
    'Les enfants dorment déjà, parle doucement s\'il te plaît.'];
  async function afficherVoix() {
    try {
      const v = await api('/api/compte/voix');
      $('cVoixEtat').textContent = !v.disponible ? 'modèle absent (il se télécharge au démarrage du conteneur)'
        : v.echantillons ? `${v.echantillons} phrase(s) enregistrée(s)` + (v.echantillons < v.conseille ? ` — ${v.conseille} conseillées` : ' — reconnue')
        : 'pas encore enregistrée';
      $('cVoixBtn').disabled = !v.disponible;
    } catch (e) { $('cVoixEtat').textContent = e.message; }
  }
  $('cVoixBtn').onclick = async () => {
    let flux;
    try { flux = await navigator.mediaDevices.getUserMedia({audio: true}); }
    catch (e) { msg('voix', 'Micro refusé ou indisponible.', false); return; }
    const phrase = PHRASES[Math.floor(Math.random() * PHRASES.length)];
    $('cVoixPhrase').hidden = false;
    $('cVoixPhrase').textContent = 'Lis à voix haute : « ' + phrase + ' »';
    const morceaux = [], rec = new MediaRecorder(flux);
    rec.ondataavailable = e => { if (e.data.size) morceaux.push(e.data); };
    rec.onstop = async () => {
      flux.getTracks().forEach(t => t.stop());
      $('cVoixPhrase').hidden = true; $('cVoixBtn').disabled = false;
      try {
        const r = await fetch('/api/compte/voix', {method: 'POST', body: new Blob(morceaux, {type: rec.mimeType})});
        const o = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(o.detail || 'erreur ' + r.status);
        msg('voix', 'Phrase enregistrée.', true);
      } catch (e) { msg('voix', e.message, false); }
      afficherVoix();
    };
    $('cVoixBtn').disabled = true;
    msg('voix', 'Enregistrement…', true);
    rec.start();
    setTimeout(() => rec.state !== 'inactive' && rec.stop(), 5000);
  };
  $('cVoixOublier').onclick = async () => {
    if (!confirm('Oublier ta voix ? Il faudra la réenregistrer pour être reconnu.')) return;
    try {
      const r = await fetch('/api/compte/voix', {method: 'DELETE'});
      if (!r.ok) throw new Error('erreur ' + r.status);
      msg('voix', 'Voix oubliée.', true);
    } catch (e) { msg('voix', e.message, false); }
    afficherVoix();
  };

  $('cMonNomBtn').onclick = async () => {
    const nom = $('cMonNom').value.trim();
    if (!nom) return;
    try {
      const o = await api('/api/compte/nom', {nom});
      msg('nom', `C'est ${o.nom}` + (o.souvenirs_mis_a_jour ? ` (${o.souvenirs_mis_a_jour} souvenir(s) mis à jour)` : '') + `. Identifiant de connexion inchangé : ${o.identifiant}.`, true);
      chargerComptes(); chargerMemoire();
    } catch (e) { msg('nom', e.message, false); }
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
    } else if (b.dataset.enr === 'defauts') {
      const voixD = {fenetre_s: Math.max(0, Math.min(3600, +$('cFenetreDefaut').value || 0)), toujours_a_l_ecoute: $('cToujoursDefaut').checked};
      if ($('cVoixDefaut').value) voixD.voix = $('cVoixDefaut').value;
      enregistrer('defauts', {identite: {theme: $('cThemeDefaut').value}, voix: voixD});
    } else if (b.dataset.enr === 'modeles') {
      const f = {};
      if (idClaude) f[idClaude] = {modele: $('cClaudeModele').value};
      if (idOllama) f[idOllama] = {modele: $('cOllamaModele').value, url: $('cOllamaUrl').value.trim()};
      enregistrer('modeles', {roles: {rapide: $('cRoleRapide').value, raisonnement: $('cRoleRaison').value, secours: $('cRoleSecours').value}, fournisseurs: f});
    } else if (b.dataset.enr === 'ha') {
      const jeton = $('cHaJeton').value.trim();
      enregistrer('ha', {home_assistant: {url: $('cHaUrl').value.trim()}},
        jeton ? async () => { await api('/api/reglages/secret', {nom: 'HA_JETON', valeur: jeton}); $('cHaJeton').value = ''; } : null);
    } else if (['services', 'agenda', 'serveur', 'telegram'].includes(b.dataset.enr)) {
      // les quatre rubriques partagent un enregistrement (mêmes sections du fichier de réglages)
      const mdp = $('cAgendaMdp').value;
      enregistrer(b.dataset.enr === 'telegram' ? 'telegram-enr' : b.dataset.enr, {
        proactif: {active: $('cProActif').checked, heures_calmes: $('cCalme').value.trim(), briefing_heure: $('cBriefing').value.trim(), ville: $('cVille').value.trim()},
        agenda: {url: $('cAgendaUrl').value.trim(), utilisateur: $('cAgendaUser').value.trim(), annonce_avant_min: +$('cAgendaAvant').value || 0},
        docker: {url: $('cDockerUrl').value.trim(), unraid_url: $('cUnraidUrl').value.trim(), mises_a_jour: $('cMaj').checked,
                 plein_pct: +$('cPlein').value || 0, libre_min_pct: +$('cLibreMin').value || 0, parite_max_jours: +$('cPariteJours').value || 0},
        telegram: {docker: $('cTgDocker').checked, disques: $('cTgDisques').checked, agenda: $('cTgAgenda').checked,
                   mises_a_jour: $('cTgMaj').checked}},
        async () => {
          if (mdp) { await api('/api/reglages/secret', {nom: 'AGENDA_MOT_DE_PASSE', valeur: mdp}); $('cAgendaMdp').value = ''; }
          const cle = $('cUnraidCle').value.trim();
          if (cle) { await api('/api/reglages/secret', {nom: 'UNRAID_CLE', valeur: cle}); $('cUnraidCle').value = ''; }
          const cleA = $('cUnraidCleActions').value.trim();
          if (cleA) { await api('/api/reglages/secret', {nom: 'UNRAID_CLE_ACTIONS', valeur: cleA}); $('cUnraidCleActions').value = ''; }
          const tgj = $('cTgJeton').value.trim();
          if (tgj) { await api('/api/reglages/secret', {nom: 'TELEGRAM_JETON', valeur: tgj}); $('cTgJeton').value = ''; }
        });
    } else if (b.dataset.enr === 'acces') {
      enregistrer('acces', {securite: {proxies_de_confiance: liste($('cProxies').value), reseaux_autorises: liste($('cReseaux').value),
                                        session_jours: +$('cSession').value}});
    }
  });

  for (const [id, action, ok] of [['cTgTrouver', 'trouver', o => `Relié au chat de ${o.nom || o.chat_id} : un message vient de t'être envoyé.`],
                                  ['cTgTest', 'test', () => 'Message envoyé.']]) {
    $(id).onclick = async () => {
      try { msg('telegram', ok(await api('/api/telegram/' + action, {})), true); }
      catch (e) { msg('telegram', e.message, false); }
    };
  }

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

  // Au chargement de la page : le menu ne propose que ce que ce compte a le droit d'ouvrir
  (async () => {
    try {
      const s = await api('/api/securite');
      const p = new Set(s.permissions || []);
      for (const [id, perm] of [['ouvrirHisto', 'maison.historique'], ['ouvrirAgenda', 'agenda.lire']]) {
        const b = $(id);
        if (b && s.compte) b.style.display = p.has(perm) ? '' : 'none';
      }
      appliquerRole(!!s.admin);
    } catch (e) {}
  })();

  // chargement à chaque ouverture du panneau
  new MutationObserver(() => { if (dlg.open) charger(); }).observe(dlg, {attributes: true, attributeFilter: ['open']});
  if (dlg.open) charger();
})();
