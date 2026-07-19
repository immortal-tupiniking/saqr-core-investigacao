# SAQR MVP v0.3.0

Sistema local e auditável para avaliação de quebras e rendimentos do Grupo Vale Fértil.

Esta versão implementa a primeira interface web do SAQR. A interface não acessa os arquivos importados e não contém regras de negócio. Todo valor é solicitado à API e entregue pelo Motor Analítico.

## Fluxo obrigatório

```text
Arquivos importados
        ↓
Motor de Dados
        ↓
Modelo de Domínio em SQLite
        ↓
Motor Analítico versionado
        ↓
API local
        ↓
Interface web
```

## Tecnologias

- Python 3.11 ou superior
- FastAPI e Uvicorn
- SQLite
- Jinja2
- HTML, CSS e JavaScript sem framework
- pypdf como alternativa portátil para leitura do PDF
- pytest e FastAPI TestClient

## Início rápido no Windows

1. Extraia o pacote em uma pasta local.
2. Dê dois cliques em `iniciar_saqr.bat`.
3. Na primeira execução, o ambiente virtual e as dependências serão instalados.
4. O navegador abrirá em `http://127.0.0.1:8000`.

O pacote já contém o banco homologado do dia operacional de 10/07/2026 para navegação imediata.
Os três cenários de consumo estão pré-calculados nesse banco, permitindo alternância imediata durante a homologação.

## Execução manual

### Windows PowerShell ou Prompt de Comando

```text
py -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python run_local.py
```

### Linux ou macOS

```text
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python run_local.py
```

## Importação

Na tela **Importação**, carregue:

1. `movimentação diárias.xlsx`
2. `Movimentações com centro de recurso.xlsx`
3. `MPOP301.PDF`
4. `perda padrão atual VF 03.2026.xlsx`
5. `Categorias.xlsx`
6. relatório de referência, opcional

O sistema valida extensões, abas e colunas obrigatórias. Após a importação, apresenta status, quantidade de registros, alertas, erros, data e hora e SHA-256 de cada fonte.

O relatório de referência é armazenado isoladamente e utilizado somente para homologação.

## Telas

- Resumo Executivo
- Consumo por Linha
- Consumo por OP
- Detalhamento da OP por categoria
- Perdas MPOP
- Repasse
- Auditoria
- Importação

## Estratégias de consumo

A seleção da estratégia apenas solicita uma execução já calculada ou uma nova execução ao Motor Analítico:

- janela operacional;
- acumulado completo da OP;
- acumulado da OP rateado pela produção do dia.

A interface não recalcula os valores.

## Modo Auditor

Ative **Modo Auditor** e selecione **Como este valor foi calculado?** em um indicador. O sistema apresenta:

- regra e versão;
- fórmula documentada pelo Motor Analítico;
- parâmetros aplicados;
- estratégia ativa;
- indicadores-fonte;
- registros originais;
- arquivo e linha de origem;
- hash do registro e SHA-256 da fonte;
- nível de confiança;
- divergências relacionadas.

## Testes

```text
python -m pytest -q tests
```

Validação objetiva adicional:

```text
python scripts/validate_increment_03.py
```

O resultado é gravado em `validation_result.json`.

## Estrutura principal

```text
saqr_mvp_v0_3/
├── app/
│   ├── main.py
│   ├── static/
│   │   ├── app.js
│   │   └── styles.css
│   └── templates/
│       └── index.html
├── config/
├── docs/
├── screenshots/
├── scripts/
├── src/saqr/
├── tests/
├── workspace/output/
├── data.sqlite
├── run_local.py
├── iniciar_saqr.bat
├── iniciar_saqr.sh
└── requirements.txt
```

## Limitações conhecidas

- Uso local, sem autenticação e sem publicação externa.
- Sem OneDrive, Microsoft Graph ou banco remoto.
- A perda padrão ainda depende de mapeamento parametrizado por descrição e permanece estimada.
- O MPOP não informa o horário individual de cada perda.
- OPs sem Centro de Recurso permanecem pendentes.
- OP multicentro não recebe rateio automático.
- A densidade do azeite permanece parâmetro provisório.
- A leitura do PDF prioriza `pdftotext` quando disponível e usa `pypdf` como alternativa. A alternativa preserva o resultado validado do dia, mas pode produzir pequenas diferenças na segmentação global de linhas do relatório.
- O banco SQLite é adequado ao MVP local, não a acesso simultâneo por muitos usuários.

## Segurança operacional

O servidor escuta apenas em `127.0.0.1`. Ele não é publicado na rede e não deve ser usado como servidor de produção.
