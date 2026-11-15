# 🌱 RotaDF

Aplicação para análise de propriedades rurais e sugestão de rotação de culturas no Distrito Federal, utilizando dados de satélite e indicadores ambientais.

## Tecnologias

- Python
- Google Earth Engine
- Streamlit
- Folium
- Pandas

## Como executar

### 1. Clone o repositório

```bash
git clone URL_DO_REPOSITORIO
cd NOME_DO_REPOSITORIO
```

### 2. Crie e ative um ambiente virtual

```bash
python -m venv venv
```

**Windows:**
```bash
venv\Scripts\activate
```

**Linux/Mac:**
```bash
source venv/bin/activate
```

### 3. Instale as dependências

```bash
pip install -r requirements.txt
```

### 4. Configure o Google Earth Engine

É necessário possuir uma conta com acesso ao Google Earth Engine e um projeto Google Cloud configurado.

Realize a autenticação:

```bash
earthengine authenticate
```

Crie um arquivo `.env` na raiz do projeto:

```env
GEE_PROJECT=seu-projeto-google-cloud
```

### 5. Execute a aplicação

```bash
streamlit run app.py
```

A aplicação será aberta no navegador.

## Como utilizar

1. Desenhe a área da propriedade no mapa.
2. Informe a rotação de culturas atual.
3. Selecione sua prioridade.
4. Consulte a recomendação e os indicadores ambientais.

## Dá para plantar nessa área? (`aptidao.py`)

Antes da sugestão, o satélite identifica o que existe na terra marcada:

- **Dynamic World** (Sentinel-2, 10 m, classe mais frequente nos últimos 6 meses): construções, água, brejo/vereda,
  árvores, cerrado arbustivo, lavoura, pasto e solo exposto.
- **JRC Global Surface Water**: água permanente e áreas que alagam em alguns anos.
- **WDPA**: unidades de conservação de proteção integral (ex.: Parque Nacional de Brasília).

Resultado: 🟢 apta, 🟡 com cuidados (com a área plantável estimada) ou 🔴 não recomendada (≥ 30% construído,
≥ 30% água ou brejo, dentro de UC de proteção integral ou quase só vegetação nativa). Com 🔴 a sugestão fica
escondida até o produtor marcar que sabe que a área é plantável.

## El Niño / La Niña (`enso.py`)

A fase do ENSO vem do **ONI** da NOAA/CPC (`oni.ascii.txt`, cache de 6 h): El Niño com ONI ≥ +0,5 °C, La Niña com
≤ −0,5 °C, confirmado depois de 5 trimestres seguidos (antes disso, "em formação"). Com El Niño moderado ou mais
forte e sem irrigação, culturas de alta demanda de água perdem pontos, as de baixa ganham, e o ZARC passa a aceitar
só datas com risco de 20%.

## Calendário oficial ZARC de Brasília (`zarc.py`)

Usa a **Tábua de Risco do ZARC** (MAPA, dados abertos) do município de Brasília (IBGE 5300108), que cobre o DF,
gravada em `data/zarc_brasilia.csv`. Soja, milho, feijão e sorgo são conferidos pelo tipo de solo (AD1 a AD6,
perguntado no passo 2) e pelo mês de plantio: fora do zoneamento a cultura perde 30 pontos (sem seguro rural/Proagro);
com risco acima do aceito no ano, 10. Para atualizar quando sair uma safra nova (o arquivo nacional é grande):

```bash
python scripts/atualizar_zarc.py 2026-2027
```

Uma vez por ano, em janeiro, atualize a climatologia local da NASA POWER com `python scripts/gerar_clima_df.py --ano-fim <ano-anterior>` e versione `data/clima_power_df.json` (a fonte mostra o período gravado no arquivo).
