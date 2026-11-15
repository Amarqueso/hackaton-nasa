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

Uma vez por ano, em janeiro, atualize a climatologia local da NASA POWER com `python scripts/gerar_clima_df.py --ano-fim <ano-anterior>` e versione `data/clima_power_df.json` (a fonte mostra o período gravado no arquivo).
