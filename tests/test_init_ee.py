"""Issues #19 e #20: o Earth Engine inicializa em segundo plano, uma vez por processo, e só se espera por ele onde um dado
dele é necessário; uma falha tem pausa de 120 s e a espera tem prazo de 30 s desde o início da tentativa."""
import concurrent.futures
import threading

import pytest
import streamlit_folium
from streamlit.testing.v1 import AppTest

from conftest import APP
from test_retentativa import mudar_resposta

CONECTANDO = "Conectando ao Earth Engine…"


def falhas_gee(app):
    return [c for c in app.legendas() if c.startswith("Falha em 'gee'")]


def nova_sessao(app):
    """Outra aba no mesmo processo: sessão nova, mesmo `cache_resource`."""
    app.at = AppTest.from_file(APP, default_timeout=30)
    return app


@pytest.fixture
def init_lento(app):
    """Initialize que só volta quando o teste solta (`solta.set()` ou `soltar_em(s)`); o fim do teste solta sempre."""
    solta = threading.Event()
    app.ee.init_erro = lambda: solta.wait(10)
    solta.soltar_em = lambda s: threading.Timer(s, solta.set).start()
    yield solta
    solta.set()


@pytest.fixture
def rede_travada(init_lento, monkeypatch):
    """Initialize que não responde; o prazo de 30 s é encurtado para não esperar de verdade."""
    prazos = []
    original = concurrent.futures.Future.result

    def result(self, timeout=None):
        if timeout is not None:
            prazos.append(timeout)
            timeout = min(timeout, 0.2)
        return original(self, timeout)

    monkeypatch.setattr(concurrent.futures.Future, "result", result)
    return prazos


# --- #20: não bloquear a página ---

def test_sem_terra_a_pagina_nao_espera(app, init_lento):
    app.run()
    assert app.ee.inits == 1  # começou em segundo plano
    assert CONECTANDO not in app.spinners
    assert app.at.button[0].disabled  # passo 1 desenhado ("Apagar" desligado sem terra)


def test_mapa_e_formulario_saem_antes_de_esperar(app, init_lento, monkeypatch):
    antes_do_mapa = []
    original = streamlit_folium.st_folium

    def st_folium(*a, **kw):
        antes_do_mapa.append(list(app.spinners))
        return original(*a, **kw)

    monkeypatch.setattr(streamlit_folium, "st_folium", st_folium)
    init_lento.soltar_em(0.3)
    app.marcar_terra()
    assert antes_do_mapa == [[]]  # o mapa saiu sem nenhuma espera antes
    # o formulário (passo 2) vem antes do "Consultando os satélites", que vem antes da espera pelo init
    assert app.spinners.index("Consultando os satélites… pode levar um minuto.") < app.spinners.index(CONECTANDO)
    assert app.at.selectbox(key="mes").value
    assert falhas_gee(app) == []
    assert app.ee.chamadas["SRTMGL1"] == 1  # os dados do EE entram na mesma execução


def test_init_ja_pronto_nao_mostra_aviso(app):
    app.run()  # a pessoa abre a página e só depois marca a terra
    app.marcar_terra()
    assert CONECTANDO not in app.spinners
    assert app.ee.chamadas["SRTMGL1"] == 1


def test_aviso_so_enquanto_espera(app, init_lento):
    init_lento.soltar_em(0.3)
    app.marcar_terra()
    assert CONECTANDO in app.spinners
    assert not any("init_ee" in s for s in app.spinners)
    mudar_resposta(app, 2)
    assert CONECTANDO not in app.spinners  # já inicializado: não espera de novo


def test_clima_nao_espera_pelo_earth_engine(app, rede_travada, relogio_fontes):
    vistos = []
    resposta = app.get.return_value
    app.get.side_effect = lambda *a, **kw: (vistos.append(list(app.spinners)), resposta)[1]
    app.marcar_terra()
    assert app.get.call_count == 1
    assert CONECTANDO not in vistos[0]  # a POWER foi consultada antes de esperar pelo init
    assert any("Temperatura" in t for t in app.textos()) or app.recomendacao()


