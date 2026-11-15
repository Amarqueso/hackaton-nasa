"""Issue #10: a mesma sessão troca a janela mensal sem reiniciar ou limpar caches."""
import datetime as dt
import json
from unittest import mock

import pytest
import streamlit as st

from conftest import resposta_power
from fake_ee import serie_mensal
from test_cache_disco import fonte_do_clima, NASA
from test_cache_regional import clima_mensal
from test_coletar import dados


@pytest.fixture
def data_atual(monkeypatch):
    agora = [dt.date(2026, 11, 30)]

    class DataFixa(dt.date):
        @classmethod
        def today(cls):
            return agora[0]

    monkeypatch.setattr(dt, "date", DataFixa)
    return agora


def inicio_consultado(linhagem):
    datas = [x.removeprefix("date:") for x in linhagem if x.startswith("date:")]
    assert len(datas) == 1
    return dt.date.fromisoformat(datas[0])


def meses(inicio):
    return [f"{inicio.year + (inicio.month - 1 + i) // 12}-{(inicio.month - 1 + i) % 12 + 1:02d}" for i in range(12)]


@pytest.mark.parametrize("antes, depois", [("2026-11-30", "2026-12-01"), ("2026-12-31", "2027-01-01")])
def test_mes_novo_atualiza_janelas_solo_clima_e_seletor_na_mesma_sessao(app, data_atual, antes, depois):
    dia_antes, dia_depois = dt.date.fromisoformat(antes), dt.date.fromisoformat(depois)
    data_atual[0] = dia_antes
    inicios = [dia.replace(year=dia.year - 1, day=1) for dia in (dia_antes, dia_depois)]
    consultas = {"veg": [], "radar": [], "solo": []}

    def serie_consultada(linhagem, tipo):
        inicio = inicio_consultado(linhagem)
        consultas[tipo].append(inicio)
        # A resposta deriva da data efetivamente passada a ee.Date, não do relógio do teste.
        n = 4 if inicio == inicios[0] else 5
        valores = ([2000.0] * n + [5000.0] * (12 - n)) if tipo == "veg" else [-15.0] * 12
        return {"features": [{"properties": {"m": m, "v": v}} for m, v in zip(meses(inicio), valores)]}

    app.ee.respostas["MOD13Q1"] = lambda lin: serie_consultada(lin, "veg")
    app.ee.respostas["S1_GRD"] = lambda lin: serie_consultada(lin, "radar")

    def solo(linhagem):
        limites = next(json.loads(x.removeprefix("filterDate:")) for x in linhagem if x.startswith("filterDate:"))
        consultas["solo"].append(limites)
        primeiro_fim = dt.date(dia_antes.year + (dia_antes.month == 12), dia_antes.month % 12 + 1, 1).isoformat()
        novo = limites[1] != primeiro_fim
        # Produto publicado com atraso: último dado dois meses antes, em cada consulta.
        ref = dia_depois if novo else dia_antes
        ultimo = (ref.year - (ref.month < 3), (ref.month - 3) % 12 + 1)
        return serie_mensal(lambda a, m: (0.1 if novo else 0.5) if a == ultimo[0] else (0.4 if a % 2 else 0.6), ate=ultimo)

    app.ee.respostas["FLDAS"] = solo

    def power(url, params, **kw):
        resposta = resposta_power()
        parametros = resposta.json.return_value["properties"]["parameter"]
        parametros["T2M"] = {k: float(params["end"] - 2000) for k in parametros["T2M"]}
        return resposta

    app.get.side_effect = power
    with mock.patch("streamlit.line_chart", wraps=st.line_chart) as graficos:
        app.marcar_terra().tecnico(True)
        assert [list(c.args[0].index) for c in graficos.call_args_list[-2:]] == [meses(inicios[0])] * 2
        assert dados(app)["Meses com pouca planta no último ano"] == "4 de 12"
        solo_antes = dados(app)["Umidade do solo"]
        assert solo_antes.startswith("Normal (")
        chamadas = app.ee.chamadas.copy()

        # Mesma sessão, mesma terra, sem clear() e sem reiniciar.
        data_atual[0] = dia_depois
        graficos.reset_mock()
        app.run()
        assert [list(c.args[0].index) for c in graficos.call_args_list] == [meses(inicios[1])] * 2
        assert consultas["veg"] == inicios and consultas["radar"] == inicios
        assert len(consultas["solo"]) == 2 and consultas["solo"][0] != consultas["solo"][1]
        assert dados(app)["Meses com pouca planta no último ano"] == "5 de 12"
        assert dados(app)["Umidade do solo"].startswith("Bem mais seca que o normal (")
        assert dados(app)["Umidade do solo"] != solo_antes
        assert app.ee.chamadas["FLDAS"] == chamadas["FLDAS"] + 1
        assert fonte_do_clima(app).startswith(NASA + str(dia_depois.year - 1) + ",")
        assert clima_mensal(app)["T média (°C)"].eq(float(dia_depois.year - 2001)).all()
        assert [c.kwargs["params"]["end"] for c in app.get.call_args_list] == sorted({dia_antes.year - 1, dia_depois.year - 1})
        seletor = next(s for s in app.at.selectbox if s.label == "Quando pretende plantar?")
        este_mes = [s for s in seletor.options if "(este mês)" in s]
        assert len(este_mes) == 1
        assert este_mes[0].startswith({1: "Janeiro", 12: "Dezembro"}[dia_depois.month])


def test_mesmo_mes_reutiliza_series_e_solo_inclusive_outro_dia(app, data_atual):
    data_atual[0] = dt.date(2026, 11, 1)
    app.marcar_terra().tecnico(True)
    chamadas, power, resultado = app.ee.chamadas.copy(), app.get.call_count, app.recomendacao()
    data_atual[0] = dt.date(2026, 11, 30)
    app.run()
    assert app.ee.chamadas == chamadas
    assert app.get.call_count == power
    assert app.recomendacao() == resultado


def test_layers_muda_na_virada_e_preserva_recencia_diaria_apos_ttl(app, data_atual, monkeypatch):
    from streamlit.runtime.caching import cache_utils

    relogio = [1000.0]
    monkeypatch.setattr(cache_utils, "TTLCACHE_TIMER", lambda: relogio[0])
    data_atual[0] = dt.date(2026, 11, 30)
    app.marcar_terra().tecnico(True)
    assert len(app.ee.mapas) == 3
    assert {inicio_consultado(lin) for lin, url in app.ee.mapas} == {dt.date(2026, 11, 30)}

    # O TTL ainda não venceu: o mês na chave basta para invalidar as três URLs.
    data_atual[0] = dt.date(2026, 12, 1)
    app.run()
    assert len(app.ee.mapas) == 6
    assert {inicio_consultado(lin) for lin, url in app.ee.mapas[-3:]} == {dt.date(2026, 12, 1)}

    # No mesmo mês a renovação do TTL usa o dia atual, sem ancorar a janela no dia 1.
    data_atual[0] = dt.date(2026, 12, 15)
    relogio[0] += 3601
    app.run()
    assert len(app.ee.mapas) == 9
    assert {inicio_consultado(lin) for lin, url in app.ee.mapas[-3:]} == {dt.date(2026, 12, 15)}
