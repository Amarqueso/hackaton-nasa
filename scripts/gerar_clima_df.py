"""Gera a climatologia local com dados reais da NASA POWER, sem importar o app Streamlit."""
import argparse
import ast
import datetime as dt
import json
import math
from pathlib import Path
import time

import requests

RAIZ = Path(__file__).resolve().parents[1]
URL = "https://power.larc.nasa.gov/api/temporal/monthly/point"


def configuracao_app():
    """Reutiliza constantes e a regra real de célula sem executar imports/interface do app."""
    arvore = ast.parse((RAIZ / "app.py").read_text())
    nomes = {"GRADE_POWER", "DF_BOUNDS", "DIAS"}
    nos = [n for n in arvore.body if
           (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in nomes for t in n.targets))
           or (isinstance(n, ast.FunctionDef) and n.name == "celula")]
    contexto = {"math": math}
    exec(compile(ast.Module(body=nos, type_ignores=[]), "app.py:grade", "exec"), contexto)
    return contexto


def celulas_do_mapa(config):
    bounds, grade, celula = config["DF_BOUNDS"], config["GRADE_POWER"], config["celula"]
    sul, oeste = celula(bounds["min_lat"], bounds["min_lon"], grade)
    norte, leste = celula(bounds["max_lat"], bounds["max_lon"], grade)
    return [(round(sul + i * grade[0][1], 6), round(oeste + j * grade[1][1], 6))
            for i in range(round((norte - sul) / grade[0][1]) + 1)
            for j in range(round((leste - oeste) / grade[1][1]) + 1)]


def media_mensal(resposta, dias, inicio, fim):
    parametros = resposta["properties"]["parameter"]

    def media(nome, mes):
        valores = [v for k, v in parametros[nome].items()
                   if len(k) == 6 and k[:4].isdigit() and inicio <= int(k[:4]) <= fim
                   and k.endswith(f"{mes:02d}") and isinstance(v, (int, float))
                   and not isinstance(v, bool) and math.isfinite(v) and v > -900]
        if not valores:
            raise ValueError(f"Sem valores válidos: {nome}, mês {mes}")
        return sum(valores) / len(valores)

    return {str(m): {"t": media("T2M", m), "chuva": media("PRECTOTCORR", m) * dias[m - 1],
                     "ur": media("RH2M", m)} for m in range(1, 13)}


def gerar(inicio=2015, fim=None, respostas=None):
    fim = dt.date.today().year - 1 if fim is None else fim
    if inicio != 2015 or not inicio <= fim < dt.date.today().year:
        raise ValueError("O período deve começar em 2015 e terminar em um ano completo anterior ao atual")
    config = configuracao_app()
    grade = config["GRADE_POWER"]
    dados = {"versao": 1, "fonte": "NASA POWER", "periodo": {"inicio": inicio, "fim": fim},
             "gerado_em": dt.datetime.now(dt.timezone.utc).isoformat(),
             "grade": {"origem_lat": grade[0][0], "passo_lat": grade[0][1],
                       "origem_lon": grade[1][0], "passo_lon": grade[1][1]}, "celulas": {}}
    for lat, lon in celulas_do_mapa(config):
        chave = f"{lat:g},{lon:g}"
        if respostas is not None and chave in respostas:
            resposta = respostas[chave]
        else:
            for tentativa in range(3):
                try:
                    r = requests.get(URL, timeout=40, params=dict(parameters="T2M,PRECTOTCORR,RH2M", community="AG",
                                     longitude=lon, latitude=lat, start=inicio, end=fim, format="JSON"))
                    r.raise_for_status()
                    resposta = r.json()
                    break
                except (requests.RequestException, ValueError):
                    if tentativa == 2:
                        raise
                    time.sleep(tentativa + 1)
        dados["celulas"][chave] = media_mensal(resposta, config["DIAS"], inicio, fim)
        print(f"{chave}: 12 meses, {inicio}–{fim}", flush=True)
    return dados


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ano-fim", type=int, default=dt.date.today().year - 1)
    parser.add_argument("--saida", type=Path, default=RAIZ / "data" / "clima_power_df.json")
    args = parser.parse_args()
    dados = gerar(fim=args.ano_fim)
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    temporario = args.saida.with_suffix(".tmp")
    temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporario.replace(args.saida)  # só substitui o arquivo completo se todas as células deram certo


if __name__ == "__main__":
    main()
