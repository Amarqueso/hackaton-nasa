"""RotaDF - guia de plantio para produtores do Distrito Federal.
Clique no mapa -> clima, terreno e época -> o que plantar e o que plantar depois (rotação).
Rodar: earthengine authenticate ; streamlit run app.py   (.env: GEE_PROJECT=seu-projeto)
"""
from dotenv import load_dotenv
load_dotenv()
import os, json, math, datetime as dt
import ee, folium, pandas as pd, requests, streamlit as st
from folium.plugins import Draw
from streamlit_folium import st_folium

st.set_page_config(page_title="RotaDF", layout="wide", initial_sidebar_state="collapsed")

@st.cache_resource
def init_ee():
    ee.Initialize(project=os.environ.get("GEE_PROJECT"))
try:
    init_ee(); EE_OK, EE_ERRO = True, ""
except Exception as _e:
    EE_OK, EE_ERRO = False, str(_e)[:300]

TODAY = dt.date.today()
START = dt.date(TODAY.year - 1, TODAY.month, 1)  # janela de 12 meses
NDVI_VIS = {"min": 0, "max": 0.9, "palette": ["#8c510a", "#d8b365", "#f6e8c3", "#a6d96a", "#1a9850", "#006837"]}
SAR_VIS = {"min": -25, "max": -5}
# Limites do mapa: DF (lat -16.05/-15.50, lon -48.29/-47.31) + ~50 km de margem
DF_BOUNDS = dict(min_lat=-16.50, max_lat=-15.05, min_lon=-48.76, max_lon=-46.84)
MES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
DIAS = [31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

def lee(img, size=2, enl=4.4):
    """Filtro Lee adaptativo (speckle) em escala linear; entrada e saída em dB."""
    lin = ee.Image(10).pow(img.divide(10))
    k = ee.Kernel.square(size, "pixels")
    m = lin.reduceNeighborhood(ee.Reducer.mean(), k)
    v = lin.reduceNeighborhood(ee.Reducer.variance(), k)
    ci2 = v.divide(m.pow(2))
    w = ci2.subtract(1 / enl).divide(ci2).max(0)
    return m.add(w.multiply(lin.subtract(m))).log10().multiply(10)

def s1(geom):
    return (ee.ImageCollection("COPERNICUS/S1_GRD").filterBounds(geom)
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH")).select("VH"))

def month_series(build, band, geom, scale, n=12):
    def f(i):
        s = ee.Date(START.isoformat()).advance(i, "month")
        v = build(s, s.advance(1, "month")).reduceRegion(ee.Reducer.mean(), geom, scale, maxPixels=1e9).get(band, -9999)
        return ee.Feature(None, {"m": s.format("YYYY-MM"), "v": v})
    feats = ee.FeatureCollection(ee.List.sequence(0, n - 1).map(f)).getInfo()["features"]
    ser = pd.Series({x["properties"]["m"]: x["properties"].get("v") for x in feats}, dtype="float")
    return ser.where(ser > -9000)  # meses sem dado (ex.: IMERG ainda não publicado) viram NaN

# ---------------- Culturas (referência agronômica geral, a validar com Emater-DF / Embrapa) ----------------
K = "nome emoji fam meses tmin tmax agua irrig dif fixa_n obj ciclo ha_min cobre desc".split()
_C = [
 ("Soja","🌱","Fabaceae",{10,11,12},20,30,"alta",0,3,1,{"renda"},4,20,0,"Plantada nas chuvas; pede máquinas e insumos."),
 ("Milho","🌽","Poaceae",{10,11,12,1,2},18,30,"média",0,2,0,{"renda","comida"},4,3,0,"Grão ou milho verde; boa para vender e para a família."),
 ("Feijão","🫘","Fabaceae",{10,11,2,3,5,6,7},15,27,"média",0,2,1,{"comida","renda"},3,0.5,0,"Ciclo curto e ajuda a adubar a terra com nitrogênio."),
 ("Sorgo","🌾","Poaceae",{11,12,1,2,3},20,32,"baixa",0,1,0,{"renda","terra"},4,5,0,"Aguenta seca; bom depois da soja ou do milho."),
 ("Mandioca","🥔","Euphorbiaceae",{9,10,11,12,4,5},18,30,"baixa",0,1,0,{"comida","renda"},8,0.3,0,"Rústica e fácil; aguenta pouca chuva."),
 ("Batata-doce","🍠","Convolvulaceae",{10,11,12,1,2,3},20,30,"baixa",0,1,0,{"comida"},4,0.2,1,"Fácil, cobre o chão e aguenta pouca água."),
 ("Abóbora","🎃","Cucurbitaceae",{9,10,11,12,1,2},20,30,"média",0,1,0,{"comida","renda"},4,0.3,1,"Fácil de cultivar e cobre bem o solo."),
 ("Adubo verde (crotalária/milheto)","🌿","Adubo verde",{10,11,12,1,2,3,4},20,32,"baixa",0,1,1,{"terra"},3,0,1,"Não dá colheita, mas protege e aduba o solo."),
 ("Capim para pasto (braquiária)","🐄","Poaceae",{10,11,12,1,2,3},20,32,"baixa",0,1,0,{"terra","renda"},24,2,1,"Cobre o solo e serve de pasto para o gado."),
 ("Alface","🥬","Asteraceae",{3,4,5,6,7,8,9},14,24,"alta",1,2,0,{"renda","comida"},2,0.05,0,"Colheita rápida; não gosta de calor e pede irrigação."),
 ("Couve","🥬","Brassicaceae",{2,3,4,5,6,7,8,9},15,26,"média",0,1,0,{"comida","renda"},3,0.05,0,"Resistente e de colheita longa."),
 ("Cheiro-verde (salsa e cebolinha)","🌿","Cheiro-verde",{3,4,5,6,7,8,9},14,26,"média",1,1,0,{"renda","comida"},3,0.05,0,"Pouco espaço, boa venda em feiras."),
 ("Tomate","🍅","Solanaceae",{3,4,5,6},18,28,"alta",1,3,0,{"renda"},5,0.2,0,"Rende bem, mas dá trabalho e tem muitas doenças."),
 ("Cenoura","🥕","Apiaceae",{3,4,5,6,7,8},15,25,"média",1,2,0,{"renda","comida"},4,0.2,0,"Gosta de clima mais fresco e de solo solto."),
 ("Morango","🍓","Rosaceae",{4,5,6},14,25,"alta",1,3,0,{"renda"},8,0.1,0,"Muito valorizado; exige cuidado e irrigação."),
 ("Café","☕","Rubiaceae",{11,12,1,2},18,25,"média",0,3,0,{"renda"},36,2,1,"Cultura de vários anos; demora a dar colheita."),
 ("Goiaba","🍈","Myrtaceae",{11,12,1,2,3},18,30,"média",0,2,0,{"renda","comida"},60,0.5,1,"Fruteira de vários anos, tradicional no DF."),
]
CAT = [dict(zip(K, r)) for r in _C]
POR_NOME = {c["nome"]: c for c in CAT}

NIVEIS = {"Nunca plantei": 1, "Já planto": 2, "Tenho experiência": 3}
AGUA = {"Só chuva": False, "Posso irrigar": True}
OBJ = {"Renda": "renda", "Alimentação": "comida", "Cuidar da terra": "terra"}
TAM = {"Pequena (cerca de 2 hectares)": 2, "Média (cerca de 10 hectares)": 10,
       "Grande (cerca de 50 hectares)": 50, "Muito grande (cerca de 200 hectares)": 200}
NUNCA, PARADA = "Nunca plantei / não sei", "Terra parada (nada)"

# ---------------- Geografia ----------------
def pontos(gj): return json.loads(gj)["coordinates"][0]

def centro_de(gj):
    p = pontos(gj)[:-1]
    return sum(x[1] for x in p) / len(p), sum(x[0] for x in p) / len(p)

def area_ha(gj):
    p = pontos(gj); c = math.cos(math.radians(centro_de(gj)[0]))
    xy = [(x[0] * 111320 * c, x[1] * 110540) for x in p]
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(xy, xy[1:]))) / 2 / 1e4

