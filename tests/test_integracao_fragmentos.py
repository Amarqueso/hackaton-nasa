"""#6 com #8/#10/#14: rerun só do formulário precisa atualizar dados e parecer, sem reenviar o mapa."""
import datetime as dt
import json
from unittest import mock

import pytest
import streamlit as st

from conftest import resposta_power
from fake_ee import serie, serie_mensal
from test_cache_disco import fonte_do_clima, NASA
from test_cache_regional import A, clima_mensal, dados, falhas
from test_clima_local import instalar
from test_retentativa import consultas, simular_falha
from test_virada_mes import data_atual, inicio_consultado, meses


@pytest.fixture
def pagina(app):
    with mock.patch("streamlit_folium._component_func", return_value=None) as componente:
        app.componente = componente
        yield app


def formulario(app, mes=3):
    app.at.selectbox(key="mes").set_value(mes)
    return app.rodar_fragmento("passo2")


@pytest.mark.parametrize("fonte", ["clima", "terreno", "solo", "veg"])
def test_fragmento_recupera_so_fonte_falha_e_atualiza_parecer_e_confianca(pagina, relogio_fontes, fonte):
    app = pagina
    recuperar = simular_falha(app, fonte)
    app.marcar_terra(A)
    assert any(c.startswith(f"Falha em '{fonte}':") for c in falhas(app))
    antes, mapa = consultas(app), app.componente.call_count
    recuperar()
    relogio_fontes[0] += 119
    formulario(app, 2)
    assert consultas(app) == antes
    assert any(c.startswith(f"Falha em '{fonte}':") for c in falhas(app))

    relogio_fontes[0] += 2
    formulario(app, 3)
    assert consultas(app) == {k: n + (k == fonte) for k, n in antes.items()}
    assert not falhas(app)
    assert all(v is not None for v in dados(app).values())
    assert all(v == "sim" for k, v in app.fontes().items() if not k.startswith("Radar"))
    assert any("Confiança da sugestão: alta" in t for t in app.recomendacao())
    assert app.componente.call_count == mapa
    assert len(app.at.expander) == 1
    assert sum('class="dados"' in t for t in app.textos()) == 1


@pytest.mark.parametrize("antes,depois", [("2026-11-30", "2026-12-01"), ("2026-12-31", "2027-01-01")])
def test_fragmento_atualiza_janelas_solo_clima_parecer_e_calendario_sem_mapa(pagina, data_atual, antes, depois):
    app = pagina
    inicio_antes = dt.date.fromisoformat(antes).replace(day=1)
    data_atual[0] = dt.date.fromisoformat(antes)
    inicio_serie = inicio_antes.replace(year=inicio_antes.year - 1)

    def resposta_serie(lin, radar=False):
        inicio = inicio_consultado(lin)
        n = 4 if inicio == inicio_serie else 5
        valores = [-15.0] * 12 if radar else [2000.0] * n + [5000.0] * (12 - n)
        return {"features": [{"properties": {"m": m, "v": v}} for m, v in zip(meses(inicio), valores)]}

    app.ee.respostas["MOD13Q1"] = resposta_serie
    app.ee.respostas["S1_GRD"] = lambda lin: resposta_serie(lin, radar=True)

    def solo(lin):
        limite = next(json.loads(x.removeprefix("filterDate:"))[1] for x in lin if x.startswith("filterDate:"))
        fim = dt.date.fromisoformat(limite)
        atual = fim.replace(day=1) - dt.timedelta(days=1)
        referencia = atual.replace(day=1)
        ultimo = (referencia.year - (referencia.month < 3), (referencia.month - 3) % 12 + 1)
        novo = referencia != inicio_antes
        return serie_mensal(lambda a, m: (0.1 if novo else 0.5) if a == ultimo[0] else (0.4 if a % 2 else 0.6), ate=ultimo)

    app.ee.respostas["FLDAS"] = solo

    def power(url, params, **kw):
        resposta = resposta_power()
        par = resposta.json.return_value["properties"]["parameter"]
        par["T2M"] = {k: float(params["end"] - 2000) for k in par["T2M"]}
        return resposta

    app.get.side_effect = power
    with mock.patch("streamlit.line_chart", wraps=st.line_chart) as graficos:
        app.marcar_terra(A).tecnico(True)
        assert dados(app)["Meses com pouca planta no último ano"] == "4 de 12"
        solo_antes = dados(app)["Umidade do solo"]
        assert solo_antes.startswith("Normal (")
        mapa, camadas, chamadas = app.componente.call_count, len(app.ee.mapas), consultas(app)

        data_atual[0] = dt.date.fromisoformat(depois)
        graficos.reset_mock()
        formulario(app)
        nova_ref = data_atual[0].replace(year=data_atual[0].year - 1, day=1)
        assert [list(c.args[0].index) for c in graficos.call_args_list] == [meses(nova_ref)] * 2
        assert dados(app)["Meses com pouca planta no último ano"] == "5 de 12"
        assert dados(app)["Umidade do solo"].startswith("Bem mais seca que o normal (")
        assert dados(app)["Umidade do solo"] != solo_antes
        assert fonte_do_clima(app).startswith(NASA + str(data_atual[0].year - 1) + ",")
        assert clima_mensal(app)["T média (°C)"].eq(float(data_atual[0].year - 2001)).all()
        seletor = app.at.selectbox(key="mes")
        assert [x for x in seletor.options if "(este mês)" in x] == [{1: "Janeiro", 12: "Dezembro"}[data_atual[0].month] + " (este mês)"]
        assert consultas(app)["terreno"] == chamadas["terreno"]
        assert app.componente.call_count == mapa and len(app.ee.mapas) == camadas
        assert not falhas(app)
        assert len(app.at.expander) == 1


