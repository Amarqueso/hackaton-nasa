"""Issue #14: climatologia real versionada, com fallback seguro para a API."""
import datetime as dt
import json
from pathlib import Path
from unittest import mock

import pytest
import requests

from conftest import APP, resposta_power
from scripts import gerar_clima_df as gerador
from test_cache_disco import data_fixa, fonte_do_clima, NASA
from test_cache_regional import A, CLIMA, clima_mensal, dados, falhas

REAL = Path(APP).parent / "data" / "clima_power_df.json"


def documento_real():
    return json.loads(REAL.read_text())


def instalar(arquivo_clima, documento=None):
    documento = documento_real() if documento is None else documento
    arquivo_clima.write_text(json.dumps(documento))
    return documento


def assert_tabela(app, meses):
    tabela = clima_mensal(app)
    for coluna, chave in [("T média (°C)", "t"), ("Chuva (mm)", "chuva"), ("UR (%)", "ur")]:
        # A tabela arredonda com pandas/numpy; valores a meio décimo podem diferir do round do Python.
        assert tabela[coluna].tolist() == pytest.approx([meses[str(m)][chave] for m in range(1, 13)], abs=0.050000001)


def test_todas_as_16_celulas_e_bordas_do_mapa_tem_clima_sem_api_apos_reiniciar(app, arquivo_clima):
    documento = instalar(arquivo_clima)
    config = gerador.configuracao_app()
    bounds = config["DF_BOUNDS"]
    celulas = gerador.celulas_do_mapa(config)
    assert len(celulas) == 16
    assert set(documento["celulas"]) == {f"{lat:g},{lon:g}" for lat, lon in celulas}
    pontos = [(max(bounds["min_lat"], min(lat, bounds["max_lat"])),
               max(bounds["min_lon"], min(lon, bounds["max_lon"]))) for lat, lon in celulas]
    pontos += [(lat, lon) for lat in (bounds["min_lat"], bounds["max_lat"])
               for lon in (bounds["min_lon"], bounds["max_lon"])]
    pontos += [(-15.75, -47.8125), (-16.25, -48.4375)]  # divisas: mesma regra norte/leste da #4
    app.get.side_effect = requests.ConnectionError("NASA POWER bloqueada")
    for ponto in pontos:
        app.reiniciar().marcar_terra(ponto)
        assert all(dados(app)[k] is not None for k in CLIMA)
        centro = config["celula"](*ponto, config["GRADE_POWER"])
        assert_tabela(app, documento["celulas"][f"{centro[0]:g},{centro[1]:g}"])
        assert not falhas(app)
    assert app.get.call_count == 0


def test_fonte_local_mantem_periodo_do_arquivo_em_ano_futuro_e_fora_da_area_usa_api(app, arquivo_clima, monkeypatch):
    documento = instalar(arquivo_clima)
    data_fixa(monkeypatch, dt.date(2030, 1, 1))
    app.marcar_terra(A)
    assert fonte_do_clima(app).startswith(NASA + str(documento["periodo"]["fim"]) + ",")
    assert app.get.call_count == 0
    app.marcar_terra((-14.0, -47.5))  # célula fora do arquivo, não apenas fora da borda do mapa
    assert fonte_do_clima(app).startswith(NASA + "2029,")
    assert app.get.call_count == 1
    assert app.get.call_args.kwargs["params"]["end"] == 2029
    assert all(dados(app)[k] is not None for k in CLIMA)


@pytest.mark.parametrize("corrupcao", ["ausente", "truncado", "lista", "periodo", "mes", "campo", "nan", "infinito", "celula"])
def test_arquivo_ausente_ou_corrompido_nao_quebra_e_consulta_api(app, arquivo_clima, corrupcao):
    documento = documento_real()
    meses = documento["celulas"]["-15.5,-47.5"]
    if corrupcao == "periodo": documento["periodo"]["fim"] = "2025"
    if corrupcao == "mes": del meses["12"]
    if corrupcao == "campo": meses["1"]["t"] = "quente"
    if corrupcao == "nan": meses["1"]["t"] = float("nan")
    if corrupcao == "infinito": meses["1"]["ur"] = float("inf")
    if corrupcao == "celula": documento["celulas"]["nan,-47.5"] = documento["celulas"].pop("-15.5,-47.5")
    if corrupcao == "lista": documento = []
    if corrupcao != "ausente":
        arquivo_clima.write_text('{"celulas":' if corrupcao == "truncado" else json.dumps(documento))
    app.marcar_terra(A)
    assert app.get.call_count == 1
    assert all(dados(app)[k] is not None for k in CLIMA)
    assert not falhas(app)


