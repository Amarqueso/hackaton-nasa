"""#18: cliques do mapa propagam as condições locais ao ranking e ao plano, sem saturar a ordenação."""
import re
from unittest import mock

import pytest
import streamlit as st

from conftest import resposta_power
from fake_ee import geometria, serie
from test_cache_regional import A, B, BRAZLANDIA, PAD_DF, dados, pontos_power


@pytest.fixture
def pagina(app):
    with mock.patch("streamlit_folium._component_func", return_value=None) as componente:
        app.componente = componente
        app.run()
        yield app


def clicar(app, ponto, fragmento=False):
    app.componente.return_value = {"last_clicked": dict(zip(("lat", "lng"), ponto))}
    return app.rodar_fragmento("mapa") if fragmento else app.run()


def perfil(app, ultimo="Soja"):
    app.at.selectbox(key="mes").set_value(1)
    app.at.selectbox(key="ult").set_value(ultimo)
    return app.run()


def top(app):
    return next(t for t in app.textos() if 'class="destaque"' in t)


def plano(app):
    return next(t for t in app.textos() if 'class="trilha"' in t)


def primeira(app):
    return re.search(r"<h3>Plantar (.*?) em janeiro</h3>", top(app)).group(1)


def configurar_clima(app):
    # 150 mm em todo mês, inclusive fevereiro: mesmo cenário para qualquer data do teste.
    def power(*args, **kwargs):
        r = resposta_power()
        par = r.json.return_value["properties"]["parameter"]
        par["PRECTOTCORR"] = {k: 150 / (28 if k[4:] == "02" else 30 if k[4:] in {"04", "06", "09", "11"} else 31)
                              for k in par["PRECTOTCORR"]}
        return r
    app.get.side_effect = power


def area_b(lin):
    return geometria(lin)["coordinates"][0][0][0] < -47.735


def test_cliques_fragmento_a_b_a_preservam_condicoes_e_ordenam_antes_do_teto(pagina):
    app = pagina
    configurar_clima(app)
    app.ee.respostas["SRTMGL1"] = lambda lin: {"elevation": 1200 if area_b(lin) else 1000,
                                               "slope": 9 if area_b(lin) else 2}
    app.ee.respostas["MOD13Q1"] = lambda lin: serie([1000 if area_b(lin) else 5000] * 12)
    clicar(app, A)
    perfil(app)
    antes, plano_a = app.recomendacao(), plano(app)
    assert primeira(app) == "Sorgo"  # empate real com Capim: ordem do catálogo permanece determinística
    assert dados(app)["Terreno"] == "Plano"
    assert re.findall(r"<time>(.*?)</time><b>(.*?)</b>", plano_a) == [
        ("janeiro", "Sorgo"), ("maio", "Mandioca"), ("janeiro", "Sorgo")]
    # Sorgo (4 meses) → Mandioca (8 meses) → Sorgo: janelas válidas e famílias alternadas.

    clicar(app, B, fragmento=True)
    assert app.at.session_state["centro"] == B
    assert dados(app)["Altitude"] == "1200 m" and dados(app)["Terreno"] == "Muito inclinado"
    assert dados(app)["Meses com pouca planta no último ano"] == "12 de 12"
    assert primeira(app) == "Capim para pasto (braquiária)"
    assert "Protege o solo" in top(app)
    assert '<i style="width:100%">' in top(app)  # adequação 119, barra limitada a 100
    linhas = [t for t in app.textos() if 'class="linha"' in t]
    assert "Abóbora" in linhas[0] and '<i style="width:100%">' in linhas[0]  # adequação 111, abaixo do Capim
    assert "<b>Capim para pasto (braquiária)</b>" in plano(app)
    assert plano(app).count("<li>") == 1  # cultura plurianual encerra a sequência
    assert app.get.call_count == 1 and app.ee.chamadas["FLDAS"] == 1
    assert app.ee.chamadas["SRTMGL1"] == app.ee.chamadas["MOD13Q1"] == 2

    clicar(app, A, fragmento=True)
    assert app.at.session_state["centro"] == A
    assert app.recomendacao() == antes and plano(app) == plano_a
    assert app.at.selectbox(key="mes").value == 1 and app.at.selectbox(key="ult").value == "Soja"
    assert app.ee.chamadas["SRTMGL1"] == 2  # volta usa os dados corretos do cache


