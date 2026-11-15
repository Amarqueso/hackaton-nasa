"""Issue #2: as consultas de `coletar` (NASA POWER e Earth Engine) rodam ao mesmo tempo."""
import logging
import re
import threading

import pytest
import requests

from fake_ee import geometria

CONSULTANDO = "Consultando os satélites… pode levar um minuto."
EE = ("SRTMGL1", "FLDAS", "MOD13Q1")  # terreno, solo, NDVI
SEM_DADO = 'class="falta">Sem dado'


def dados(app):
    """Seção "O que sabemos sobre a sua terra" como {rótulo: valor} (valor None = "Sem dado")."""
    html = next(t for t in app.textos() if 'class="dados"' in t)
    itens = re.findall(r"<dt>(.*?)</dt><dd( class=\"falta\")?>(.*?)</dd>", html)
    return {rot: None if falta else val for rot, falta, val in itens}


def esperar_todas(app, n=4, timeout=5):
    """Faz POWER e as três consultas do Earth Engine esperarem umas pelas outras numa barreira.
    Se `coletar` consultasse uma de cada vez, a primeira ficaria presa e a barreira estouraria."""
    barreira = threading.Barrier(n, timeout=timeout)
    for k in EE:
        resp, feito = app.ee.respostas[k], []

        def r(resp=resp, feito=feito):
            if not feito:  # só a primeira consulta de cada coleção espera
                feito.append(1)
                barreira.wait()
            return resp()
        app.ee.respostas[k] = r
    get = app.get.return_value

    def power(*a, **kw):
        barreira.wait()
        return get
    app.get.side_effect = power


def test_consultas_rodam_ao_mesmo_tempo(app):
    esperar_todas(app)
    app.marcar_terra()
    assert not [c for c in app.legendas() if c.startswith("Falha em")]
    assert all(v == "sim" for k, v in app.fontes().items() if not k.startswith("Radar"))


def test_consultas_rodam_fora_da_thread_principal_sem_contexto_do_streamlit(app):
    from streamlit.runtime.scriptrunner_utils.script_run_context import get_script_run_ctx
    contextos = {}
    for k in EE:
        resp = app.ee.respostas[k]

        def r(resp=resp, k=k):
            contextos.setdefault(k, get_script_run_ctx(suppress_warning=True))
            return resp()
        app.ee.respostas[k] = r
    app.marcar_terra()
    principal = threading.current_thread()
    assert all(t is not principal for t in app.ee.threads.values())
    assert contextos == {k: None for k in EE}, "as threads não rodam código do Streamlit"


@pytest.fixture
def avisos_streamlit():
    """Guarda os avisos de log do Streamlit (ex.: "missing ScriptRunContext")."""
    registros = []

    class H(logging.Handler):
        def emit(self, rec):
            registros.append(rec)
    h = H(level=logging.WARNING)
    log = logging.getLogger("streamlit")
    log.addHandler(h)
    yield registros
    log.removeHandler(h)


def test_threads_nao_geram_aviso_de_script_run_context(app, avisos_streamlit):
    app.marcar_terra()
    app.marcar_terra((-15.60, -47.65))
    assert app.get.call_count == 2
    fora = [r.getMessage() for r in avisos_streamlit if r.threadName != threading.current_thread().name]
    assert not fora, fora


def test_resultado_tem_o_mesmo_formato_e_aparece_na_tela(app):
    app.marcar_terra()
    assert app.get.call_count == 1
    assert {k: app.ee.chamadas[k] for k in EE} == {"SRTMGL1": 1, "FLDAS": 1, "MOD13Q1": 1}
    tela = dados(app)
    assert None not in tela.values(), tela
    assert tela["Altitude"] == "1100 m"
    assert any("Confiança da sugestão: alta" in t for t in app.recomendacao())
    assert CONSULTANDO in app.spinners


