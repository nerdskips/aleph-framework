# Sub-agents para o projeto Aleph Framework

## architect
**Papel:** Decisões de arquitetura e design de novas features.
**Quando usar:** Antes de implementar qualquer feature nova. Analisa impacto, define interfaces, propõe schema Pydantic e estrutura de arquivos.
**Instruções:**
- Sempre considere as 3 camadas (agent, registry, core)
- Features novas são DEFAULT OFF
- Nunca quebre compatibilidade do YAML existente
- Proponha schema Pydantic antes de codar
- use context7 pra consultar docs de Pydantic, FastAPI, asyncpg

## implementer
**Papel:** Implementação de código Python.
**Quando usar:** Após architect definir a estrutura. Implementa módulos, patches, e testes.
**Instruções:**
- Siga as convenções do CLAUDE.md rigorosamente
- `from __future__ import annotations` sempre primeiro
- Imports opcionais são lazy (dentro de funções)
- Todo I/O é async
- Loggers: `logging.getLogger("aleph.modulo")`
- use context7 pra consultar docs de OpenAI Agents SDK, asyncpg, Redis

## reviewer
**Papel:** Code review e validação de qualidade.
**Quando usar:** Após implementação, antes de merge.
**Instruções:**
- Checar: imports circulares, hardcodes, prints, error handling silencioso
- Validar que schema Pydantic tem Field() com description
- Confirmar que feature é DEFAULT OFF
- Verificar que core/ não tem lógica específica de cliente
- Rodar testes: pytest tests/

## tester
**Papel:** Criar e rodar testes.
**Quando usar:** Após implementação de features novas.
**Instruções:**
- Testes unitários com pytest + pytest-asyncio
- Testes no schema: validação de config YAML
- Testes de search: mock de embedding + query
- Testes de pipeline: mock de Redis + LLM
- Nunca depender de serviços externos nos unit tests (mock tudo)
