# Retour arrière backend compatible 0050

Base fonctionnelle : 1f1cd3f2fae99d3d9876a310c582a97abb3e8868, version précédemment publiée. Seul le démarrage est complété pour reconnaître exactement le registre 0050 et vérifier sa relation nullable et sa clé étrangère dans une transaction en lecture seule. Aucune migration n’est appliquée ni inversée ; les modèles, routes, calculs et permissions sont ceux de la base précédente.

Qualification : export réel du 6 novembre restauré dans une base locale vide, passage local contrôlé en 0050 puis exécution de cette garde en lecture seule. Empreintes historiques inchangées ; lectures API health et dashboard de l’ancien backend réussies. Une seconde restauration au schéma 0049 est refusée par le vérificateur 0050 (le démarrage normal 0049 reste assuré par la garde existante).

Ce candidat de retour arrière ne doit être déployé qu’en cas de nécessité, en coordination avec le retour Web à e735f051af607347bb0edbd1e95ab95730eccd5f. Il retire l’accès aux nouvelles fonctions backend mais conserve leurs tables et données. Ne pas supprimer de table, restaurer destructivement la production ou changer les secrets. Déploiement automatique désactivé.