def quadrado(lat, lon, ha):
    meio = math.sqrt(ha * 1e4) / 2; dy = meio / 110540; dx = meio / (111320 * math.cos(math.radians(lat)))
    return json.dumps({"type": "Polygon", "coordinates": [[[lon - dx, lat - dy], [lon + dx, lat - dy],
                       [lon + dx, lat + dy], [lon - dx, lat + dy], [lon - dx, lat - dy]]]})

# ---------------- Dados: NASA POWER (clima) e Earth Engine (terreno, umidade do solo, vegetação, radar) ----------------
@st.cache_data(show_spinner=False)
def clima_power(lat, lon):
    r = requests.get("https://power.larc.nasa.gov/api/temporal/monthly/point", timeout=40, params=dict(
        parameters="T2M,PRECTOTCORR,RH2M", community="AG", longitude=lon, latitude=lat,
        start=2015, end=TODAY.year - 1, format="JSON"))
    r.raise_for_status(); p = r.json()["properties"]["parameter"]
    def med(par, m):
        v = [x for k, x in p[par].items() if len(k) == 6 and k.endswith(f"{m:02d}") and x > -900]
        return sum(v) / len(v) if v else None
    out = {}
    for m in range(1, 13):
        ch = med("PRECTOTCORR", m)
        out[m] = dict(t=med("T2M", m), chuva=None if ch is None else ch * DIAS[m - 1], ur=med("RH2M", m))
    return out

def terreno(geom):
    e = ee.Image("USGS/SRTMGL1_003")
    r = e.addBands(ee.Terrain.slope(e)).reduceRegion(ee.Reducer.mean(), geom, 30, maxPixels=1e9).getInfo()
    return dict(alt=r["elevation"], decl=r["slope"])

def umidade_solo(geom):
    """Umidade do solo (0-10 cm, NASA GLDAS, ~25 km): último mês disponível vs. o mesmo mês em 2010-hoje."""
    ponto, banda = geom.centroid(1), "SoilMoi0_10cm_inst"
    col = ee.ImageCollection("NASA/GLDAS/V021/NOAH/G025/T3H").select(banda)
    ser = month_series(lambda s, e: col.filterDate(s, e).mean(), banda, ponto, 27830).dropna()
    if ser.empty: return None
    mes, atual = int(ser.index[-1][5:]), float(ser.iloc[-1])
    def f(y):
        s = ee.Date.fromYMD(y, mes, 1)
        v = col.filterDate(s, s.advance(1, "month")).mean().reduceRegion(ee.Reducer.mean(), ponto, 27830).get(banda, -9999)
        return ee.Feature(None, {"v": v})
    feats = ee.FeatureCollection(ee.List.sequence(2010, TODAY.year - 1).map(f)).getInfo()["features"]
    vals = [x["properties"]["v"] for x in feats if x["properties"].get("v") is not None and x["properties"]["v"] > -9000]
    return dict(pct=sum(v < atual for v in vals) / len(vals), mes=mes) if vals else None

