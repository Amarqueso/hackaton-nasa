"""RotaDF - sugestão de rotação de culturas para produtores do Distrito Federal.
Desenhe uma área no mapa -> NDVI (NASA), chuva (GPM), relevo (SRTM), radar filtrado (Lee) -> comparação de rotações.
Rodar: earthengine authenticate ; GEE_PROJECT=seu-projeto streamlit run app.py
"""
from dotenv import load_dotenv
load_dotenv()
import os, json, calendar, datetime as dt
import ee, folium, pandas as pd, streamlit as st
from folium.plugins import Draw
from streamlit_folium import st_folium
import enso, zarc, aptidao

st.set_page_config(page_title="RotaDF", layout="wide")

@st.cache_resource
def init_ee():
    ee.Initialize(project=os.environ.get("GEE_PROJECT"))
init_ee()

TODAY = dt.date.today()
START = dt.date(TODAY.year - 1, TODAY.month, 1)  # janela de 12 meses completos
MOD = ee.ImageCollection("MODIS/061/MOD13Q1")
IMERG = ee.ImageCollection("NASA/GPM_L3/IMERG_MONTHLY_V07")
NDVI_VIS = {"min": 0, "max": 0.9, "palette": ["#8c510a", "#d8b365", "#f6e8c3", "#a6d96a", "#1a9850", "#006837"]}
SAR_VIS = {"min": -25, "max": -5}
# Limites do mapa: DF (lat -16.05/-15.50, lon -48.29/-47.31) + ~50 km de margem
DF_BOUNDS = dict(min_lat=-16.50, max_lat=-15.05, min_lon=-48.76, max_lon=-46.84)

SAFRINHA = "Soja e depois milho safrinha"
# Notas qualitativas (0-5, maior = melhor). Heurística a validar com Emater-DF/Embrapa.
STRATS = {
    "Soja e depois terra descansando (pousio)": dict(agua=4, cobertura=1, renda=2, solo=1,
        bom="Não gasta água nem dinheiro com um segundo plantio.",
        risco="A terra fica sem plantas na seca: o solo pode ser levado pela chuva e perder fertilidade."),
    SAFRINHA: dict(agua=1, cobertura=3, renda=5, solo=3,
        bom="É uma segunda colheita no ano: a opção que mais rende.",
        risco="Depende da chuva de abril e maio; se faltar chuva, pode perder a safra."),
    "Soja e depois milho com capim (braquiária)": dict(agua=2, cobertura=5, renda=4, solo=4,
        bom="Cobre bem a terra, segura a água e deixa o solo mais fértil; o capim ainda pode virar pasto.",
        risco="Pede mais manejo (capim e palhada) e algum investimento no começo."),
    "Soja e depois plantas de cobertura (milheto/crotalária)": dict(agua=3, cobertura=4, renda=1, solo=5,
        bom="Protege o solo, e a crotalária ajuda a adubar a terra com nitrogênio.",
        risco="Não dá renda direta; precisa planejar bem o plantio e o manejo."),
}
PRIOR = {"💧 Gastar menos água": "agua", "🛡️ Proteger o solo da erosão": "cobertura",
         "💰 Ganhar mais": "renda", "🌿 Deixar a terra mais fértil": "solo"}

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

def baseline(geom):
    def f(m):
        m = ee.Number(m)
        img = (MOD.select("NDVI").filter(ee.Filter.calendarRange(2015, 2024, "year"))
               .filter(ee.Filter.calendarRange(m, m, "month")).mean())
        return ee.Feature(None, {"mo": m, "v": img.reduceRegion(ee.Reducer.mean(), geom, 250, maxPixels=1e9).get("NDVI")})
    feats = ee.FeatureCollection(ee.List.sequence(1, 12).map(f)).getInfo()["features"]
    return {int(x["properties"]["mo"]): x["properties"]["v"] * 1e-4 for x in feats if x["properties"].get("v") is not None}

