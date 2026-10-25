# Pilote Render — sécurisation spécifique

Base métier : b815c45b08cceb2827ded119cf0c07ea1119dc46. Branche dédiée : feature/offline-pilot-render-guard ; aucune fusion dans main.

Le Blueprint crée uniquement backend-elevage-pilot et elevage-pilot-db (Virginia, 7 + 7,50 USD/mois estimés), dans un projet Pilote. Aucun service existant n'est déclaré. Les previews et l'auto-déploiement du service sont désactivés. Après création, désactiver également la synchronisation automatique du Blueprint.

`config.pilot_settings` exige APP_ENV=pilot et contrôle la destination PostgreSQL interne, le nom elevage_pilot et le rôle elevage_pilot_owner. Il n'autorise ni SQLite ni une destination différente. HTTPS et cookies sécurisés ; hôte pilote exact ; CORS désactivé ; courriel dummy par défaut, SMTP seulement sur sélection explicite. Les paramètres de production ne changent pas.

Render génère SECRET_KEY, PILOT_ED25519_SEED et deux mots de passe de comptes fictifs. Le secret aléatoire PILOT_ED25519_SEED est transformé par SHA-256 avec un préfixe de domaine en un seed 32 octets, qui crée en mémoire une clé Ed25519 serveur stable, indépendante de toute clé Android ou serveur existante. Aucune de ces valeurs ne doit apparaître dans les journaux, le dépôt ou les rapports.

La commande preDeploy `pilot_deploy` vérifie en lecture seule le nom, le rôle, l'hôte, PostgreSQL18 et l'inventaire des tables avant tout migrate. Un verrou advisory de session empêche deux déploiements concurrents. Une base non vide sans django_migrations, ou avec des comptes non reconnus, bloque l'opération. Les migrations ne figurent pas dans le lancement Gunicorn.

Les comptes pilot-owner et pilot-operator et leur exploitation sont fictifs, sans privilège administrateur Django. La politique hors ligne reste désactivée initialement pour l'exploitation destinée à la tablette. Aucune clé/tablette principale synthétique n'y est enrôlée.

`pilot_smoke` contrôle HTTPS, refus du Host de production, login, capabilities et rejet d'un JWT signé par une clé étrangère synthétique. Une seule sonde transactionnelle vérifie la clé serveur réellement configurée via l’API de délivrance d’autorisation ; ses objets sont annulés. Les trois classes complètes de régression sont exécutées par le test runner Django sur une base dédiée, via le workflow pilot-security et la répétition locale. Elles ne doivent jamais être lancées directement sur la base peuplée du pilote. Aucune TransactionTestCase ni commande flush n'est appelée sur cette base. Les comptes, dispositifs et déclarations sont comptés avant/après. Les requêtes APIClient simulent HTTP ; elles ne prouvent pas le TLS réseau public et ne remplacent pas les essais sur tablette physique.

Le transport e-mail reste dummy par défaut. Une variable EMAIL_BACKEND explicite peut sélectionner SMTP dans le seul pilote. Les autres paramètres SMTP sont hérités de config.settings ; EMAIL_TIMEOUT lit l’environnement, avec 15 secondes par défaut. Le secret n’est jamais versionné. La livraison password-reset utilise la file chiffrée et sa commande dédiée ; aucun traitement périodique n’est créé par ce changement.

Vérifier également l'URL publique et les journaux après déploiement. Le nom onrender.com est attribué à la création : si un suffixe est ajouté, ajuster PILOT_WEB_HOST et l'autorisation Flutter avant tout APK. L'isolation Hobby est logique et par credentials ; aucune option réseau Pro n'est souscrite.

La commande pilot_deploy est réservée à la base neuve pilote. Ne jamais lancer un test runner ou une répétition de migrations sur une base de production. Aucun APK ne doit être construit dans cette mission.
