# SAQR — Modelo de Domínio Oficial v1.0

## 1. Princípios do domínio

O modelo é organizado pelos conceitos do processo industrial, não pelos layouts dos arquivos. Os arquivos são adaptadores de entrada. O domínio permanece estável quando um arquivo, relatório ou ERP muda.

Regras estruturais:

1. Uma informação possui uma fonte oficial definida.
2. Divergências nunca são resolvidas silenciosamente.
3. Eventos descrevem o que aconteceu; indicadores descrevem o resultado calculado.
4. Produção, Consumo, Lixo e Repasse não são duplicados em tabelas paralelas. Eles são especializações de Evento Industrial e projeções analíticas versionadas.
5. Todo resultado calculado aponta para uma Regra, uma Execução de Cálculo, os Parâmetros vigentes e sua linhagem.

## 2. Entidades oficiais

### 2.1 Dia Operacional

| Item | Definição |
|---|---|
| Objetivo | Delimitar a janela industrial que pode atravessar duas datas civis. |
| Principais atributos | data operacional, início, fim, status, data de fechamento, metadados. |
| Chave primária | `operational_days.id`. Chave de negócio: `operational_date`. |
| Relacionamentos | 1:N com Ordem de Produção, Evento Industrial e Execução de Cálculo. |
| Responsabilidade | Determinar o recorte temporal oficial de seleção e cálculo. |
| Fonte oficial | Parâmetro operacional aprovado, não inferido de arquivo. |
| SQLite | **Implementado** em `operational_days`. |

### 2.2 Ordem de Produção (OP)

| Item | Definição |
|---|---|
| Objetivo | Representar a unidade de execução e rastreamento da produção. |
| Principais atributos | chave da OP, número, tipo de origem, produto de saída, dia operacional, status e confiança. |
| Chave primária | `production_orders.id`. Chave de negócio atual: `op_key = origem + número da OP`. |
| Relacionamentos | N:1 Produto; N:1 Dia Operacional; 1:N Eventos, Movimentações, Execuções da OP, perdas e indicadores. |
| Responsabilidade | Agregar os acontecimentos e resultados pertencentes à mesma ordem. |
| Fonte oficial | Movimentações para identidade/produção; arquivos de recurso e MPOP como fontes complementares. |
| SQLite | **Implementado** em `production_orders`. O vínculo físico de `resource_executions` ainda usa o número textual da OP. |

### 2.3 Evento Industrial

| Item | Definição |
|---|---|
| Objetivo | Registrar “o que aconteceu” no processo industrial. |
| Principais atributos | tipo, data/hora do fato, data/hora do registro, dia operacional, OP, produto, componente, centro, quantidade, unidade, confiança, fonte, regra e payload. |
| Chave primária | `events.id`. |
| Relacionamentos | N:1 registro original; N:1 OP lógica; pode participar da linhagem de indicadores. |
| Responsabilidade | Tornar o núcleo internamente Event Driven sem exigir filas ou microsserviços. |
| Fonte oficial | Gerado a partir de cada fonte oficial e de eventos internos do motor. |
| SQLite | **Implementado** em `events`; visão semântica `industrial_events`. |

### 2.4 Execução da OP

| Item | Definição |
|---|---|
| Objetivo | Representar uma execução concreta da OP em determinado recurso, operação e intervalo. |
| Principais atributos | OP, produto, estágio, operação, sequência, centro, início, fim, quantidade, operador e turno. |
| Chave primária | `resource_executions.id`. Chave natural candidata: OP + operação + sequência + início + centro. |
| Relacionamentos | N:1 OP; N:1 Centro de Recurso; N:1 Linha por vigência do centro. |
| Responsabilidade | Permitir OPs multicentro e preservar o histórico de execução. |
| Fonte oficial | `Movimentações com centro de recurso.xlsx`. |
| SQLite | **Implementado** em `resource_executions`. |

### 2.5 Linha

| Item | Definição |
|---|---|
| Objetivo | Representar a unidade produtiva utilizada para análise operacional. |
| Principais atributos | código, nome, status e metadados. |
| Chave primária | `lines.id`. Chave de negócio: `line_code`. |
| Relacionamentos | N:N temporal com Centro de Recurso; 1:N resultados analíticos por linha. |
| Responsabilidade | Servir como dimensão de consolidação, sem conter cálculos. |
| Fonte oficial | Derivada do Centro de Recurso; nomes podem ser parametrizados. |
| SQLite | **Implementado** em `lines` e `resource_center_lines`. |

### 2.6 Centro de Recurso

