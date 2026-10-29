# Publication contrôlée et retour arrière

Aucune publication dans cette mission. Développement sur D:, source officielle sur GitHub, production sur Render. Une PR vers develop n’est pas une autorisation de publication.

## Backend Django
Référence production vérifiée le 29 octobre 2026 : c14f4899c06087508aa4ba7604de35decc74f94a, branche codex/android-users-compatibility-20261027, déploiement automatique désactivé. La commande de démarrage existante exécute core.production_upgrade : toute livraison backend peut donc entraîner des écritures/migrations et nécessite un accord distinct et explicite. Ne pas changer ces paramètres pendant le développement.

Nouvelle migration additive 0038_passwordresetlimit après 0037. Valider migration et droits sur PostgreSQL de test. La récupération conserve l’audit et la rotation des identifiants existants, donc révocation des sessions et autorisations selon le protocole V2 sans effacement de données appareil. Évaluer la reprise opérateur avant livraison.

E-mail : configurer hors Git EMAIL_HOST/PORT/USER/PASSWORD, DEFAULT_FROM_EMAIL et TLS ou SSL, SECRET_KEY stable ; planifier send_password_reset_emails idéalement chaque minute et cleanup_password_resets. La file chiffrée n’envoie rien sans exécution de la commande. Test SMTP réel uniquement après accord sur la configuration et le destinataire. Aucun secret dans les preuves.

## Flutter Web
Site existant elevage-mobile.onrender.com : master auto-déploie, commande actuellement API pilote. Ne pas pousser master. Le candidat se compile avec APP_ENV=production et API_URL=https://backend-elevage.onrender.com/api. Préparer un changement contrôlé de commande Render et confirmer CORS pour l’origine Web avant accord de publication. Pas d’écriture métier de test dans la base production. Le Web est réservé au propriétaire ; les opérateurs restent sur Android avec appareil V2.

## Flutter Android
Même code Flutter que le Web. Avant une future livraison : incrémenter le numéro au-delà de 113 avec accord, utiliser le flavor production, API identique, certificat permanent existant chargé hors Git. Comparer certificat/applicationId/version avant distribution. Ne pas remplacer l’APK 113 de référence ni réinstaller la tablette dans cette mission. Un rollback Android ne doit pas forcer un downgrade ni perdre les données ; privilégier une version corrective au numéro supérieur.

## Validation et accord de publication
Présenter commits backend/Flutter compatibles, artefacts avec SHA256, résultats ciblés, compatibilité migrations, configuration e-mail, tests navigateur propriétaire/opérateur et reset, CORS et reprise V2. Le candidat reste NO-GO tant que les points ouverts dans le rapport ne sont pas clos.

## Retour arrière
Conserver le déploiement et l’artefact Web précédents, commandes Render et références de branches. Après accord, republier l’artefact Web précédent avec sa configuration cohérente ; ne pas faire de push forcé. Revenir au backend précédent seulement si compatible avec le schéma courant ; conserver les tables additives de 0038 plutôt que supprimer les données. Un rollback de code ne restaure pas la base. Toute restauration de base est une décision séparée. Android 113 et données locales ne sont jamais utilisés comme variables de rollback serveur.