@st.cache_data(show_spinner=False)
def analyze(gj):
    geom = ee.Geometry(json.loads(gj))
    ndvi = month_series(lambda s, e: MOD.filterDate(s, e).select("NDVI").mean(), "NDVI", geom, 250) * 1e-4
    base = baseline(geom)
    # IMERG mensal vem em mm/h -> mm/mês; ponto central porque o pixel (~11 km) é maior que a área
    rain = month_series(lambda s, e: IMERG.filterDate(s, e).select("precipitation").mean(), "precipitation",
                        geom.centroid(1), 11132)
    rain = pd.Series({k: v * 24 * calendar.monthrange(int(k[:4]), int(k[5:]))[1] for k, v in rain.items()})
    sar = month_series(lambda s, e: lee(s1(geom).filterDate(s, e).median()).rename("VH"), "VH", geom, 30)
    slope = ee.Terrain.slope(ee.Image("USGS/SRTMGL1_003")).reduceRegion(ee.Reducer.mean(), geom, 30).get("slope").getInfo()
    df = pd.DataFrame({"NDVI": ndvi, "NDVI médio 2015-2024": [base.get(int(k[5:])) for k in ndvi.index],
                       "Chuva (mm)": rain, "Radar VH filtrado (dB)": sar})
    return df, slope, geom.area(1).getInfo() / 1e4

@st.cache_data(show_spinner=False)
def layers(gj):
    geom = ee.Geometry(json.loads(gj)); box = geom.buffer(1500).bounds()
    end = ee.Date(TODAY.isoformat())
    hls = (ee.ImageCollection("NASA/HLS/HLSL30/v002").filterBounds(geom).filterDate(end.advance(-120, "day"), end)
           .map(lambda i: i.updateMask(i.select("Fmask").bitwiseAnd(14).eq(0)).normalizedDifference(["B5", "B4"]))
           .median().clip(box))
    raw = s1(geom).filterDate(end.advance(-60, "day"), end).median()
    out = {"Saúde da vegetação (verde = saudável)": (hls, NDVI_VIS), "Radar (bruto, com ruído)": (raw.clip(box), SAR_VIS),
           "Radar (filtrado)": (lee(raw).clip(box), SAR_VIS),
           "O que tem na área (água, construção, mata…)": (aptidao.dynamic_world(geom, TODAY.isoformat()).clip(box),
                                                         {"min": 0, "max": 8, "palette": aptidao.DW_PALETA})}
    return {k: i.getMapId(v)["tile_fetcher"].url_format for k, (i, v) in out.items()}

@st.cache_data(show_spinner=False)
def cobertura(gj):
    return aptidao.calcular(ee.Geometry(json.loads(gj)), TODAY.isoformat())

@st.cache_data(ttl=6 * 3600, show_spinner=False)
def enso_atual():
    return enso.obter()

@st.cache_data(show_spinner=False)
def zarc_base():
    return zarc.carregar()

# Limites do DF (o ZARC usado é o do município de Brasília, que cobre o DF inteiro)
DF_LIMITES = (-16.05, -15.50, -48.29, -47.31)

def dentro_df(gj):
    pts = json.loads(gj)["coordinates"][0]
    lat = sum(p[1] for p in pts) / len(pts); lon = sum(p[0] for p in pts) / len(pts)
    return DF_LIMITES[0] <= lat <= DF_LIMITES[1] and DF_LIMITES[2] <= lon <= DF_LIMITES[3]

def score(ev, chave, atual):
    # a prioridade escolhida pesa 10x mais que as outras: assim cada escolha muda de fato o resultado
    w, notes = {k: (5 if k == chave else 0.5) for k in ('agua', 'cobertura', 'renda', 'solo')}, []
    en = ev.get("enso")
    nino_forte = en and en["fase"] == "El Niño" and en["intensidade"] in ("moderado", "forte", "muito forte")
    if nino_forte:
        w["agua"] *= 1.3; w["cobertura"] *= 1.2
        notes.append(f"O Pacífico está em **{enso.rotulo(en)}** (ONI {en['oni']:+.1f} °C): no DF isso costuma trazer "
                     "chuva irregular e veranicos, então economizar água e manter palhada no solo vale mais.")
    elif en and en["fase"] == "La Niña":
        notes.append(f"O Pacífico está em **{enso.rotulo(en)}** (ONI {en['oni']:+.1f} °C): tendência de chuvas mais "
                     "regulares no Centro-Oeste, mas fique atento a um fim de chuvas mais cedo.")
    if ev["slope"] and ev["slope"] > 5:
        w["cobertura"] *= 1.5; notes.append(f"Sua terra é inclinada (cerca de {ev['slope']:.0f}°): a chuva pode levar o solo, então manter a terra coberta é importante.")
    if ev["bare"] >= 3:
        w["cobertura"] *= 1.3; notes.append(f"No último ano, sua área ficou com pouca vegetação por cerca de {ev['bare']} meses.")
    if ev["anom"] is not None and ev["anom"] < -0.05:
        w["agua"] *= 1.3; notes.append("A vegetação nos últimos meses está mais fraca do que o normal para a época.")
    rows = []
    for n, a in STRATS.items():
        s = sum(w[k] * a[k] for k in w) / (5 * sum(w.values())) * 100
        if n == SAFRINHA and ev["rain_am"] is not None and ev["rain_am"] < 150:
            s -= 15; notes.append(f"Choveu pouco em abril e maio ({ev['rain_am']:.0f} mm): o milho safrinha fica arriscado.")
        if n == SAFRINHA and nino_forte:
            s -= 10
        # ZARC: há como encaixar soja + 2ª safra no calendário oficial, com o risco aceito para o clima do ano?
        z = ev.get("zarc") or {}
        if n in z and z[n] is not None and len(z[n]) == 0:
            s -= 20
            notes.append(f"Pelo ZARC de Brasília, com o risco máximo de {ev['zarc_tol']}% aceito neste ano, não há janela "
                         f"segura para *{n.lower()}* no seu tipo de solo: a 2ª safra ficaria fora do zoneamento (sem seguro/Proagro).")
        rows.append({"nome": n, "pontos": max(round(s), 0)})
    return sorted(rows, key=lambda r: -r["pontos"]), notes

