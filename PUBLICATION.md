# Publication contrÃ´lÃ©e et retour arriÃ¨re

Aucune publication dans cette mission. DÃ©veloppement sur D:, source officielle sur GitHub, production sur Render. Une PR vers develop nâ€™est pas une autorisation de publication.

## Backend Django
RÃ©fÃ©rence production vÃ©rifiÃ©e le 29 octobre 2026 : c14f4899c06087508aa4ba7604de35decc74f94a, branche codex/android-users-compatibility-20261027, dÃ©ploiement automatique dÃ©sactivÃ©. La commande de dÃ©marrage existante exÃ©cute core.production_upgrade : toute livraison backend peut donc entraÃ®ner des Ã©critures/migrations et nÃ©cessite un accord distinct et explicite. Ne pas changer ces paramÃ¨tres pendant le dÃ©veloppement.

Nouvelle migration additive 0038_passwordresetlimit aprÃ¨s 0037. Valider migration et droits sur PostgreSQL de test. La rÃ©cupÃ©ration conserve lâ€™audit et la rotation des identifiants existants, donc rÃ©vocation des sessions et autorisations selon le protocole V2 sans effacement de donnÃ©es appareil. Ã‰valuer la reprise opÃ©rateur avant livraison.

E-mail : configurer hors Git EMAIL_HOST/PORT/USER/PASSWORD, DEFAULT_FROM_EMAIL et TLS ou SSL, SECRET_KEY stable ; planifier send_password_reset_emails idÃ©alement chaque minute et cleanup_password_resets. La file chiffrÃ©e nâ€™envoie rien sans exÃ©cution de la commande. Test SMTP rÃ©el uniquement aprÃ¨s accord sur la configuration et le destinataire. Aucun secret dans les preuves.

## Flutter Web
Site existant elevage-mobile.onrender.com : master auto-dÃ©ploie, commande actuellement API pilote. Ne pas pousser master. Le candidat se compile avec APP_ENV=production et API_URL=https://backend-elevage.onrender.com/api. PrÃ©parer un changement contrÃ´lÃ© de commande Render et confirmer CORS pour lâ€™origine Web avant accord de publication. Pas dâ€™Ã©criture mÃ©tier de test dans la base production. Le Web est rÃ©servÃ© au propriÃ©taire ; les opÃ©rateurs restent sur Android avec appareil V2.

## Flutter Android
MÃªme code Flutter que le Web. Avant une future livraison : incrÃ©menter le numÃ©ro au-delÃ  de 113 avec accord, utiliser le flavor production, API identique, certificat permanent existant chargÃ© hors Git. Comparer certificat/applicationId/version avant distribution. Ne pas remplacer lâ€™APK 113 de rÃ©fÃ©rence ni rÃ©installer la tablette dans cette mission. Un rollback Android ne doit pas forcer un downgrade ni perdre les donnÃ©es ; privilÃ©gier une version corrective au numÃ©ro supÃ©rieur.

## Validation et accord de publication
PrÃ©senter commits backend/Flutter compatibles, artefacts avec SHA256, rÃ©sultats ciblÃ©s, compatibilitÃ© migrations, configuration e-mail, tests navigateur propriÃ©taire/opÃ©rateur et reset, CORS et reprise V2. Le candidat reste NO-GO tant que les points ouverts dans le rapport ne sont pas clos.

## Retour arriÃ¨re
Conserver le dÃ©ploiement et lâ€™artefact Web prÃ©cÃ©dents, commandes Render et rÃ©fÃ©rences de branches. AprÃ¨s accord, republier lâ€™artefact Web prÃ©cÃ©dent avec sa configuration cohÃ©rente ; ne pas faire de push forcÃ©. Revenir au backend prÃ©cÃ©dent seulement si compatible avec le schÃ©ma courant ; conserver les tables additives de 0038 plutÃ´t que supprimer les donnÃ©es. Un rollback de code ne restaure pas la base. Toute restauration de base est une dÃ©cision sÃ©parÃ©e. Android 113 et donnÃ©es locales ne sont jamais utilisÃ©s comme variables de rollback serveur.

## Blocage précis du démarrage avec 0038
Le code existant core.production_upgrade attend exactement 37 migrations core et 0037_offline_business_revocation. Avec le nouveau code et une base encore en 0037, migrate --check refuse 0038 en attente. Après une migration externe vers 0038, son contrôle exact de schéma refuse également cet état. Il faut donc préparer et qualifier une adaptation contrôlée de cette garde avant une future publication backend ; ne pas lancer le démarrage actuel avec ce candidat en production. Les tests PostgreSQL valident les migrations Django, pas cette procédure de démarrage production. Aucun contournement ni changement de cette garde n’a été réalisé dans cette mission d’organisation.