| Item | Definição |
|---|---|
| Objetivo | Identificar o recurso oficial em que uma execução ocorreu. |
| Principais atributos | código, nome, fonte, status e metadados. |
| Chave primária | `resource_centers.id`. Chave de negócio: `center_code`. |
| Relacionamentos | 1:N Execuções da OP; N:N temporal com Linha. |
| Responsabilidade | Fonte oficial para determinação da linha. |
| Fonte oficial | Arquivo de Centro de Recurso. |
| SQLite | **Implementado** em `resource_centers`. |

### 2.7 Produto

| Item | Definição |
|---|---|
| Objetivo | Representar o item produzido ou movimentado como produto. |
| Principais atributos | código, derivação, descrição, família, confiança e registro de origem. |
| Chave primária | `products.id`. Chave de negócio: código + derivação. |
| Relacionamentos | N:1 Família; 1:N OPs; 1:N eventos e movimentações. |
| Responsabilidade | Preservar identidade do item independentemente da descrição textual. |
| Fonte oficial | Cadastro implícito nas movimentações do ERP no MVP. |
| SQLite | **Implementado** em `products`. |

### 2.8 Componente

| Item | Definição |
|---|---|
| Objetivo | Representar o item consumido, devolvido, perdido ou repassado. |
| Principais atributos | código, derivação, descrição preferencial, status da descrição e metadados. |
| Chave primária | `components.id`. Chave de negócio: código + derivação. |
| Relacionamentos | 1:N classificações vigentes; 1:N movimentos, perdas e eventos. |
| Responsabilidade | Separar a identidade do item de sua classificação e de descrições variáveis. |
| Fonte oficial | Movimentações; MPOP e Categorias complementam descrição/classificação. |
| SQLite | **Implementado** em `components`. |

### 2.9 Família

| Item | Definição |
|---|---|
| Objetivo | Agrupar produtos segundo a família existente no ERP. |
| Principais atributos | código, nome, fonte, confiança e metadados. |
| Chave primária | `families.id`. Chave de negócio: `family_code`. |
| Relacionamentos | 1:N Produtos. |
| Responsabilidade | Dimensão corporativa de agrupamento, distinta da classificação analítica do componente. |
| Fonte oficial | Campo Família das movimentações. |
| SQLite | **Implementado** em `families`. |

### 2.10 Classificação

| Item | Definição |
|---|---|
| Objetivo | Organizar componentes em uma hierarquia analítica controlada. |
| Principais atributos | código, nome, nível, classificação pai e metadados. |
| Chave primária | `classifications.id`. Chave de negócio: `classification_code`. |
| Relacionamentos | autorrelacionamento hierárquico; N:N temporal com Componente por atribuições. |
| Responsabilidade | Determinar se um item é matéria-prima, embalagem, salmoura, aditivo etc., sem alterar a identidade do componente. |
| Fonte oficial | `Categorias.xlsx`. Regras de fallback são parâmetros e geram confiança estimada. |
| SQLite | **Implementado** em `classifications` e `component_classification_assignments`. Conflitos permanecem registrados. |

### 2.11 Movimentação

| Item | Definição |
|---|---|
| Objetivo | Preservar o fato contábil normalizado do ERP. |
| Principais atributos | transação, OP, origem, produto/componente, data, data/hora de registro, quantidade, unidade, direção, família, derivação, filial e fonte. |
| Chave primária | `movements.id`; rastreabilidade pelo `source_record_id`. |
| Relacionamentos | N:1 registro original; relação lógica com OP, Produto e Componente; origina eventos. |
| Responsabilidade | Ser a fonte normalizada para produção, consumo, devolução e repasse. |
| Fonte oficial | `movimentação diárias.xlsx`. |
| SQLite | **Implementado** em `movements`. |

### 2.12 Produção

| Item | Definição |
|---|---|
| Objetivo | Representar apontamentos e estornos de produção. |
| Principais atributos | evento, OP, produto, quantidade, unidade, transação, horário, confiança e origem. |
| Chave primária | `events.id` para cada fato. Resultados possuem `indicator_results.id`. |
| Relacionamentos | especialização de Evento Industrial; agregada por OP, Linha e Dia Operacional. |
| Responsabilidade | Calcular Produção Líquida por 90201 − 90252, mantendo unidades separadas. |
| Fonte oficial | Movimentações. |
| SQLite | **Implementado sem tabela duplicada**: eventos + visão `production_facts` + indicadores `PRODUCTION_NET`. |

### 2.13 Consumo

| Item | Definição |
|---|---|
| Objetivo | Representar baixas e devoluções relacionadas ao consumo. |
| Principais atributos | evento, OP, componente, quantidade, unidade, estratégia temporal, conversão, classificação e confiança. |
| Chave primária | `events.id` para cada fato; `indicator_results.id` para cada resultado. |
| Relacionamentos | especialização de Evento Industrial; deriva de Movimentação; alimenta MP Aproveitada e Rendimento. |
| Responsabilidade | Aplicar 90251 − 90202 sem misturar unidades e disponibilizar estratégias parametrizáveis. |
| Fonte oficial | Movimentações. |
| SQLite | **Implementado sem tabela duplicada**: visão `consumption_facts`, indicadores por componente/OP/linha/dia. |

