"""Issue #5: o cache de `clima_power` fica em disco e sobrevive a um reinício do servidor.

"Reiniciar" é `app.reiniciar()`: apaga o cache em memória e a sessão, e mantém a pasta do cache em disco
(temporária, fixture `cache_disco`). "Bloquear a NASA POWER" é o `requests.get` falso levantar ConnectionError.
"""
import datetime as dt
import pathlib
from unittest import mock

import pytest
import requests

from conftest import resposta_power
from streamlit.runtime.caching.storage import local_disk_cache_storage
from test_cache_regional import A, BRAZLANDIA, CLIMA, clima_mensal, dados, falhas, power_por_ponto

BLOQUEADA = requests.ConnectionError("power.larc.nasa.gov bloqueado")
NASA = "NASA POWER, média mensal 2015–"


def bloquear(app):
    app.get.side_effect = BLOQUEADA


def clima(app):
    tela = dados(app)
    return {k: tela[k] for k in CLIMA}


def fonte_do_clima(app):
    df = app.at.expander[0].dataframe[0].value
    return df.loc[df["Dado"].str.startswith("Clima"), "Fonte / resolução"].item()


def memos(pasta):
    return sorted(p.name for p in pasta.glob("*.memo")) if pasta.is_dir() else []


def test_ponto_ja_consultado_mostra_o_mesmo_clima_depois_de_reiniciar_sem_a_power(app, cache_disco):
    power_por_ponto(app)
    app.marcar_terra(A)
    antes, tabela = clima(app), clima_mensal(app)
    assert all(antes.values()) and not falhas(app)
    assert len(memos(cache_disco)) == 1  # só o clima vai para o disco

    app.reiniciar()
    bloquear(app)
    app.marcar_terra(A)
    assert clima(app) == antes
    assert clima_mensal(app).equals(tabela)
    assert not falhas(app)
    assert app.get.call_count == 1  # a consulta de antes do reinício; depois, nenhuma


def test_ponto_nunca_consultado_com_a_power_bloqueada_continua_sem_dado(app, cache_disco):
    app.marcar_terra(A)
    app.reiniciar()
    bloquear(app)
    app.marcar_terra(BRAZLANDIA)  # outra célula da POWER
    assert all(v is None for v in clima(app).values())
    assert falhas(app) == ["Falha em 'clima': power.larc.nasa.gov bloqueado"]
    assert len(memos(cache_disco)) == 1


@pytest.mark.parametrize("falha", [
    requests.Timeout("Read timed out. (read timeout=40)"),
    requests.HTTPError("503 Server Error: Service Unavailable"),
])
def test_falha_da_power_nao_fica_gravada_no_disco(app, cache_disco, falha):
    if isinstance(falha, requests.HTTPError):
        r = resposta_power()
        r.raise_for_status.side_effect = falha
        app.get.return_value = r
    else:
        app.get.side_effect = falha
    app.marcar_terra(A)
    assert all(v is None for v in clima(app).values())
    assert falhas(app) == [f"Falha em 'clima': {falha}"]
    assert memos(cache_disco) == []

    app.reiniciar()
    app.get.side_effect, app.get.return_value = None, resposta_power()  # desbloqueia
    app.marcar_terra(A)
    assert all(clima(app).values()) and not falhas(app)
    assert app.get.call_count == 2
    assert len(memos(cache_disco)) == 1


def data_fixa(monkeypatch, dia):
    class DataFixa(dt.date):
        @classmethod
        def today(cls):
            return dia
    monkeypatch.setattr(dt, "date", DataFixa)


def test_na_virada_do_ano_o_clima_inclui_o_ano_que_terminou(app, monkeypatch):
    def get(url, params, **kw):  # a POWER responde uma temperatura que depende do último ano pedido
        r = resposta_power()
        par = r.json.return_value["properties"]["parameter"]
        par["T2M"] = {k: float(params["end"] - 2000) for k in par["T2M"]}
        return r
    app.get.side_effect = get

    data_fixa(monkeypatch, dt.date(2026, 12, 31))
    app.marcar_terra(A)
    assert fonte_do_clima(app).startswith(NASA + "2025,")
    assert clima_mensal(app)["T média (°C)"].eq(25.0).all()

    app.reiniciar()
    data_fixa(monkeypatch, dt.date(2027, 1, 1))
    app.marcar_terra(A)
    assert fonte_do_clima(app).startswith(NASA + "2026,")
    assert clima_mensal(app)["T média (°C)"].eq(26.0).all()
    assert [c.kwargs["params"]["end"] for c in app.get.call_args_list] == [2025, 2026]
    assert not falhas(app)

    app.reiniciar()  # o ano que vem do disco também é o da data de hoje
    bloquear(app)
    app.marcar_terra(A)
    assert fonte_do_clima(app).startswith(NASA + "2026,")
    assert clima_mensal(app)["T média (°C)"].eq(26.0).all()


def test_nao_avisa_que_o_ttl_sera_ignorado(app):
    with mock.patch.object(local_disk_cache_storage, "_LOGGER") as log:
        app.marcar_terra(A)
    avisos = [str(c) for c in log.warning.call_args_list]
    assert not [a for a in avisos if "TTL that will be ignored" in a]


def test_cache_em_disco_fica_fora_do_repositorio():
    """A pasta padrão do Streamlit (sem o desvio da fixture) é ~/.streamlit/cache, fora do repositório."""
    pasta = pathlib.Path(local_disk_cache_storage.get_cache_folder_path()).resolve()
    repo = pathlib.Path(__file__).resolve().parents[1]
    assert pasta == pathlib.Path.home().resolve() / ".streamlit" / "cache"
    assert repo not in pasta.parents