def _ou_none(ser): return None if ser.dropna().empty else ser

@st.cache_data(show_spinner=False)
def coletar(gj):
    """Junta tudo o que o satélite sabe da área. O que falhar fica None (e a tela avisa que não temos o dado)."""
    lat, lon = centro_de(gj); d, erros = {}, {}
    def tentar(k, fn):
        try: d[k] = fn()
        except Exception as e: d[k] = None; erros[k] = str(e)[:300]
    tentar("clima", lambda: clima_power(round(lat, 1), round(lon, 1)))
    if EE_OK:
        geom = ee.Geometry(json.loads(gj))
        tentar("terreno", lambda: terreno(geom))
        tentar("solo", lambda: umidade_solo(geom))
        tentar("veg", lambda: _ou_none(month_series(lambda s, e: ee.ImageCollection("MODIS/061/MOD13Q1")
                .filterDate(s, e).select("NDVI").mean(), "NDVI", geom, 250) * 1e-4))
    else:
        erros["gee"] = EE_ERRO
    d["erros"] = erros
    return d

@st.cache_data(show_spinner=False)
def radar_vh(gj):
    """Série mensal do radar Sentinel-1 VH com filtro Lee (só no modo técnico; None se não houver imagem)."""
    geom = ee.Geometry(json.loads(gj))
    return _ou_none(month_series(lambda s, e: lee(s1(geom).filterDate(s, e).median()).rename("VH"), "VH", geom, 30))

# ---------------- Recomendação (regras explicáveis) ----------------
def janela(mes, n): return [(mes - 1 + i) % 12 + 1 for i in range(max(1, min(n, 4)))]

def clima_janela(clima, mes, n=4):
    if not clima: return None
    j = [clima[m] for m in janela(mes, n)]
    if any(x["t"] is None or x["chuva"] is None for x in j): return None
    ur = [x["ur"] for x in j]
    return dict(t=sum(x["t"] for x in j) / len(j), chuva=sum(x["chuva"] for x in j),
                ur=None if None in ur else sum(ur) / len(ur))

def avaliar(c, d, p, mes, ultimo):
    if mes not in c["meses"]: return "fora"
    if c["irrig"] and not p["irriga"]: return "irrigar"
    pts, bom, cuid = 50, [], []
    cj = clima_janela(d.get("clima"), mes, c["ciclo"])
    if cj:
        if c["tmin"] - 1 <= cj["t"] <= c["tmax"] + 1:
            pts += 15; bom.append(f"A temperatura esperada ({cj['t']:.0f} °C em média) combina com ela.")
        else:
            pts -= 25; cuid.append(f"Deve ficar {'quente' if cj['t'] > c['tmax'] else 'frio'} demais para ela ({cj['t']:.0f} °C em média).")
        if not p["irriga"]:
            if cj["chuva"] >= {"baixa": 150, "média": 300, "alta": 450}[c["agua"]]:
                pts += 10; bom.append(f"Deve chover o bastante ({cj['chuva']:.0f} mm) sem precisar irrigar.")
            else:
                pts -= 40; cuid.append(f"Deve chover pouco ({cj['chuva']:.0f} mm) para ela sem irrigação.")
    else:
        cuid.append("Sem dado de clima agora: não deu para conferir se o tempo combina.")
    if c["dif"] > p["nivel"]:
        pts -= 20 * (c["dif"] - p["nivel"]); cuid.append("Pede mais experiência: vale buscar apoio da Emater-DF.")
    elif p["nivel"] == 1 and c["dif"] == 1:
        pts += 8; bom.append("É uma boa escolha para quem está começando.")
    if p["ha"] < c["ha_min"]:
        pts -= 25; cuid.append(f"Costuma compensar em áreas a partir de {c['ha_min']:g} hectares.")
    if p["obj"] in c["obj"]:
        pts += 12; bom.append({"renda": "Tem boa saída para venda.", "comida": "Serve bem para alimentar a família.",
                               "terra": "Ajuda a cuidar da terra."}[p["obj"]])
    if ultimo:
        if ultimo["fam"] == c["fam"]:
            pts -= 35; cuid.append(f"É da mesma família do que veio antes ({ultimo['nome']}): pode acumular pragas e doenças.")
        elif c["fixa_n"] and not ultimo["fixa_n"]:
            pts += 10; bom.append("Devolve nitrogênio ao solo depois do que veio antes.")
        elif ultimo["fixa_n"] and c["fam"] == "Poaceae":
            pts += 8; bom.append(f"Aproveita o nitrogênio que {ultimo['nome']} deixa no solo.")
    t = d.get("terreno")
    if t and t["decl"] > 6:
        if c["cobre"]: pts += 10; bom.append("Protege o solo em um terreno inclinado.")
        else: pts -= 10; cuid.append("O terreno é inclinado: a chuva pode levar o solo. Plante em curva de nível.")
    veg = d.get("veg"); sem = int((veg < 0.3).sum()) if veg is not None else 0
    if sem >= 4 and c["cobre"]:
        pts += 6; bom.append(f"Cobre a terra, que ficou {sem} meses com pouca planta no último ano.")
    s = d.get("solo")
    if s and not p["irriga"] and s["pct"] < 0.3:
        if c["agua"] == "baixa": pts += 8; bom.append("Aguenta bem o solo mais seco que o normal na região.")
        elif c["agua"] == "alta": pts -= 8; cuid.append("O solo da região está mais seco que o normal.")
    return dict(c, pts=max(0, min(100, round(pts))), bom=bom, cuid=cuid)