### 2.14 Lixo

| Item | Definição |
|---|---|
| Objetivo | Representar perda física registrada no MPOP301. |
| Principais atributos | OP, componente, quantidade de perda original, capacidade da descrição, perda convertida em kg, classificação, confiança e snapshot. |
| Chave primária | `mpop_components.id`; resultado em `indicator_results.id`. |
| Relacionamentos | N:1 OP lógica; N:1 Componente lógico; participa do cálculo de MP Aproveitada. |
| Responsabilidade | Converter somente perdas elegíveis conforme regra aprovada e preservar perdas não incluídas. |
| Fonte oficial | MPOP301. |
| SQLite | **Implementado** em `mpop_components`, eventos, visão `loss_facts` e indicador `MPOP_LOSS_KG`. |

### 2.15 Repasse

| Item | Definição |
|---|---|
| Objetivo | Representar devoluções 90202 identificadas como REPASSE. |
| Principais atributos | OP, item, descrição, quantidade original, unidade, capacidade, kg convertido, horário e confiança. |
| Chave primária | movimento/evento de origem; resultado em `indicator_results.id`. |
| Relacionamentos | especialização de Evento Industrial; deriva de Movimentação; participa de cenários de MP Aproveitada. |
| Responsabilidade | Aplicar a regra oficial sem forçar o relatório de referência. |
| Fonte oficial | Movimento 90202 das Movimentações. |
| SQLite | **Implementado sem tabela duplicada**: visão `repasse_facts` e indicador `REPASSE_KG`. |

### 2.16 Perda Padrão

| Item | Definição |
|---|---|
| Objetivo | Manter o padrão de perda vigente e versionável. |
| Principais atributos | código, nome, percentual, vigência, status, confiança, fonte e metadados. |
| Chave primária | `loss_standards.id`; unicidade atual por código + início de vigência. |
| Relacionamentos | aplicada a Produto/OP por parâmetro de mapeamento; participa do Excesso sobre o Padrão. |
| Responsabilidade | Ser a única fonte oficial do limite metodológico. |
| Fonte oficial | `perda padrão atual VF 03.2026.xlsx`, bloco oficial A:B. |
| SQLite | **Implementado** em `loss_standards`; linhas não estruturadas preservadas em `loss_standard_rows`. Mapeamento produto→padrão ainda é parametrizado e classificado como estimado. |

### 2.17 Indicador

| Item | Definição |
|---|---|
| Objetivo | Definir um resultado analítico governado e seus metadados. |
| Principais atributos | código, nome, objetivo, fórmula, unidade padrão, frequência, fonte, responsável, política de confiança e regra. |
| Chave primária | `indicator_catalog.indicator_code`. Resultado: `indicator_results.id`. |
| Relacionamentos | 1:N Resultados; N:1 Regra; resultados possuem dependências e linhagem. |
| Responsabilidade | Formar o catálogo oficial de indicadores e impedir cálculos nas telas. |
| Fonte oficial | Catálogo interno governado. |
| SQLite | **Implementado** em `indicator_catalog` e `indicator_results`. |

### 2.18 Regra

| Item | Definição |
|---|---|
| Objetivo | Versionar fórmulas e critérios metodológicos. |
| Principais atributos | código, versão, fórmula/configuração, vigência, status e metadados. |
| Chave primária | composta: regra + versão. |
| Relacionamentos | 1:N resultados; N:N lógica com parâmetros. |
| Responsabilidade | Permitir reprodução histórica e evitar alteração silenciosa do passado. |
| Fonte oficial | Configuração aprovada e registrada pelo SAQR. |
| SQLite | **Implementado** em `rule_versions`. Todas as regras do incremento usam versão `1.0.0`. |

### 2.19 Parâmetro

| Item | Definição |
|---|---|
| Objetivo | Controlar decisões metodológicas sem lógica fixa no código. |
| Principais atributos | chave, valor, tipo, vigência/escopo e snapshot da execução. |
| Chave primária | `parameters.parameter_key`; snapshot: cálculo + chave. |
| Relacionamentos | aplicado por Regra e registrado em Execução de Cálculo. |
| Responsabilidade | Controlar origem considerada, estratégia de consumo, rateio, fórmula de MP aproveitada, densidades e mapeamentos. |
| Fonte oficial | Configuração aprovada. |
| SQLite | **Implementado** em `parameters` e `calculation_run_parameters`. |

### 2.20 Execução de Cálculo

