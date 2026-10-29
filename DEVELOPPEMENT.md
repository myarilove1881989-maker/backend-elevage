# Développement Élev’Age

Référence officielle : https://github.com/myarilove1881989-maker/backend-elevage. Un clone courant : D:/ElevAge-Dev/backend. `develop` reçoit les développements validés ; `feature/...` porte chaque modification. Les branches de production restent inchangées. Pas d’auto-publication depuis develop/feature.

## Reprise ordinaire dans PowerShell

Depuis ce dépôt :

```powershell
./tools/dev.ps1 setup
./tools/dev.ps1 start -Name correction-ciblee
# Modifier les sources et choisir les tests correspondant au diff.
./tools/dev.ps1 test
./tools/dev.ps1 save -Message "Corriger le comportement observé"
./tools/dev.ps1 push
```

Ouvrir ensuite une PR de feature/... vers develop. La fusion vers develop ne publie pas. Après validation, Work peut intégrer dans develop sans toucher aux branches Render. Pour reprendre un travail en cours : `git fetch origin`, puis `git switch feature/nom` ; ne pas refaire start.

Python est partagé dans D:/ElevAge-Dev/.venv ; Flutter est verrouillé sur le SDK de 113, configurable par ELEVAGE_FLUTTER_SDK et ELEVAGE_PUB_CACHE. Les caches Gradle, Python, Android et temporaires vont sur D:. Les artefacts restent hors Git.

Backend : `./tools/dev.ps1 test -Postgres` lance uniquement une base PostgreSQL locale synthétique sur 127.0.0.1:57831, teste les migrations et les contrats concernés puis arrête le serveur. Aucun DSN distant accepté. SQLite rapide : `./tools/dev.ps1 test`.

Flutter : `./tools/dev.ps1 test -Tests test/nom_test.dart` ; build Web : `./tools/dev.ps1 build-web`. Les 52 tests du candidat du 29 octobre sont des preuves réutilisables si leurs sources sont identiques. La CI de développement ne déploie rien.

Gardes : hook pre-push (develop/feature uniquement), scan de secrets avant commit/push, opérations Git sans force et mise à jour ff-only. `setup` les active sur chaque nouveau clone. Ces protections locales ne remplacent pas des règles serveur GitHub ; aucune protection serveur ou permission sensible n’a été changée.
