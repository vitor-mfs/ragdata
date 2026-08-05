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
export RAGDATA_DP_SERVER="bRO"   # base usada pelo cliente LATAM (padrão)
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
por padrão (`RAGDATA_CACHE_TTL`, em segundos).

## Variáveis de ambiente

| Variável | Padrão | Para quê |
| --- | --- | --- |
| `DIVINE_PRIDE_API_KEY` | — | Chave da API do Divine Pride |
| `RAGDATA_DP_SERVER` | `bRO` | Servidor consultado no Divine Pride |
| `RAGDATA_CACHE_DIR` | `~/.cache/ragdata` | Onde ficam tabelas e cache HTTP |
| `RAGDATA_CACHE_TTL` | `2592000` | Validade do cache HTTP, em segundos |

## Testes

```bash
uv run pytest
```

Os testes rodam offline: as chamadas HTTP usam transportes falsos e as tabelas
de jogo têm fixtures reduzidas.
