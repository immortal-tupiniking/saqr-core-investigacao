# SAQR - Entrega do Incremento 03

## 1. Objetivo

Disponibilizar o primeiro SAQR navegável em ambiente local, consumindo exclusivamente resultados do Motor Analítico v0.2.0 e preservando rastreabilidade, confiança, versões de regra e divergências.

## 2. O que foi implementado

### Tela de importação

- upload das cinco fontes oficiais;
- relatório de referência opcional;
- validação de formato, abas e colunas;
- execução do Motor de Dados e do Motor Analítico;
- substituição atômica do banco ativo após sucesso;
- status, registros, alertas, erros, data e hora e hash por fonte.

### Resumo Executivo

Filtros por dia operacional, empresa, linha, OP, produto, estratégia e confiança.

Indicadores apresentados:

- OPs analisadas;
- linhas em operação;
- produção separada por BD, BR e CX;
- consumo real de MP;
- lixo;
- repasse;
- MP aproveitada;
- rendimento operacional;
- perda real;
- perda padrão;
- excesso sobre o padrão.

### Consumo por Linha

Tabela analítica com centro, linha, OPs, produção por unidade, consumo, lixo, repasse, MP aproveitada, rendimento, padrão, excesso, confiança e pendências. A seleção abre as OPs relacionadas.

### Consumo por OP

Tabela com empresa, OP, produto, descrição, centro, linha, produção, consumo teórico, consumo real, diferença, lixo, repasse, MP aproveitada, rendimento, padrão, excesso e confiança.

### Detalhamento da OP

Seções independentes para:

- Produção;
- Matéria-prima;
- Salmoura;
- Aditivos;
- Azeites e óleos;
- Embalagens;
- Lixo;
- Repasse.

Categorias sem registros permanecem visíveis com estado vazio. Cada registro informa código, descrição, grupo, movimento, quantidade original, unidade, quantidade convertida, origem, data e hora, confiança, arquivo, linha e hash.

### Modo Auditor

Explica indicadores individuais e consolidados. A explicação é servida pela API e contém regra, versão, fórmula documentada, parâmetros, estratégia, linhagem, registros originais, hashes e divergências.

### Auditoria

Categorias implementadas:

- componentes conflitantes;
- OPs sem Centro de Recurso;
- OPs multicentro;
- perdas sem classificação;
- movimentos fora da janela;
- divergências com o relatório de referência;
- parâmetros provisórios;
- itens sem peso identificado;
- possíveis duplas deduções.

## 3. Como foi implementado

A aplicação possui uma API FastAPI fina sobre o SQLite e o Motor Analítico. O navegador solicita dados prontos por endpoints JSON. Não existem transações, fórmulas industriais, conversões ou regras de cálculo no HTML ou JavaScript.

O upload é processado em uma pasta temporária. O banco em uso só é substituído após importação e cálculo concluídos com sucesso.

## 4. Tecnologias

- Python 3.11+
- FastAPI 0.128.2
- Uvicorn 0.48.0
- SQLite
- Jinja2
- JavaScript, HTML e CSS nativos
- pypdf 5.9.0
- pytest 9.0.2

## 5. Arquivos criados ou alterados

### Criados

- `app/main.py`
- `app/templates/index.html`
- `app/static/app.js`
- `app/static/styles.css`
- `src/saqr/mvp.py`
- `src/saqr/query.py`
- `tests/test_mvp.py`
- `run_local.py`
- `iniciar_saqr.bat`
- `iniciar_saqr.sh`
- `requirements.txt`
- `README.md`
- `scripts/validate_increment_03.py`
- `validation_result.json`
- capturas em `screenshots/`

### Evoluídos a partir da baseline v0.2.0

- `src/saqr/analytical.py`
- `src/saqr/pipeline.py`
- `src/saqr/mpop_parser.py`
- `config/default.json`
- `data.sqlite`

## 6. Endpoints principais

- `GET /api/state`
- `GET /api/imports`
- `POST /api/import`
- `POST /api/strategy`
- `GET /api/summary`
- `GET /api/lines`
- `GET /api/ops`
- `GET /api/ops/{op}/components`
- `GET /api/losses`
- `GET /api/repasse`
- `GET /api/audit`
- `GET /api/explain/{indicator_id}`
- `POST /api/explain-aggregate`

## 7. Procedimento objetivo de validação

1. Executar `python -m pytest -q tests`.
2. Executar `python scripts/validate_increment_03.py`.
3. Iniciar o sistema com `python run_local.py`.
4. Confirmar as seis fontes na tela Importação.
5. Confirmar 16 OPs no resumo de 10/07/2026.
6. Confirmar a produção separada em 12.970 CX, 45 BR e 80 BD.
7. Confirmar lixo MPOP de 292,90955 kg.
8. Alternar as três estratégias e confirmar que a interface recebe novas execuções do Motor Analítico.
9. Abrir a OP 39629 e percorrer as oito categorias.
10. Ativar Modo Auditor e explicar um KPI, uma linha e uma OP.
11. Confirmar que as divergências de repasse e Centro de Recurso continuam visíveis.

## 8. Resultado esperado e obtido

- Importação real das seis fontes: aprovado.
- Navegação por resumo, linha, OP e componente: aprovado.
- Três estratégias: aprovado.
- Unidades incompatíveis separadas: aprovado.
- Explicação até o registro original: aprovado.
- Relatório de referência isolado dos cálculos: aprovado.
- Varredura da interface sem regras industriais codificadas: aprovado.

## 9. Limitações

- Sem autenticação, integrações externas, banco remoto ou publicação.
- As limitações metodológicas da baseline v0.2.0 permanecem registradas na auditoria.
- Algumas OPs não podem ser associadas a linhas porque o Centro de Recurso não existe na fonte oficial.
- O parser portátil `pypdf` pode segmentar o relatório completo de forma ligeiramente diferente de `pdftotext`, embora preserve o resultado homologado do dia operacional.

## 10. Critério de conclusão

O Incremento 03 é considerado concluído quando `validation_result.json` registra aprovação e a suíte automatizada passa integralmente.

## 11. Resultado final dos testes

- Testes automatizados: **12 de 12 aprovados**.
- Validações funcionais e de auditoria: **20 de 20 aprovadas**.
- Upload real das seis fontes: aprovado.
- JavaScript: sintaxe validada.
- Python: módulos compilados sem erro.
- Varredura da camada visual: nenhuma regra industrial ou transação codificada no HTML/JavaScript.
- Estratégias homologadas e pré-calculadas no banco de demonstração: janela operacional, acumulado completo e acumulado rateado.
