# DÃ©veloppement Ã‰levâ€™Age

RÃ©fÃ©rence officielle : https://github.com/myarilove1881989-maker/backend-elevage. Un clone courant : D:/ElevAge-Dev/backend. `develop` reÃ§oit les dÃ©veloppements validÃ©s ; `feature/...` porte chaque modification. Les branches de production restent inchangÃ©es. Pas dâ€™auto-publication depuis develop/feature.

## Reprise ordinaire dans PowerShell

Depuis ce dÃ©pÃ´t :

```powershell
./tools/dev.ps1 setup
./tools/dev.ps1 start -Name correction-ciblee
# Modifier les sources et choisir les tests correspondant au diff.
./tools/dev.ps1 test
./tools/dev.ps1 save -Message "Corriger le comportement observÃ©"
./tools/dev.ps1 push
```

Ouvrir ensuite une PR de feature/... vers develop. La fusion vers develop ne publie pas. AprÃ¨s validation, Work peut intÃ©grer dans develop sans toucher aux branches Render. Pour reprendre un travail en cours : `git fetch origin`, puis `git switch feature/nom` ; ne pas refaire start.

Python est partagÃ© dans D:/ElevAge-Dev/.venv ; Flutter est verrouillÃ© sur le SDK de 113, configurable par ELEVAGE_FLUTTER_SDK et ELEVAGE_PUB_CACHE. Les caches Gradle, Python, Android et temporaires vont sur D:. Les artefacts restent hors Git.

Backend : `./tools/dev.ps1 test -Postgres` lance uniquement une base PostgreSQL locale synthÃ©tique sur 127.0.0.1:57831, teste les migrations et les contrats concernÃ©s puis arrÃªte le serveur. Aucun DSN distant acceptÃ©. SQLite rapide : `./tools/dev.ps1 test`.

Flutter : `./tools/dev.ps1 test -Tests test/nom_test.dart` ; build Web : `./tools/dev.ps1 build-web`. Les 52 tests du candidat du 29 octobre sont des preuves rÃ©utilisables si leurs sources sont identiques. La CI de dÃ©veloppement ne dÃ©ploie rien.

Gardes : hook pre-push (develop/feature uniquement), scan de secrets avant commit/push, opÃ©rations Git sans force et mise Ã  jour ff-only. `setup` les active sur chaque nouveau clone. Ces protections locales ne remplacent pas des rÃ¨gles serveur GitHub ; aucune protection serveur ou permission sensible nâ€™a Ã©tÃ© changÃ©e.

La validation GitHub se lance sur les PR vers develop (ou manuellement), sans déploiement ni secret production. Le mot de passe du conteneur PostgreSQL CI est une valeur synthétique réservée à ce conteneur éphémère.
