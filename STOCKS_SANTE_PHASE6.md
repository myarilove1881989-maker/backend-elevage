# Stocks & Santé — qualification locale, phase 6

## Périmètre
Cartes physiques aliments et vaccins dans le tableau de bord, métadonnées de seuils du référentiel existant, historique paginé des mouvements immuables et alertes visuelles. Aucune notification réelle, publication, installation Android ou migration production. Les anciens calculs financiers ne sont pas modifiés.

## Contrats et cohérence
Les kilogrammes utilisent les milli-kilogrammes exacts côté Flutter ; les doses sont entières. Les filtres de lot incluent le stock partagé de son espèce et son seul stock réservé. Les vues de lots ne doivent pas être additionnées : elles partagent potentiellement les mêmes quantités. Les produits archivés avec un reliquat restent visibles.
Les doses utilisables sont recalculées selon les frontières de péremption serveur et la durée après ouverture explicitement fournie. Le temps local ne crée aucun protocole. Les doses périmées/inutilisables restent physiquement présentes et historisées. Un flacon disponible désigne un flacon avec au moins une dose utilisable, entamé ou non.
Les attentes et conflits proviennent exclusivement de la file chiffrée existante. L’estimation vaccinale porte sur les flacons confirmés ; les réceptions locales non synchronisées ne créent pas des flacons fictifs et sont exclues de cette estimation. Les corrections versionnées non confirmées ne sont pas recalculées par approximation. Aucune déclaration originale n’est modifiée.

## Autorisations
Lecture : stocks.read existant, droit de base des membres actifs. La révocation d’adhésion bloque les lectures serveur ; un appareil déconnecté apprend une révocation à la reconnexion ou à l’expiration de son autorisation. Les seuils sont configurés par OWNER via le service audité existant, avec contrôle de valeur attendue sous verrou d’exploitation. Aucun nouveau rôle ni permission n’est ajouté. Les alertes sanitaires utilisent agenda.read et les tâches existantes.
Les horizons visuels de péremption proche et de vaccination prochaine sont explicitement saisis par utilisateur/exploitation (0 à 365 jours), vides par défaut. Ce sont des préférences d’affichage, sans protocole médical ni fréquence de vaccination. Un seuil faible de zéro désactive cette alerte ; la rupture alimentaire reste signalée. Les seuils globaux ne sont pas comparés à un sous-ensemble filtré.

## Hors ligne et compatibilité
Schéma SQLCipher 9 inchangé ; aucune réinitialisation des données Android. Les métadonnées de présentation du cache alimentaire sont demandées explicitement avec presentation=1. Sans cette option, l’ancien instantané conserve ses champs historiques et sa comparaison stricte à révision égale. Le nouveau client ignore uniquement les métadonnées d’affichage lors de cette comparaison ; une quantité contradictoire reste refusée. Les vieux instantanés sans seuil/horodatage restent lisibles et signalés comme anciens.
Les historiques déjà chargés peuvent provenir du cache existant ; les pages jamais chargées et les changements de seuil nécessitent une connexion. Les montants financiers ne figurent pas dans les cartes physiques.
Android minSdk 24 inchangé ; SDK, dépendances, applicationId, signature et numéro de version ne sont pas modifiés. La compatibilité du protocole est vérifiée par tests locaux, pas par exécution d’un ancien APK sur appareil.

## Performance et limites
La lecture alimentaire agrège les mouvements par produit/espèce/lot en une requête au lieu d’une requête par produit. La lecture vaccinale groupe entrées, soldes et ouvertures au lieu d’interroger chaque flacon. Un test vérifie un nombre constant de requêtes (au plus 15) entre 10 et 20 flacons. Cela ne constitue pas une mesure de latence sur une exploitation volumineuse.
Plafonds : 1 000 produits de chaque type, 20 000 positions alimentaires, 10 000 flacons et 10 000 intervalles de couverture de synchronisation par instantané. Un dépassement est refusé explicitement, sans tronquer un solde. Les instantanés stock restent monolithiques : la limite de page du cache (200 entités, demandes de 50) ne découpe pas leurs tableaux internes. L’historique physique est paginé (50 lignes par défaut, 100 au maximum). La liste HTTP des vaccinations préexistante reste limitée à 1 000 éléments.
Un important historique de flacons, de mouvements ou des intervalles fragmentés peut saturer ces limites, même avec peu de stock restant. Une pagination cohérente des instantanés et une mesure sur volumes représentatifs seront nécessaires avant de relever ces plafonds. Aucun plafond n’a été relevé silencieusement. Les données en attente/conflit ne sont jamais supprimées pour gagner de la place.

## Limites conservées
La correction/annulation des achats vaccinaux reste bloquée en l’absence de parcours coordonné entre dépense, réception et flacons déjà utilisés. Les corrections/annulations d’administration gardent leur historique existant.
Planification/attribution/report sanitaire avec connexion ; consultation et actions compatibles hors ligne via les mécanismes existants. La récurrence reste explicitement bornée, sans fréquence automatique. Aucun push en temps réel ajouté. Les alertes visuelles dépendent de l’instantané chargé ; leur horodatage et les attentes restent visibles.

## Recette physique obligatoire avant mise en service Android
Aucune tablette disponible : non réalisé. À effectuer sur un appareil de test isolé, sans données utilisateur : mise à jour conservant SQLCipher et profils, réouverture après arrêt forcé et redémarrage, synchronisation interrompue puis reprise, changement de profil, autorisations révoquées, connexion lente, affichage réel téléphone/tablette et grandes listes. Vérifier la réception réelle des rappels, les autorisations système, l’économie de batterie et la reprise après redémarrage. Les tests de notifications locaux utilisent uniquement des simulateurs ; aucun envoi réel réalisé.

## Publication et retour arrière
Candidat local uniquement. Aucune nouvelle migration en phase 6 ; les migrations additives jusqu’à 0049 sont validées sur PostgreSQL synthétique, y compris la préservation des données historiques. Une mise en service doit être approuvée séparément avec sauvegarde/restauration qualifiée, plan de migration compatible, artefacts signés et recette appareil. Le démarrage production peut déclencher des migrations : ne pas déployer sans validation explicite.
Retour arrière phase 6 : revenir à l’interface et aux routes de phase 5 compatibles, sans supprimer les mouvements, les déclarations ou les tables additives. Ne pas forcer un downgrade Android ni effacer le stockage chiffré. Les nouveaux seuils restent des paramètres du référentiel existant ; leur retrait visuel ne nécessite aucune restauration de données.
