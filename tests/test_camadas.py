"""Issue #7: o mapa aparece na hora com a terra, e as camadas do modo técnico entram depois (st.rerun no fim)."""
import json
from unittest import mock

import folium
import pytest
from folium.plugins import Draw
from streamlit.delta_generator import DeltaGenerator

from conftest import PONTO_DF, camadas_tecnicas
from fake_ee import geometria

CAMADAS = ("Saúde da vegetação (verde = saudável)", "Radar (bruto, com ruído)", "Radar (filtrado)")
CARREGANDO = "Carregando as camadas de vegetação e radar…"
TODAS_FALHARAM = "Não foi possível carregar as camadas de vegetação e radar."
OUTRO_PONTO = (-15.60, -47.65)  # longe de PONTO_DF: outra terra
OUTRAS = ("SRTMGL1", "FLDAS", "MOD13Q1")  # terreno, solo, NDVI


@pytest.fixture
def eventos():
    """Tudo o que a página desenha, em ordem, inclusive antes do st.rerun interno (que o AppTest não guarda):
    ("mapa", folium.Map) a cada st_folium e ("aviso", texto) a cada .caption de um st.empty/container.
    `cliques` é uma fila de pontos que o st_folium devolve como último clique, um por desenho do mapa."""
    ev, cliques, caption = [], [], DeltaGenerator.caption

    def st_folium(m, *a, **kw):
        m.grupos_tecnicos = kw.get("feature_group_to_add")  # camadas do modo técnico (#15)
        ev.append(("mapa", m))
        if cliques:
            lat, lng = cliques.pop(0)
            return {"last_clicked": {"lat": lat, "lng": lng}}
        return None

    def gravar_caption(self, texto, *a, **kw):
        ev.append(("aviso", texto))
        return caption(self, texto, *a, **kw)

    with mock.patch("streamlit_folium.st_folium", side_effect=st_folium), \
            mock.patch.object(DeltaGenerator, "caption", gravar_caption):
        yield Eventos(ev, cliques)


class Eventos:
    def __init__(self, ev, cliques):
        self.ev, self.cliques = ev, cliques

    def limpar(self):
        self.ev.clear()

    def mapas(self):
        return [m for k, m in self.ev if k == "mapa"]

    def avisos(self):
        return [t for k, t in self.ev if k == "aviso"]


def camadas(m):
    return camadas_tecnicas(m.grupos_tecnicos)


def terra(m):
    """Geometria da terra no mapa (o folium pode guardar como FeatureCollection depois de renderizar)."""
    d = next((c.data for c in m._children.values() if isinstance(c, folium.GeoJson)), None)
    return d["features"][0]["geometry"] if d and d.get("type") == "FeatureCollection" else d


def enquadramento(m):
    return [c.bounds for c in m._children.values() if type(c).__name__ == "FitBounds"]


def limites(gj):
    pts = gj["coordinates"][0]
    return [[min(p[1] for p in pts), min(p[0] for p in pts)], [max(p[1] for p in pts), max(p[0] for p in pts)]]


def sem_aviso_de_camadas(app):
    return not [c for c in app.legendas() if "camada" in c]


def test_terra_nova_mostra_mapa_e_aviso_antes_e_as_camadas_depois(app, eventos):
    app.run().tecnico(True)  # modo técnico ligado, nenhuma terra marcada: nada de aviso nem camadas
    assert not eventos.avisos() and not app.ee.mapas
    eventos.limpar()

    app.marcar_terra()
    primeiro, ultimo = eventos.mapas()[0], eventos.mapas()[-1]
    # 1º desenho: contorno amarelo, sem camadas, e o aviso logo depois do mapa
    assert terra(primeiro) is not None and camadas(primeiro) == {}
    assert eventos.ev[1] == ("aviso", CARREGANDO)
    # depois do st.rerun: as três camadas, vegetação ligada, e o aviso sumiu
    assert len(eventos.mapas()) == 2
    cs = camadas(ultimo)
    assert list(cs) == list(CAMADAS)
    assert {n: c.show for n, c in cs.items()} == {CAMADAS[0]: True, CAMADAS[1]: False, CAMADAS[2]: False}
    assert eventos.avisos() == [CARREGANDO]
    assert CARREGANDO not in app.legendas() and sem_aviso_de_camadas(app)
    assert len(app.ee.mapas) == 3
    # continua centralizado na terra marcada, não no DF inteiro
    assert terra(ultimo) == terra(primeiro)
    assert enquadramento(ultimo) == [limites(terra(ultimo))]
    assert app.recomendacao()


def test_ligar_modo_tecnico_com_terra_marcada_nao_refaz_as_outras_consultas(app, eventos):
    app.marcar_terra()
    # modo técnico desligado: nenhum aviso e nenhuma camada
    assert not eventos.avisos() and not app.ee.mapas
    assert all(camadas(m) == {} for m in eventos.mapas())
    antes, power, resultado = {k: app.ee.chamadas[k] for k in OUTRAS}, app.get.call_count, app.recomendacao()
    eventos.limpar()

    app.tecnico(True)
    assert camadas(eventos.mapas()[0]) == {} and terra(eventos.mapas()[0]) is not None
    assert list(camadas(eventos.mapas()[-1])) == list(CAMADAS)
    assert eventos.avisos() == [CARREGANDO]
    assert {k: app.ee.chamadas[k] for k in OUTRAS} == antes, "clima, terreno, solo e NDVI não devem ser consultados de novo"
    assert app.get.call_count == power
    assert app.recomendacao() == resultado


