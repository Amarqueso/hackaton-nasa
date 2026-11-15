"""Diferencial: El Niño / La Niña (NOAA ONI), calendário oficial ZARC de Brasília e aptidão da área (Dynamic World)."""
import re
from unittest import mock

import pytest

import aptidao
import enso
import zarc
from conftest import oni
from fake_ee import cobertura
from test_cache_regional import dados

SOLO = "Não sei (usar solo médio)"


@pytest.fixture
def pagina(app):
    app.marcar_terra()
    return app


def janeiro(app):
    app.at.selectbox(key="mes").set_value(1)
    return app.run()


def textos(app):
    return " ".join(app.textos())


# ---------------- El Niño / La Niña ----------------

def test_classifica_fases_e_intensidade():
    assert enso.classificar(oni(0.1, 0.0, -0.2, 0.3, 0.1))["fase"] == "Neutro"
    forte = enso.classificar(oni(0.6, 0.9, 1.2, 1.4, 1.6, 1.7))
    assert forte["fase"] == "El Niño" and forte["intensidade"] == "forte" and forte["confirmado"]
    formando = enso.classificar(oni(0.1, 0.2, -0.6, -0.8))
    assert formando["fase"] == "La Niña" and not formando["confirmado"]  # só 2 trimestres seguidos
    assert enso.rotulo(formando) == "La Niña fraco (em formação)"


def test_mostra_a_fase_do_enso_nos_dados_da_terra(pagina):
    assert dados(pagina)["Pacífico (El Niño / La Niña)"].startswith("Neutro")
    assert "aceitamos datas com risco de perda de até 30%" in textos(pagina)
    assert pagina.fontes()["El Niño / La Niña"] == "sim"


def test_el_nino_forte_restringe_o_zarc_a_20_por_cento(app):
    app.oni.return_value = oni(0.8, 1.1, 1.4, 1.7, 2.0, 2.3)
    app.marcar_terra()
    assert dados(app)["Pacífico (El Niño / La Niña)"].startswith("El Niño muito forte")
    assert "aceitamos datas com risco de perda de até 20%" in textos(app)


def test_falha_da_noaa_nao_derruba_a_pagina(app):
    app.oni.side_effect = RuntimeError("NOAA fora do ar")
    app.marcar_terra()
    assert dados(app)["Pacífico (El Niño / La Niña)"] is None
    assert "Falha em 'enso': NOAA fora do ar" in app.legendas()
    assert any('class="destaque"' in t for t in app.textos())  # a recomendação continua


# ---------------- ZARC ----------------

def test_risco_do_zarc_por_mes_e_cultura():
    assert zarc.risco_mes("Milho", SOLO, 1) == (20, 1)        # milho 2ª safra em janeiro
    assert zarc.risco_mes("Soja", SOLO, 6) == (0, None)        # soja em junho: fora do zoneamento
    assert zarc.risco_mes("Alface", SOLO, 6) is None           # cultura sem ZARC
    assert zarc.risco_mes("Feijão", SOLO, 7, True)[0] == 20    # feijão irrigado em julho
    assert zarc.tolerancia(None) == 30


def test_cultura_no_calendario_explica_o_risco(pagina):
    janeiro(pagina)
    assert "Está no calendário oficial (ZARC)" in " ".join(pagina.recomendacao())


def test_cultura_fora_do_calendario_perde_pontos(pagina):
    janeiro(pagina)
    antes = re.search(r"<h3>Plantar (.*?) em janeiro</h3>", textos(pagina)).group(1)
    assert antes in zarc.DO_CATALOGO  # no cenário padrão, uma cultura com ZARC lidera em janeiro
    fora = lambda cultura, *a: (0, None) if cultura in zarc.DO_CATALOGO else None  # as demais não têm ZARC
    with mock.patch("zarc.risco_mes", side_effect=fora):
        pagina.run()
        depois = re.search(r"<h3>Plantar (.*?) em janeiro</h3>", textos(pagina)).group(1)
    assert depois != antes and depois not in zarc.DO_CATALOGO


def test_tipo_de_solo_vem_do_formulario(pagina):
    assert pagina.at.selectbox(key="solo").value == SOLO
    pagina.at.selectbox(key="solo").set_value("Argiloso, segura bem a água (AD5)")
    pagina.run()
    assert not pagina.at.exception


# ---------------- Aptidão da área ----------------

def test_regras_de_aptidao():
    base = dict(agua=0, arvores=0, campo=0, alagado=0, lavoura=0, arbustos=0, construcao=0, solo_exposto=0, neve=0)
    info = lambda **c: {"cobertura": {**base, **c}, "agua_perm": c.get("agua", 0), "agua_sazonal": 0,
                        "uc_frac": 0, "uc_nomes": [], "sem_dado": False}
    assert aptidao.avaliar(info(lavoura=0.9, campo=0.1))["nivel"] == "apta"
    assert aptidao.avaliar(info(construcao=0.6, campo=0.4))["nivel"] == "inapta"
    assert aptidao.avaliar(info(agua=0.5, lavoura=0.5))["nivel"] == "inapta"
    r = aptidao.avaliar(info(lavoura=0.8, agua=0.1, campo=0.1), ha=100)
    assert r["nivel"] == "restricao" and round(r["util_ha"]) == 90
    uc = {**info(arbustos=0.6, arvores=0.4), "uc_frac": 1.0, "uc_nomes": ["Parque Nacional de Brasília"]}
    assert "Parque Nacional de Brasília" in aptidao.avaliar(uc)["motivos"][0]


def test_area_apta_mostra_selo_verde(pagina):
    assert "Área apta para plantio" in textos(pagina)
    assert pagina.fontes()["Cobertura do solo"] == "sim"
    assert not pagina.at.checkbox


def test_area_construida_esconde_a_sugestao_ate_o_produtor_confirmar(app):
    app.ee.respostas["DYNAMICWORLD"] = lambda: cobertura({"construcao": 0.95, "campo": 0.05})
    app.marcar_terra()
    assert "Esta área não parece própria para plantio" in textos(app)
    assert "95% da área tem prédios" in textos(app)
    assert not any('class="destaque"' in t for t in app.textos())
    app.at.checkbox(key="forcar").check()
    app.run()
    assert any('class="destaque"' in t for t in app.textos())


def test_falha_da_cobertura_nao_derruba_a_pagina(app):
    def falha():
        raise RuntimeError("Dynamic World indisponível")
    app.ee.respostas["DYNAMICWORLD"] = falha
    app.marcar_terra()
    assert "Falha em 'cobertura': Dynamic World indisponível" in app.legendas()
    assert any('class="destaque"' in t for t in app.textos())
