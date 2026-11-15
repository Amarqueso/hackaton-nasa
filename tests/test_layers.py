"""Issue #9: renova as URLs temporárias das três camadas na próxima renderização."""
from unittest import mock

import folium
import pytest
from streamlit.runtime.caching import cache_utils

from conftest import PONTO_DF, camadas_tecnicas

CAMADAS = {
    "Saúde da vegetação (verde = saudável)",
    "Radar (bruto, com ruído)",
    "Radar (filtrado)",
}


@pytest.fixture
def relogio(monkeypatch):
    # É o timer usado pelo storage TTL real do Streamlit; não substituímos o cache.
    agora = [1000.0]
    monkeypatch.setattr(cache_utils, "TTLCACHE_TIMER", lambda: agora[0])
    return agora


@pytest.fixture
def mapas():
    with mock.patch("streamlit_folium.st_folium", return_value=None) as componente:
        yield componente


def mapa(componente):
    return componente.call_args.args[0]


def urls(componente):
    grupos = componente.call_args.kwargs["feature_group_to_add"]
    return {nome: c.tiles for nome, c in camadas_tecnicas(grupos).items()}


def terra(componente):
    return next(c.data for c in mapa(componente)._children.values() if isinstance(c, folium.GeoJson))


@pytest.mark.parametrize("segundos", [3601, 6 * 3600])
def test_cache_reutiliza_antes_de_uma_hora_e_renova_todas_as_urls_depois(app, relogio, mapas, segundos):
    inicio = relogio[0]
    app.marcar_terra().tecnico(True)
    antigas, area, resultado = urls(mapas), terra(mapas), app.recomendacao()
    assert set(antigas) == CAMADAS
    assert len(set(antigas.values())) == 3
    assert len(app.ee.mapas) == 3

    relogio[0] = inicio + 3599
    app.run()
    assert urls(mapas) == antigas
    assert len(app.ee.mapas) == 3

    relogio[0] = inicio + segundos
    app.run()
    novas = urls(mapas)
    assert set(novas) == CAMADAS
    assert len(set(novas.values())) == 3
    assert set(novas.values()).isdisjoint(antigas.values())
    assert len(app.ee.mapas) == 6
    assert app.at.session_state["centro"] == PONTO_DF
    assert terra(mapas) == area
    assert app.recomendacao() == resultado


def test_falha_na_renovacao_preserva_mapa_terra_e_resultados(app, relogio, mapas):
    app.marcar_terra().tecnico(True)
    area, resultado = terra(mapas), app.recomendacao()
    assert set(urls(mapas)) == CAMADAS
    app.ee.mapa_erro = RuntimeError("Earth Engine temporariamente indisponível")

    relogio[0] += 3601
    app.run()
    assert len(app.ee.mapas) == 6  # tentou renovar as três (cada uma separada, #7); não reutilizou URLs antigas
    assert urls(mapas) == {}
    base = {c.layer_name for c in mapa(mapas)._children.values() if isinstance(c, folium.TileLayer)}
    assert {"Mapa", "Satélite"} <= base
    assert app.at.session_state["centro"] == PONTO_DF
    assert terra(mapas) == area
    assert app.recomendacao() == resultado
