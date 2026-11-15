"""Issue #6: mudar uma resposta do passo 2 atualiza só os resultados, sem reconstruir nem mandar o mapa de novo.

O passo 2 e os resultados ficam num `st.fragment` ("passo2"). Mexer num widget dele faz o navegador pedir para rodar
só esse fragmento; `app.rodar_fragmento("passo2")` simula isso. O mapa é outro fragmento: se ele não roda, o
`st_folium` não é chamado e nada do mapa chega ao navegador (zoom, posição e camada escolhida ficam como estão).

Como em `tests/test_aba_parada.py`, só o componente do mapa (`_component_func`) é falso: guarda o que receberia e
devolve o que o navegador devolveria (um clique, um desenho).
"""
import json
from unittest import mock

import pytest
from streamlit.runtime import forward_msg_queue

from conftest import PONTO_DF

OUTRAS = ("SRTMGL1", "FLDAS", "MOD13Q1")  # terreno, solo e NDVI (o clima vem da POWER, `app.get`)
SECOES = ("O que combina com a sua terra", "E depois? Um plano de rotação", "O que sabemos sobre a sua terra")
AVISO_SEM_TERRA = "Marque a sua terra no mapa para ver o que combina com ela."
AVISO_GRANDE = "Essa área é muito grande (mais de 5.000 hectares). Marque só a sua terra."


@pytest.fixture
def componente():
    with mock.patch("streamlit_folium._component_func", return_value=None) as c:
        yield c


@pytest.fixture
def deltas():
    """(delta_path, texto) de cada markdown mandado ao navegador, na ordem."""
    out, real = [], forward_msg_queue.ForwardMsgQueue.enqueue

    def enqueue(self, msg):
        if msg.HasField("delta") and msg.delta.new_element.HasField("markdown"):
            out.append((tuple(msg.metadata.delta_path), msg.delta.new_element.markdown.body))
        return real(self, msg)

    with mock.patch.object(forward_msg_queue.ForwardMsgQueue, "enqueue", enqueue):
        yield out


@pytest.fixture
def pagina(app, componente):
    app.componente = componente
    return app


def resultados(app):
    """Títulos das seções e o que a recomendação mostra."""
    return [t for t in app.textos() if any(s in t for s in SECOES)] + app.recomendacao()


def tem(app, trecho):
    return any(trecho in t for t in app.textos())


def consultas(app):
    return {k: app.ee.chamadas[k] for k in OUTRAS}, app.get.call_count


def poligono(*pts):
    return {"type": "Polygon", "coordinates": [[[lon, lat] for lat, lon in pts + pts[:1]]]}


def clicar(app, lat, lng):
    app.componente.return_value = {"last_clicked": {"lat": lat, "lng": lng}, "last_active_drawing": None}
    return app.run()


def desenhar(app, geom):
    app.componente.return_value = {"last_clicked": None, "last_active_drawing": {"geometry": geom}}
    return app.run()


def mapa(app):
    """O que o mapa recebeu por último: o `key` (identidade do iframe; o st_folium o deriva do script, então muda
    quando muda a terra) e o script (terra e enquadramento)."""
    kw = app.componente.call_args.kwargs
    return kw["key"], kw["script"]


RESPOSTAS = [
    ("button_group", "nivel", "Tenho experiência"),
    ("button_group", "agua", "Posso irrigar"),
    ("button_group", "obj", "Cuidar da terra"),
    ("selectbox", "mes", 3),
    ("selectbox", "ult", "Sorgo"),
]


def responder(app, tipo, chave, valor):
    getattr(app.at, tipo)(key=chave).set_value(valor)


@pytest.mark.parametrize("tipo,chave,valor", RESPOSTAS, ids=[c for _, c, _ in RESPOSTAS])
def test_mudar_o_passo2_atualiza_os_resultados_sem_mandar_o_mapa(pagina, deltas, tipo, chave, valor):
    app = pagina
    app.marcar_terra()
    antes, chamadas_mapa, outras = resultados(app), app.componente.call_count, consultas(app)
    paths = {p for p, t in deltas if any(s in t for s in SECOES)}

    deltas.clear()
    responder(app, tipo, chave, valor)
    app.rodar_fragmento("passo2")
    depois = resultados(app)
    assert depois != antes, "os resultados devem mudar com a resposta"
    assert len([t for t in depois if 'class="dados"' in t]) == 1 and len([t for t in depois if SECOES[0] in t]) == 1
    assert app.componente.call_count == chamadas_mapa, "o mapa não pode ser reconstruído nem mandado de novo"
    assert consultas(app) == outras, "os dados da terra já consultada são reaproveitados"
    # os resultados substituem os anteriores no mesmo lugar da página (não se acumulam abaixo deles)
    assert {p for p, t in deltas if any(s in t for s in SECOES)} == paths
    assert not app.at.error

    app.run()  # a próxima execução completa mostra o mesmo, com a resposta escolhida
    assert resultados(app) == depois
    assert app.at.session_state[chave] == valor