def test_mesmas_condicoes_em_dois_pontos_preservam_ordem_e_plano(pagina):
    app = pagina
    configurar_clima(app)
    clicar(app, A)
    perfil(app)
    antes = app.recomendacao()
    clicar(app, B, fragmento=True)
    assert app.recomendacao() == antes
    assert app.get.call_count == 1 and app.ee.chamadas["FLDAS"] == 1
    assert app.ee.chamadas["SRTMGL1"] == app.ee.chamadas["MOD13Q1"] == 2


def test_celulas_com_climas_distintos_mudam_justificativas_e_recomendacao(pagina):
    app = pagina
    def power(url, params, **kw):
        r = resposta_power()
        par = r.json.return_value["properties"]["parameter"]
        quente = params["longitude"] > -48
        par["T2M"] = {k: 18 if quente else 22 for k in par["T2M"]}
        return r
    app.get.side_effect = power
    clicar(app, BRAZLANDIA)
    perfil(app)
    assert "A temperatura esperada (22 °C" in top(app)
    antes = primeira(app)
    clicar(app, PAD_DF, fragmento=True)
    assert dados(app)["Temperatura nos próximos meses"] == "18 °C em média"
    assert "A temperatura esperada (18 °C" in top(app)
    assert primeira(app) != antes
    assert pontos_power(app) == [(-15.5, -48.125), (-16.0, -47.5)]


def test_fonte_local_sem_dado_nao_reutiliza_a_selecao_anterior(pagina):
    app = pagina
    app.ee.respostas["MOD13Q1"] = lambda lin: serie([None if area_b(lin) else 1000] * 12)
    clicar(app, A)
    perfil(app)
    assert dados(app)["Meses com pouca planta no último ano"] == "12 de 12"
    clicar(app, B, fragmento=True)
    assert dados(app)["Meses com pouca planta no último ano"] is None
    assert app.fontes()["NDVI mensal"] == "NÃO"
    assert "Confiança da sugestão: média" in " ".join(app.recomendacao())
    assert "ficou 12 meses" not in " ".join(app.recomendacao())


def test_earth_engine_indisponivel_continua_com_clima_da_selecao(pagina):
    app = pagina
    app.ee.init_erro = "Earth Engine indisponível"
    st.cache_resource.clear()
    clicar(app, A)
    perfil(app)
    assert dados(app)["Terreno"] is None
    assert any("Earth Engine indisponível" in t for t in app.legendas())
    assert "Confiança da sugestão: baixa" in " ".join(app.recomendacao())
    antes = app.recomendacao()
    clicar(app, B, fragmento=True)
    assert app.recomendacao() == antes
    assert app.get.call_count == 1


def test_falha_clima_em_nova_celula_nao_mantem_dados_anteriores(pagina):
    app = pagina
    clicar(app, BRAZLANDIA)
    perfil(app)
    assert dados(app)["Temperatura nos próximos meses"] == "22 °C em média"
    app.get.side_effect = RuntimeError("POWER indisponível nesta célula")
    clicar(app, PAD_DF, fragmento=True)
    assert dados(app)["Temperatura nos próximos meses"] is None
    assert dados(app)["Chuva esperada em 4 meses"] is None
    assert "Sem dado de clima agora" in top(app)
    assert "Confiança da sugestão: baixa" in " ".join(app.recomendacao())
    assert any("POWER indisponível nesta célula" in t for t in app.legendas())
