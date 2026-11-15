# Backlog — performance e bugs do RotaDF

Todo o código está em `app.py` (app Streamlit único). Os dados vêm da NASA POWER (HTTP) e do Google Earth Engine (`ee`, cada `.getInfo()`/`.getMapId()` é uma ida e volta ao servidor). As tarefas são independentes entre si; quando duas mexem na mesma função, a descrição avisa.

## 1. Tirar o radar Sentinel-1 do carregamento principal
Goal: não calcular a série de radar com filtro Lee quando ninguém vai vê-la.
Description: Em `coletar` (`app.py`, chave `"radar"`), a série mensal de radar VH com filtro Lee é a consulta mais pesada ao Earth Engine e não entra na recomendação (`avaliar` não usa `d["radar"]`); ela só aparece no expander "Parecer técnico". Mover esse cálculo para uma função cacheada própria, chamada apenas quando o modo técnico (`tecnico`, toggle na sidebar) estiver ligado, e mostrar o gráfico só nesse caso.

## 2. Paralelizar as consultas de `coletar`
Goal: o tempo de carregamento passar a ser o da consulta mais lenta, não a soma de todas.
Description: `coletar` chama em sequência a NASA POWER (`clima_power`) e cada consulta ao Earth Engine (`terreno`, `umidade_solo`, NDVI MODIS, radar). Rodar essas chamadas em paralelo com `concurrent.futures.ThreadPoolExecutor`, mantendo o comportamento atual de `tentar` (o que falhar vira `None` e o erro vai para `d["erros"]`). Não chamar funções `st.*` dentro das threads.

## 3. Trocar o GLDAS de 3 horas por um produto mensal na umidade do solo
Goal: reduzir de milhares para dezenas o número de imagens processadas em `umidade_solo`.
Description: `umidade_solo` usa `NASA/GLDAS/V021/NOAH/G025/T3H` (8 imagens por dia) e tira a média de cada mês, primeiro nos últimos 12 meses e depois no mesmo mês de 2010 até hoje, em dois `getInfo` seguidos. Trocar por um produto já mensal com umidade do solo superficial (ex.: `NASA/FLDAS/NOAH01/C/GL/M/V001`, banda `SoilMoi00_10cm_tavg`) e, se possível, buscar toda a série numa única chamada e calcular o percentil em Python. Manter o mesmo retorno (`{"pct", "mes"}`) e atualizar o texto da fonte na interface e no parecer técnico.

## 4. Cache regional para clima e umidade do solo
Goal: clicar em outro ponto ou mudar o tamanho da terra não refazer consultas de dados regionais.
Description: `coletar` é cacheado pela geometria exata (`gj`), então qualquer clique novo ou troca de "Tamanho aproximado" refaz tudo, inclusive clima (~50 km) e umidade do solo (~25 km), que são iguais para toda a região. Arredondar as coordenadas para a grade nativa de cada fonte (POWER ≈ 0,5°; GLDAS/FLDAS 0,25° ou 0,1°) e cachear essas funções por célula, separadas de `coletar`. Hoje o clima é arredondado para 0,1°, o que gera chamadas repetidas para o mesmo dado.

## 5. Persistir o cache de clima em disco
Goal: o primeiro acesso depois de reiniciar o servidor não esperar de novo pela NASA POWER.
Description: `clima_power` usa `st.cache_data` em memória, que se perde quando o processo reinicia. Passar `persist="disk"` (e um `ttl` longo, como 30 dias, já que é média de 2015 até o ano passado). Opcional: gerar um JSON com a climatologia das poucas células da grade POWER que cobrem o DF e ler dele, caindo para a API só fora dessa área.

## 6. Isolar mapa e formulário com `st.fragment`
Goal: trocar uma opção do formulário não reconstruir nem recarregar o mapa.
Description: Cada interação com um widget roda o script inteiro, o que reconstrói o mapa folium (`construir_mapa`) e reenvia o iframe do `st_folium`. Separar o mapa num `@st.fragment` para que mudar mês, nível, água, objetivo ou último plantio só refaça a parte de resultados. Conferir que clicar ou desenhar no mapa continua atualizando a terra marcada (hoje isso é feito via `st.session_state` + `st.rerun()`).

## 7. Não bloquear o mapa com as camadas do modo técnico
Goal: o mapa aparecer na hora, mesmo com o modo técnico ligado.
Description: `layers` gera três camadas (HLS NDVI, radar bruto e radar filtrado) com três `getMapId` em sequência, e só depois o mapa é desenhado. Fazer as três chamadas em paralelo e/ou mostrar o mapa primeiro e adicionar as camadas numa segunda passada (ex.: mostrar um aviso de "carregando camadas" e completar no rerun seguinte).

## 8. Bug: falhas temporárias ficam presas no cache
Goal: um timeout ou erro passageiro não ficar salvo para sempre.
Description: `coletar` captura as exceções dentro de `tentar` e devolve um resultado com `None`; como a função é cacheada, esse resultado incompleto fica guardado para aquela área até o servidor reiniciar. Fazer com que resultados com falha não sejam cacheados (ex.: cachear cada fonte separadamente e deixar a exceção subir na função cacheada, tratando-a fora) ou dar um `ttl` curto nesse caso.

## 9. Bug: camadas do Earth Engine expiram no mapa
Goal: as camadas do modo técnico continuarem aparecendo depois de horas com o app no ar.
Description: `layers` é cacheado sem `ttl`, mas as URLs de tiles do `getMapId` expiram depois de um tempo. Com o app rodando por muito tempo, as camadas param de carregar. Adicionar um `ttl` menor que a validade das URLs (ex.: 1 hora) em `@st.cache_data` de `layers`.

## 10. Bug: dados ficam velhos na virada do mês
Goal: as séries mensais seguirem a janela de 12 meses atual.
Description: `TODAY`/`START` definem a janela das séries (NDVI, radar, solo), mas o cache de `coletar` e `layers` é só pela geometria. Se o app ficar no ar de um mês para o outro, ele continua mostrando a janela antiga. Incluir a data de referência (ex.: `START.isoformat()`) nos argumentos das funções cacheadas ou usar um `ttl` de um dia.

## 11. Bug: `config.toml` não é lido
Goal: aplicar o tema e as configurações definidas no projeto.
Description: O `config.toml` está na raiz do repositório, mas o Streamlit só lê `.streamlit/config.toml`. Mover o arquivo para `.streamlit/config.toml` (sem ignorar no git; o `.gitignore` só ignora `secrets.toml`) e conferir que o tema verde e `gatherUsageStats = false` passaram a valer.

## 12. Tirar o `@import` de fontes que trava a renderização
Goal: o texto aparecer sem esperar o download das fontes do Google.
Description: O CSS injetado (`CSS`, `app.py`) começa com `@import` do Google Fonts, que atrasa a aplicação do resto do estilo. Avaliar trocar por fontes do sistema ou servir as fontes localmente (pasta `static/` do Streamlit), mantendo `display=swap`. O ganho é pequeno; fazer depois das outras tarefas.