NOTA_MIN = 50

def ranking(d, p, mes, ultimo):
    rs = [avaliar(c, d, p, mes, ultimo) for c in CAT]
    ok = [r for r in rs if isinstance(r, dict) and r["pts"] >= NOTA_MIN]  # abaixo disso não vale recomendar
    return sorted(ok, key=lambda r: -r["pts"]), [r for r in rs if isinstance(r, str)]

def plano(d, p, mes, ultimo, passos=3):
    """Sequência de plantios (rotação): cada passo escolhe a melhor cultura depois do ciclo da anterior."""
    seq, m = [], mes
    for _ in range(passos):
        for espera in range(7):
            ok, _ = ranking(d, p, (m - 1 + espera) % 12 + 1, ultimo)
            if ok: break
        else:
            break
        top, mm = ok[0], (m - 1 + espera) % 12 + 1
        seq.append((mm, top, espera))
        if top["ciclo"] >= 12: break  # cultura de vários anos: fica no lugar
        m, ultimo = (mm - 1 + top["ciclo"]) % 12 + 1, top
    return seq

def confianca(d):
    if d.get("clima") is None: return "baixa"
    n = sum(d.get(k) is not None for k in ("clima", "terreno", "solo", "veg"))
    return "alta" if n == 4 else "média" if n >= 2 else "baixa"

@st.cache_data(show_spinner=False)
def layers(gj):
    geom = ee.Geometry(json.loads(gj)); box = geom.buffer(1500).bounds()
    end = ee.Date(TODAY.isoformat())
    hls = (ee.ImageCollection("NASA/HLS/HLSL30/v002").filterBounds(geom).filterDate(end.advance(-120, "day"), end)
           .map(lambda i: i.updateMask(i.select("Fmask").bitwiseAnd(14).eq(0)).normalizedDifference(["B5", "B4"]))
           .median().clip(box))
    raw = s1(geom).filterDate(end.advance(-60, "day"), end).median()
    out = {"Saúde da vegetação (verde = saudável)": (hls, NDVI_VIS), "Radar (bruto, com ruído)": (raw.clip(box), SAR_VIS),
           "Radar (filtrado)": (lee(raw).clip(box), SAR_VIS)}
    return {k: i.getMapId(v)["tile_fetcher"].url_format for k, (i, v) in out.items()}

# --- MAPA ---
from branca.element import MacroElement, Template

class _TextoPT(MacroElement):
    """Traduz os textos da ferramenta de desenho para português (precisa rodar antes do Draw)."""
    _template = Template("""{% macro script(this, kwargs) %}
    (function () {
      try {
        var t = L.drawLocal.draw.toolbar, h = L.drawLocal.draw.handlers;
        t.actions.title = 'Cancelar desenho'; t.actions.text = 'Cancelar';
        t.finish.title = 'Terminar desenho'; t.finish.text = 'Terminar';
        t.undo.title = 'Apagar o último ponto'; t.undo.text = 'Apagar último ponto';
        t.buttons.polygon = 'Desenhar a área da sua terra'; t.buttons.rectangle = 'Desenhar um retângulo';
        h.polygon.tooltip.start = 'Clique no mapa para começar a marcar sua terra';
        h.polygon.tooltip.cont = 'Clique para continuar marcando';
        h.polygon.tooltip.end = 'Clique no primeiro ponto para terminar';
        h.rectangle.tooltip.start = 'Clique e arraste para desenhar o retângulo';
        h.simpleshape.tooltip.end = 'Solte o mouse para terminar';
      } catch (e) {}
    })();
    {% endmacro %}""")

class _LimitarMapa(MacroElement):
    """Trava o mapa: não arrasta para fora da caixa e não afasta além do zoom mínimo."""
    _template = Template("""{% macro script(this, kwargs) %}
    var _m = {{ this._parent.get_name() }};
    _m.setMaxBounds(L.latLngBounds([{{ this.sul }}, {{ this.oeste }}], [{{ this.norte }}, {{ this.leste }}]));
    _m.options.maxBoundsViscosity = 1.0;
    _m.setMinZoom({{ this.zmin }});
    _m.setMaxZoom(17);
    if (_m.getZoom() < {{ this.zmin }}) { _m.setZoom({{ this.zmin }}); }
    try {
      _m.zoomControl._zoomInButton.title = 'Aproximar';
      _m.zoomControl._zoomOutButton.title = 'Afastar';
    } catch (e) {}
    {% endmacro %}""")
    def __init__(self, b, zmin):
        super().__init__()
        self.sul, self.oeste, self.norte, self.leste, self.zmin = b["min_lat"], b["min_lon"], b["max_lat"], b["max_lon"], zmin

