"""Fixtures comuns: roda o app.py com AppTest, com Earth Engine e NASA POWER falsos (sem rede)."""
import contextlib
import pathlib
import sys
from unittest import mock

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

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
def app(fake_ee):
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
