"""Issue #12: fontes servidas pelo próprio app (static/), sem `@import` nem requisição ao Google Fonts."""
import pathlib
import re
import tomllib

import pytest
from streamlit.web.server.component_file_utils import guess_content_type

RAIZ = pathlib.Path(__file__).resolve().parents[1]
STATIC = RAIZ / "static"
FAMILIAS = {"Bricolage Grotesque": "500 700", "Public Sans": "400 600"}


@pytest.fixture
def css(app):
    """O `<style>` que o app injeta na página."""
    app.run()
    estilo = [t for t in app.textos() if t.startswith("<style>")]
    assert len(estilo) == 1
    return estilo[0]


def font_faces(css):
    return {re.search(r"font-family:'([^']+)'", b).group(1): b for b in re.findall(r"@font-face\{([^}]*)\}", css)}


def test_css_nao_importa_nada_nem_chama_o_google_fonts(css):
    assert "@import" not in css
    assert "googleapis" not in css and "gstatic" not in css
    # toda URL do CSS aponta para os arquivos do próprio app
    assert all(u.startswith("app/static/") for u in re.findall(r"url\('?([^')]+)", css))


def test_cada_familia_tem_font_face_local_com_swap_e_so_os_pesos_usados(css):
    faces = font_faces(css)
    assert set(faces) == set(FAMILIAS)
    for familia, pesos in FAMILIAS.items():
        face = faces[familia]
        assert "font-display:swap" in face
        assert f"font-weight:{pesos};" in face
        arquivo = re.search(r"url\('app/static/([^']+)'\) format\('woff2'\)", face).group(1)
        assert (STATIC / arquivo).read_bytes()[:4] == b"wOF2"  # é woff2 de verdade


def test_titulos_em_bricolage_texto_em_public_sans_com_as_reservas_de_sistema(css):
    regras = dict(re.findall(r"\n([^{}\n@]+)\{font-family:([^;}]+)", css))
    titulos = next(v for k, v in regras.items() if ".marca" in k)
    assert {"h2", ".marca", ".passo span"} <= set(next(k for k in regras if ".marca" in k).split(","))
    assert titulos == "'Bricolage Grotesque',system-ui,sans-serif"
    assert regras[".stApp,.stMarkdown,.stMarkdown p,label,button,input,[data-baseweb]"] == \
        "'Public Sans',system-ui,sans-serif"


def test_acentos_do_portugues_estao_no_unicode_range_das_fontes(css):
    for face in font_faces(css).values():
        faixas = []
        for item in re.search(r"unicode-range:([^;}]+)", face).group(1).split(","):
            ini, _, fim = item.strip()[2:].partition("-")
            faixas.append((int(ini, 16), int(fim or ini, 16)))
        for c in "RotaDF Distrito Federal Saúde época atenção ÇÃÕÁÉÍÓÚÂÊÔÀ ãõâêôà – …":
            assert any(a <= ord(c) <= b for a, b in faixas), c


def test_config_liga_o_static_serving():
    config = tomllib.loads((RAIZ / ".streamlit" / "config.toml").read_text())
    assert config["server"]["enableStaticServing"] is True


def test_woff2_sai_com_content_type_de_fonte():
    # mesma função que o Streamlit usa para responder em app/static/
    for arquivo in STATIC.glob("*.woff2"):
        assert guess_content_type(str(arquivo)) == "font/woff2"


@pytest.mark.parametrize("arquivo", ["OFL-BricolageGrotesque.txt", "OFL-PublicSans.txt"])
def test_licenca_ofl_das_fontes_esta_em_static(arquivo):
    assert "SIL Open Font License, Version 1.1" in (STATIC / arquivo).read_text()