def farol(v, bom, medio, alto_bom=True):
    """Devolve 🟢/🟡/🔴 conforme o valor."""
    ok = (v >= bom) if alto_bom else (v <= bom)
    meio = (v >= medio) if alto_bom else (v <= medio)
    return "🟢" if ok else ("🟡" if meio else "🔴")

# --- MAPA ---
import json, folium
from folium.plugins import Draw
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
COR = "#ff5a1f"

def construir_mapa(gj, urls):
    # obs.: com tiles=None o folium ignora min_zoom/max_zoom; os limites de zoom são aplicados em _LimitarMapa
    m = folium.Map(location=[-15.78, -47.80], zoom_start=10, tiles=None, max_bounds=True, **DF_BOUNDS)
    folium.TileLayer("OpenStreetMap", name="Mapa", no_wrap=True, show=False).add_to(m)
    folium.TileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                     attr="Esri", name="Satélite", no_wrap=True).add_to(m)
    _TextoPT().add_to(m)  # antes do Draw
    Draw(draw_options={"polyline": False, "circle": False, "marker": False, "circlemarker": False,
                       "polygon": {"allowIntersection": False, "shapeOptions": {"color": COR, "weight": 3},
                                   "drawError": {"color": "#e1e100", "message": "As linhas não podem se cruzar."}},
                       "rectangle": {"shapeOptions": {"color": COR, "weight": 3}}},
         edit_options={"edit": False, "remove": False}).add_to(m)  # apagar é pelo botão "Apagar área"
    if gj:
        geo = json.loads(gj)
        pts = geo["coordinates"][0]
        folium.GeoJson(geo, name="Sua área", style_function=lambda _: {"color": COR, "weight": 3, "fillOpacity": 0.08}).add_to(m)
        for name, url in urls.items():
            folium.TileLayer(tiles=url, attr="Google Earth Engine", name=name, overlay=True,
                             show=name.startswith("Saúde")).add_to(m)
        m.fit_bounds([[min(p[1] for p in pts), min(p[0] for p in pts)], [max(p[1] for p in pts), max(p[0] for p in pts)]],
                     padding=(40, 40), max_zoom=16)  # centraliza no polígono desenhado
    else:
        m.fit_bounds([[-16.07, -48.30], [-15.48, -47.30]])  # começa mostrando o DF inteiro
    folium.LayerControl(collapsed=True).add_to(m)
    _LimitarMapa(DF_BOUNDS, ZOOM_MIN).add_to(m)
    return m
# --- FIM MAPA ---

# ---------------- Interface ----------------
st.title("🌱 Qual cultura plantar na sua terra?")
st.caption("Marque sua propriedade no mapa e receba uma sugestão baseada em dados de satélite da NASA.")
st.session_state.setdefault("map_v", 0)

def limpar():
    st.session_state["gj"] = None
    st.session_state["map_v"] += 1  # novo mapa: some o desenho e volta a mostrar o DF

left, right = st.columns([3, 2], gap="large")
gj = st.session_state.get("gj")

with left:
    try:
        urls = layers(gj) if gj else {}
    except Exception:
        urls = {}  # o mapa continua funcionando sem as camadas
    out = st_folium(construir_mapa(gj, urls), height=560, use_container_width=True,
                    key=f"mapa{st.session_state['map_v']}", returned_objects=["last_active_drawing"])
    d = (out or {}).get("last_active_drawing")
    if d and json.dumps(d["geometry"]) != gj:
        st.session_state["gj"] = json.dumps(d["geometry"]); st.rerun()

