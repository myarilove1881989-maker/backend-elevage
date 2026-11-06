# Compatibilité Web, Android 113 et futur Android

## Références et portée de la preuve

Sources Android 113 : `86969c89dd8bbd3aff9d18c8b1eb92755b1716f9`, branche `feature/consolidation-113`, version déclarée `1.0.0+113`. L'inventaire adjacent contient 63 modèles d'URL extraits de ses services/offline. Cette mission ne construit, ne signe, n'installe et ne distribue aucun APK. Elle établit la compatibilité des contrats API ; elle ne certifie ni un futur APK ni son fonctionnement sur appareil.

Les routes, contrôles JWT, durées, révocations et formats signés hors ligne ne sont pas modifiés. Le propriétaire conserve les seules fonctions Web autorisées ; l'opérateur conserve son circuit Android et ses preuves appareil. Les réponses supplémentaires sont additives. Les nouveaux contacts sanitaires ne créent ni utilisateur ni permission.

| Fonctionnalité | Web candidat | Android 113 | Futur Android | Modification API | Risque / limite |
|---|---|---|---|---|---|
| Connexion/JWT | Rafraîchissement partagé, 401 concurrent unique | Champs access/refresh et révocation conservés | Composant partagé prêt à intégrer | Aucune côté backend | Reconnexion nécessaire après révocation ou expiration réelle ; aucune session illimitée |
| Lots/mouvements | Saisie successive et contexte conservé | Routes, IDs, quantités et dates conservés | Même contrat | Champ readonly prix_unitaire_origine ajouté au détail | Valeur nulle pour origines incertaines ; aucune valorisation inventée |
| Naissances | Nouveau lot des seuls vivants | Contrat historique conservé | Même règle métier partagée | Aucune | Parents et comptabilité ne reçoivent pas de stock/vente fictif |
| Achats/dépenses | Montants lisibles, erreurs visibles | Dates, montants et contrôles existants conservés | Même API, composants monétaires partagés | Aucune | Le format d'affichage ne remplace pas la valeur canonique |
| Aliments/vaccins | Achats physiques idempotents, historiques allégés | Contrats legacy conservés | Contrats stock_version/vaccine_version à sélectionner explicitement | Aucune nouvelle modification | Préserver la distinction entre ancien historique et journal physique ; aucune déduction implicite depuis un ancien client |
| Programmation sanitaire | Utilisateur existant ou contact, multidates sans heure | assigned_to/date historiques conservés | responsable/contact et dates explicites utilisables | Nouveau endpoint health-responsibles ; responsible_id alternatif, dates ; réponse health.responsible additive | Planification nouvelle en ligne, sans stock consommé ; contact sans accès au compte |
| Vaccination réalisée | Journal physique existant conservé | Routes et permissions existantes conservées | Même journal et UUID | Aucune | Ne pas confondre programmation et administration réelle |
| Historique propriétaire | Web audité + terrain, auteur réel | Lecture historique conservée | Même représentation | Ajout des faits Web, IDs audit négatifs et operation_id online:ID | Pas de fausse signature terrain ; isolation et pagination conservées |
| Facturation/paiements | Relance modifiable, devise configurée, partage manuel | Réponses et paiements existants conservés | Générateur de message partagé prêt | Aucune | Aucun envoi automatique ; aucune dépense supplémentaire |
| Synchronisation | Incertitude et UUID préservés | Enveloppes, signatures, droits, reprise et rapprochement conservés | Même protocole versionné | Aucune | Aucune répétition automatique après résultat incertain ; rejeu uniquement après refus 401 d'authentification |
| Mot de passe | Lien signé dans fragment, usage unique après validation | Code six chiffres et révocation conservés | Même backend | Aucune | File chiffrée conservée ; aucun job e-mail payant créé ni Gmail réel utilisé |
| Design/navigation | V2 sur 22 familles, cinq KPI et barre existante | Aucun binaire modifié | Composants Flutter partagés, qualification appareil ultérieure | Aucune | Les 66 captures sont locales, pas une preuve d'exécution du futur APK |

## Qualification

`test_android113_web_compatibility` vérifie les modèles d'URL de la référence, les champs JWT, les durées finies, le contrat de capacités legacy, les types de lots, le refus inter-exploitations et la planification à responsable existant sans consommation de stock.

Le parcours porcin d'intégration utilise uniquement une base PostgreSQL locale synthétique : 20 adultes achetés, aliment acheté/distribué, vaccin acheté/administré, deux ventes partielles, une mortalité, naissance de 10 vivants dans un nouveau lot, facture et deux paiements partiels. Les valeurs attendues sont 14 adultes restants, 10 porcelets, 450 kg d'aliment, 10 doses restantes, 3 dépenses, 2 ventes, 30 000 payés sur la première facture et 45 000 restant sur celle-ci ; bilan du lot adulte : charges enregistrées 216 000 et résultat -89 000. Les rejouements des achats physiques et vaccinations n'ajoutent pas de doublon.

Les tests existants de révocation, mots de passe, signatures appareil, permissions, dates, naissances, montants, isolation et reprise hors ligne restent actifs. Les assertions historiques ne sont pas affaiblies.

## Migration et publication

Render est déjà en 0049 ; 0038 ne doit pas être rejouée et aucun paramètre RESET_UPGRADE ne doit être ajouté. Seule l'évolution additive 0050 des contacts sanitaires est requise par ce candidat. L'export réel du 6 novembre est conservé hors Git avec son SHA256 ; son catalogue et sa décompression sont vérifiés, sa restauration dans une base locale vide reproduit les empreintes des 50 tables métier de Render. La répétition locale 0049 vers 0050 conserve toutes les colonnes historiques et accepte le redémarrage 0050.

La publication utilise le SDK Flutter 3.47.6 épinglé, le lockfile inchangé, APP_ENV=production et l'API publique existante. L'auto-déploiement reste désactivé. Aucun SECRET_KEY ou secret de messagerie n'est modifié. Aucun service payant n'est ajouté.

## Retour arrière

Le Web peut revenir à e735f051af607347bb0edbd1e95ab95730eccd5f avec la même API. Le backend 1f1cd3f ne doit pas être lancé aveuglément sur 0050 : utiliser une version explicitement compatible avec le registre et le schéma 0050, sans suppression de table ni migration inverse. La restauration des données de production reste une opération séparée qui exige une nouvelle décision.
