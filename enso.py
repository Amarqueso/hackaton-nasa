"""Detecção de El Niño / La Niña pelo índice ONI (Oceanic Niño Index) da NOAA/CPC.

ONI = média móvel de 3 meses da anomalia de temperatura do mar na região Niño 3.4.
Critério NOAA: El Niño quando ONI >= +0,5 °C e La Niña quando ONI <= -0,5 °C
por pelo menos 5 trimestres sobrepostos seguidos. Com menos que isso, chamamos de "em formação".
"""
import requests

ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
LIMIAR = 0.5
MIN_SEQ = 5  # trimestres seguidos para confirmar o evento

# Impactos esperados no DF / Cerrado do Planalto Central. O sinal do ENSO no Centro-Oeste é
# mais fraco que no Sul e no Norte/Nordeste: são tendências, não certezas.
IMPACTO_DF = {
    "El Niño": ("Tende a atrasar e deixar irregular o início das chuvas no Planalto Central, com mais "
                "veranicos (semanas secas no meio da chuva) e calor acima do normal. O fim das chuvas em "
                "abril/maio também pode ser mais fraco, o que pesa contra o milho safrinha."),
    "La Niña": ("Tende a trazer chuvas mais regulares no Centro-Oeste na primavera/verão, mas pode haver "
                "fim de chuvas mais cedo e veranicos isolados. No Sul do Brasil costuma causar seca."),
    "Neutro": "Sem influência forte do Pacífico: o clima tende a seguir o padrão normal da região.",
}


def intensidade(oni):
    a = abs(oni)
    if a >= 2.0: return "muito forte"
    if a >= 1.5: return "forte"
    if a >= 1.0: return "moderado"
    if a >= LIMIAR: return "fraco"
    return ""


def baixar_oni(timeout=30):
    """Devolve lista [(trimestre, ano, anomalia)] em ordem cronológica."""
    txt = requests.get(ONI_URL, timeout=timeout).text
    out = []
    for ln in txt.splitlines()[1:]:
        p = ln.split()
        if len(p) == 4:
            try:
                out.append((p[0], int(p[1]), float(p[3])))
            except ValueError:
                pass
    return out


def classificar(serie):
    """Classifica a fase atual do ENSO a partir da série ONI."""
    if not serie:
        return None
    seas, ano, oni = serie[-1]
    # quantos trimestres seguidos (do fim para trás) estão do mesmo lado do limiar
    sinal = 1 if oni >= LIMIAR else -1 if oni <= -LIMIAR else 0
    seq = 0
    for _, _, v in reversed(serie):
        if sinal and (v >= LIMIAR if sinal > 0 else v <= -LIMIAR):
            seq += 1
        else:
            break
    fase = "El Niño" if sinal > 0 else "La Niña" if sinal < 0 else "Neutro"
    confirmado = sinal != 0 and seq >= MIN_SEQ
    tendencia = oni - serie[-4][2] if len(serie) >= 4 else 0.0  # variação em 3 trimestres
    return {
        "fase": fase,
        "oni": oni,
        "trimestre": f"{seas} {ano}",
        "intensidade": intensidade(oni),
        "confirmado": confirmado,
        "seq": seq,
        "tendencia": tendencia,
        "historico": serie[-24:],
        "impacto": IMPACTO_DF[fase],
    }


def rotulo(info):
    """Texto curto para o usuário, ex.: 'El Niño forte' ou 'La Niña em formação'."""
    if info is None:
        return "sem dado"
    if info["fase"] == "Neutro":
        return "Neutro"
    return f"{info['fase']} {info['intensidade']}" + ("" if info["confirmado"] else " (em formação)")


def obter():
    try:
        return classificar(baixar_oni())
    except Exception:
        return None
