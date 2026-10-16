# Rastreabilidade da origem da investigação

Proposta complementar à correção do frontend `saqr-base-setup`, baseada no commit `13a968ede11a0530fd440251c04da53e3a35a568`.

`loss_id` é uma referência de origem opcional recebida no POST e devolvida no detalhe. É persistida no snapshot de origem e no evento de criação existentes, sem migração de tabelas, alteração de fórmulas ou mudança no contrato obrigatório de `calculation_id`, `indicator_id` e `title`. Registros anteriores retornam `loss_id: null`.

A existência e a associação cálculo/indicador continuam validadas pelo serviço. `loss_id` preserva o texto original, inclusive zeros à esquerda; essa extensão não valida automaticamente sua associação ao indicador. Não se deve converter um id de perda em id de indicador.

Validação: `PYTHONPATH=src python -m pytest tests/test_investigation.py tests/test_rules.py -q` — 20 testes passaram. Inclui criação → listagem filtrada → abertura, preservação do id de origem e lineage, rejeição do payload contendo apenas loss_id e rejeição de indicador de outro cálculo.

A suíte completa encontrou quatro falhas de `test_mvp.py` por ausência da base analítica esperada (503 e tabela calculation_runs ausente), além de um teste ignorado. Não foi fabricada base operacional para contornar essas dependências.

Não publicar: a API ativa ainda não foi identificada. O Lovable informou projeto não publicado e a prévia está protegida por login. O cliente usa VITE_SAQR_API_URL sem valor versionado. O frontend consome /api/perdas-mpop com envelope rows; o backend oferece /api/losses em outro formato. Ainda falta implementar e validar o produtor dos vínculos loss → calculation/indicator no ambiente correto e homologar com perda real antes de merge/deploy.
