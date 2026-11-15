"""Issue #4: clima (NASA POWER) e umidade do solo (FLDAS) têm cache por célula da grade de cada fonte.

Grades: POWER com células de 0,5° lat × 0,625° lon centradas em lat = 0,5·k e lon = -180 + 0,625·k
(bordas no DF: lat -15,75 e -16,25; lon -48,4375, -47,8125 e -47,1875). FLDAS com pixels de 0,1° e bordas em
múltiplos de 0,1° (centro em x,x5).
"""
import json
from unittest import mock

import pytest
import requests

from conftest import resposta_power
from fake_ee import geometria, serie_mensal
from test_coletar import altitude_da_terra, dados as _dados

CLIMA = ("Temperatura nos próximos meses", "Chuva esperada em 4 meses", "Umidade do ar")
SOLO = "Umidade do solo"
A = (-15.74, -47.73)    # célula POWER (-15,5; -47,5) e pixel FLDAS (-15,75; -47,75)
B = (-15.747, -47.738)  # ~1 km de A, mesma célula e mesmo pixel
BRAZLANDIA, PAD_DF = (-15.68, -48.20), (-15.95, -47.50)  # células POWER diferentes


def dados(app):
    """Como `test_coletar.dados`, com a linha de umidade do solo sempre na chave SOLO (o rótulo pode ganhar o mês)."""
    return {SOLO if k.startswith("Umidade do solo") else k: v for k, v in _dados(app).items()}


def power_por_ponto(app):
    """A POWER falsa responde uma temperatura que depende do ponto consultado (dá para ver qual célula foi)."""
    def get(url, params, **kw):
        r = resposta_power()
        t = round(20 + params["latitude"] + 50 + (params["longitude"] + 48) * 3, 3)
        par = r.json.return_value["properties"]["parameter"]
        par["T2M"] = {k: t for k in par["T2M"]}
        return r
    app.get.side_effect = get


def pontos_power(app):
    return [(c.kwargs["params"]["latitude"], c.kwargs["params"]["longitude"]) for c in app.get.call_args_list]


def registrar_solo(app):
    """Guarda o ponto (lat, lon) de cada consulta ao FLDAS e responde com a série padrão."""
    pontos, resp = [], app.ee.respostas["FLDAS"]

    def r(lin):
        lon, lat = geometria(lin)["coordinates"]
        pontos.append((lat, lon))
        return resp()
    app.ee.respostas["FLDAS"] = r
    return pontos


def clima_mensal(app):
    """Tabela "Clima mensal (média do período)" do parecer técnico."""
    return app.at.expander[0].dataframe[1].value


def regionais(app):
    tela = dados(app)
    return {k: tela[k] for k in CLIMA + (SOLO,)}


def falhas(app):
    return [c for c in app.legendas() if c.startswith("Falha em")]


def test_trocar_tamanho_nao_consulta_clima_nem_solo(app):
    app.marcar_terra(A)
    antes = regionais(app)
    assert all(antes.values())
    app.at.selectbox(key="tam").set_value("Grande (cerca de 50 hectares)")
    app.run()
    assert regionais(app) == antes
    assert app.get.call_count == 1 and app.ee.chamadas["FLDAS"] == 1
    assert app.ee.chamadas["SRTMGL1"] == 2 and app.ee.chamadas["MOD13Q1"] == 2  # só terreno e NDVI de novo


def test_ponto_a_1km_usa_o_mesmo_clima_e_solo_e_a_altitude_muda(app, fake_ee):
    power_por_ponto(app)
    solo = registrar_solo(app)
    fake_ee.respostas["SRTMGL1"] = altitude_da_terra
    app.marcar_terra(A)
    tabela, tela_a = clima_mensal(app), dados(app)
    app.marcar_terra(B)
    tela_b = dados(app)
    assert clima_mensal(app).equals(tabela)
    assert tela_b[SOLO] == tela_a[SOLO]
    assert tela_a["Altitude"] == "1574 m" and tela_b["Altitude"] == "1575 m"
    assert pontos_power(app) == [(-15.5, -47.5)]  # uma consulta, no centro da célula
    assert solo == [(-15.75, -47.75)]  # uma consulta, no centro do pixel


def test_celulas_diferentes_da_power_dao_climas_diferentes(app):
    power_por_ponto(app)
    app.marcar_terra(BRAZLANDIA)
    oeste = clima_mensal(app)
    app.marcar_terra(PAD_DF)
    leste = clima_mensal(app)
    assert not leste.equals(oeste)
    assert pontos_power(app) == [(-15.5, -48.125), (-16.0, -47.5)]
    app.marcar_terra(BRAZLANDIA)  # volta: o clima é o da célula dele de novo
    assert clima_mensal(app).equals(oeste)