with right:
    st.markdown("### 1️⃣ Marque sua terra")
    st.write("No mapa, use os botões no canto esquerdo: **⬠ desenhar a área** (clique nos cantos e termine no primeiro ponto) "
             "ou **▭ retângulo** (clique e arraste).")
    if not gj:
        st.info("👈 Desenhe sua área no mapa para continuar.")
        st.stop()
    st.success("Área marcada! O mapa já centralizou nela.")
    st.button("🗑️ Apagar área e recomeçar", on_click=limpar)

    st.markdown("### 2️⃣ Conte um pouco sobre você")
    atual = st.selectbox("Hoje, na época da seca, depois da soja, você…", list(STRATS))
    prior = st.radio("O que é mais importante para você?", list(PRIOR))
    solo = st.selectbox("Como é o solo da sua terra?", list(zarc.SOLOS),
                        help="Usado para consultar o ZARC (calendário oficial de plantio do Ministério da Agricultura). "
                             "AD = água disponível no solo: AD1 segura pouca água, AD6 segura muita.")

# daqui para baixo os resultados usam a largura toda da página
try:
    with st.spinner("Olhando sua terra pelo satélite… (pode levar um minuto)"):
        df, slope, ha = analyze(gj)
except Exception as e:
    st.error("Não consegui buscar os dados agora. Tente de novo ou desenhe uma área menor.")
    with st.expander("Detalhe técnico do erro"):
        st.code(str(e))
    st.stop()
if ha > 5000:
    st.warning("Essa área é muito grande (mais de 5.000 hectares). Desenhe uma área menor."); st.stop()
if ha < 6:
    st.warning("Área pequena: os satélites têm pouco detalhe para ela. Use o resultado como uma indicação geral.")

# --- O satélite olha o que existe na área: dá para plantar? ---
st.divider()
st.markdown("### 🛰️ Dá para plantar nessa área?")
try:
    with st.spinner("Vendo o que existe na área (construções, água, mata)…"):
        cob = cobertura(gj)
except Exception as e:
    cob = None
    with st.expander("Não consegui identificar a cobertura da área (detalhe técnico)"):
        st.code(str(e))
apt = aptidao.avaliar(cob, slope, ha)
va, vb = st.columns([3, 2], gap="large")
with va:
    lista = "\n".join(f"- {m}" for m in apt["motivos"])
    if apt["nivel"] == "inapta":
        st.error("**Esta área não parece própria para plantio.**\n\n" + lista, icon="🔴")
    elif apt["nivel"] == "restricao":
        mil = lambda v: f"{v:,.0f}".replace(",", ".")
        st.warning(f"**Pode plantar, com cuidados.** Área plantável estimada: "
                   f"**{mil(apt['util_ha'])} ha** de {mil(ha)} ha.\n\n" + lista, icon="🟡")
    elif apt["nivel"] == "apta":
        st.success(f"**Área apta para plantio.** O satélite viu lavoura, pasto ou campo em "
                   f"{apt['frac_util']:.0%} da área, sem construções, água ou áreas protegidas relevantes.", icon="🟢")
    else:
        st.info(lista, icon="⚪")
    st.caption("Fontes: Dynamic World (Sentinel-2, 10 m, últimos 6 meses), JRC Global Surface Water e WDPA. "
               "O satélite pode confundir classes (ex.: pasto seco com solo exposto); confira no mapa pela camada "
               "\"O que tem na área\".")
with vb:
    if cob and not cob.get("sem_dado"):
        tab = pd.Series({aptidao.NOMES[k]: v * 100 for k, v in cob["cobertura"].items() if v >= 0.005}).sort_values()
        st.bar_chart(tab, horizontal=True, height=220, x_label="% da área")
        st.markdown("Cores da camada no mapa: " + " ".join(
            f"<span style='color:#{c}'>■</span> {aptidao.NOMES[k]}" for k, c in zip(aptidao.DW_CLASSES[:8], aptidao.DW_PALETA)),
            unsafe_allow_html=True)
if apt["nivel"] == "inapta" and not st.checkbox("Mostrar a sugestão de rotação mesmo assim (sei que a área é plantável)"):
    st.stop()

