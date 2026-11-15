"""Aptidão da área para plantio: o satélite identifica o que existe dentro do polígono.

Fontes (Google Earth Engine):
- Dynamic World V1 (Google/WRI, Sentinel-2, 10 m): classe mais frequente nos últimos 6 meses
  (água, árvores, campo, vegetação alagada, lavoura, arbusto, construção, solo exposto).
- JRC Global Surface Water (Landsat 1984-2021, 30 m): quanto do tempo cada pixel ficou com água.
- WDPA (áreas protegidas): unidades de conservação de proteção integral (IUCN Ia, Ib, II, III).

As regras ficam em avaliar() (sem Earth Engine), para poderem ser testadas e ajustadas.
O `ee` é importado dentro das funções: assim usa o módulo atual de `sys.modules` (nos testes, o falso).
"""

DW_CLASSES = ["agua", "arvores", "campo", "alagado", "lavoura", "arbustos", "construcao", "solo_exposto", "neve"]
NOMES = {"agua": "Água", "arvores": "Árvores / mata", "campo": "Pasto / campo", "alagado": "Brejo / vereda",
         "lavoura": "Lavoura", "arbustos": "Cerrado arbustivo", "construcao": "Construções / asfalto",
         "solo_exposto": "Solo exposto", "neve": "Neve"}
DW_PALETA = ["419bdf", "397d49", "88b053", "7a87c6", "e49635", "dfc35a", "c4281b", "a59b8f", "b39fe1"]
PLANTAVEL = ("campo", "lavoura", "solo_exposto")  # arbustos no DF costumam ser cerrado nativo
UC_RESTRITAS = ["Ia", "Ib", "II", "III"]


def dynamic_world(geom, fim, dias=180):
    import ee
    return (ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1").filterBounds(geom)
            .filterDate(ee.Date(fim).advance(-dias, "day"), ee.Date(fim)).select("label").mode().rename("label"))  # mode() renomeia para label_mode


def calcular(geom, fim):
    """Faz uma única chamada ao Earth Engine e devolve frações (0-1) de cada cobertura."""
    import ee
    dw =dynamic_world(geom, fim)
    hist = dw.reduceRegion(ee.Reducer.frequencyHistogram(), geom, 10, maxPixels=1e10, bestEffort=True).get("label")
    occ = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence").unmask(0)
    agua = (occ.gte(50).rename("perm").addBands(occ.gte(10).And(occ.lt(50)).rename("sazonal"))
            .reduceRegion(ee.Reducer.mean(), geom, 30, maxPixels=1e10, bestEffort=True))
    ucs = (ee.FeatureCollection("WCMC/WDPA/current/polygons").filterBounds(geom)
           .filter(ee.Filter.inList("IUCN_CAT", UC_RESTRITAS)))
    uc_frac = (ee.Image(0).paint(ucs, 1).rename("uc")
               .reduceRegion(ee.Reducer.mean(), geom, 30, maxPixels=1e10, bestEffort=True).get("uc"))
    r = ee.Dictionary({"hist": hist, "agua": agua, "uc_frac": uc_frac,
                       "uc_nomes": ucs.aggregate_array("NAME").distinct()}).getInfo()
    h = {int(float(k)): v for k, v in (r.get("hist") or {}).items()}
    tot = sum(h.values()) or 1
    frac = {c: h.get(i, 0) / tot for i, c in enumerate(DW_CLASSES)}
    return {"cobertura": frac,
            "agua_perm": (r.get("agua") or {}).get("perm") or 0.0,
            "agua_sazonal": (r.get("agua") or {}).get("sazonal") or 0.0,
            "uc_frac": r.get("uc_frac") or 0.0,
            "uc_nomes": r.get("uc_nomes") or [],
            "sem_dado": not h}


def avaliar(info, slope=None, ha=None):
    """Regras de aptidão. Devolve {'nivel': 'apta'|'restricao'|'inapta', 'motivos': [...], 'util_ha': ...}."""
    if info is None or info.get("sem_dado"):
        return {"nivel": "desconhecido", "motivos": ["Sem imagem do satélite sem nuvens para esta área nos últimos 6 meses."],
                "util_ha": None, "frac_util": None}
    c = info["cobertura"]
    agua = max(c["agua"], info["agua_perm"])
    bloq, alerta = [], []

    if info["uc_frac"] > 0.05:
        nomes = ", ".join(info["uc_nomes"][:3]) or "unidade de conservação"
        bloq.append(f"{info['uc_frac']:.0%} da área está dentro de unidade de conservação de proteção integral ({nomes}): "
                    "plantio não é permitido.")
    if c["construcao"] >= 0.30:
        bloq.append(f"{c['construcao']:.0%} da área tem prédios, casas ou asfalto: parece área urbana ou construída.")
    elif c["construcao"] >= 0.05:
        alerta.append(f"{c['construcao']:.0%} da área tem construções (casas, galpões, estradas). Elas não entram na área plantável.")
    if agua >= 0.30:
        bloq.append(f"{agua:.0%} da área é coberta por água (lago, represa ou rio).")
    elif agua >= 0.03:
        alerta.append(f"{agua:.0%} da área tem água. Margens de rios, lagos e nascentes são APP (Código Florestal): "
                      "não plante nelas.")
    if info["agua_sazonal"] >= 0.10:
        alerta.append(f"{info['agua_sazonal']:.0%} da área já ficou alagada em alguns anos (histórico 1984-2021): "
                      "risco de encharcamento na chuva.")
    if c["alagado"] >= 0.30:
        bloq.append(f"{c['alagado']:.0%} da área é brejo ou vereda: áreas úmidas são protegidas e o solo fica encharcado.")
    elif c["alagado"] >= 0.03:
        alerta.append(f"{c['alagado']:.0%} da área parece brejo ou vereda (área de preservação). Evite plantar nessa parte.")
    if c["arvores"] >= 0.50:
        alerta.append(f"{c['arvores']:.0%} da área é coberta por árvores. Se for mata nativa do Cerrado, abrir a área "
                      "exige autorização ambiental (Brasília Ambiental/IBRAM) e pode ser Reserva Legal.")
    elif c["arvores"] >= 0.15:
        alerta.append(f"{c['arvores']:.0%} da área tem árvores; confira se é mata nativa ou Reserva Legal antes de plantar.")
    if slope is not None and slope > 25:
        alerta.append(f"Terreno muito inclinado (média de {slope:.0f}°): acima de 25° o uso é restrito e acima de 45° é APP.")

    if c["arbustos"] >= 0.30:
        alerta.append(f"{c['arbustos']:.0%} da área parece cerrado nativo (arbustos). Abrir vegetação nativa exige "
                      "autorização ambiental; se for pasto sujo, desconsidere.")

    frac_util = sum(c[k] for k in PLANTAVEL)
    nativa = c["arvores"] + c["arbustos"]
    if frac_util < 0.30 and not bloq:
        if nativa >= 0.50:
            bloq.append(f"{nativa:.0%} da área é vegetação (mata ou cerrado), sem lavoura ou pasto. Plantar exigiria "
                        "desmatar, o que depende de autorização ambiental e respeito à Reserva Legal (20% no Cerrado).")
        else:
            bloq.append(f"Só {frac_util:.0%} da área parece ter lavoura, pasto ou campo: a maior parte não é plantável.")
    nivel = "inapta" if bloq else "restricao" if alerta else "apta"
    return {"nivel": nivel, "motivos": bloq + alerta, "frac_util": frac_util,
            "util_ha": None if ha is None else ha * frac_util}
