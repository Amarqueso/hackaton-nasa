"""Issue #1: o radar Sentinel-1 só é consultado no modo técnico, fora de `coletar`."""
import pytest

RADAR = "Radar VH com filtro Lee"
CARREGANDO_RADAR = "Carregando o radar…"
CONSULTANDO = "Consultando os satélites… pode levar um minuto."
OUTRAS = ("SRTMGL1", "GLDAS", "MOD13Q1")  # terreno, solo, NDVI


def consultas_sem_radar(app):
    return app.get.call_count, {k: app.ee.chamadas[k] for k in OUTRAS}


def test_modo_tecnico_desligado_nao_consulta_radar(app):
    app.marcar_terra()
    assert app.ee.chamadas["S1_GRD"] == 0
    assert app.recomendacao(), "a recomendação deveria aparecer"
    assert not app.tem_grafico_radar()
    assert not any("Falha em 'radar'" in c for c in app.legendas())
    assert CARREGANDO_RADAR not in app.spinners


def test_modo_tecnico_desligado_tabela_mostra_so_no_modo_tecnico(app):
    app.marcar_terra()
    fontes = app.fontes()
    assert fontes[RADAR] == "só no modo técnico"
    assert all(v == "sim" for k, v in fontes.items() if k != RADAR)


def test_ligar_modo_tecnico_carrega_so_o_radar(app):
    app.marcar_terra()
    antes = consultas_sem_radar(app)
    app.tecnico(True)
    assert app.ee.chamadas["S1_GRD"] == 1
    assert consultas_sem_radar(app) == antes, "clima, terreno, solo e NDVI não devem ser consultados de novo"
    assert app.tem_grafico_radar()
    assert app.fontes()[RADAR] == "sim"
    assert CARREGANDO_RADAR in app.spinners  # aviso próprio em volta da consulta de radar
    assert app.recomendacao()


def test_religar_modo_tecnico_usa_cache_do_radar(app):
    app.marcar_terra()
    app.tecnico(True)
    app.tecnico(False)
    assert not app.tem_grafico_radar()
    app.tecnico(True)
    assert app.ee.chamadas["S1_GRD"] == 1, "a segunda vez deveria vir do cache"
    assert app.tem_grafico_radar()


def test_falha_no_radar_aparece_no_parecer_e_nao_muda_recomendacao(app):
    def falha():
        raise RuntimeError("Computation timed out.")
    app.ee.respostas["S1_GRD"] = falha
    app.marcar_terra()
    rec = app.recomendacao()
    app.tecnico(True)
    assert "Falha em 'radar': Computation timed out." in app.legendas()
    assert app.fontes()[RADAR] == "NÃO"
    assert not app.tem_grafico_radar()
    assert app.recomendacao() == rec


def test_serie_de_radar_vazia_mostra_nao_sem_erro(app):
    app.ee.respostas["S1_GRD"] = lambda: {"features": [{"properties": {"m": f"2025-{m:02d}", "v": -9999}}
                                                       for m in range(1, 13)]}
    app.marcar_terra().tecnico(True)
    assert app.fontes()[RADAR] == "NÃO"
    assert not app.tem_grafico_radar()
    assert not any(c.startswith("Falha em") for c in app.legendas())


def test_modo_tecnico_nao_muda_recomendacao_plano_nem_confianca(app):
    app.marcar_terra()
    desligado = app.recomendacao()
    assert any("Confiança da sugestão: alta" in t for t in desligado)
    assert any('class="trilha"' in t for t in desligado)
    assert app.tecnico(True).recomendacao() == desligado
    assert app.tecnico(False).recomendacao() == desligado


@pytest.fixture
def ee_indisponivel(fake_ee):
    fake_ee.init_erro = "GEE_PROJECT inválido"
    return fake_ee


def test_sem_earth_engine_modo_tecnico_nao_quebra(ee_indisponivel, app):
    app.marcar_terra().tecnico(True)  # run() já confere que não houve exceção
    assert app.ee.chamadas["S1_GRD"] == 0
    assert not app.tem_grafico_radar()
    assert "Falha em 'gee': GEE_PROJECT inválido" in app.legendas()
    assert app.fontes()[RADAR] == "NÃO"
    assert app.recomendacao()