ZOOM_MIN = 9
COR = "#F5B700"  # amarelo de ipê: contrasta com o satélite e com o mapa

def construir_mapa(gj, urls, desenho):
    # obs.: com tiles=None o folium ignora min_zoom/max_zoom; os limites de zoom são aplicados em _LimitarMapa
    m = folium.Map(location=[-15.78, -47.80], zoom_start=10, tiles=None, max_bounds=True, **DF_BOUNDS)
    folium.TileLayer("OpenStreetMap", name="Mapa", no_wrap=True, show=False).add_to(m)
    folium.TileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                     attr="Esri", name="Satélite", no_wrap=True).add_to(m)
    if desenho:
        _TextoPT().add_to(m)  # antes do Draw
        Draw(draw_options={"polyline": False, "circle": False, "marker": False, "circlemarker": False,
                           "polygon": {"allowIntersection": False, "shapeOptions": {"color": COR, "weight": 3},
                                       "drawError": {"color": "#e1e100", "message": "As linhas não podem se cruzar."}},
                           "rectangle": {"shapeOptions": {"color": COR, "weight": 3}}},
             edit_options={"edit": False, "remove": False}).add_to(m)
    if gj:
        pts = pontos(gj)
        folium.GeoJson(json.loads(gj), name="Sua terra", style_function=lambda _: {"color": COR, "weight": 3, "fillOpacity": 0.15}).add_to(m)
        for name, url in urls.items():
            folium.TileLayer(tiles=url, attr="Google Earth Engine", name=name, overlay=True, show=name.startswith("Saúde")).add_to(m)
        m.fit_bounds([[min(p[1] for p in pts), min(p[0] for p in pts)], [max(p[1] for p in pts), max(p[0] for p in pts)]],
                     padding=(40, 40), max_zoom=15)  # centraliza na terra marcada
    else:
        m.fit_bounds([[-16.07, -48.30], [-15.48, -47.30]])  # começa mostrando o DF inteiro
    folium.LayerControl(collapsed=True).add_to(m)
    _LimitarMapa(DF_BOUNDS, ZOOM_MIN).add_to(m)
    return m
# --- FIM MAPA ---

# ---------------- Interface ----------------
import html as _h
CSS = """
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700&family=Public+Sans:wght@400;500;600&display=swap');
:root{--bg:#F2F4F1;--papel:#fff;--tinta:#16241D;--verde:#1D4D3B;--verde-cl:#DDE8E0;--ipe:#E8B000;--terra:#9C4A2F;--linha:#D9DFDA;--mut:#5F6B64}
.stApp{background:var(--bg);color:var(--tinta)}
.stApp,.stMarkdown,.stMarkdown p,label,button,input,[data-baseweb]{font-family:'Public Sans',system-ui,sans-serif}
.block-container{max-width:1280px;padding:1.2rem 1.5rem 4rem}
header[data-testid="stHeader"]{background:transparent}
#MainMenu,footer{visibility:hidden}
h1,h2,h3,h4,h5,.marca,.passo span,.trilha b{font-family:'Bricolage Grotesque',system-ui,sans-serif;color:var(--verde);letter-spacing:-.015em}
.topo{display:flex;flex-wrap:wrap;align-items:baseline;gap:.3rem 1.2rem;padding:.2rem 0 1rem;border-bottom:1px solid var(--linha);margin-bottom:1.2rem}
.marca{font-size:1.5rem;font-weight:700;display:inline-flex;align-items:center;gap:.5rem}
.marca::before{content:"";width:.8rem;height:.8rem;border-radius:50%;background:var(--ipe)}
.lema{color:var(--mut)}
iframe[title*="folium"]{border:1px solid var(--linha);border-radius:12px;height:clamp(400px,68vh,700px)!important;background:#dfe6e0}
[data-testid="stVerticalBlockBorderWrapper"]{border-color:var(--linha)!important;border-radius:12px;background:var(--papel)}
[data-testid="stExpander"]{border:1px solid var(--linha);border-radius:12px;background:var(--papel)}
[data-testid="stWidgetLabel"] p{font-size:.86rem;color:var(--mut);font-weight:500}
.stButton button{border-radius:8px;border:1px solid var(--verde);color:var(--verde);background:transparent}
.stButton button:hover{background:var(--verde-cl);border-color:var(--verde);color:var(--verde)}
.passo{display:flex;align-items:center;gap:.6rem;margin:.3rem 0 .6rem}
.passo b{width:1.6rem;height:1.6rem;border-radius:50%;background:var(--verde);color:#fff;display:grid;place-items:center;font-size:.85rem}
.passo span{font-weight:700;font-size:1.2rem}
h2.sec{font-size:1.6rem;margin:2.4rem 0 .9rem;font-weight:700}
.destaque{background:var(--verde);color:#fff;border-radius:14px;padding:1.6rem 1.7rem}
.destaque h3{color:#fff;font-size:1.8rem;margin:0 0 .2rem;font-weight:700}
.destaque p{margin:0 0 1rem;color:#CFE0D6}
.destaque small{color:#CFE0D6;display:block;margin-top:.4rem}
.destaque .fit{background:rgba(255,255,255,.22)}
.destaque .fit i{background:var(--ipe)}
.destaque h5{color:#fff;font-size:1.02rem;margin:.2rem 0 .4rem}
.duas{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1rem 2.2rem;margin-top:1.2rem}
.destaque ul{list-style:none;padding:0;margin:0}
.destaque li{position:relative;padding-left:1.1rem;margin:.4rem 0;font-size:.95rem;line-height:1.45}
.destaque li::before{content:"";position:absolute;left:0;top:.55em;width:.5rem;height:.5rem;border-radius:50%;background:#fff;opacity:.5}
.destaque li.ok::before{background:var(--ipe);opacity:1}
.fit{display:block;height:6px;border-radius:3px;background:var(--verde-cl);overflow:hidden;min-width:90px}
.fit i{display:block;height:100%;background:var(--verde);border-radius:3px}
.linha{display:grid;grid-template-columns:minmax(0,1.3fr) 150px minmax(0,1.6fr);gap:1rem;align-items:center;padding:.95rem .2rem;border-bottom:1px solid var(--linha)}
.linha b{display:block;font-weight:600}
.linha div span{display:block;color:var(--mut);font-size:.88rem}
.linha p{margin:0;font-size:.92rem}
.trilha{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr))}
.trilha li{position:relative;padding:1.2rem 1.2rem 0 0}
.trilha li::before{content:"";position:absolute;top:0;left:0;right:0;border-top:2px solid var(--linha)}
.trilha li::after{content:"";position:absolute;top:-5px;left:0;width:10px;height:10px;border-radius:50%;background:var(--ipe)}
.trilha time{display:block;text-transform:capitalize;color:var(--mut);font-size:.88rem}
.trilha b{font-size:1.25rem;display:block;font-weight:700}
.trilha span{display:block;font-size:.9rem;margin-top:.2rem}
.trilha em{display:block;color:var(--terra);font-size:.85rem;font-style:normal;margin-top:.3rem}
.dados{margin:0;border-top:1px solid var(--linha)}
.dados>div{display:grid;grid-template-columns:minmax(150px,1fr) minmax(120px,1fr) minmax(0,1.6fr);gap:.3rem 1rem;padding:.75rem 0;border-bottom:1px solid var(--linha)}
.dados dt{font-weight:600}.dados dd{margin:0}
.dados .fonte{color:var(--mut);font-size:.88rem}
.dados .falta{color:var(--terra);font-weight:600}
.nota{border-left:3px solid var(--ipe);padding:.3rem 1rem;margin:1rem 0;line-height:1.55}
.mut{color:var(--mut);font-size:.92rem}
@media (max-width:720px){.block-container{padding:.8rem .9rem 3rem}.linha{grid-template-columns:1fr;gap:.4rem}
.dados>div{grid-template-columns:1fr}.destaque{padding:1.2rem}.destaque h3{font-size:1.45rem}h2.sec{font-size:1.35rem}}
"""
st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)

