"""Issue #19: o Earth Engine inicializa uma vez por processo; uma falha tem pausa de 120 s e o Initialize tem prazo."""
import concurrent.futures
import threading

import pytest
from streamlit.testing.v1 import AppTest

from conftest import APP
from test_retentativa import mudar_resposta


def falhas_gee(app):
    return [c for c in app.legendas() if c.startswith("Falha em 'gee'")]


def nova_sessao(app):
    """Outra aba no mesmo processo: sessão nova, mesmo `cache_resource`."""
    app.at = AppTest.from_file(APP, default_timeout=30)
    return app


def test_sucesso_inicializa_uma_vez_por_processo(app):
    app.marcar_terra()
    mudar_resposta(app, 2)
    app.tecnico(True)
    nova_sessao(app).marcar_terra()
    assert app.ee.inits == 1
    assert app.ee.chamadas["SRTMGL1"] == 1  # o Earth Engine está sendo usado


def test_aviso_diz_que_esta_conectando(app):
    app.marcar_terra()
    assert "Conectando ao Earth Engine…" in app.spinners
    assert not any("init_ee" in s for s in app.spinners)
    mudar_resposta(app, 2)
    assert "Conectando ao Earth Engine…" not in app.spinners  # já inicializado: não espera de novo


def test_falha_nao_refaz_o_init_em_cada_rerun(app, relogio_fontes):
    app.ee.init_erro = "GEE_PROJECT inválido"
    app.marcar_terra()
    for mes in (2, 3, 4):
        mudar_resposta(app, mes)
    app.tecnico(True)
    assert app.ee.inits == 1
    assert sum(app.ee.chamadas.values()) == 0
    assert falhas_gee(app) == ["Falha em 'gee': GEE_PROJECT inválido"]
    assert "Conectando ao Earth Engine…" not in app.spinners


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


@pytest.fixture
def rede_travada(app, monkeypatch):
    """Initialize que não responde até o fim do teste; o prazo de 30 s é encurtado para não esperar de verdade."""
    solta = threading.Event()
    app.ee.init_erro = lambda: solta.wait(10)
    prazos = []
    original = concurrent.futures.Future.result

    def result(self, timeout=None):
        if timeout == 30:
            prazos.append(timeout)
            timeout = 0.2
        return original(self, timeout)

    monkeypatch.setattr(concurrent.futures.Future, "result", result)
    yield prazos
    solta.set()


def test_rede_travada_desiste_no_prazo(app, rede_travada, relogio_fontes):
    app.marcar_terra()
    assert rede_travada == [30]
    assert falhas_gee(app) == ["Falha em 'gee': Earth Engine não respondeu em 30 s"]
    assert sum(app.ee.chamadas.values()) == 0
    assert app.get.call_count == 1  # o resto do app segue sem o Earth Engine


def test_tentativa_ainda_presa_nao_e_repetida(app, rede_travada, relogio_fontes):
    app.marcar_terra()
    relogio_fontes[0] += 121
    mudar_resposta(app, 2)
    assert app.ee.inits == 1  # a primeira ainda não voltou: não abre outra
    assert falhas_gee(app) == ["Falha em 'gee': Earth Engine não respondeu em 30 s"]