ndvi = df["NDVI"]
rain_am = df["Chuva (mm)"][[i for i in df.index if i[5:] in ("04", "05")]].dropna()
anom = (ndvi - df["NDVI médio 2015-2024"]).tail(3).mean()
ev = {"slope": slope, "bare": int((ndvi < 0.3).sum()), "anom": None if pd.isna(anom) else float(anom),
      "rain_am": rain_am.sum() if len(rain_am) == 2 else None}

# El Niño / La Niña + ZARC Brasília
ev["enso"] = enso_atual()
zdf = zarc_base()
ev["zarc_tol"] = zarc.tolerancia(ev["enso"])
safra = zarc.safras(zdf)[0] if zdf is not None else None
if zdf is not None:
    ev["zarc"] = {
        SAFRINHA: zarc.combinar_soja_milho(zdf, solo, safra, "Milho 2ª Safra", ev["zarc_tol"]),
        "Soja e depois milho com capim (braquiária)":
            zarc.combinar_soja_milho(zdf, solo, safra, "Milho 2ª Safra consorciado com Braquiária", ev["zarc_tol"]),
    }

st.divider()
st.markdown(f"### 3️⃣ O que vemos na sua terra ({ha:,.0f} hectares)".replace(",", "."))
c1, c2, c3 = st.columns(3)
c4, c5, c6 = st.columns(3)  # 2 linhas de 3: em 6 colunas os textos ficam cortados
c1.metric("Vegetação agora", "sem dado" if ev["anom"] is None else
          f"{farol(ev['anom'], -0.05, -0.10)} " + ("Normal" if ev["anom"] >= -0.05 else "Abaixo do normal" if ev["anom"] >= -0.10 else "Bem fraca"))
c2.metric("Terra sem plantas", f"{farol(ev['bare'], 1, 3, False)} {ev['bare']} meses/ano")
c3.metric("Terreno", "sem dado" if slope is None else f"{farol(slope, 3, 6, False)} " + ("Plano" if slope <= 3 else "Inclinado" if slope <= 6 else "Muito inclinado"))
c4.metric("Chuva abril+maio", "sem dado" if ev["rain_am"] is None else
          f"{farol(ev['rain_am'], 200, 150)} {ev['rain_am']:.0f} mm")
en = ev["enso"]
icone = "⚪" if en is None else {"El Niño": "🔴", "La Niña": "🔵", "Neutro": "🟢"}[en["fase"]]
c5.metric("Pacífico (El Niño / La Niña)", f"{icone} {enso.rotulo(en)}",
          None if en is None else f"ONI {en['oni']:+.2f} °C ({en['trimestre']})", delta_color="off")
zs = (ev.get("zarc") or {}).get(SAFRINHA)
c6.metric("ZARC: soja + milho safrinha", "sem dado" if zs is None else
          ("🟢 Cabe no calendário" if zs else "🔴 Fora do calendário"),
          f"risco aceito até {ev['zarc_tol']}%", delta_color="off")
if not dentro_df(gj):
    st.warning("Sua área parece estar fora do DF. O ZARC mostrado é o de Brasília; confira o do seu município.")

st.divider()
rank, notes = score(ev, PRIOR[prior], atual)
pts = {r["nome"]: r["pontos"] for r in rank}
top = rank[0]["nome"]
st.markdown("## ✅ Nossa sugestão")
if top == atual:
    st.success(f"**Continue com: {top}.** Para o que você prioriza, sua rotação atual já é a mais indicada.")
else:
    st.success(f"**Mudar para: {top}.**  \n(Hoje: {atual})")
st.caption(f"Nota para o que você prioriza: {pts[top]}/100 · sua rotação atual: {pts[atual]}/100")
a, b = st.columns(2)
with a:
    st.markdown("**Por que?**")
    st.markdown(f"- {STRATS[top]['bom']}")
    for n in notes:
        st.markdown(f"- {n}")
with b:
    st.markdown("**Cuidados**")
    st.markdown(f"- {STRATS[top]['risco']}")
    st.markdown("**Outras opções**")
    for r in rank[1:]:
        dif = r["pontos"] - pts[atual]
        extra = " _(a que você faz hoje)_" if r["nome"] == atual else f" _({dif:+d} pontos em relação a hoje)_"
        st.markdown(f"{'⭐' * max(round(r['pontos'] / 20), 1)} {r['nome']}{extra}")
st.info("💡 Esta é uma ajuda para pensar, não uma receita. Não consideramos análise do seu solo, preços, máquinas e custos. "
        "Converse com um agrônomo ou com a Emater-DF antes de decidir.")

