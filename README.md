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
3. Selecione sua prioridade e o tipo de solo.
4. Consulte a recomendação, os indicadores ambientais, a fase do El Niño/La Niña e o calendário ZARC.

## Dá para plantar nessa área? (`aptidao.py`)

Antes da sugestão, o satélite identifica o que existe dentro do polígono:

- **Dynamic World** (Sentinel-2, 10 m, classe mais frequente nos últimos 6 meses): construções, água,
  brejo/vereda, árvores, cerrado arbustivo, lavoura, pasto e solo exposto.
- **JRC Global Surface Water**: água permanente e áreas que alagam em alguns anos.
- **WDPA**: unidades de conservação de proteção integral (ex.: Parque Nacional de Brasília).

Resultado: 🟢 apta, 🟡 com restrições (mostra a área plantável estimada) ou 🔴 não recomendada
(≥ 30% construído, ≥ 30% água ou brejo, dentro de UC de proteção integral ou quase só vegetação nativa).
Com 🔴 a sugestão de rotação fica oculta, mas o produtor pode exibi-la mesmo assim. No mapa, a camada
"O que tem na área" mostra a classificação.

## El Niño / La Niña (`enso.py`)

A fase do ENSO é detectada pelo **ONI** (Oceanic Niño Index) da NOAA/CPC, baixado ao vivo
(`https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt`, cache de 6 h):

- **El Niño**: ONI ≥ +0,5 °C · **La Niña**: ONI ≤ −0,5 °C · **Neutro**: entre os dois.
- Confirmado após 5 trimestres seguidos além do limiar; antes disso aparece como "em formação".
- Intensidade: fraco (0,5), moderado (1,0), forte (1,5), muito forte (≥ 2,0).

Efeito na recomendação: com El Niño moderado ou mais forte, o app dá mais peso a economia de água e
cobertura do solo, penaliza o milho safrinha e aceita só decêndios do ZARC com risco de 20%.

## ZARC Brasília/DF (`zarc.py`)

Usa a **Tábua de Risco do ZARC** (MAPA, dados abertos) filtrada para Brasília (IBGE 5300108),
salva em `data/zarc_brasilia.csv`. Para atualizar (o arquivo nacional é grande, demora alguns minutos):

```bash
python scripts/atualizar_zarc.py
```

Opcionalmente, passe as safras: `python scripts/atualizar_zarc.py 2026-2027`.

O app mostra o calendário de plantio por decêndio (soja, milho 2ª safra, milho + braquiária, sorgo,
feijão) para o tipo de solo escolhido (AD1 a AD6) e verifica se a sequência soja → 2ª safra cabe no
zoneamento, estimando a colheita da soja pelo grupo de ciclo (≈100/120/130 dias).