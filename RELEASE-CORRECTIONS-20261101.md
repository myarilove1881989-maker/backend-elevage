# Élev’Age — soldes, agenda et navigation (1 novembre 2026)

## Diagnostic et corrections

- Flutter demandait `/api/dettes-clients` : Django renvoie 301 vers le chemin avec slash. Le transport de production interdit les redirections. La correction utilise `/api/dettes-clients/`, sans assouplir le transport.
- L’échec API laissait afficher les totaux initiaux à zéro et un faux état vide. Il affiche maintenant un message d’erreur et un bouton Réessayer ; les chiffres restent masqués jusqu’au chargement réussi.
- Le backend agrège séparément les ventes actives et les paiements, en Decimal, avec exploitation du client, du lot et du paiement contrôlée. Une vente réglée reste visible avec solde zéro. Aucun rapprochement automatique des anciens mouvements sans vente liée, aucune dette inventée.
- Agenda : début et fin inclusifs, tous les jours ou jours de semaine choisis, période bornée à 365 jours d’écart. `POST /api/tasks/repeat/` crée atomiquement les occurrences ordinaires, sous verrou d’exploitation, avec les permissions existantes. Les occurrences identiques non annulées (exploitation, titre, date, opérateur) sont ignorées lors d’un nouvel envoi. Les tâches terminées conservent leur compte rendu. Les dates civiles Flutter sont comptées en UTC pour éviter les effets du changement d’heure.
- L’entrée de gestion des utilisateurs disparaît de la barre latérale ; l’entrée Paramètres « Création des utilisateurs » devient « Gestion des utilisateurs ». L’écran et ses fonctions restent disponibles au propriétaire selon les règles existantes.

## Vérification

- 42 tests backend sur PostgreSQL local synthétique : ventes, paiements partiels/réglés, annulations, exploitation, répétitions, dates, doublons et permissions.
- 24 tests Flutter ciblés : erreur/reprise des soldes, URL canonique, répétitions, navigation, permissions et protection réseau.
- Recette Chrome locale supplémentaire : formulaire de répétition, accès Paramètres propriétaire/opérateur et régressions Web J1.
- Build Flutter Web release avec SDK verrouillé, `APP_ENV=production` et API `https://backend-elevage.onrender.com/api`.
- Aucune migration ajoutée. La production est déjà sur `0038_passwordresetlimit` ; la garde de démarrage vérifie ce schéma sans migration.

## Publication et reprise

Déployer d’abord le backend, puis le Web après contrôle de santé. Les services sont en déploiement manuel. Aucun appel métier de test en écriture en production.

Références de retour arrière compatibles : backend `cc96154435f262e5f94b11c5531af3e6534dfc0e`, Web `1849dee1200afb4f712f6eef84750a221fa26036`. Un retour de code ne restaure pas une base ; aucune restauration de données n’est prévue.

Les mêmes sources Dart portent les corrections de la prochaine version Android. Aucun APK généré, installé ou diffusé ; APK 117, signature, données appareil et acquis hors ligne préservés. Aucun changement à `web/telecharger/index.html`, aux fichiers Android, au numéro de version ni au lockfile.

La création de séries reste une action propriétaire connectée ; les occurrences sont des Task existantes, donc leur consultation et progression Android/hors ligne gardent le contrat actuel.