def test_clicar_em_outro_ponto_move_o_quadrado_e_mantem_as_respostas(pagina):
    app = pagina
    app.marcar_terra()
    app.at.selectbox(key="mes").set_value(3)
    app.at.button_group(key="agua").set_value("Posso irrigar")
    app.rodar_fragmento("passo2")
    app.run()
    key, script = mapa(app)
    solo = app.ee.chamadas["FLDAS"]

    clicar(app, -15.60, -47.65)  # outro ponto, em outra célula do FLDAS
    assert app.at.session_state["centro"] == (-15.60, -47.65)
    novo_key, novo_script = mapa(app)
    assert novo_key != key and "-47.65" in novo_script and "-47.65" not in script  # o quadrado foi para o novo ponto
    assert app.ee.chamadas["FLDAS"] == solo + 1  # resultados da terra nova
    assert app.at.session_state["mes"] == 3 and app.at.session_state["agua"] == "Posso irrigar"
    assert tem(app, "O que combina com a sua terra em março") and app.recomendacao()


def test_desenhar_contorno_mostra_a_area_e_os_resultados(pagina):
    app = pagina
    app.run()
    app.at.button_group(key="modo").set_value("Desenhar contorno")
    app.run()
    assert not resultados(app) and tem(app, AVISO_SEM_TERRA)
    desenhar(app, poligono((-15.73, -47.74), (-15.73, -47.72), (-15.75, -47.72), (-15.75, -47.74)))
    assert any(t.startswith('<p class="mut">Terra marcada: cerca de') and "hectares" in t for t in app.textos())
    assert app.recomendacao() and [s for s in SECOES if tem(app, s)] == list(SECOES)


def test_mudar_o_tamanho_redesenha_o_quadrado_e_atualiza_os_resultados(pagina):
    app = pagina
    app.marcar_terra()
    key, script = mapa(app)
    resultado = resultados(app)
    assert tem(app, "Terra marcada: cerca de 10 hectares.")
    app.at.selectbox(key="tam").set_value("Pequena (cerca de 2 hectares)")
    app.run()
    assert tem(app, "Terra marcada: cerca de 2 hectares.")
    novo_key, novo_script = mapa(app)
    assert novo_key != key and novo_script != script  # quadrado novo, como antes do #6
    assert resultados(app) != resultado  # o tamanho entra na recomendação (área mínima das culturas)


def sem_terra(app):
    assert tem(app, AVISO_SEM_TERRA)
    assert not resultados(app) and not app.at.expander
    assert not tem(app, "Conte um pouco sobre você")


@pytest.mark.parametrize("acao", ["apagar", "trocar o modo"])
def test_apagar_ou_trocar_o_modo_limpa_a_marcacao(pagina, acao):
    app = pagina
    clicar(app, *PONTO_DF)
    key, _ = mapa(app)
    assert resultados(app)
    app.componente.return_value = None  # mapa novo no navegador: sem clique
    if acao == "apagar":
        next(b for b in app.at.button if b.label == "Apagar e começar de novo").click()
    else:
        app.at.button_group(key="modo").set_value("Desenhar contorno")
    app.run()
    assert app.at.session_state["centro"] is None and app.at.session_state["gj_desenho"] is None
    novo_key, script = mapa(app)
    assert novo_key != key and app.at.session_state["map_v"] == 1  # outro mapa
    assert "geo_json" not in script and "[-16.07, -48.3]" in script  # sem a terra, enquadrando o DF inteiro
    sem_terra(app)


def test_sem_terra_mostra_so_o_passo1(pagina):
    sem_terra(pagina.run())


def test_area_muito_grande_mostra_o_aviso_e_nada_abaixo(pagina):
    app = pagina
    app.run()
    app.at.button_group(key="modo").set_value("Desenhar contorno")
    app.run()
    desenhar(app, poligono((-15.60, -47.90), (-15.60, -47.70), (-15.80, -47.70), (-15.80, -47.90)))
    textos = app.textos()
    assert textos[-2] == f'<p class="nota">{AVISO_GRANDE}</p>'  # o último é o título da sidebar
    assert not resultados(app) and not app.at.expander and not tem(app, "Conte um pouco sobre você")
    assert "passo2" not in app.fragmentos()


def test_modo_tecnico_e_passo2_juntos(pagina):
    """Com o modo técnico ligado, mudar o passo 2 também não manda o mapa (nem consulta as camadas ou o radar)."""
    app = pagina
    app.run().tecnico(True)
    app.marcar_terra()  # o fim da página pede as camadas e roda de novo (#7)
    assert len(app.ee.mapas) == 3
    chamadas_mapa, radar, resultado = app.componente.call_count, app.ee.chamadas["S1_GRD"], resultados(app)
    app.at.selectbox(key="mes").set_value(5)
    app.rodar_fragmento("passo2")
    assert resultados(app) != resultado
    assert app.componente.call_count == chamadas_mapa
    assert len(app.ee.mapas) == 3 and app.ee.chamadas["S1_GRD"] == radar
    app.run().tecnico(False)
    assert app.at.session_state["mes"] == 5 and tem(app, "O que combina com a sua terra em maio")