def test_voltar_a_uma_terra_ja_carregada_mostra_as_camadas_sem_aviso(app, eventos):
    app.run().tecnico(True)
    app.marcar_terra()
    app.marcar_terra(OUTRO_PONTO)
    assert len(app.ee.mapas) == 6
    eventos.limpar()

    app.marcar_terra(PONTO_DF)
    assert len(eventos.mapas()) == 1, "do cache: sem desenho intermediário nem st.rerun"
    assert list(camadas(eventos.mapas()[0])) == list(CAMADAS)
    assert not eventos.avisos()
    assert len(app.ee.mapas) == 6


@pytest.mark.parametrize("colecao, falharam, mensagem", [
    ("HLSL30", [CAMADAS[0]], f"Não foi possível carregar a camada “{CAMADAS[0]}”."),
    ("S1_GRD", CAMADAS[1:], f"Não foi possível carregar as camadas “{CAMADAS[1]}” e “{CAMADAS[2]}”."),
])
def test_falha_de_uma_camada_nao_apaga_as_outras(app, eventos, colecao, falharam, mensagem):
    app.ee.mapa_erro = lambda lin: (RuntimeError("Image.select: no bands") if any(colecao in x for x in lin) else None)
    app.run().tecnico(True)
    app.marcar_terra()
    assert list(camadas(eventos.mapas()[-1])) == [c for c in CAMADAS if c not in falharam]
    assert eventos.avisos() == [CARREGANDO, mensagem]
    assert mensagem in app.legendas() and CARREGANDO not in app.legendas()
    # a falha fica no cache junto com as outras: não fica tentando de novo a cada execução
    n = len(app.ee.mapas)
    app.run()
    assert len(app.ee.mapas) == n
    assert mensagem in app.legendas()


def test_todas_as_camadas_falham_e_o_mapa_continua(app, eventos):
    app.ee.mapa_erro = RuntimeError("Earth Engine temporariamente indisponível")
    app.run().tecnico(True)
    app.marcar_terra()
    assert len(app.ee.mapas) == 3  # tentou as três, cada uma separada
    ultimo = eventos.mapas()[-1]
    assert camadas(ultimo) == {} and terra(ultimo) is not None
    assert {"Mapa", "Satélite"} <= {c.layer_name for c in ultimo._children.values() if isinstance(c, folium.TileLayer)}
    assert TODAS_FALHARAM in app.legendas() and CARREGANDO not in app.legendas()
    assert app.recomendacao()
    app.run()
    assert len(app.ee.mapas) == 3, "não deve entrar num ciclo de st.rerun"


def test_earth_engine_fora_do_ar_mapa_clique_e_desenho_funcionam_sem_camadas(app, eventos):
    app.ee.init_erro = "GEE_PROJECT inválido"
    app.run().tecnico(True)
    eventos.cliques.append(PONTO_DF)  # clique no mapa
    app.run()
    assert app.at.session_state["centro"] == PONTO_DF
    assert terra(eventos.mapas()[-1]) is not None and camadas(eventos.mapas()[-1]) == {}
    assert CARREGANDO not in eventos.avisos()
    assert TODAS_FALHARAM in app.legendas()
    assert app.recomendacao()
    assert not app.ee.mapas

    desenho = {"type": "Polygon", "coordinates": [[[-47.81, -15.79], [-47.79, -15.79], [-47.79, -15.77],
                                                   [-47.81, -15.79]]]}
    app.at.session_state["modo"] = "Desenhar contorno"
    app.at.session_state["gj_desenho"] = json.dumps(desenho)
    eventos.limpar()
    app.run()
    m = eventos.mapas()[-1]
    assert any(isinstance(c, Draw) for c in m._children.values())
    assert terra(m) == desenho and camadas(m) == {}
    assert CARREGANDO not in eventos.avisos()
    assert app.recomendacao()


def test_clicar_em_outro_ponto_nao_mostra_camadas_da_terra_anterior(app, eventos):
    app.run().tecnico(True)
    app.marcar_terra()
    eventos.limpar()
    eventos.cliques.append(OUTRO_PONTO)  # clica em outra terra, cujas camadas ainda não existem
    app.run()
    assert app.at.session_state["centro"] == OUTRO_PONTO
    geom_da_url = {url: geometria(lin) for lin, url in app.ee.mapas}
    novos = [m for m in eventos.mapas() if limites(terra(m)) != limites(terra(eventos.mapas()[0]))]
    assert novos, "o mapa deveria ter sido desenhado com a terra nova"
    assert camadas(novos[0]) == {}  # 1º desenho da terra nova: sem camadas (nem as da anterior)
    for m in novos:
        for c in camadas(m).values():
            assert geom_da_url[c.tiles] == terra(m), "camada recortada para outra terra"
    assert list(camadas(novos[-1])) == list(CAMADAS)