| Item | Definição |
|---|---|
| Objetivo | Registrar uma execução reproduzível do Motor Analítico. |
| Principais atributos | início, fim, dia operacional, versão do motor, snapshot de configuração e status. |
| Chave primária | `calculation_runs.id`. |
| Relacionamentos | 1:N resultados, parâmetros aplicados e auditorias. |
| Responsabilidade | Garantir reexecução, comparação entre cenários e rastreabilidade temporal. |
| Fonte oficial | Gerada pelo Motor Analítico. |
| SQLite | **Implementado** em `calculation_runs`. |

### 2.21 Auditoria

| Item | Definição |
|---|---|
| Objetivo | Registrar inconsistências, divergências, limitações e decisões observadas. |
| Principais atributos | cálculo, severidade, código, entidade, mensagem, valor oficial, referência, diferença, metadados e data/hora. |
| Chave primária | `audit_log.id`. |
| Relacionamentos | N:1 Execução de Cálculo; relacionada logicamente a OP, movimento, regra, indicador ou fonte. |
| Responsabilidade | Preservar divergências e alimentar o Modo Auditor. |
| Fonte oficial | Gerada pelas validações e comparações do sistema. |
| SQLite | **Implementado** em `audit_log`; problemas de importação ficam em `validation_issues`. |

## 3. Diagrama textual dos relacionamentos

```text
Importação 1 ─── N Registro Original
                     │
                     ├── 0..1 Movimentação ─── N Evento Industrial
                     ├── 0..1 Execução da OP ── 1 Centro de Recurso
                     ├── 0..1 Perda MPOP
                     ├── 0..1 Classificação observada
                     └── 0..1 Perda Padrão

Dia Operacional 1 ─── N Ordem de Produção
       │                    │
       │                    ├── N Evento Industrial
       │                    ├── N Execução da OP
       │                    ├── N Movimentação
       │                    ├── N Perda MPOP
       │                    └── N Indicador por OP
       │
       └── N Execução de Cálculo ─── N Indicador Calculado
                                          │
                                          ├── N Linhagem direta ─── Registro Original / Evento
                                          ├── N Dependência ─────── outro Indicador Calculado
                                          └── N ocorrências de Auditoria

Família 1 ─── N Produto 1 ─── N Ordem de Produção

Componente N ─── N Classificação

Centro de Recurso N ─── N Linha, com vigência

Regra 1 ─── N Indicador Calculado
Parâmetro N ─── N Execução de Cálculo, por snapshot
Perda Padrão 1 ─── N Indicadores de OP, por mapeamento parametrizado
```

## 4. Sobreposições identificadas e decisão oficial

| Conceitos potencialmente sobrepostos | Decisão do domínio |
|---|---|
| `movements` e `events` | Não são duplicatas. Movimento é o fato normalizado do ERP; Evento é sua interpretação no domínio. O evento mantém referência ao registro original. |
| Produção/Consumo/Lixo/Repasse e `events` | São especializações sem tabela física paralela. Persistem como eventos e como resultados analíticos. |
| `component_classifications` e `classifications` | A primeira preserva observações brutas da fonte. A segunda é a hierarquia canônica; `component_classification_assignments` registra a atribuição e o conflito. |
| Família e Classificação | Família é cadastro/agrupamento de Produto no ERP. Classificação é taxonomia analítica de Componente. Não devem ser fundidas. |
| Linha e Centro de Recurso | No MVP podem coincidir, mas permanecem entidades distintas para suportar reorganização, vigência e vários centros por linha. |
| `loss_standard_rows` e `loss_standards` | A primeira preserva todas as linhas brutas; a segunda contém somente padrões estruturados e utilizáveis. |
| `indicator_lineage` e `indicator_dependencies` | Linhagem aponta para registros/eventos originais. Dependência conecta indicadores derivados a indicadores de entrada, evitando copiar a mesma linhagem repetidamente. |
| Relatório de referência e fontes oficiais | `reference_op_results` fica isolada para homologação. Nunca participa da linhagem de cálculo. |
| Execução da OP e Ordem de Produção | OP é a identidade/agregado; Execução é cada passagem por operação/centro/tempo. Uma OP pode possuir várias execuções. |

## 5. Pendências estruturais conhecidas

1. `resource_executions.op` ainda é uma referência textual, porque o arquivo não informa de forma confiável a origem da OP. O vínculo lógico existe, mas a FK física será adicionada quando a chave corporativa da OP estiver formalizada.
2. Produto e Componente compartilham códigos em alguns contextos do ERP. Permanecem entidades distintas por papel no processo; uma futura tabela de Item Mestre poderá unificar identidade sem fundir responsabilidades.
3. O relacionamento Produto→Perda Padrão ainda usa regras parametrizadas de descrição. A solução definitiva é um cadastro explícito e versionado por produto/família.
4. Centros ausentes ou multicentro permanecem pendentes e não são rateados automaticamente.