M_CLICK, M_DESENHO = "Clicar no mapa", "Desenhar contorno"
for k, v in dict(map_v=0, centro=None, gj_desenho=None, ultimo_clique=None).items():
    st.session_state.setdefault(k, v)

def limpar():
    st.session_state.update(centro=None, gj_desenho=None, ultimo_clique=None)
    st.session_state["map_v"] += 1  # mapa novo: some a marcação e volta a mostrar o DF

def escolha(rotulo, opcoes, chave, **kw):
    """Seletor em formato de botões (cai para radio em versões antigas do Streamlit)."""
    f = getattr(st, "segmented_control", None)
    if f is None: return st.radio(rotulo, opcoes, key=chave, horizontal=True, **kw)
    return f(rotulo, opcoes, default=opcoes[0], key=chave, **kw) or opcoes[0]

esc = lambda x: _h.escape(str(x))
html = lambda s: st.markdown(s, unsafe_allow_html=True)
lis = lambda xs, c: "".join(f'<li class="{c}">{esc(x)}</li>' for x in xs)
barra = lambda p: f'<span class="fit"><i style="width:{p}%"></i></span>'

def destaque(r, mes):
    rot = "Combina muito com a sua terra" if r["pts"] >= 70 else "Combina com a sua terra"
    return ('<section class="destaque">' f'<h3>Plantar {esc(r["nome"])} em {MES[mes - 1]}</h3><p>{esc(r["desc"])}</p>'
            f'{barra(r["pts"])}<small>{rot}</small><div class="duas">'
            f'<div><h5>Por que combina</h5><ul>{lis(r["bom"][:4] or ["Sem pontos fortes com os dados que temos."], "ok")}</ul></div>'
            f'<div><h5>Pontos de atenção</h5><ul>{lis(r["cuid"][:4] or ["Nenhum alerta com os dados que temos."], "nd")}</ul></div></div></section>')

def linha(r):
    t = (r["bom"] or r["cuid"] or [r["desc"]])[0]
    return f'<div class="linha"><div><b>{esc(r["nome"])}</b><span>{esc(r["desc"])}</span></div>{barra(r["pts"])}<p>{esc(t)}</p></div>'

def dado(rot, val, fonte):
    v = f"<dd>{esc(val)}</dd>" if val else '<dd class="falta">Sem dado</dd>'
    return f'<div><dt>{rot}</dt>{v}<dd class="fonte">{fonte}</dd></div>'

