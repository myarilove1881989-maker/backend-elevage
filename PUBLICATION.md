# Publication Web 113 — procédure contrôlée

La production reste inchangée jusqu’à l’accord explicite de publication. Les branches de travail et develop ne déploient rien.

## Backend
La montée 0037 → 0038 accepte seulement les deux CreateModel de la migration 0038_passwordresetlimit. Le registre exact, le plan unique et atomique, la cible Render, TLS, les verrous, les délais et les empreintes des données historiques sont contrôlés. Une base en 0015 doit d’abord passer par la livraison 0037 existante : aucune ancienne migration n’est élargie par cette livraison.

Accord requis avant de renseigner ELEVAGE_ALLOW_RESET_UPGRADE=true et ELEVAGE_RESET_BACKUP_SHA256=empreinte d’une sauvegarde préalable vérifiée. Sans ces paramètres, une base 0037 n’est pas modifiée. Après succès, 0038 est accepté au redémarrage en lecture seule ; enlever le drapeau d’autorisation après validation. Ne jamais saisir un faux digest en production.

## Récupération
Le lien HTTPS contient uniquement un jeton signé dans le fragment du navigateur. Il expire au plus tard dix minutes après la demande, ne se consomme pas à l’ouverture et se consomme après un changement valide. Le code à six chiffres reste disponible pour Android 113. La rotation existante révoque les sessions et autorisations selon V2 sans effacer les données appareil.

La file e-mail est chiffrée avec une clé dérivée du SECRET_KEY existant : ne pas changer cette clé. Le plan deployment/password-reset-email-plan.json prépare un job Render payant à la minute ; aucune création sans accord et plafond financier confirmé. Son accès à DATABASE_URL, SECRET_KEY et au mot de passe d’application Gmail doit être autorisé et renseigné uniquement dans le gestionnaire de secrets. Aucun secret V2 n’est requis par ce job. Les tests utilisent une boîte locale, jamais Gmail réel.

## Web
Utiliser Flutter 3.47.6 révision 5fc346839b5d0eef006ed8404392afb4dfae428d, lockfile inchangé et build release APP_ENV=production, API_URL=https://backend-elevage.onrender.com/api. Corriger la commande Render actuelle qui vise le pilote, après accord. Passer le site en déploiement manuel avant la livraison contrôlée. Vérifier CORS pour https://elevage-mobile.onrender.com. Le propriétaire consulte et gère les seules fonctions autorisées ; l’opérateur reste sur Android.

## Sauvegarde et retour arrière
Avant toute écriture production : export logique Render, disponibilité vérifiée et empreinte SHA256 conservée hors Git ; restauration de vérification dans une base isolée et vide si l’export est utilisé. La répétition locale tools/rehearse_restore.py compare toutes les lignes d’une sauvegarde synthétique restaurée.

Ne pas redéployer aveuglément c14f489 après 0038 : son garde de démarrage refuserait ce schéma. Un retour backend doit conserver une garde compatible 0038 ; aucune suppression de table additive ni restauration destructive sans nouvelle autorisation. Le retour Web utilise l’artefact précédent et sa configuration API cohérente. Arrêter le job e-mail en cas de retrait de la fonctionnalité. La restauration de données est une opération séparée.

Android 113, son certificat et ses données restent intacts. Aucun APK n’est distribué ou installé par cette mission Web.
