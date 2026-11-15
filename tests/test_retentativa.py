"""Issue #8: falhas por fonte têm pausa de 120s, sucessos/None legítimos permanecem cacheados."""
import datetime as dt

import pytest
import requests

from conftest import App
from fake_ee import serie, serie_mensal
from test_cache_regional import A, B, falhas, dados, CLIMA
from test_clima_local import instalar, assert_tabela
from test_virada_mes import data_atual

COLECOES = {"terreno": "SRTMGL1", "solo": "FLDAS", "veg": "MOD13Q1", "radar": "S1_GRD"}


def consultas(app):
    return {"clima": app.get.call_count, **{k: app.ee.chamadas[c] for k, c in COLECOES.items()}}


def simular_falha(app, fonte):
    if fonte == "clima":
        app.get.side_effect = requests.Timeout("Read timed out")
        return lambda: setattr(app.get, "side_effect", None)
    colecao = COLECOES[fonte]
    anterior = app.ee.respostas[colecao]

    def falha():
        raise RuntimeError("Earth Engine indisponível")

    app.ee.respostas[colecao] = falha
    return lambda: app.ee.respostas.__setitem__(colecao, anterior)


def mudar_resposta(app, mes):
    app.at.selectbox(key="mes").set_value(mes)
    return app.run()


@pytest.mark.parametrize("fonte", ["clima", "terreno", "solo", "veg"])
def test_falha_recupera_apenas_a_fonte_apos_pausa_na_mesma_terra(app, relogio_fontes, fonte):
    recuperar = simular_falha(app, fonte)
    app.marcar_terra(A)
    assert any(c.startswith(f"Falha em '{fonte}':") for c in falhas(app))
    if fonte == "clima":
        assert all(dados(app)[k] is None for k in CLIMA)
        assert any("Confiança da sugestão: baixa" in t for t in app.recomendacao())
    else:
        assert all(dados(app)[k] is not None for k in CLIMA)
        assert any("Confiança da sugestão: média" in t for t in app.recomendacao())
    antes = consultas(app)
    recuperar()
    relogio_fontes[0] += 119
    mudar_resposta(app, 2)
    mudar_resposta(app, 3)
    assert consultas(app) == antes  # sucesso e erro em pausa não fazem HTTP/EE de novo
    assert any(c.startswith(f"Falha em '{fonte}':") for c in falhas(app))

    relogio_fontes[0] += 2
    mudar_resposta(app, 4)
    assert consultas(app) == {k: n + (k == fonte) for k, n in antes.items()}
    assert not falhas(app)
    assert all(v is not None for v in dados(app).values())
    assert any("Confiança da sugestão: alta" in t for t in app.recomendacao())
    depois = consultas(app)
    relogio_fontes[0] += 3600
    mudar_resposta(app, 5)
    assert consultas(app) == depois  # resultado recuperado passa ao cache normal


def test_fonte_continua_falhando_no_maximo_uma_tentativa_por_intervalo(app, relogio_fontes):
    simular_falha(app, "clima")
    app.marcar_terra(A)
    antes = consultas(app)
    for mes in (1, 2, 3): mudar_resposta(app, mes)
    assert consultas(app) == antes
    relogio_fontes[0] += 121
    mudar_resposta(app, 4)
    assert consultas(app)["clima"] == antes["clima"] + 1
    for mes in (5, 6, 7): mudar_resposta(app, mes)
    assert consultas(app) == {k: n + (k == "clima") for k, n in antes.items()}


@pytest.mark.parametrize("fonte", ["clima", "solo"])
def test_pausa_regional_compartilhada_na_celula_nao_refaz_fonte_em_ponto_vizinho(app, relogio_fontes, fonte):
    recuperar = simular_falha(app, fonte)
    app.marcar_terra(A)
    antes = consultas(app)
    recuperar()
    app.marcar_terra(B)
    assert consultas(app)[fonte] == antes[fonte]
    assert any(c.startswith(f"Falha em '{fonte}':") for c in falhas(app))
    relogio_fontes[0] += 121
    app.marcar_terra(A)
    assert consultas(app)[fonte] == antes[fonte] + 1
    assert not falhas(app)