with st.sidebar:
    st.markdown("### Modo técnico")
    tecnico = st.toggle("Camadas de vegetação e radar no mapa")
    st.caption("Para quem entende de sensoriamento remoto. O parecer técnico completo fica no fim da página.")

html('<div class="topo"><span class="marca">RotaDF</span>'
     '<span class="lema">Guia de plantio para o Distrito Federal, com dados de satélite da NASA</span></div>')
modo = st.session_state.get("modo") or M_CLICK
ha_tam = TAM[st.session_state.get("tam", list(TAM)[1])]
if modo == M_CLICK and st.session_state["centro"]:
    gj = quadrado(*st.session_state["centro"], ha_tam)
else:
    gj = st.session_state["gj_desenho"] if modo == M_DESENHO else None

left, right = st.columns([1.7, 1], gap="large")
with left:
    try:
        urls = layers(gj) if (gj and tecnico and EE_OK) else {}
    except Exception:
        urls = {}  # o mapa continua funcionando sem as camadas
    out = st_folium(construir_mapa(gj, urls, modo == M_DESENHO), height=600, use_container_width=True,
                    key=f"mapa{st.session_state['map_v']}", returned_objects=["last_clicked", "last_active_drawing"]) or {}
    if modo == M_CLICK:
        c = out.get("last_clicked")
        if c and (c["lat"], c["lng"]) != st.session_state["ultimo_clique"]:
            st.session_state["ultimo_clique"] = (c["lat"], c["lng"]); st.session_state["centro"] = (c["lat"], c["lng"]); st.rerun()
    else:
        dr = out.get("last_active_drawing")
        if dr and json.dumps(dr["geometry"]) != gj:
            st.session_state["gj_desenho"] = json.dumps(dr["geometry"]); st.rerun()

with right, st.container(border=True):
    html('<div class="passo"><b>1</b><span>Marque a sua terra</span></div>')
    escolha("Como prefere marcar?", [M_CLICK, M_DESENHO], "modo", on_change=limpar)
    if modo == M_CLICK:
        st.selectbox("Tamanho aproximado", list(TAM), index=1, key="tam", help="1 hectare é mais ou menos um campo de futebol.")
        st.caption("Clique no mapa, no meio da terra. Errou? Clique em outro ponto.")
    else:
        st.caption("Use o botão de polígono do mapa: clique nos cantos e termine no primeiro ponto. "
                   "Errou no meio do desenho? Use Cancelar, no próprio mapa.")
    st.button("Apagar e começar de novo", on_click=limpar, disabled=not gj)
    if not gj:
        html('<p class="nota">Marque a sua terra no mapa para ver o que combina com ela.</p>')
        st.stop()
    ha = area_ha(gj)
    if ha > 5000:
        html('<p class="nota">Essa área é muito grande (mais de 5.000 hectares). Marque só a sua terra.</p>'); st.stop()
    html(f'<p class="mut">Terra marcada: cerca de {ha:,.0f} hectares.</p>'.replace(",", "."))

    html('<div class="passo"><b>2</b><span>Conte um pouco sobre você</span></div>')
    nivel = escolha("Você já plantou antes?", list(NIVEIS), "nivel")
    irriga = escolha("Tem água para irrigar?", list(AGUA), "agua")
    obj = escolha("O que você quer?", list(OBJ), "obj")
    mes = st.selectbox("Quando pretende plantar?", list(range(1, 13)), index=TODAY.month - 1, key="mes",
                       format_func=lambda m: MES[m - 1].capitalize() + (" (este mês)" if m == TODAY.month else ""))
    ult_nome = st.selectbox("O que plantou por último nesta terra?", [NUNCA, PARADA] + [c["nome"] for c in CAT], key="ult")

with st.spinner("Consultando os satélites… pode levar um minuto."):
    d = coletar(gj)
perfil = dict(nivel=NIVEIS[nivel], irriga=AGUA[irriga], obj=OBJ[obj], ha=ha)
ultimo = POR_NOME.get(ult_nome)
ok, motivos = ranking(d, perfil, mes, ultimo)

html(f'<h2 class="sec">O que combina com a sua terra em {MES[mes - 1]}</h2>')
if not ok:
    html('<p class="nota">Nada combina bem com a sua terra nesta época. ' + (
        "Sem irrigação a chuva não é suficiente e várias culturas dessa época precisam de água. Tente outro mês ou veja se dá para irrigar."
        if not perfil["irriga"] else "Tente outro mês.") + "</p>")
else:
    html(destaque(ok[0], mes))
    if len(ok) > 1:
        html("<div>" + "".join(linha(r) for r in ok[1:4]) + "</div>")
    if len(ok) > 4:
        html('<p class="mut">Também possível: ' + esc(", ".join(r["nome"] for r in ok[4:7])) + ".</p>")

html('<h2 class="sec">E depois? Um plano de rotação</h2>')
seq = plano(d, perfil, mes, ultimo)
html('<ol class="trilha">' + "".join(
    f'<li><time>{MES[mm - 1]}</time><b>{esc(r["nome"])}</b><span>{esc(r["bom"][0] if r["bom"] else r["desc"])}</span>'
    + (f"<em>A terra descansa {e} mês(es) antes.</em>" if e else "") + "</li>" for mm, r, e in seq) + "</ol>")