st.markdown("## 🌦️ Clima do ano e calendário oficial de plantio (Brasília/DF)")
ce, cz = st.columns([2, 3], gap="large")
with ce:
    st.markdown("**El Niño / La Niña agora**")
    if en is None:
        st.info("Não consegui buscar o índice da NOAA agora. Tente de novo mais tarde.")
    else:
        st.markdown(f"### {icone} {enso.rotulo(en)}")
        tend = "esquentando" if en["tendencia"] > 0.2 else "esfriando" if en["tendencia"] < -0.2 else "estável"
        st.caption(f"ONI {en['oni']:+.2f} °C no trimestre {en['trimestre']} · {en['seq']} trimestre(s) seguidos "
                   f"além de ±0,5 °C · tendência: {tend}")
        st.write(en["impacto"])
        _cm = {s: i + 1 for i, s in enumerate(["DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ", "JJA", "JAS", "ASO", "SON", "OND", "NDJ"])}
        hist = pd.DataFrame({"ONI (°C)": [v for _, _, v in en["historico"]]},
                            index=pd.to_datetime([f"{a}-{_cm[s]:02d}-01" for s, a, _ in en["historico"]]))
        hist["El Niño (+0,5)"], hist["La Niña (−0,5)"] = 0.5, -0.5
        st.line_chart(hist, height=200)
        st.caption("Fonte: NOAA/CPC, Oceanic Niño Index (região Niño 3.4).")
with cz:
    st.markdown("**ZARC: quando plantar com menor risco**")
    if zdf is None:
        st.info("Base do ZARC não encontrada. Rode `python scripts/atualizar_zarc.py` para baixá-la.")
    else:
        st.caption(f"Safra {safra}/{int(safra) + 1} · solo: {solo} · sequeiro. Números = chance de perda (%) "
                   f"se plantar naquele decêndio; vazio = fora do zoneamento. Neste ano aceitamos até **{ev['zarc_tol']}%**"
                   + (" por causa do El Niño." if ev["zarc_tol"] == 20 else "."))
        cal = zarc.calendario(zdf, solo, safra, ["Soja", "Milho 2ª Safra", "Milho 2ª Safra consorciado com Braquiária",
                                                  "Sorgo Granífero 2ª Safra", "Feijão 2ª Safra", "Milho 1ª Safra"])
        cal = cal.loc[:, (cal > 0).any()]  # só os decêndios com alguma indicação
        tol = ev["zarc_tol"]
        cor = lambda v: ("" if v == 0 else "background-color:#1a9850;color:white" if v <= 20 else
                         "background-color:#fee08b" if v <= tol else "background-color:#f4a582")
        st.dataframe(cal.style.map(cor).format(lambda v: f"{v}%" if v else ""), width="stretch")
        for nome, combos in ev["zarc"].items():
            if combos:
                c = combos[0]
                st.markdown(f"✅ **{nome}**: plante soja **{c['soja_ciclo']}** em **{zarc.nome_dec(c['soja_dec'])}** "
                            f"(risco {c['soja_risco']}%) → colheita e 2ª safra por volta de **{zarc.nome_dec(c['milho_dec'])}** "
                            f"(risco {c['milho_risco']}%).")
            else:
                st.markdown(f"⚠️ **{nome}**: sem combinação soja → 2ª safra dentro de {tol}% de risco neste solo.")
        st.caption("Fonte: MAPA, Tábua de Risco do ZARC (dados abertos). A data de colheita da soja é estimada pelo "
                   "grupo (≈100/120/130 dias); confira o ciclo da sua cultivar. Plantas de cobertura (milheto, "
                   "crotalária) não têm ZARC próprio.")

with st.expander("📊 Ver detalhes técnicos (gráficos e dados)"):
    st.caption("Dados: NASA MODIS (vegetação), NASA GPM (chuva), NASA SRTM (relevo) e radar Sentinel-1 da ESA com filtro Lee, "
               "que enxerga através das nuvens. No mapa, use o ícone de camadas para ver o radar bruto e o filtrado.")
    t1, t2 = st.columns(2)
    t1.markdown("**Vegetação (NDVI): este ano vs. média 2015–2024**")
    t1.line_chart(df[["NDVI", "NDVI médio 2015-2024"]])
    t2.markdown("**Chuva mensal (mm)**")
    t2.bar_chart(df["Chuva (mm)"])
    st.markdown("**Radar VH filtrado (dB)**: queda forte pode indicar colheita ou solo exposto")
    st.line_chart(df["Radar VH filtrado (dB)"])
