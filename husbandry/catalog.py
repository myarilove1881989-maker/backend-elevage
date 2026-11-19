FAMILIES={'poule':'POULTRY','poulet':'POULTRY','vache':'CATTLE','bovin':'CATTLE','porc':'PIG','lapin':'RABBIT'}
REPRODUCTION_FAMILIES={'CATTLE','PIG','RABBIT'}
def family(species):return FAMILIES.get(species.nom.strip().casefold())
def templates(species):
    common=[('temperature','Température ambiante','NUMBER','°C'),('humidity','Humidité relative','NUMBER','%'),('ventilation','Ventilation','TEXT','')]
    additions={'POULTRY':[('lighting','Durée d’éclairage','NUMBER','h/jour'),('housing','État de la litière','TEXT','')],
      'CATTLE':[('housing','État du logement ou du sol','TEXT',''),('water','Disponibilité de l’eau','BOOL','')],
      'PIG':[('housing','État du sol ou de la litière','TEXT',''),('water','Disponibilité de l’eau','BOOL','')],
      'RABBIT':[('housing','Propreté des cages ou clapiers','TEXT',''),('water','Disponibilité de l’eau','BOOL','')]}
    return common+additions.get(family(species),[])
