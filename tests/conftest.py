"""Fixtures comuns: roda o app.py com AppTest, com Earth Engine e NASA POWER falsos (sem rede)."""
import contextlib
import builtins
import inspect
import pathlib
import sys
import types
from unittest import mock

import folium
import pytest
import streamlit as st
from streamlit.runtime.caching import cache_data_api
from streamlit.runtime.caching.storage import local_disk_cache_storage
from streamlit.testing.v1 import AppTest, app_test, local_script_runner

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from fake_ee import FakeEE  # noqa: E402

APP = str(pathlib.Path(__file__).resolve().parents[1] / "app.py")
PONTO_DF = (-15.78, -47.80)  # um ponto qualquer no DF


def resposta_power():
    """JSON mínimo da NASA POWER (monthly/point) para 2015-2024."""
    par = {}
    for nome, v in {"T2M": 22.0, "PRECTOTCORR": 5.0, "RH2M": 70.0}.items():
        par[nome] = {f"{a}{m:02d}": v for a in range(2015, 2025) for m in range(1, 14)}
    r = mock.Mock()
    r.json.return_value = {"properties": {"parameter": par}}
    r.raise_for_status.return_value = None
    return r


def camadas_tecnicas(grupos):
    """Camadas do modo técnico passadas ao st_folium em `feature_group_to_add` (#15), em ordem:
    {nome: (show, url)}. O grupo sem controle (`control=False`) só ajusta a visibilidade e fica de fora."""
    out = {}
    for g in grupos or []:
        if g.control:
            tiles = [c for c in g._children.values() if isinstance(c, folium.TileLayer)]
            assert len(tiles) == 1, g.layer_name
            out[g.layer_name] = types.SimpleNamespace(show=g.show, tiles=tiles[0].tiles)
    return out


class App:
    """Envolve o AppTest e guarda os contadores de chamadas (Earth Engine, POWER e spinners)."""

    def __init__(self, ee, get, spinners):
        self.ee, self.get, self.spinners = ee, get, spinners
        self.at = AppTest.from_file(APP, default_timeout=30)

    def run(self):
        self.spinners.clear()
        self.at.run()
        assert not self.at.exception, self.at.exception
        return self

    def reiniciar(self):
        """Simula reiniciar o `streamlit run`: some o cache em memória (de todas as funções) e a sessão;
        o cache em disco (`persist="disk"`, pasta temporária do teste) fica."""
        cache_data_api._data_caches._function_caches = {}
        st.cache_resource.clear()
        self.at = AppTest.from_file(APP, default_timeout=30)
        return self

    def fragmentos(self):
        """{nome da função: id} dos fragmentos (`st.fragment`) registrados na última execução: "mapa" e "passo2"."""
        out = {}
        for fid, f in self.at._fragment_storage._fragments.items():
            func = inspect.getclosurevars(f).nonlocals["non_optional_func"]
            out[func.__name__] = fid
        return out

    def rodar_fragmento(self, nome, auto=False):
        """Roda só o fragmento `nome`, como o navegador pede: ao mexer num widget dele (`auto=False`) ou pelo
        `run_every` (`auto=True`). Depois disso `self.at` só tem o que o fragmento desenhou."""
        ids, real = [self.fragmentos()[nome]], local_script_runner.RerunData
        rerun = lambda **kw: real(fragment_id_queue=ids, is_auto_rerun=auto, **kw)
        with mock.patch.object(local_script_runner, "RerunData", rerun):
            return self.run()

    def rodar_sozinho(self):
        """Simula o `run_every` do mapa numa aba parada (#15): o navegador pede para rodar só o fragmento do mapa
        (`is_auto_rerun`), sem a página inteira."""
        return self.rodar_fragmento("mapa", auto=True)

    def marcar_terra(self, ponto=PONTO_DF):
        self.at.session_state["centro"] = ponto
        return self.run()

    def tecnico(self, ligado):
        self.at.toggle[0].set_value(ligado)
        return self.run()

    # --- leitura da tela ---
    def textos(self):
        return [m.value for m in self.at.markdown]

    def legendas(self):
        return [c.value for c in self.at.caption]

    def fontes(self):
        """Tabela de fontes do parecer técnico (primeiro dataframe do expander), como {Dado: Disponível}."""
        df = self.at.expander[0].dataframe[0].value
        return dict(zip(df["Dado"], df["Disponível"]))

    def tem_grafico_radar(self):
        return any("Radar VH filtrado" in t for t in self.textos())

    def recomendacao(self):
        """O que a recomendação mostra: destaque, demais culturas, plano de rotação, dados da terra e confiança."""
        chaves = ('class="destaque"', 'class="linha"', 'class="trilha"', 'class="dados"', "Confiança da sugestão")
        return [t for t in self.textos() if any(c in t for c in chaves)]


@pytest.fixture
def fake_ee(monkeypatch):
    fake = FakeEE()
    monkeypatch.setitem(sys.modules, "ee", fake)
    return fake


@pytest.fixture
def cache_disco(tmp_path, monkeypatch):
    """Cache em disco (`persist="disk"`) como no `streamlit run`, numa pasta temporária do teste.
    O AppTest usa um cache só em memória (ignora `persist`); aqui ele passa a usar o mesmo do servidor
    (`LocalDiskCacheStorageManager`). A pasta é desviada para não ler nem apagar o ~/.streamlit/cache de verdade."""
    pasta = tmp_path / "streamlit-cache"
    monkeypatch.setattr(local_disk_cache_storage, "get_cache_folder_path", lambda: str(pasta))
    monkeypatch.setattr(app_test, "MemoryCacheStorageManager", local_disk_cache_storage.LocalDiskCacheStorageManager)
    return pasta


@pytest.fixture
def arquivo_clima(tmp_path, monkeypatch):
    """Isola o JSON local. Por padrão não existe, para os testes da API continuarem cobrindo o fallback."""
    destino = tmp_path / "clima_power_df.json"
    original = builtins.open
    caminho_real = pathlib.Path(APP).parent / "data" / "clima_power_df.json"

    def abrir(nome, *args, **kwargs):
        if isinstance(nome, (str, pathlib.Path)) and pathlib.Path(nome) == caminho_real:
            nome = destino
        return original(nome, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", abrir)
    return destino


@pytest.fixture
def relogio_fontes(monkeypatch):
    agora = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: agora[0])
    return agora


@pytest.fixture
def app(fake_ee, cache_disco, arquivo_clima):
    """App com EE falso e POWER falso. Limpa os caches do Streamlit (são globais no processo)."""
    st.cache_data.clear()
    st.cache_resource.clear()
    spinners = []
    real = st.spinner

    @contextlib.contextmanager
    def spinner(texto, *a, **kw):
        spinners.append(texto)
        with real(texto, *a, **kw):
            yield

    with mock.patch("requests.get", return_value=resposta_power()) as get, \
            mock.patch("streamlit.spinner", spinner):
        yield App(fake_ee, get, spinners)
    st.cache_data.clear()
    st.cache_resource.clear()
