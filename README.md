# ragdata

Auxiliar de build para **Ragnarok Online Renewal (servidor LATAM)**.

Você descreve um personagem — por texto, YAML/JSON ou mandando prints da janela
de status e de equipamentos — e o ragdata calcula os stats derivados com as
fórmulas oficiais do Renewal, aponta o que está desperdiçado e sugere melhorias
para o objetivo que você declarar (upar, farmar, MVP, PvP/WoE, tankar, suporte).

Os dados de itens e monstros vêm do [Divine Pride](https://www.divine-pride.net)
e o contexto de classes/habilidades do [browiki](https://browiki.org).

## O que ele faz

| Recurso | Estado |
| --- | --- |
| Stats derivados (ATK, MATK, HIT, FLEE, CRIT, DEF/MDEF, ASPD, cast, HP/SP, traits) | ✅ |
| Validação: pontos desperdiçados, breakpoints de ASPD e de conjuração | ✅ |
| Sugestões priorizadas por objetivo | ✅ |
| Simulação "e se eu mudar X?" com diferença número a número | ✅ |
| Leitura de personagem a partir de prints | ✅ (via MCP, o Claude lê a imagem) |
| Consulta de itens/monstros no Divine Pride, com cache e 1 req/s | ✅ |
| Busca de armas/armaduras por necessidade (resistência a Dragão, dano em Amorfo, dano de habilidade), só itens listados no LATAM | ✅ |
| Contexto de classe/habilidade do browiki | ✅ |
| Simulação de dano por skill contra um alvo | ❌ ainda não |

## Instalação

```bash
uv venv && uv pip install -e ".[mcp]"
```

Depois baixe as tabelas de jogo (uma vez, precisa de internet):

```bash
ragdata setup
```

E configure a chave da API do Divine Pride — pegue a sua em
<https://www.divine-pride.net/account> (a API aceita **1 requisição por
segundo**, e o ragdata respeita esse limite):

```bash
export DIVINE_PRIDE_API_KEY="sua-chave"
export RAGDATA_DP_SERVER="LATAM"   # base do Ragnarok LATAM no Divine Pride (padrão)
export RAGDATA_DP_LANGUAGE="pt"    # idioma dos nomes e descrições (padrão)
```

## Uso pela linha de comando

```bash
# Analisa um personagem descrito em YAML
ragdata analyze exemplos/rune_knight.yaml --goal mvp

# Só os números, sem as sugestões
ragdata stats exemplos/rune_knight.yaml

# "E se eu subir AGI para 120 e refinar a arma para +13?"
ragdata simulate exemplos/rune_knight.yaml '{"stats":{"agi":120},"refine":{"Espada Rúnica":13}}'

# Consulta o Divine Pride e o browiki
ragdata item 1201
ragdata monster 1002
ragdata wiki "Cavaleiro Rúnico"

# Que arma/armadura me dá isso? (só itens listados na base LATAM)
ragdata find "resistência a dragão"
ragdata find "dano em amorfo" -c arma -s "espada de duas mãos" -j "Cavaleiro Rúnico"
ragdata find "dano de [Sopro do Dragão]" --min-level 150 --json
ragdata web   # o mesmo, numa página local no navegador

# Diagnóstico da instalação (tabelas, cache, API key, campos do Divine Pride)
ragdata doctor
```

Um personagem em YAML é assim:

```yaml
name: Kaya
job: Cavaleiro Rúnico       # aceita PT-BR, inglês ou a chave do rAthena
base_level: 175
job_level: 60
goal: mvp
stats: { str: 120, agi: 90, vit: 80, int: 40, dex: 90, luk: 30 }
equipment:
  - slot: right_hand
    name: Espada Rúnica
    weapon_type: 2hSword
    weapon_level: 4
    base_atk: 220
    refine: 10
    slots: 2
    cards:
      - name: Carta Hydra
        note: "+20% de dano em Demi-Humano"
  - slot: armor
    name: Armadura de Placas
    base_def: 85
    refine: 7
```

## Busca por necessidade

`ragdata find` responde "qual equipamento me dá X?" a partir do que você precisa:

| Necessidade | Exemplo | Tipo (`--kind`) |
| --- | --- | --- |
| Resistência a uma raça | `resistência a dragão`, `aguentar demônio` | `resistencia_raca` |
| Dano (físico/mágico) a uma raça | `dano em amorfo`, `dano mágico em morto-vivo` | `dano_raca`, `dano_magico_raca` |
| Resistência a uma propriedade | `resistência a fogo`, `resistência a propriedade sombria` | `resistencia_elemento` |
| Dano a uma propriedade | `dano em fogo`, `dano mágico contra água` | `dano_elemento`, `dano_magico_elemento` |
| Dano a um tamanho | `dano em tamanho grande`, `dano mágico em pequeno` | `dano_tamanho`, `dano_magico_tamanho` |
| Dano de uma habilidade | `dano de [Sopro do Dragão]` | `dano_habilidade` |
| Recarga de uma habilidade | `recarga de [Esquife de Gelo]` | `recarga_habilidade` |
| Atributo | `FOR`, `+INT` | `atributo` |

Raças: amorfo, morto-vivo, bruto, planta, inseto, peixe, demônio, humanoide,
anjo, dragão, jogador. Propriedades: neutro, água, terra, fogo, vento, veneno,
sagrado, sombrio, fantasma, maldito. Tamanhos: pequeno, médio, grande. Nomes de
habilidade são os do cliente LATAM em português.

Como funciona, e por que assim:

1. A API do Divine Pride só responde por ID e proíbe enumerar IDs em massa. Os
   candidatos vêm da **listagem do site** (`/database/item/<categoria>`), com o
   filtro de função do item (ex.: "Reduce damage taken from a race") e a
   palavra-chave na descrição ("Dragão"), servida na base **LATAM em português**.
2. Só entram linhas com o badge **LATAM** e com nome em português. Itens que o
   Divine Pride tem na base LATAM mas sem nome (provavelmente não lançados) são
   descartados e contados em `excluidos.sem_nome_latam`.
3. Cada candidato é lido pela **API** com `x-server: LATAM` (cache em disco,
   1 req/s). Resposta 404 ou de outra região → fora. Por padrão até 25 itens
   são consultados (`--limit`); o resto fica em `nao_consultados`.
4. As linhas da descrição que atendem ao pedido são extraídas com a condição
   (refino, conjunto) e o percentual; os itens vêm ordenados pelo maior valor.
   Candidatos sem linha reconhecível aparecem em `possiveis`, com a descrição.

Filtros: `-c/--category` (arma, armadura, carta, sombra), `-s/--subtype` (capa,
bota, escudo, acessório, espada de duas mãos…), `-j/--job` (classe),
`--min-level`/`--max-level`, `--min-slots`, `--pages`, `--no-details`, `--json`.

### Interface web para testar

```bash
ragdata web            # sobe em http://127.0.0.1:8765 e abre o navegador
ragdata web -p 9000 --no-browser
```

A página roda só na sua máquina e usa o mesmo cliente da CLI (cache, 1 req/s):
caixa de texto com exemplos clicáveis, filtros (categoria, subtipo, classe,
nível, slots), modo estruturado (tipo + alvo), resultados com link para o
Divine Pride, as linhas de efeito com condição e o JSON bruto. A busca fica na
URL (`/?necessidade=resistência+a+dragão&categoria=armadura`), então dá para
compartilhar ou repetir. Sem `DIVINE_PRIDE_API_KEY` ela mostra só a listagem.

## Uso como servidor MCP (recomendado)

É aqui que a leitura por print funciona: o Claude enxerga a imagem, extrai os
dados no formato esperado e chama as ferramentas de análise.

```bash
claude mcp add ragdata -- ragdata-mcp
```

Ferramentas expostas:

| Ferramenta | Para quê |
| --- | --- |
| `schema_do_personagem` | Devolve o schema JSON + instruções de leitura de print |
| `analisar_personagem` | Stats derivados + achados + sugestões por objetivo |
| `calcular_stats` | Só os números derivados |
| `simular_mudanca` | Compara a build atual com uma alteração de atributos/equipamento |
| `ler_texto_de_personagem` | Rascunho a partir de uma descrição solta |
| `buscar_item` / `buscar_monstro` | Divine Pride (com cache e 1 req/s) |
| `buscar_equipamento_por_necessidade` | Armas/armaduras do LATAM por necessidade (resistência, dano, habilidade…) |
| `vocabulario_de_necessidades` | Tipos e alvos aceitos pela busca por necessidade |
| `consultar_browiki` | Texto de uma página do browiki |
| `estado_do_ragdata` | Diagnóstico: tabelas, cache e chave de API |

Fluxo típico: você manda os prints → o Claude chama `schema_do_personagem` para
saber o formato → monta o JSON do personagem → chama `analisar_personagem`.

## Como os números são calculados

As fórmulas seguem o [rAthena](https://github.com/rathena/rathena) (branch
`master`, modo Renewal), que é a implementação aberta de referência. Cada função
em `src/ragdata/engine.py` cita a origem — por exemplo `status_base_atk`,
`status_base_amotion_pc`, `skill_vfcastfix`. As divisões inteiras do C são
reproduzidas para os valores baterem com o servidor.

O que o motor **não** faz (e por isso não finge fazer):

- não interpreta scripts de item — efeitos de carta/encantamento entram como
  bônus numéricos explícitos ou como observação em texto;
- não simula dano de habilidade contra um alvo;
- não modela buffs temporários, comida ou consumíveis, a menos que você os
  informe como bônus.

Se o seu servidor tiver fórmulas customizadas, os números vão divergir.

## Dados de jogo e licenças

As tabelas do rAthena (`job_stats`, `job_aspd`, `job_basepoints`, `statpoint`,
`refine`, `enchantgrade`) são **baixadas para o cache local** por `ragdata setup`
e não são versionadas neste repositório — o rAthena é GPL-3.0 e o ragdata é MIT.
O cache fica em `~/.cache/ragdata` (ou `$RAGDATA_CACHE_DIR`).

O Divine Pride e o browiki são consultados pela rede, com cache local de 30 dias
por padrão (`RAGDATA_CACHE_TTL`, em segundos); as páginas de listagem do Divine
Pride ficam no cache por até 1 dia, porque mudam quando entram itens novos. A
documentação da API está em <https://www.divine-pride.net/tools/api-doc>.

## Variáveis de ambiente

| Variável | Padrão | Para quê |
| --- | --- | --- |
| `DIVINE_PRIDE_API_KEY` | — | Chave da API do Divine Pride |
| `RAGDATA_DP_SERVER` | `LATAM` | Servidor (região) consultado no Divine Pride; a busca por necessidade usa sempre `LATAM` |
| `RAGDATA_DP_LANGUAGE` | `pt` | Idioma dos nomes e descrições (`Accept-Language` da API) |
| `RAGDATA_CACHE_DIR` | `~/.cache/ragdata` | Onde ficam tabelas e cache HTTP |
| `RAGDATA_CACHE_TTL` | `2592000` | Validade do cache HTTP, em segundos |

## Testes

```bash
uv run pytest
```

Os testes rodam offline: as chamadas HTTP usam transportes falsos e as tabelas
de jogo têm fixtures reduzidas.
