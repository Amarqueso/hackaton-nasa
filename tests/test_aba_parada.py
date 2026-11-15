"""Issue #15: numa aba parada há horas, com o modo técnico ligado, as camadas continuam aparecendo.

O mapa fica num `st.fragment(run_every=...)`. O navegador roda esse fragmento sozinho de tempos em tempos
(`app.rodar_sozinho()` simula isso); passado o `ttl` de `layers`, ele pede URLs novas ao Earth Engine e manda ao
mapa já aberto só as camadas novas (`feature_group_to_add`), sem recarregar o mapa.

Aqui o `st_folium` é o de verdade; só o componente (`_component_func`) é falso e guarda o que receberia no navegador.
O `key` é a identidade do componente: igual = o mesmo iframe, que não roda o `script` de novo (mantém zoom e
posição) e só troca os grupos de `feature_group`.
"""
import json
import re
from unittest import mock

import pytest
import streamlit as st
from streamlit.runtime.caching import cache_utils

from conftest import PONTO_DF

CAMADAS = ("Saúde da vegetação (verde = saudável)", "Radar (bruto, com ruído)", "Radar (filtrado)")
OUTRAS = ("SRTMGL1", "FLDAS", "MOD13Q1", "S1_GRD")  # terreno, solo, NDVI e o gráfico do radar
RENOVAR = 600


@pytest.fixture
def relogio(monkeypatch):
    agora = [1000.0]  # timer do ttl do st.cache_data (como em tests/test_layers.py)
    monkeypatch.setattr(cache_utils, "TTLCACHE_TIMER", lambda: agora[0])
    return agora


@pytest.fixture
def componente():
    with mock.patch("streamlit_folium._component_func", return_value=None) as c:
        yield c


@pytest.fixture
def run_every():
    """run_every de cada st.fragment da página, em ordem."""
    pedidos, real = [], st.fragment

    def fragment(func=None, **kw):
        pedidos.append(kw.get("run_every"))
        return real(func, **kw)

    with mock.patch("streamlit.fragment", fragment):
        yield pedidos


def ultimo(componente):
    return componente.call_args.kwargs


def urls(args):
    return re.findall(r"https://fake/\d+/\{z\}/\{x\}/\{y\}", args["feature_group"] or "")


def consultas(app):
    return {k: app.ee.chamadas[k] for k in OUTRAS}, app.get.call_count


@pytest.fixture
def pagina(app, componente, run_every, relogio):
    app.componente = componente
    return app


def tecnico_com_terra(app):
    app.run().tecnico(True)
    app.marcar_terra()  # 1ª execução: mapa sem camadas; st.rerun no fim: com as três
    antes = ultimo(app.componente)
    assert len(urls(antes)) == 3 and len(app.ee.mapas) == 3
    return antes


@pytest.mark.parametrize("horas", [1, 6, 12])
def test_aba_parada_renova_as_camadas_sem_recarregar_o_mapa(pagina, relogio, run_every, horas):
    app = pagina
    antes = tecnico_com_terra(app)
    assert run_every[-1] == RENOVAR
    outras, resultado = consultas(app), app.recomendacao()

    # aba parada: o fragmento roda sozinho a cada 10 min; antes de 1 h usa o cache, depois pede URLs novas
    for minuto in range(10, horas * 60 + 1, 10):
        relogio[0] = 1000.0 + minuto * 60
        app.rodar_sozinho()
    renovacoes = horas  # uma por hora (ttl de 3600 s)
    assert len(app.ee.mapas) == 3 * (1 + renovacoes)

    depois = ultimo(app.componente)
    assert len(urls(depois)) == 3
    assert set(urls(depois)).isdisjoint(urls(antes)), "as URLs mandadas ao mapa devem ser as novas"
    assert set(urls(depois)) == {url for _, url in app.ee.mapas[-3:]}
    # o mesmo componente (não recarrega o iframe): terra, zoom e posição ficam como o usuário deixou
    assert depois["key"] == antes["key"]
    assert depois["zoom"] is None and depois["center"] is None  # o fragmento não mexe na visão do mapa
    assert "layer_control" in depois and depois["layer_control"].count("feature_group_feature_group_") == 3
    # só as camadas foram consultadas de novo; a sessão (terra marcada) continua
    assert consultas(app) == outras
    assert app.at.session_state["centro"] == PONTO_DF

    app.run()  # o usuário volta e mexe na página: tudo como antes, sem consultar de novo
    assert ultimo(app.componente)["key"] == antes["key"]
    assert set(urls(ultimo(app.componente))) == set(urls(depois))
    assert len(app.ee.mapas) == 3 * (1 + renovacoes)
    assert app.recomendacao() == resultado


