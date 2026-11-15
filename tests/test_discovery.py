"""Issue #22: o discovery da API do Earth Engine fica em memória e em disco por 24 h, em vez de ser baixado duas vezes
a cada `ee.Initialize`."""
import inspect
import json
import os
import time

import pytest
from googleapiclient import discovery

URL = "https://earthengine.googleapis.com/$discovery/rest?version=v1&prettyPrint=false"
OUTRA = "https://www.googleapis.com/discovery/v1/apis/drive/v3/rest"
DIA = 24 * 3600


class Downloads:
    """Faz o papel do `_retrieve_discovery_doc` original: conta os downloads e devolve um documento por URL."""

    def __init__(self):
        self.urls = []

    def __call__(self, url, http, cache_discovery, serviceName, version, cache=None, developerKey=None,
                 num_retries=1, static_discovery=True):
        self.urls.append(url)
        return json.dumps({"url": url, "n": len(self.urls)})


@pytest.fixture
def baixar(app, tmp_path, monkeypatch):
    """Discovery sem rede, com o cache numa pasta temporária. `baixar.reiniciar()` simula um processo novo: o hook é
    instalado de novo (memória vazia) e o disco fica."""
    pasta = tmp_path / "rotadf"
    monkeypatch.setenv("ROTADF_CACHE", str(pasta))
    fake = Downloads()
    fake.pasta, fake.arquivo = pasta, pasta / "ee_discovery.json"

    def reiniciar():
        monkeypatch.setattr(discovery, "_retrieve_discovery_doc", fake)
        app.reiniciar().run()
        return discovery._retrieve_discovery_doc

    fake.reiniciar = reiniciar
    fake.hook = reiniciar()
    return fake


def pedir(hook, url=URL):
    return hook(url, None, False, "earthengine", "v1")


def envelhecer(arquivo, segundos):
    t = time.time() - segundos
    os.utime(arquivo, (t, t))


def test_hook_instalado_uma_vez(baixar, app):
    app.run()
    assert discovery._retrieve_discovery_doc is baixar.hook  # o rerun não embrulha de novo


def test_um_download_por_processo(baixar):
    docs = [pedir(baixar.hook) for _ in range(4)]  # 2 clientes por Initialize, 2 tentativas
    assert baixar.urls == [URL]
    assert len(set(docs)) == 1 and json.loads(docs[0])["url"] == URL


def test_processo_novo_usa_o_disco(baixar):
    pedir(baixar.hook)
    hook = baixar.reiniciar()
    assert hook is not baixar.hook
    assert json.loads(pedir(hook))["n"] == 1
    assert baixar.urls == [URL]


def test_disco_com_24h_baixa_de_novo(baixar):
    pedir(baixar.hook)
    envelhecer(baixar.arquivo, DIA)
    hook = baixar.reiniciar()
    assert json.loads(pedir(hook))["n"] == 2
    assert len(baixar.urls) == 2
    assert json.loads(json.loads(baixar.arquivo.read_text())["doc"])["n"] == 2  # regravou


def test_disco_com_menos_de_24h_vale(baixar):
    pedir(baixar.hook)
    envelhecer(baixar.arquivo, DIA - 60)
    pedir(baixar.reiniciar())
    assert len(baixar.urls) == 1


@pytest.mark.parametrize("conteudo", ["", "{não é json", "[]", json.dumps({"url": URL, "doc": "{quebrado"}),
                                      json.dumps({"url": URL})])
def test_arquivo_estragado_baixa_e_regrava(baixar, conteudo):
    baixar.pasta.mkdir(parents=True)
    baixar.arquivo.write_text(conteudo)
    doc = pedir(baixar.reiniciar())
    assert json.loads(doc)["n"] == 1
    assert json.loads(baixar.arquivo.read_text())["doc"] == doc


def test_documento_de_outra_url_nao_serve(baixar):
    baixar.pasta.mkdir(parents=True)
    baixar.arquivo.write_text(json.dumps({"url": URL.replace("v1", "v2"), "doc": "{}"}))
    pedir(baixar.reiniciar())
    assert baixar.urls == [URL]


def test_nao_conseguir_gravar_nao_quebra(baixar, monkeypatch):
    baixar.pasta.write_text("um arquivo no lugar da pasta")  # makedirs falha
    hook = baixar.reiniciar()
    assert json.loads(pedir(hook))["n"] == 1
    assert json.loads(pedir(hook))["n"] == 1  # a memória segura o resto do processo
    assert len(baixar.urls) == 1


def test_outros_discovery_passam_direto(baixar):
    pedir(baixar.hook, OUTRA)
    pedir(baixar.hook, OUTRA)
    assert baixar.urls == [OUTRA, OUTRA]
    assert not baixar.arquivo.exists()


def test_assinatura_diferente_segue_sem_cache(app, monkeypatch):
    def outra(endereco, cliente): return "{}"
    monkeypatch.setattr(discovery, "_retrieve_discovery_doc", outra)
    app.run()
    assert discovery._retrieve_discovery_doc is outra


def test_sem_a_funcao_segue_sem_cache(app, monkeypatch):
    monkeypatch.delattr(discovery, "_retrieve_discovery_doc")
    app.run()
    assert not hasattr(discovery, "_retrieve_discovery_doc")


def test_assinatura_real_do_googleapiclient():
    """Se o googleapiclient mudar a função, este teste avisa que o cache de #22 parou de valer."""
    original = discovery._retrieve_discovery_doc
    while getattr(original, "rotadf", False):  # outro teste já instalou o hook neste processo
        original = inspect.getclosurevars(original).nonlocals["original"]
    assert list(inspect.signature(original).parameters)[:2] == ["url", "http"]