@pytest.mark.parametrize("remover", [True, False])
def test_remover_ou_corromper_arquivo_apos_sucesso_invalida_coletar_na_mesma_sessao(app, arquivo_clima, remover):
    instalar(arquivo_clima)
    app.marcar_terra(A)
    assert app.get.call_count == 0
    if remover:
        arquivo_clima.unlink()
    else:
        arquivo_clima.write_text('{}')
    app.run()  # sem reiniciar e sem limpar cache
    assert app.get.call_count == 1
    assert_tabela(app, {str(m): {"t": 22.0, "chuva": 5.0 * dias, "ur": 70.0}
                       for m, dias in enumerate(gerador.configuracao_app()["DIAS"], 1)})


def test_json_precede_cache_api_antigo_e_atualizacao_do_conteudo_troca_resultado(app, arquivo_clima):
    app.marcar_terra(A)  # sem arquivo: preenche cache de clima em disco com resposta da API
    assert app.get.call_count == 1
    documento = instalar(arquivo_clima)
    app.reiniciar()
    app.get.side_effect = requests.ConnectionError("NASA POWER bloqueada")
    app.marcar_terra(A)
    assert_tabela(app, documento["celulas"]["-15.5,-47.5"])
    assert app.get.call_count == 1
    documento["celulas"]["-15.5,-47.5"]["1"]["t"] += 1.0
    instalar(arquivo_clima, documento)
    app.run()
    assert_tabela(app, documento["celulas"]["-15.5,-47.5"])
    assert app.get.call_count == 1


def test_gerador_usa_as_16_celulas_reais_periodo_e_mesma_media_do_app_sem_importar_app():
    resposta = resposta_power()
    p = resposta.json.return_value["properties"]["parameter"]
    # Mês anual (13), falta (-999) e ano fora do período não entram na média.
    p["T2M"]["201501"] = -999.0
    p["T2M"]["202501"] = 32.0
    p["T2M"]["202601"] = 100.0
    with mock.patch.object(gerador.requests, "get", return_value=resposta) as get:
        documento = gerador.gerar(fim=2025)
    config = gerador.configuracao_app()
    assert get.call_count == 16
    assert {(c.kwargs["params"]["latitude"], c.kwargs["params"]["longitude"]) for c in get.call_args_list} == set(gerador.celulas_do_mapa(config))
    assert all(c.kwargs["params"]["start"] == 2015 and c.kwargs["params"]["end"] == 2025 for c in get.call_args_list)
    assert documento["periodo"] == {"inicio": 2015, "fim": 2025}
    for meses in documento["celulas"].values():
        assert set(meses) == {str(m) for m in range(1, 13)}
        assert meses["1"]["t"] == 23.0  # nove anos a 22 + 2025 a 32; 2015 mascarado
        assert meses["2"]["chuva"] == 5.0 * 28.25
        assert meses["12"]["ur"] == 70.0


def test_gerador_recusa_mes_sem_dado_e_nao_substitui_arquivo(tmp_path, monkeypatch):
    destino = tmp_path / "clima.json"
    destino.write_text('arquivo anterior')
    resposta = resposta_power()
    p = resposta.json.return_value["properties"]["parameter"]
    p["RH2M"] = {k: -999.0 for k in p["RH2M"]}
    monkeypatch.setattr(gerador.requests, "get", lambda *a, **kw: resposta)
    monkeypatch.setattr("sys.argv", ["gerar_clima_df.py", "--saida", str(destino), "--ano-fim", "2025"])
    with pytest.raises(ValueError, match="Sem valores válidos"):
        gerador.main()
    assert destino.read_text() == 'arquivo anterior'


def test_gerador_repete_erro_transitorio_sem_gravar_dados_inventados(monkeypatch):
    resposta = resposta_power()
    get = mock.Mock(side_effect=[requests.Timeout("timeout")] + [resposta] * 16)
    monkeypatch.setattr(gerador.requests, "get", get)
    with mock.patch.object(gerador.time, "sleep") as espera:
        documento = gerador.gerar(fim=2025)
    assert len(documento["celulas"]) == 16
    assert get.call_count == 17
    assert get.call_args_list[0] == get.call_args_list[1]
    espera.assert_called_once_with(1)