@pytest.mark.parametrize("ponto, power, pixel", [
    ((-15.75, -47.8125), (-15.5, -47.5), (-15.75, -47.85)),    # divisa da POWER nos dois eixos: norte/leste
    ((-15.7, -47.7), (-15.5, -47.5), (-15.65, -47.65)),        # divisa do FLDAS nos dois eixos: norte/leste
    ((-16.25, -48.4375), (-16.0, -48.125), (-16.25, -48.45)),  # canto de quatro células da POWER
])
def test_ponto_na_divisa_cai_sempre_na_mesma_celula(app, ponto, power, pixel):
    power_por_ponto(app)
    solo = registrar_solo(app)
    app.marcar_terra(ponto)
    clima = clima_mensal(app)
    app.marcar_terra(A)
    app.marcar_terra(ponto)  # clicar de novo no mesmo lugar
    assert clima_mensal(app).equals(clima)
    assert not falhas(app)
    assert pontos_power(app)[0] == power and solo[0] == pixel
    assert pontos_power(app).count(power) == 1 and solo.count(pixel) == 1


def poligono(*cantos):
    return json.dumps({"type": "Polygon", "coordinates": [[[lon, lat] for lat, lon in cantos + cantos[:1]]]})


def test_desenho_usa_a_celula_do_centro_e_redesenhar_nao_consulta_de_novo(app):
    power_por_ponto(app)
    solo = registrar_solo(app)
    app.at.session_state["modo"] = "Desenhar contorno"
    # centro (média dos cantos) = (-15.74, -47.73), como o ponto A
    app.at.session_state["gj_desenho"] = poligono((-15.73, -47.74), (-15.73, -47.72), (-15.75, -47.71), (-15.75, -47.75))
    app.run()
    antes = regionais(app)
    assert all(antes.values())
    app.at.session_state["gj_desenho"] = poligono((-15.731, -47.741), (-15.729, -47.72), (-15.751, -47.709),
                                                  (-15.75, -47.751))
    app.run()
    assert regionais(app) == antes
    assert pontos_power(app) == [(-15.5, -47.5)] and solo == [(-15.75, -47.75)]
    assert app.ee.chamadas["SRTMGL1"] == 2  # o terreno é da terra exata


def test_consulta_o_centro_da_celula_que_contem_o_centro_da_terra(app):
    """Antes a POWER era consultada em (round(lat, 1), round(lon, 1)) e o FLDAS no centróide da terra; agora no
    centro da célula/pixel onde o centro da terra fica, que dá o mesmo valor (a mesma célula)."""
    power_por_ponto(app)
    solo = registrar_solo(app)
    app.marcar_terra((-15.78, -47.80))
    assert pontos_power(app) == [(-16.0, -47.5)]  # célula lat -16,25..-15,75, lon -47,8125..-47,1875
    assert solo == [(-15.75, -47.75)]             # pixel lat -15,8..-15,7, lon -47,8..-47,7


def test_falha_do_clima_mostra_sem_dado_e_nao_fica_no_cache_da_celula(app, relogio_fontes):
    app.get.side_effect = requests.ConnectionError("sem internet")
    app.marcar_terra(A)
    tela = dados(app)
    assert all(tela[k] is None for k in CLIMA) and tela[SOLO]
    assert falhas(app) == ["Falha em 'clima': sem internet"]
    app.get.side_effect, app.get.return_value = None, resposta_power()
    relogio_fontes[0] += 121  # #8: a célula compartilha a pausa de retentativa de 120s nesta sessão
    app.marcar_terra(B)
    assert all(dados(app)[k] for k in CLIMA) and not falhas(app)
    assert app.get.call_count == 2


def test_falha_do_solo_mostra_sem_dado(app, relogio_fontes):
    def falha():
        raise RuntimeError("Computation timed out.")
    app.ee.respostas["FLDAS"] = falha
    app.marcar_terra(A)
    tela = dados(app)
    assert tela[SOLO] is None and all(tela[k] for k in CLIMA)
    assert falhas(app) == ["Falha em 'solo': Computation timed out."]
    app.ee.respostas["FLDAS"] = lambda: serie_mensal(lambda a, m: 0.2)
    relogio_fontes[0] += 121
    app.marcar_terra(B)
    assert app.ee.chamadas["FLDAS"] == 2 and not falhas(app)


def test_solo_sem_dado_no_pixel_fica_no_cache(app):
    """Pixel mascarado (sem dado) não é falha: o resultado (None) vale para a célula toda."""
    app.ee.respostas["FLDAS"] = lambda: serie_mensal(lambda a, m: None)
    app.marcar_terra(A)
    app.marcar_terra(B)
    assert dados(app)[SOLO] is None and not falhas(app)
    assert app.ee.chamadas["FLDAS"] == 1


def test_consultas_novas_continuam_nas_threads(app):
    """Clima e solo fora do cache rodam nas threads, sem ScriptRunContext; vindos do cache, não são consultados."""
    from streamlit.runtime.scriptrunner_utils.script_run_context import get_script_run_ctx
    ctx, resp = [], app.ee.respostas["FLDAS"]

    def r():
        ctx.append(get_script_run_ctx(suppress_warning=True))
        return resp()
    app.ee.respostas["FLDAS"] = r
    get = app.get.return_value

    def power(*a, **kw):
        ctx.append(get_script_run_ctx(suppress_warning=True))
        return get
    app.get.side_effect = power
    app.marcar_terra(A)
    assert ctx == [None, None]  # POWER e FLDAS, nenhuma com contexto do Streamlit
    assert app.ee.threads["FLDAS"].name != "MainThread"
    app.marcar_terra(B)
    assert len(ctx) == 2
