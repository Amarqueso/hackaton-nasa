"""Um `ee` (Earth Engine) falso para os testes: não usa rede nem credenciais.

Cada objeto `Expr` guarda de quais coleções/imagens ele veio ("linhagem"). Quando o app chama
`.getInfo()`, a resposta é escolhida pela linhagem (ex.: tem "COPERNICUS/S1_GRD" -> é o radar).
`FakeEE.respostas` mapeia um trecho do id da coleção para uma função que devolve o resultado (ou levanta erro),
e `FakeEE.chamadas` conta quantas vezes cada uma foi consultada.
`ee.Geometry(geojson)` entra na linhagem como "geom:<geojson>"; uma resposta que recebe um argumento ganha a linhagem
(dá para responder conforme a terra, ex.: `geometria(lin)`). `FakeEE.threads` guarda em qual thread cada coleção
foi consultada.
"""
import inspect
import json
import threading
import types
from collections import Counter

MESES = [f"2025-{m:02d}" for m in range(1, 13)]


def serie(valores):
    """Resposta de `month_series`: FeatureCollection com 'm' (mês) e 'v' (valor)."""
    return {"features": [{"properties": {"m": m, "v": v}} for m, v in zip(MESES, valores)]}


def serie_mensal(valor, ate=(2026, 9), de=(2010, 1)):
    """Resposta da umidade do solo (FLDAS): uma Feature por imagem mensal, com 'm' (YYYY-MM) e 'v'.
    `valor(ano, mes)` devolve o valor, None (pixel mascarado: 'v' nulo) ou `SEM_V` (Feature sem 'v')."""
    feats, (a, m) = [], de
    while (a, m) <= ate:
        v = valor(a, m)
        props = {"m": f"{a}-{m:02d}"} if v is SEM_V else {"m": f"{a}-{m:02d}", "v": v}
        feats.append({"type": "Feature", "geometry": None, "properties": props})
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)
    return {"type": "FeatureCollection", "features": feats}


SEM_V = object()

CLASSES_DW = ["agua", "arvores", "campo", "alagado", "lavoura", "arbustos", "construcao", "solo_exposto", "neve"]


def cobertura(fracoes, agua_perm=0.0, agua_sazonal=0.0, uc_frac=0.0, uc_nomes=()):
    """Resposta de `aptidao.calcular` (um ee.Dictionary): histograma do Dynamic World (10 000 pixels repartidos
    conforme `fracoes`, por nome de classe), frações de água do JRC e da área em unidade de conservação (WDPA)."""
    hist = {str(CLASSES_DW.index(k)): round(v * 10000) for k, v in fracoes.items()}
    return {"hist": hist, "agua": {"perm": agua_perm, "sazonal": agua_sazonal},
            "uc_frac": uc_frac, "uc_nomes": list(uc_nomes)}


def _solo_padrao(a, m):
    """O último mês (2026-09) fica no meio dos anos anteriores: classe "Normal"."""
    return 0.20 + 0.01 * (a - 2010 if a < 2026 else 7.5)


def _pede_argumento(fn):
    return any(p.default is p.empty for p in inspect.signature(fn).parameters.values())


class Expr:
    def __init__(self, fake, linhagem=frozenset()):
        self._fake, self._lin = fake, frozenset(linhagem)

    def _juntar(self, args):
        lin = set(self._lin)
        for a in args:
            if isinstance(a, Expr):
                lin |= a._lin
            elif isinstance(a, dict):
                lin |= self._juntar(a.values())
            elif isinstance(a, (list, tuple)):
                lin |= self._juntar(a)
        return lin

    def __getattr__(self, nome):
        if nome.startswith("__"):
            raise AttributeError(nome)

        def metodo(*args, **kw):
            lin = self._juntar(list(args) + list(kw.values()))
            if nome == "filterDate" and all(isinstance(a, str) for a in args):
                lin.add("filterDate:" + json.dumps(args))
            if nome == "map":  # ee.List.map / ImageCollection.map: roda a função para pegar a linhagem
                for a in args:
                    if callable(a) and not isinstance(a, Expr):
                        r = a(Expr(self._fake))
                        if isinstance(r, Expr):
                            lin |= r._lin
            if nome == "getInfo":
                return self._fake.responder(lin)
            if nome == "getMapId":
                return self._fake.mapear(lin)
            return Expr(self._fake, lin)
        return metodo

    def __call__(self, *args, **kw):
        return Expr(self._fake, self._juntar(args))


class FakeEE(types.ModuleType):
    def __init__(self, init_erro=None):
        super().__init__("ee")
        self.init_erro = init_erro
        self.inits = 0
        self.chamadas = Counter()
        self.mapas = []
        self.mapa_erro = None
        self.threads = {}
        self._trava = threading.Lock()
        self.respostas = {
            "SRTMGL1": lambda: {"elevation": 1100.0, "slope": 2.0},
            "FLDAS": lambda: serie_mensal(_solo_padrao),
            "MOD13Q1": lambda: serie([5000.0] * 12),
            "S1_GRD": lambda: serie([-15.0 + 0.1 * i for i in range(12)]),
            "DYNAMICWORLD": lambda: cobertura({"lavoura": 0.85, "campo": 0.13, "arvores": 0.02}),
        }

    def Initialize(self, *a, **kw):
        self.inits += 1
        if callable(self.init_erro):  # ex.: uma função que trava, para simular a rede sem resposta
            return self.init_erro()
        if self.init_erro:
            raise Exception(self.init_erro)

    def responder(self, linhagem):
        for chave, fn in self.respostas.items():
            if any(chave in x for x in linhagem):
                with self._trava:  # as consultas de `coletar` rodam em threads
                    self.chamadas[chave] += 1
                    self.threads.setdefault(chave, threading.current_thread())
                return fn(linhagem) if _pede_argumento(fn) else fn()
        raise AssertionError(f"getInfo sem resposta configurada para {sorted(linhagem)}")

    def Geometry(self, g, *a, **kw):
        return Expr(self, {"geom:" + json.dumps(g, sort_keys=True)})

    def Date(self, valor):
        return Expr(self, {"date:" + valor} if isinstance(valor, str) else valor._lin)

    def mapear(self, linhagem):
        with self._trava:
            url = f"https://fake/{len(self.mapas) + 1}/{{z}}/{{x}}/{{y}}"
            self.mapas.append((frozenset(linhagem), url))
        # mapa_erro: uma exceção (todas as camadas falham) ou função(linhagem) -> exceção ou None (só algumas)
        erro = self.mapa_erro(linhagem) if callable(self.mapa_erro) else self.mapa_erro
        if erro:
            raise erro
        return {"tile_fetcher": types.SimpleNamespace(url_format=url)}

    def _fonte(self, x=None, *a, **kw):
        return Expr(self, {x} if isinstance(x, str) else set())

    def __getattr__(self, nome):
        if nome.startswith("__"):
            raise AttributeError(nome)
        if nome in ("Image", "ImageCollection"):
            return self._fonte
        return Expr(self)  # Geometry, Date, Filter, Kernel, Reducer, Terrain, List, Feature, FeatureCollection...


def geometria(linhagem):
    """O GeoJSON (dict) da terra que entrou na consulta, a partir da linhagem."""
    return next(json.loads(x[5:]) for x in linhagem if x.startswith("geom:"))