@pytest.mark.parametrize("fonte", ["solo", "veg", "radar"])
def test_sem_dado_legitimo_nao_e_erro_e_permanece_no_cache(app, relogio_fontes, fonte):
    app.ee.respostas[COLECOES[fonte]] = (lambda: serie_mensal(lambda a, m: None)) if fonte == "solo" else (lambda: serie([-9999.0] * 12))
    app.marcar_terra(A)
    if fonte == "radar": app.tecnico(True)
    antes = consultas(app)
    assert not falhas(app)
    relogio_fontes[0] += 121
    app.run()
    assert consultas(app) == antes
    assert not falhas(app)
    if fonte == "radar": assert not app.tem_grafico_radar()
    elif fonte == "solo": assert dados(app)["Umidade do solo"] is None
    else: assert dados(app)["Meses com pouca planta no último ano"] is None


def test_radar_respeita_pausa_e_so_recupera_quando_modo_tecnico_ligado(app, relogio_fontes):
    recuperar = simular_falha(app, "radar")
    app.marcar_terra(A)
    assert consultas(app)["radar"] == 0
    app.tecnico(True)
    assert any("Falha em 'radar':" in c for c in falhas(app))
    antes = consultas(app)
    recuperar()
    mudar_resposta(app, 2)
    assert consultas(app) == antes
    app.tecnico(False)
    relogio_fontes[0] += 121
    mudar_resposta(app, 3)
    assert consultas(app) == antes  # desligado: nem após a pausa há tentativa
    app.tecnico(True)
    assert consultas(app) == {k: n + (k == "radar") for k, n in antes.items()}
    assert app.tem_grafico_radar() and not falhas(app)


def test_json_local_ignora_erro_api_em_pausa_e_fallback_fora_da_area_recupera(app, arquivo_clima, relogio_fontes):
    recuperar = simular_falha(app, "clima")
    app.marcar_terra(A)
    assert falhas(app)
    documento = instalar(arquivo_clima)
    app.run()  # JSON chegou: a falha anterior não esconde dados locais
    assert_tabela(app, documento["celulas"]["-15.5,-47.5"])
    assert not falhas(app) and app.get.call_count == 1
    ponto_fora = (-14.0, -47.5)
    app.marcar_terra(ponto_fora)
    assert app.get.call_count == 2 and falhas(app)
    recuperar()
    relogio_fontes[0] += 121
    app.run()
    assert app.get.call_count == 3 and not falhas(app)


def test_referencia_nova_nao_herda_falha_de_solo_mes_anterior(app, data_atual, relogio_fontes):
    data_atual[0] = dt.date(2026, 11, 30)
    recuperar = simular_falha(app, "solo")
    app.marcar_terra(A)
    antes = consultas(app)
    recuperar()
    data_atual[0] = dt.date(2026, 12, 1)
    app.run()  # sem passar 120s, a nova chave mensal não herda o erro anterior
    assert not falhas(app)
    assert consultas(app)["solo"] == antes["solo"] + 1
    assert consultas(app)["veg"] == antes["veg"] + 1
    assert consultas(app)["terreno"] == antes["terreno"]
    assert consultas(app)["clima"] == antes["clima"]


def test_pausa_de_falha_e_so_da_sessao_sucessos_sao_compartilhados(app, relogio_fontes):
    recuperar = simular_falha(app, "clima")
    app.marcar_terra(A)
    antes = consultas(app)
    recuperar()
    outra = App(app.ee, app.get, app.spinners)
    outra.marcar_terra(A)  # outra sessão, mesmo processo e cache de sucessos
    assert consultas(app) == {k: n + (k == "clima") for k, n in antes.items()}
    assert not falhas(outra)
    app.run()  # sucesso compartilhado tem precedência sobre a falha desta sessão
    assert not falhas(app)
