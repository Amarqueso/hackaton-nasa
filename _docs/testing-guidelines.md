# Testes

## Como rodar

```
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

Os testes ficam em `tests/` e não usam rede nem credenciais: não precisa de `.env` nem de `earthengine authenticate`.

## Como os testes rodam o app

`app.py` monta a interface do Streamlit ao ser importado, então os testes não importam o arquivo:
rodam ele inteiro com `streamlit.testing.v1.AppTest` (fixture `app` em `tests/conftest.py`).

- `app.marcar_terra()` põe um ponto do DF em `st.session_state["centro"]` e roda a página.
- `app.tecnico(True/False)` liga/desliga o modo técnico e roda de novo (mesma sessão, mesmo cache).
- `app.fontes()`, `app.legendas()`, `app.recomendacao()` leem o que aparece na tela.
- O mapa (`st_folium`) é um componente e no AppTest devolve `None`; por isso a terra é marcada pelo `session_state`.

## Mocks

- **Earth Engine**: `tests/fake_ee.py` é um módulo `ee` falso colocado em `sys.modules["ee"]` (fixture `fake_ee`),
  antes do `import ee` do app. Cada expressão guarda de quais coleções veio, e o `.getInfo()` responde pelo id da coleção
  (`fake_ee.respostas["S1_GRD"]`, `"MOD13Q1"`, `"FLDAS"`, `"SRTMGL1"`). Para simular falha, troque a resposta por uma
  função que levanta exceção. `fake_ee.chamadas` conta as consultas de cada coleção. Para simular o Earth Engine
  fora do ar (`EE_OK` falso), defina `fake_ee.init_erro` antes de rodar o app.
  Se o app passar a usar uma coleção nova, acrescente a resposta em `FakeEE.respostas`.
  Uma resposta que recebe um argumento ganha a linhagem; `fake_ee.geometria(lin)` devolve o GeoJSON da terra
  consultada (para responder conforme o ponto). `fake_ee.threads` guarda em qual thread cada coleção foi consultada.
- **Threads**: `coletar` consulta POWER e Earth Engine ao mesmo tempo. Para provar que é em paralelo, as respostas
  falsas esperam numa `threading.Barrier` (ver `tests/test_coletar.py`): se fosse uma de cada vez, a barreira estouraria.
- **NASA POWER**: `requests.get` é trocado por um `mock` com um JSON mínimo (`resposta_power`); `app.get.call_count`
  conta as consultas.
- **Spinners**: `streamlit.spinner` é envolvido para registrar os textos em `app.spinners`. O spinner do Streamlit só
  aparece depois de 0,5 s, então uma chamada que vem do cache não mostra aviso; nos testes, "não consultou de novo"
  é verificado pelos contadores, não pelo spinner.
- **Cache**: `st.cache_data`/`st.cache_resource` são globais no processo; a fixture `app` limpa os dois antes e depois
  de cada teste. Dentro de um teste o cache vale entre as execuções, como no servidor.
- **Cache regional** (#4): clima e solo têm cache por célula (`clima_power(lat, lon)`, `umidade_solo(lat, lon)`, com o
  centro da célula da POWER, 0,5° × 0,625°, ou do pixel do FLDAS, 0,1°). Dois pontos da mesma célula fazem uma consulta
  só; para ver qual célula foi consultada, leia `app.get.call_args` (POWER) ou a geometria do ponto na linhagem do
  FLDAS (`tests/test_cache_regional.py`). Falha não entra no cache da célula.
- **Cache em disco** (#5): `clima_power(lat, lon, ano_fim)` tem `persist="disk"`. O AppTest normalmente guarda o cache
  só em memória; a fixture `cache_disco` (usada por `app`) troca pelo cache em disco do `streamlit run`, numa pasta
  temporária do teste (nunca o `~/.streamlit/cache` de verdade). `app.reiniciar()` simula reiniciar o servidor: apaga
  o cache em memória e a sessão, e o disco fica (`tests/test_cache_disco.py`).
- **Camadas e fragmento do mapa** (#15): as camadas do modo técnico vão ao `st_folium` em `feature_group_to_add`, não
  como filhas do mapa; `camadas_tecnicas(grupos)` (conftest) devolve `{nome: (show, tiles)}`. O mapa fica num
  `st.fragment(run_every=...)`; `app.rodar_sozinho()` simula o navegador rodando só o fragmento numa aba parada.
  Para ver o que chegaria ao navegador (o `key` do componente, o `feature_group`), troque só
  `streamlit_folium._component_func` por um mock (`tests/test_aba_parada.py`).

## O que não dá para testar aqui

Tempo real de resposta (Earth Engine, POWER) e a aparência da página no navegador. Isso precisa de uma execução
de verdade com credenciais, e o resultado vai num comentário da issue.