html('<p class="mut">A ideia é alternar famílias de plantas e incluir uma que devolve nitrogênio ao solo (feijão, crotalária), '
     'para cuidar da terra e interromper pragas e doenças.</p>')

html('<h2 class="sec">O que sabemos sobre a sua terra</h2>')
cj, t, s, veg = clima_janela(d.get("clima"), mes, 4), d.get("terreno"), d.get("solo"), d.get("veg")
nasa = "NASA POWER, vale para a região"
html('<dl class="dados">' + "".join([
    dado("Temperatura nos próximos meses", cj and f"{cj['t']:.0f} °C em média", nasa),
    dado("Chuva esperada em 4 meses", cj and f"{cj['chuva']:.0f} mm", nasa),
    dado("Umidade do ar", cj and cj["ur"] is not None and f"{cj['ur']:.0f}%", nasa),
    dado("Altitude", t and f"{t['alt']:.0f} m", "NASA SRTM, 30 metros"),
    dado("Terreno", t and ("Plano" if t["decl"] <= 3 else "Inclinado" if t["decl"] <= 6 else "Muito inclinado"), "NASA SRTM, 30 metros"),
    dado("Umidade do solo agora", s and ("Bem mais seca que o normal" if s["pct"] < 0.25 else "Um pouco mais seca que o normal"
         if s["pct"] < 0.4 else "Normal" if s["pct"] <= 0.6 else "Mais úmida que o normal"), "NASA GLDAS, vale para a região"),
    dado("Meses com pouca planta no último ano", veg is not None and f"{int((veg < 0.3).sum())} de 12", "NASA MODIS, 250 metros")]) + "</dl>")
conf = confianca(d)
html(f'<p class="nota"><b>Confiança da sugestão: {conf}.</b> ' + {"alta": "Conseguimos todos os dados.",
     "média": "Faltou algum dado; veja os itens marcados como sem dado.",
     "baixa": "Faltaram dados importantes, como o clima. Use só como ideia inicial."}[conf] +
     "<br><b>O que não sabemos:</b> o tipo e a fertilidade do solo, o preço de venda, o que já foi plantado aí e a sua estrutura "
     "(máquinas, mão de obra). Clima e umidade do solo valem para a região, não só para a sua terra. "
     "Converse com um agrônomo ou com a Emater-DF antes de decidir.</p>")

radar, erros = None, dict(d.get("erros", {}))
if tecnico and EE_OK:  # o radar não entra na recomendação: só é consultado no modo técnico
    with st.spinner("Carregando o radar…"):
        try: radar = radar_vh(gj)
        except Exception as e: erros["radar"] = str(e)[:300]

with st.expander("Parecer técnico (para quem entende do assunto)"):
    fontes = [("Clima (T2M, chuva, UR)", d.get("clima"), "NASA POWER, média mensal 2015–" + str(TODAY.year - 1) + ", ~50 km"),
              ("Altitude e declive", d.get("terreno"), "NASA SRTM, 30 m"),
              ("Umidade do solo 0–10 cm", d.get("solo"), "NASA GLDAS 2.1 Noah, 0,25°; percentil do mês vs. 2010–hoje"),
              ("NDVI mensal", d.get("veg"), "NASA MODIS MOD13Q1, 250 m (últimos 12 meses)"),
              ("Radar VH com filtro Lee", radar, "Sentinel-1 GRD (ESA), 30 m, Lee 5×5, ENL 4,4")]
    disp = lambda n, v: "só no modo técnico" if n.startswith("Radar") and not tecnico else "sim" if v is not None else "NÃO"
    st.dataframe(pd.DataFrame([{"Dado": n, "Disponível": disp(n, v), "Fonte / resolução": f} for n, v, f in fontes]),
                 hide_index=True, width="stretch")
    for k, e in erros.items(): st.caption(f"Falha em '{k}': {e}")
    if d.get("clima"):
        st.markdown("**Clima mensal (média do período)**")
        st.dataframe(pd.DataFrame([{"Mês": MES[m - 1], "T média (°C)": v["t"], "Chuva (mm)": v["chuva"], "UR (%)": v["ur"]}
                                   for m, v in d["clima"].items()]).round(1), hide_index=True, width="stretch")
    if d.get("veg") is not None: st.markdown("**NDVI mensal (MODIS)**"); st.line_chart(d["veg"])
    if radar is not None: st.markdown("**Radar VH filtrado (dB)**"); st.line_chart(radar)
    st.markdown("**Método.** Ranking por regras (não é modelo treinado): janela de plantio, temperatura média e chuva do ciclo "
                "(até 4 meses) contra a faixa da cultura; exigência de irrigação; dificuldade vs. experiência; área mínima; objetivo; "
                "rotação (mesma família penaliza; leguminosa após não leguminosa e gramínea após leguminosa bonificam); declive > 6° favorece "
                "culturas de cobertura; umidade do solo abaixo do percentil 30 (sem irrigação). NDVI e filtro Lee seguem os notebooks "
                "NDVI/GEE_multiplas_datas e Lee_filter do curso. As faixas e janelas das culturas são referência agronômica geral e "
                "precisam de validação (Emater-DF, Embrapa Hortaliças/Cerrados). Clima e solo têm resolução (25–50 km) maior que a terra.")