def test_modo_tecnico_espera_o_init_para_camadas_e_radar(app, init_lento):
    app.run()
    app.tecnico(True)  # sem terra: não espera
    assert CONECTANDO not in app.spinners
    init_lento.soltar_em(0.3)
    app.marcar_terra()
    assert app.ee.mapas  # camadas calculadas depois do init, no fim da página
    assert app.ee.chamadas["S1_GRD"] >= 1  # radar
    assert falhas_gee(app) == []


# --- #19: uma vez por processo, pausa e prazo ---

def test_sucesso_inicializa_uma_vez_por_processo(app):
    app.marcar_terra()
    mudar_resposta(app, 2)
    app.tecnico(True)
    nova_sessao(app).marcar_terra()
    assert app.ee.inits == 1
    assert app.ee.chamadas["SRTMGL1"] == 1  # o Earth Engine está sendo usado


def test_duas_sessoes_esperam_a_mesma_tentativa(app, init_lento):
    app.run()
    nova_sessao(app).run()
    init_lento.soltar_em(0.3)
    app.marcar_terra()
    assert app.ee.inits == 1


def test_falha_nao_refaz_o_init_em_cada_rerun(app, relogio_fontes):
    app.ee.init_erro = "GEE_PROJECT inválido"
    app.marcar_terra()
    for mes in (2, 3, 4):
        mudar_resposta(app, mes)
    app.tecnico(True)
    assert app.ee.inits == 1
    assert sum(app.ee.chamadas.values()) == 0
    assert falhas_gee(app) == ["Falha em 'gee': GEE_PROJECT inválido"]
    assert CONECTANDO not in app.spinners


def test_pausa_vale_para_sessao_nova(app, relogio_fontes):
    app.ee.init_erro = "GEE_PROJECT inválido"
    app.marcar_terra()
    relogio_fontes[0] += 60
    nova_sessao(app).marcar_terra()
    assert app.ee.inits == 1
    assert falhas_gee(app) == ["Falha em 'gee': GEE_PROJECT inválido"]


def test_depois_da_pausa_tenta_de_novo_e_recupera(app, relogio_fontes):
    app.ee.init_erro = "GEE_PROJECT inválido"
    app.marcar_terra()
    relogio_fontes[0] += 119
    mudar_resposta(app, 2)
    assert app.ee.inits == 1

    app.ee.init_erro = None  # credenciais corrigidas / rede voltou
    relogio_fontes[0] += 2
    mudar_resposta(app, 3)
    assert app.ee.inits == 2
    assert falhas_gee(app) == []
    assert app.ee.chamadas["SRTMGL1"] == 1  # voltou a consultar sem reiniciar o servidor

    relogio_fontes[0] += 1000
    mudar_resposta(app, 4)
    assert app.ee.inits == 2  # sucesso fica para sempre no processo


def test_falha_de_novo_depois_da_pausa_comeca_outra_pausa(app, relogio_fontes):
    app.ee.init_erro = "GEE_PROJECT inválido"
    app.marcar_terra()
    relogio_fontes[0] += 121
    mudar_resposta(app, 2)
    relogio_fontes[0] += 119
    mudar_resposta(app, 3)
    assert app.ee.inits == 2


def test_reiniciar_o_servidor_tenta_de_novo(app, relogio_fontes):
    app.ee.init_erro = "GEE_PROJECT inválido"
    app.marcar_terra()
    app.reiniciar().marcar_terra()
    assert app.ee.inits == 2


def test_rede_travada_desiste_no_prazo(app, rede_travada, relogio_fontes):
    app.marcar_terra()
    assert rede_travada == [30]  # prazo contado desde o início da tentativa
    assert falhas_gee(app) == ["Falha em 'gee': Earth Engine não respondeu em 30 s"]
    assert sum(app.ee.chamadas.values()) == 0
    assert app.get.call_count == 1  # o resto do app segue sem o Earth Engine


def test_prazo_conta_desde_o_inicio_da_tentativa(app, rede_travada, relogio_fontes):
    app.run()  # a tentativa começa aqui
    relogio_fontes[0] += 20
    app.marcar_terra()
    assert rede_travada == [10]


def test_tentativa_ainda_presa_nao_e_repetida(app, rede_travada, relogio_fontes):
    app.marcar_terra()
    relogio_fontes[0] += 121
    mudar_resposta(app, 2)
    assert app.ee.inits == 1  # a primeira ainda não voltou: não abre outra
    assert falhas_gee(app) == ["Falha em 'gee': Earth Engine não respondeu em 30 s"]
