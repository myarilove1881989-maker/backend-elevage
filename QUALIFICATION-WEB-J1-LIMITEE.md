# Qualification J1 — publication Web limitée

Périmètre explicitement retenu le 30 octobre 2026 : Agenda propriétaire, attribution des tâches aux opérateurs, consultation des collectes et conservation du Web existant. Aucun droit terrain supplémentaire au propriétaire ; accès opérateur Web toujours refusé.

Les corrections Android hors connexion restent dans `feature/recette-5jours-corrections` pour la compilation finale après les cinq jours. Aucun APK ni changement de version, tablette ou candidat 117.

## Backend

Le test de contrat de migration attend désormais les 38 migrations existantes et la feuille `0038_passwordresetlimit`. Aucun fichier de migration, modèle, service métier, paramètre ou donnée n’est modifié par cette levée des réserves. Les gardes de démarrage restent applicables ; aucune autorisation de migration production n’est accordée.

## Lanceur Chrome

`test` est déclaré en dépendance de développement (version résolue 1.31.1, compatible avec `test_api` 0.7.12 imposé par Flutter 3.47.6). Les nouvelles dépendances de test sont verrouillées. Le résolveur exige aussi `package_config` 2.2.0 au lieu de 3.0.0 ; les autres versions et empreintes des dépendances déjà présentes sont inchangées. Les tests natifs doivent être relancés avec ce lockfile. La qualification d’une future compilation Android reste une étape séparée, sans build Android dans cette mission.

Sur Windows, le serveur de tests du SDK convertit les séparateurs du chemin CanvasKit avant une comparaison avec `/`, entraînant une ressource introuvable. Le lanceur Node sert uniquement les fichiers CanvasKit du même SDK verrouillé au navigateur headless qu’il vient de créer. Les fichiers et le SDK ne sont pas modifiés ; leurs empreintes sont consignées dans `.local/web-chrome-qualification/audit.json`. Les requêtes HTTP extérieures au loopback sont refusées par ce lanceur Windows. Aucun contrôle de rôle ou réponse métier n’est remplacé par ce correctif : seuls les fichiers JS/WASM du moteur sont servis. Les services applicatifs des tests utilisent leurs faux transports synthétiques habituels.

Sur Linux, le lanceur exécute directement les mêmes tests Chrome ; le contournement de chemin Windows ne s’applique pas. Le job CI de développement comporte cette passe, sans étape de déploiement.

Exécution sur Windows (Node 22+ et Chrome requis) :

```powershell
$env:CHROME_EXECUTABLE='C:/Program Files/Google/Chrome/Application/chrome.exe'
./tools/dev.ps1 test-web
```

La commande vérifie la révision Flutter `5fc346839b5d0eef006ed8404392afb4dfae428d`. Elle ne compile aucun APK et ne publie rien.

## Recette couverte

Le test navigateur ouvre le menu Agenda propriétaire, remplit une tâche et choisit un opérateur actif de la même exploitation ; il exclut les comptes inactifs et étrangers. Il consulte une collecte 120 bruts / 3 cassés / 117 nets sans commande d’écriture terrain pour le propriétaire. Il vérifie le refus de la connexion opérateur Web et du droit de correction propriétaire. Les tests de connexion/inscription et récupération de compte sont conservés ; la sonde de santé du faux service d’authentification est désormais simulée pour ne pas laisser de temporisations réseau réelles.

Les résultats, les commits finaux, le build release et le plan de publication sont consignés dans le rapport de qualification. Le GO éventuel concerne ce périmètre Web limité ; ce n’est ni une autorisation de déploiement ni une validation physique Android.