def test_falha_na_power_nao_derruba_as_outras(app):
    app.get.side_effect = requests.ConnectionError("sem internet")
    app.marcar_terra()
    tela = dados(app)
    assert tela["Temperatura nos próximos meses"] is None
    assert tela["Chuva esperada em 4 meses"] is None
    assert tela["Altitude"] == "1100 m" and tela["Terreno"] and tela["Umidade do solo"]
    assert tela["Meses com pouca planta no último ano"]
    assert "Falha em 'clima': sem internet" in app.legendas()
    assert app.fontes()["Clima (T2M, chuva, UR)"] == "NÃO"


def test_falha_no_earth_engine_nao_derruba_as_outras(app):
    def falha():
        raise RuntimeError("Computation timed out.")
    app.ee.respostas["SRTMGL1"] = falha
    app.marcar_terra()
    tela = dados(app)
    assert tela["Altitude"] is None and tela["Terreno"] is None
    assert tela["Temperatura nos próximos meses"] and tela["Umidade do solo"]
    assert tela["Meses com pouca planta no último ano"]
    assert [c for c in app.legendas() if c.startswith("Falha em")] == ["Falha em 'terreno': Computation timed out."]


def test_timeout_da_power_nao_perde_o_earth_engine(app):
    """A POWER só desiste (timeout) depois que o Earth Engine já respondeu: os dados dele continuam na tela."""
    prontos = threading.Barrier(2, timeout=5)  # POWER + a última consulta do Earth Engine (NDVI)
    resp_ndvi = app.ee.respostas["MOD13Q1"]

    def ndvi():
        r = resp_ndvi()
        prontos.wait()
        return r
    app.ee.respostas["MOD13Q1"] = ndvi

    def power(*a, **kw):
        assert kw.get("timeout") == 40
        prontos.wait()
        raise requests.Timeout("Read timed out. (read timeout=40)")
    app.get.side_effect = power
    app.marcar_terra()
    tela = dados(app)
    assert tela["Altitude"] == "1100 m" and tela["Terreno"] and tela["Umidade do solo"]
    assert tela["Meses com pouca planta no último ano"]
    assert [rot for rot, v in tela.items() if v is None] == [
        "Temperatura nos próximos meses", "Chuva esperada em 4 meses", "Umidade do ar"]
    assert "Falha em 'clima': Read timed out. (read timeout=40)" in app.legendas()


@pytest.fixture
def ee_indisponivel(fake_ee):
    fake_ee.init_erro = "GEE_PROJECT inválido"
    return fake_ee


def test_sem_earth_engine_so_consulta_o_clima(ee_indisponivel, app):
    app.marcar_terra()
    assert app.get.call_count == 1
    assert sum(app.ee.chamadas.values()) == 0
    assert [c for c in app.legendas() if c.startswith("Falha em")] == ["Falha em 'gee': GEE_PROJECT inválido"]
    tela = dados(app)
    assert tela["Temperatura nos próximos meses"] and tela["Altitude"] is None


def test_voltar_para_terra_ja_consultada_usa_o_cache(app):
    a, b = (-15.78, -47.80), (-15.60, -47.65)
    app.marcar_terra(a).marcar_terra(b)
    consultas = app.get.call_count, dict(app.ee.chamadas)
    app.marcar_terra(a)
    assert (app.get.call_count, dict(app.ee.chamadas)) == consultas


def altitude_da_terra(lin):
    """SRTM falso: a altitude depende da terra consultada (centro do quadrado)."""
    lat = sum(p[1] for p in geometria(lin)["coordinates"][0][:-1]) / 4
    return {"elevation": round(-lat * 100), "slope": 2.0}


def test_cinco_cliques_seguidos_mostram_o_ultimo_ponto(app, fake_ee):
    """No AppTest os cliques são em sequência (não dá para clicar durante o carregamento); confere que cada
    resultado é o da terra certa e que não há erro. O clique rápido no navegador fica para o teste manual."""
    fake_ee.respostas["SRTMGL1"] = altitude_da_terra
    pontos = [(-15.70, -47.80), (-15.75, -47.70), (-15.80, -47.60), (-15.85, -47.90), (-15.90, -47.85)]
    for p in pontos:
        app.marcar_terra(p)  # run() confere que não houve exceção
        assert dados(app)["Altitude"] == f"{round(-p[0] * 100)} m"
    assert not any(c.startswith("Falha em") for c in app.legendas())