@pytest.mark.parametrize("mudanca", ["atualizar", "remover", "corromper"])
def test_fragmento_rele_json_e_periodo_sem_ocultar_fallback(pagina, arquivo_clima, mudanca):
    app = pagina
    documento = instalar(arquivo_clima)
    documento["periodo"]["fim"] = 2024  # período controlado diferente do ano usado pelo fallback
    for meses_celula in documento["celulas"].values():
        for v in meses_celula.values(): v["t"] = 18.0
    instalar(arquivo_clima, documento)
    app.marcar_terra(A)
    assert fonte_do_clima(app).startswith(NASA + "2024,")
    assert app.get.call_count == 0
    mapa, chamadas = app.componente.call_count, consultas(app)

    if mudanca == "atualizar":
        documento["periodo"]["fim"] = 2025
        for meses_celula in documento["celulas"].values():
            for v in meses_celula.values(): v["t"] = 23.0
        instalar(arquivo_clima, documento)
    elif mudanca == "remover":
        arquivo_clima.unlink()
    else:
        arquivo_clima.write_text('{"celulas":')
    formulario(app)
    assert fonte_do_clima(app).startswith(NASA + "2025,")
    assert clima_mensal(app)["T média (°C)"].eq(23.0 if mudanca == "atualizar" else 22.0).all()
    assert app.get.call_count == (0 if mudanca == "atualizar" else 1)
    assert {k: v for k, v in consultas(app).items() if k != "clima"} == {k: v for k, v in chamadas.items() if k != "clima"}
    assert app.componente.call_count == mapa
    assert not falhas(app)


def test_fragmento_aceita_json_do_ano_encerrado_sem_usar_today_global(pagina, arquivo_clima, data_atual):
    app = pagina
    data_atual[0] = dt.date(2026, 12, 31)
    documento = instalar(arquivo_clima)
    app.marcar_terra(A)
    assert fonte_do_clima(app).startswith(NASA + "2025,")
    mapa = app.componente.call_count
    data_atual[0] = dt.date(2027, 1, 1)
    documento["periodo"]["fim"] = 2026
    for meses_celula in documento["celulas"].values():
        for v in meses_celula.values(): v["t"] = 23.0
    instalar(arquivo_clima, documento)
    formulario(app)
    assert app.get.call_count == 0  # não rejeita JSON novo com a data antiga e cai na API
    assert fonte_do_clima(app).startswith(NASA + "2026,")
    assert clima_mensal(app)["T média (°C)"].eq(23.0).all()
    assert app.componente.call_count == mapa


def test_fragmento_recupera_radar_e_atualiza_parecer_sem_mapa_ou_outras_fontes(pagina, relogio_fontes):
    app = pagina
    recuperar = simular_falha(app, "radar")
    app.marcar_terra(A).tecnico(True)
    assert app.fontes()["Radar VH com filtro Lee"] == "NÃO" and falhas(app)
    mapa, antes = app.componente.call_count, consultas(app)
    recuperar()
    formulario(app, 2)
    assert consultas(app) == antes
    relogio_fontes[0] += 121
    formulario(app, 3)
    assert consultas(app) == {k: n + (k == "radar") for k, n in antes.items()}
    assert app.fontes()["Radar VH com filtro Lee"] == "sim"
    assert app.tem_grafico_radar() and not falhas(app)
    assert app.componente.call_count == mapa
    depois = consultas(app)
    formulario(app, 4)
    assert consultas(app) == depois
    app.run().tecnico(False)  # AppTest só tem a árvore do fragmento; restaura a sidebar para mudar o toggle
    antes, mapa = consultas(app), app.componente.call_count
    formulario(app, 5)
    assert consultas(app) == antes
    assert not app.tem_grafico_radar() and app.fontes()["Radar VH com filtro Lee"] == "só no modo técnico"
    assert app.componente.call_count == mapa


@pytest.mark.parametrize("fonte", ["solo", "veg"])
def test_fragmento_preserva_none_legitimo_sem_retentativa(pagina, relogio_fontes, fonte):
    app = pagina
    app.ee.respostas["FLDAS" if fonte == "solo" else "MOD13Q1"] = (lambda: serie_mensal(lambda a, m: None)) if fonte == "solo" else (lambda: serie([-9999.0] * 12))
    app.marcar_terra(A)
    antes, mapa = consultas(app), app.componente.call_count
    relogio_fontes[0] += 121
    formulario(app)
    assert consultas(app) == antes and not falhas(app)
    assert dados(app)["Umidade do solo" if fonte == "solo" else "Meses com pouca planta no último ano"] is None
    assert app.componente.call_count == mapa
