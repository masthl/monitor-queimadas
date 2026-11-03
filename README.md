# 🔥 Monitor de Queimadas · DATAHORIZO

WebGIS de monitoramento de focos de calor que **se atualiza sozinho**, com dados abertos do
**INPE (Programa Queimadas)** e, opcionalmente, da **NASA FIRMS**. Ele roda 100% gratuito
no GitHub: o **GitHub Actions** baixa os dados a cada 3 horas e o **GitHub Pages** publica o mapa.

*See Further · Decide Smarter*

---

## O que ele faz

- Mapa de calor e pontos dos focos, coloridos pela intensidade (FRP, em MW)
- Filtros por período (24 h, 48 h ou tudo), satélite e município
- Indicadores (focos, municípios atingidos, FRP máximo e alertas)
- Gráfico de focos por dia e ranking dos municípios
- **Áreas monitoradas com raio de alerta em metros**: fazendas, unidades de conservação, ETAs etc.
- Alerta opcional no **Telegram** quando surge um foco novo dentro do raio
- Exportação em CSV dos focos filtrados
- Fundo escuro ou imagem de satélite, com layout adaptado ao celular

## Como funciona

```
GitHub Actions (a cada 3 h)                         GitHub Pages
┌────────────────────────────────────┐              ┌──────────────────────────┐
│ scripts/atualizar_focos.py         │   commit     │ docs/index.html (WebGIS) │
│  1. baixa CSV diário do INPE       │ ───────────► │  lê docs/data/*.json     │
│  2. (opc.) baixa FIRMS do dia      │  docs/data/  │  MapLibre + Turf         │
│  3. filtra pelo recorte do config  │              └──────────────────────────┘
│  4. calcula alertas por raio (m)   │
│  5. (opc.) envia alerta no Telegram│
└────────────────────────────────────┘
```

---

## Passo a passo para colocar no ar (cerca de 10 minutos)

### 1. Crie o repositório
1. No GitHub, clique em **New repository**, por exemplo `monitor-queimadas`. Deixe-o **Public**,
   porque no plano gratuito o Pages exige repositório público e assim os minutos do Actions são ilimitados.
2. Faça upload de **todo o conteúdo desta pasta**, incluindo a pasta oculta `.github/`.
   - Pelo navegador: **Add file › Upload files** e arraste os arquivos. Se a pasta `.github`
     não aparecer, crie o arquivo pelo botão **Add file › Create new file** com o caminho
     `.github/workflows/atualizar.yml` e cole o conteúdo.
   - Pelo terminal:
     ```bash
     git init && git add . && git commit -m "Monitor de Queimadas"
     git branch -M main
     git remote add origin https://github.com/SEU_USUARIO/monitor-queimadas.git
     git push -u origin main
     ```

### 2. Dê permissão para o robô salvar os dados
**Settings › Actions › General › Workflow permissions** e marque
**Read and write permissions**. Depois clique em **Save**.

### 3. Ligue o GitHub Pages
**Settings › Pages › Build and deployment**:
- Source: **Deploy from a branch**
- Branch: **main** e pasta **/docs**. Depois clique em **Save**.

Em 1 ou 2 minutos o site fica em `https://SEU_USUARIO.github.io/monitor-queimadas/`.

### 4. Rode a primeira coleta
Na aba **Actions**, abra **Atualizar focos de queimadas** e clique em **Run workflow**.
Quando terminar, os dados de demonstração são substituídos pelos dados reais do INPE
e a etiqueta "dados de demonstração" some do mapa. Depois disso ele roda sozinho a cada 3 horas.

---

## Configuração

### Recorte e período (`config.json`)
| Campo | O que faz |
|---|---|
| `titulo` | Título exibido no painel |
| `estados` | Lista de estados como o INPE escreve (`"SÃO PAULO"`, `"MATO GROSSO"`…). Use `[]` para o Brasil inteiro (o arquivo fica pesado na época seca) |
| `dias` | Quantos dias de histórico manter (padrão: 7) |
| `satelites_excluir` | Satélites a ignorar, por exemplo `["GOES-19"]`, que é geoestacionário, tem muitas detecções repetidas e resolução de cerca de 2.000 m |
| `firms.bbox` | Retângulo `[oeste, sul, leste, norte]` usado na consulta da NASA FIRMS |
| `mapa.centro` / `mapa.zoom` | Enquadramento inicial do mapa |

### Áreas monitoradas (`docs/data/areas_monitoradas.geojson`)
É um GeoJSON comum (EPSG:4326) com polígonos ou pontos. Cada feição pode ter estas propriedades:
```json
{ "nome": "Fazenda Santa Rita", "tipo": "Imóvel rural", "raio_m": 3000 }
```
Você pode desenhar as áreas no QGIS ou no [geojson.io](https://geojson.io), exportar e
substituir o arquivo. O `raio_m` é a distância, em metros, a partir da borda da área.
Os três exemplos que vêm no arquivo são fictícios.

### Secrets opcionais (**Settings › Secrets and variables › Actions › New repository secret**)
| Secret | Para quê |
|---|---|
| `FIRMS_MAP_KEY` | Complementa com os focos **do dia corrente**, já que o CSV diário do INPE é publicado na manhã seguinte. A chave é gratuita em <https://firms.modaps.eosdis.nasa.gov/api/map_key/> |
| `TELEGRAM_BOT_TOKEN` | Token do bot, criado com o [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | ID do chat ou grupo que recebe os alertas. Mande uma mensagem ao bot e consulte `https://api.telegram.org/bot<TOKEN>/getUpdates` |

### Botão de contato
No `docs/index.html`, as constantes `WHATSAPP` e `MSG_WA` no início do `<script>` controlam o botão "Falar com a DATAHORIZO".

---

## Testar no computador

```bash
pip install -r requirements.txt
python scripts/atualizar_focos.py          # dados reais
python scripts/atualizar_focos.py --demo   # dados sintéticos
cd docs && python -m http.server 8000      # abra http://localhost:8000
```

## Fontes e limitações
- **INPE · Programa Queimadas**: CSV diário em
  `dataserver-coids.inpe.br/queimadas/queimadas/focos/csv/diario/Brasil/`, com todos os satélites.
  Para comparar períodos, o INPE recomenda usar só o *satélite de referência*.
- **NASA FIRMS**: VIIRS (cerca de 375 m) e MODIS (cerca de 1.000 m). A FIRMS anunciou o
  fim de alguns produtos em 01/11/2026, por isso o padrão é `VIIRS_NOAA21_NRT`.
- Foco de calor **não é área queimada**. Ele indica uma anomalia térmica detectada no pixel e
  pode ter falsos positivos (telhados quentes, indústrias). Para a cicatriz real, o próximo passo
  é o dNBR com Sentinel-2 (10 a 20 m) ou o MapBiomas Fogo.
- O agendamento do GitHub Actions pode atrasar alguns minutos. Em repositório público, ele é
  pausado depois de 60 dias sem atividade. Os commits automáticos dos dados mantêm o
  repositório ativo, mas, se pausar, basta reativar na aba Actions.
- O GitHub Pages é ótimo para portfólio e demonstração. Para clientes, com login, dados
  privados e SLA, migre para Cloudflare Pages, uma VPS ou a infraestrutura do cliente.

---
Desenvolvido por **DATAHORIZO** · GIS, IA e dados ambientais · São Paulo