def test_renovacao_manda_o_mapa_uma_vez_so_ja_com_as_camadas_novas(pagina, relogio):
    """Enquanto as URLs novas são pedidas, o navegador fica com o mapa que já está aberto: nenhum mapa intermediário
    (sem camadas ou de outra identidade) é mandado, e o que chega depois é o mesmo componente."""
    app = pagina
    antes = tecnico_com_terra(app)
    chamadas_ao_mapa = []
    app.ee.mapa_erro = lambda lin: chamadas_ao_mapa.append(app.componente.call_count) and None
    n = app.componente.call_count

    relogio[0] += 3601
    app.rodar_sozinho()
    assert chamadas_ao_mapa == [n, n, n], "as camadas são pedidas antes de mandar o mapa"
    assert app.componente.call_count == n + 1
    assert ultimo(app.componente)["key"] == antes["key"]
    assert len(urls(ultimo(app.componente))) == 3


def test_falha_ao_renovar_na_aba_parada_mantem_o_mapa(pagina, relogio):
    app = pagina
    antes = tecnico_com_terra(app)
    app.ee.mapa_erro = RuntimeError("Earth Engine temporariamente indisponível")
    relogio[0] += 3601
    app.rodar_sozinho()
    depois = ultimo(app.componente)
    assert depois["key"] == antes["key"]  # o mapa não recarrega: terra, zoom e posição ficam
    assert urls(depois) == []
    assert "Não foi possível carregar as camadas de vegetação e radar." in app.legendas()
    assert app.at.session_state["centro"] == PONTO_DF


def test_camadas_comecam_com_so_a_vegetacao_visivel(pagina):
    fg = tecnico_com_terra(pagina)["feature_group"]
    inicio = re.search(r"_rotadf_visiveis = (\{.*?\})", fg).group(1)
    assert '"Radar (bruto, com ru\\u00eddo)": false' in inicio
    assert '"Radar (filtrado)": false' in inicio
    assert '"Sa\\u00fade da vegeta\\u00e7\\u00e3o (verde = saud\\u00e1vel)": true' in inicio


def test_vegetacao_ausente_no_primeiro_carregamento_mantem_padrao_ao_recuperar(pagina, relogio):
    app = pagina
    app.ee.mapa_erro = lambda lin: RuntimeError("HLS indisponível") if any("HLS" in fonte for fonte in lin) else None
    app.run().tecnico(True)
    app.marcar_terra()
    antes = ultimo(app.componente)
    assert len(urls(antes)) == 2 and len(app.ee.mapas) == 3
    inicio = json.loads(re.search(r"_rotadf_visiveis = (\{.*?\})", antes["feature_group"]).group(1))
    assert inicio == {CAMADAS[0]: True, CAMADAS[1]: False, CAMADAS[2]: False}

    app.ee.mapa_erro = None
    relogio[0] += 3601
    app.rodar_sozinho()
    depois = ultimo(app.componente)
    assert len(urls(depois)) == 3 and len(app.ee.mapas) == 6
    assert depois["key"] == antes["key"]
    assert depois["zoom"] is None and depois["center"] is None
    recuperado = json.loads(re.search(r"_rotadf_visiveis = (\{.*?\})", depois["feature_group"]).group(1))
    assert recuperado == inicio  # o estado inicial já reservava a vegetação; não a tratava como desligada


def test_mudar_o_modo_tecnico_nao_recarrega_o_mapa(pagina):
    app = pagina
    antes = tecnico_com_terra(app)
    app.tecnico(False)
    assert ultimo(app.componente)["key"] == antes["key"] and urls(ultimo(app.componente)) == []


@pytest.mark.parametrize("situacao", ["desligado", "sem terra", "ee fora do ar"])
def test_sem_camadas_a_aba_parada_nao_consulta_o_earth_engine(pagina, relogio, run_every, situacao):
    app = pagina
    if situacao == "ee fora do ar":
        app.ee.init_erro = "GEE_PROJECT inválido"
    if situacao == "desligado":
        app.marcar_terra()
    else:
        app.run().tecnico(True)
    assert run_every[-1] is None, "sem nada para renovar, o fragmento não roda sozinho"
    outras, mapas = consultas(app), len(app.ee.mapas)

    # mesmo que o navegador ainda tivesse um timer antigo, rodar o fragmento não consulta nada
    relogio[0] += 6 * 3600
    app.rodar_sozinho()
    assert consultas(app) == outras and len(app.ee.mapas) == mapas == 0


def test_desligar_o_modo_tecnico_tira_o_run_every(pagina, relogio, run_every):
    app = pagina
    tecnico_com_terra(app)
    assert run_every[-1] == RENOVAR
    app.tecnico(False)
    assert run_every[-1] is None
    outras, mapas = consultas(app), len(app.ee.mapas)
    relogio[0] += 6 * 3600
    app.rodar_sozinho()
    assert consultas(app) == outras and len(app.ee.mapas) == mapas
