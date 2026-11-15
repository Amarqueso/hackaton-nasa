"""Issue #3: a umidade do solo vem do FLDAS mensal, numa única consulta, com o percentil calculado em Python."""
import pytest

from fake_ee import SEM_V, serie_mensal
from test_coletar import dados

SOLO = "Umidade do solo 0–10 cm"
LINHA = "Umidade do solo"


def solo(app, valor, **kw):
    app.ee.respostas["FLDAS"] = lambda: serie_mensal(valor, **kw)


def falhas(app):
    return [c for c in app.legendas() if c.startswith("Falha em")]


def metodo(app):
    return next(m.value for m in app.at.expander[0].markdown if m.value.startswith("**Método.**"))


@pytest.mark.parametrize("abaixo, classe", [
    (2, "Bem mais seca que o normal"),        # 2/16 = 0,125
    (5, "Um pouco mais seca que o normal"),   # 5/16 = 0,31
    (8, "Normal"),                            # 8/16 = 0,5
    (12, "Mais úmida que o normal"),          # 12/16 = 0,75
])
def test_linha_mostra_uma_das_quatro_classes(app, abaixo, classe):
    # 2010-2025: 16 setembros; o de 2026 (último mês) fica acima de `abaixo` deles
    solo(app, lambda a, m: abaixo - 0.5 if a == 2026 else float(a - 2010))
    app.marcar_terra((-15.68, -48.20))  # perto de Brazlândia
    assert dados(app)[LINHA] == classe + " (setembro de 2026)"
    assert not falhas(app)


def test_uma_unica_consulta_ao_earth_engine(app):
    app.marcar_terra()
    assert app.ee.chamadas["FLDAS"] == 1
    assert app.ee.chamadas["GLDAS"] == 0
    assert dados(app)[LINHA] == "Normal (setembro de 2026)"


def test_textos_de_fonte_e_metodo(app):
    app.marcar_terra()
    html = next(t for t in app.textos() if 'class="dados"' in t)
    assert "NASA FLDAS, ~11 km, vale para a região" in html and "GLDAS" not in html
    df = app.at.expander[0].dataframe[0].value
    fonte = df.set_index("Dado").loc[SOLO, "Fonte / resolução"]
    assert "FLDAS" in fonte and "0,1°" in fonte and "GLDAS" not in fonte
    m = metodo(app)
    assert "resolução (10–50 km)" in m and "25–50" not in m
    assert "umidade do solo abaixo do percentil 30 (sem irrigação)" in m


def test_mes_recente_nao_publicado_usa_o_ultimo_com_dado(app):
    """Hoje o FLDAS vai até 2026-09; outubro e novembro não existem ou vêm mascarados."""
    def valor(a, m):
        if (a, m) >= (2026, 10):
            return None
        if m == 9:  # setembro de 2026 é o mais seco de todos os setembros
            return 0.05 if a == 2026 else 0.30
        return 0.90
    solo(app, valor, ate=(2026, 11))
    app.marcar_terra()
    assert dados(app)[LINHA] == "Bem mais seca que o normal (setembro de 2026)"
    assert not falhas(app)
    assert app.fontes()[SOLO] == "sim"


def test_anos_sem_dado_no_mes_sao_ignorados(app):
    # setembros: 2026 = 0,5; 2010-2015 sem dado (nulo ou sem 'v'); 2016-2025 = 0,4 ou 0,6 meio a meio
    def valor(a, m):
        if m != 9:
            return 0.0
        if a == 2026:
            return 0.5
        if a < 2013:
            return None
        if a < 2016:
            return SEM_V
        return 0.4 if a % 2 else 0.6
    solo(app, valor)
    app.marcar_terra()
    # se os anos sem dado contassem como 0 (ou abaixo), seriam 11/16 -> "Mais úmida"; ignorados, 5/10 -> "Normal"
    assert dados(app)[LINHA] == "Normal (setembro de 2026)"
    assert not falhas(app)


def test_ultimo_dado_do_ano_anterior_nao_e_comparado_com_ele_mesmo(app):
    """Em janeiro/fevereiro o último dado é de dezembro do ano passado: a base vai de 2010 até o ano anterior a ele."""
    def valor(a, m):
        if m != 12:
            return 0.0
        return 6.5 if a == 2025 else float(a - 2010)  # 6 dos 15 dezembros (2010-2024) abaixo
    solo(app, valor, ate=(2025, 12))
    app.marcar_terra()
    # 6/15 = 0,4 -> "Normal"; se 2025 entrasse na base seria 6/16 = 0,375 -> "Um pouco mais seca"
    assert dados(app)[LINHA] == "Normal (dezembro de 2025)"


def test_ponto_sem_dado_mostra_sem_dado_e_confianca_media(app):
    solo(app, lambda a, m: None)  # ex.: Lago Paranoá, mascarado como água
    app.marcar_terra((-15.80, -47.83))
    tela = dados(app)
    assert tela[LINHA] is None
    assert all(v is not None for k, v in tela.items() if k != LINHA)
    assert app.fontes()[SOLO] == "NÃO"
    assert any("Confiança da sugestão: média" in t for t in app.recomendacao())
    assert not falhas(app)


@pytest.mark.parametrize("mes, nome", [(1, "janeiro"), (3, "março"), (11, "novembro"), (12, "dezembro")])
def test_linha_identifica_o_mes_do_dado_e_preserva_a_confianca(app, mes, nome):
    """O último mês disponível, inclusive o atual, é identificado sem mudar a avaliação."""
    solo(app, lambda a, m: 0.5 if a == 2026 else (0.4 if a % 2 else 0.6), ate=(2026, mes))
    app.marcar_terra()
    assert dados(app)[LINHA] == f"Normal ({nome} de 2026)"
    assert not any("Umidade do solo agora" in texto for texto in app.textos())
    assert app.fontes()[SOLO] == "sim"
    assert any("Confiança da sugestão: alta" in texto for texto in app.recomendacao())


def test_mes_de_referencia_nao_muda_a_recomendacao(app):
    solo(app, lambda a, m: 0.5 if a == 2026 else (0.4 if a % 2 else 0.6), ate=(2026, 9))
    app.marcar_terra()
    antes = [t for t in app.recomendacao() if 'class="dados"' not in t]
    solo(app, lambda a, m: 0.5 if a == 2026 else (0.4 if a % 2 else 0.6), ate=(2026, 11))
    import streamlit as st
    st.cache_data.clear()
    app.run()
    depois = [t for t in app.recomendacao() if 'class="dados"' not in t]
    assert dados(app)[LINHA] == "Normal (novembro de 2026)"
    assert antes == depois
