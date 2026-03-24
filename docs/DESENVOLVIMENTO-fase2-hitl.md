# Zuper Agent Framework — Fase 2: Human-in-the-Loop (Escalation)

> Atualizado em 24/03/2026 — Sessão de desenvolvimento com Claude Opus 4.6
> Repo: `/root/zuper-framework/` no server Hostinger KVM2

---

## Resumo

Implementação do fluxo de escalonamento: quando o agente não sabe responder, pausa a conversa, notifica um humano responsável via WhatsApp, e retoma automaticamente quando o humano responde.

**Modelo:** Escalonamento via quote/reply na mesma instância Z-API.

---

## Arquitetura do Fluxo

### Fase 1 — Escalonar

```
Cliente envia msg → Guardrail detecta ESCALATE
  → Pipeline pausa (não chama LLM)
  → Redis: salva escalation (phone, msg, contexto, TTL 2h)
  → Z-API: notifica responsável com contexto
  → Redis: mapeia notificationMsgId → client phone
  → Cliente recebe: "Vou verificar com a equipe..."
```

### Fase 2 — Humano resolve

```
Responsável recebe notificação no WhatsApp
  → Lê contexto (cliente, mensagem, dados)
  → Responde com QUOTE na notificação
  → Z-API webhook chega com referenceMessageId
```

### Fase 3 — Retomar

```
/webhook/zapi detecta is_human_reply()
  → Resolve referenceMessageId → client phone (Redis)
  → Carrega escalation context (Redis)
  → LLM reformula instrução do humano no tom do agente
  → Z-API: envia resposta reformulada ao cliente
  → Redis: limpa escalation
  → Confirma ao responsável: "✅ Resposta enviada"
```

---

## Arquivos Criados/Modificados

### Novos

| Arquivo | Descrição |
|---|---|
| `core/session/redis_escalation.py` | `EscalationData` dataclass + métodos Redis (save, get, clear, map notification) |
| `core/human/__init__.py` | Exports do módulo |
| `core/human/escalation.py` | Lógica completa: `escalate_to_human()` + `handle_human_response()` + reformulação LLM |

### Modificados

| Arquivo | O que mudou |
|---|---|
| `core/engine/pipeline.py` | Wiring do ESCALATE/ESCALATE_NO_HABIT/TAKEOVER no pipeline. Novo param `phone`, `redis_session`, `sender`. Check de escalation ativa (step 1.5). Campo `escalated` no `PipelineResult`. |
| `core/api/webhooks.py` | Detecção de `is_human_reply()` ANTES de takeover. `_handle_escalation_reply()` async. Passa `phone/redis/sender` pro `process_message()`. `/webhook/humano` implementado com suporte a API direta. |

---

## Redis Keys (novas)

| Key | Tipo | TTL | Conteúdo |
|---|---|---|---|
| `zuper:{client_id}:esc:{phone}` | String (JSON) | `escalation_session_ttl` (2h) | `EscalationData`: client_phone, original_message, context, responsible_phone, notification_message_id, agent_name, timestamp |
| `zuper:{client_id}:esc_msg:{messageId}` | String | `escalation_session_ttl` (2h) | Client phone (reverse lookup do quote) |

---

## Config YAML — Campos Relevantes

Nenhum campo novo foi adicionado ao schema. O escalation usa os campos existentes do `HumanConfig`:

```yaml
human:
  enabled: true                           # DEFAULT ON
  escalation_session_ttl: 7200            # 2h — TTL da sessão pausada
  responsible_phones: ["5534999999999"]    # Quem recebe notificação
  notify_via: "zapi"                      # Canal de notificação (futuro: n8n)
  release_keyword: "RELEASE"              # Keyword pra soltar takeover
```

### Exemplo de guardrail que aciona escalation

```yaml
guardrails:
  input_patterns:
    - name: "cancelamento"
      keywords: ["cancelar", "quero cancelar", "cancela minha"]
      action: "escalate"
      priority: 20

    - name: "reclamacao_grave"
      keywords: ["procon", "processo", "advogado"]
      action: "escalate_no_habit"
      priority: 30
```

**Diferença `escalate` vs `escalate_no_habit`:**
- `escalate`: no futuro (quando hábitos forem implementados), consulta hábitos antes de escalonar. Se já tem resposta aprendida, usa ela.
- `escalate_no_habit`: SEMPRE escalona, nunca consulta hábitos. Para casos críticos onde humano precisa decidir.

Ambos funcionam identicamente nesta fase (hábitos ainda não implementados).

---

## Fluxo Detalhado no Código

### 1. Mensagem chega no webhook

```
webhooks.py: webhook_zapi()
  → extract_message(payload)
  → should_filter() — filtra se não for mensagem válida
  → is_human_reply() — NOVO: checa se é resposta de escalation
      Se sim → _handle_escalation_reply() async
  → is_human_takeover_message() — checa se humano digitou no WhatsApp
  → is_duplicate() — anti-spam
  → buffer_message() → _process_after_buffer()
```

### 2. Pipeline processa

```
pipeline.py: process_message()
  → classify_input() — guardrail detecta ESCALATE
  → _handle_escalation() — NOVO
      → get_context() do Redis
      → escalate_to_human()
          → build_notification_message()
          → sender.send_notification() → retorna messageId
          → redis.save_escalation()
          → redis.map_notification_to_client()
      → retorna PipelineResult(escalated=True, response=hold_message)
```

### 3. Mensagens durante escalation

```
pipeline.py: process_message()
  → Step 1.5: is_escalation_active(phone)
      Se sim → retorna "Sua dúvida já está sendo verificada..."
      (evita processar novas msgs enquanto espera humano)
```

### 4. Humano responde

```
webhooks.py: webhook_zapi()
  → is_human_reply(message, responsible_phones) = True
  → _handle_escalation_reply()
      → handle_human_response()
          → resolve_notification_to_client(referenceMessageId)
          → get_escalation(client_phone)
          → _reformulate_response() via LLM
          → sender.send_response(client_phone, reformulated)
          → clear_escalation(client_phone)
          → sender.send_notification(responsible, "✅ Enviada")
```

---

## Integração com RedisSession

Os métodos de `redis_escalation.py` precisam ser incorporados ao `RedisSession` em `core/session/redis.py`. A estrutura está separada para review limpo. Para integrar:

1. Copiar a classe `EscalationData` para `redis_escalation.py` (já está)
2. Adicionar os métodos documentados no bloco de comentário ao `RedisSession`
3. Adicionar import: `from core.session.redis_escalation import EscalationData`

Métodos a adicionar no `RedisSession`:

- `save_escalation(data: EscalationData) → None`
- `get_escalation(client_phone: str) → EscalationData | None`
- `clear_escalation(client_phone: str) → None`
- `is_escalation_active(client_phone: str) → bool`
- `map_notification_to_client(notification_message_id: str, client_phone: str) → None`
- `resolve_notification_to_client(notification_message_id: str) → str | None`

---

## Ordem de Prioridade de Detecção no Webhook

A ordem de checks no `webhook_zapi` é crítica:

1. **Filter** (grupos, newsletters, reactions, etc) — descarta lixo
2. **is_human_reply** — ANTES de takeover! Senão o quote do responsável seria tratado como takeover
3. **is_human_takeover_message** — humano digitando no WhatsApp do agente
4. **is_takeover_active** — ignora msgs do cliente durante takeover
5. **is_duplicate** — anti-spam por messageId
6. **buffer** → pipeline

---

## Decisões de Design

### Por que o humano responde pela mesma instância Z-API?

- Zero infraestrutura extra (sem segundo número, sem painel)
- O responsável recebe notificação no WhatsApp normal
- Responde com quote — o Z-API captura o `referenceMessageId`
- Framework resolve o quote → client phone automaticamente

### Por que o LLM reformula a resposta?

- Mantém tom consistente do agente (o cliente não percebe que outro respondeu)
- O humano pode ser telegráfico ("prazo 5 dias, pode acompanhar pelo app") e o LLM transforma em resposta natural
- Fallback: se LLM falhar, envia a instrução do humano direto (sem reformulação)

### Por que check de escalation ativa no step 1.5?

- Evita que o agente responda enquanto espera o humano
- Se o cliente manda "oi, alguém aí?" durante a espera, recebe uma mensagem educada
- Não consome tokens de LLM

### Por que `is_human_reply` vem antes de `is_human_takeover_message`?

- O responsável pode ser a mesma pessoa que opera o WhatsApp do agente
- Sem essa ordem, um quote do responsável seria detectado como takeover
- `is_human_reply` exige `referenceMessageId` + phone na lista de responsáveis
- `is_human_takeover_message` só exige `fromMe=true && fromApi=false`

---

## Edge Cases Tratados

| Cenário | Comportamento |
|---|---|
| Escalation mas `human.enabled=false` | Log warning, fall through pro LLM |
| Escalation sem `responsible_phones` configurado | Log error, fall through pro LLM |
| Notificação Z-API falha (API fora) | Log error, fall through pro LLM |
| Humano responde mas sessão expirou (TTL) | Notifica responsável: "sessão expirou" |
| Cliente manda msg durante escalation | Recebe: "já está sendo verificada..." |
| LLM falha na reformulação | Fallback: envia instrução do humano direto |
| Responsável não responde (TTL expira) | Sessão limpa automaticamente pelo Redis TTL |
| Quote de mensagem que não é escalation | `resolve_notification_to_client` retorna None, ignorado |

---

## Testes Necessários

### Unitários (pytest)

- [ ] `EscalationData` serialização/deserialização (to_json/from_json)
- [ ] `build_notification_message` com e sem contexto
- [ ] `is_human_reply` com normalização de telefone BR (55XX9 vs 55XX)

### Integração (precisa Redis)

- [ ] `save_escalation` → `get_escalation` → `clear_escalation`
- [ ] `map_notification_to_client` → `resolve_notification_to_client`
- [ ] `is_escalation_active` true/false
- [ ] TTL expira corretamente

### E2E (precisa Z-API + Redis)

- [ ] Guardrail ESCALATE → notificação chega → quote → resposta chega ao cliente
- [ ] Mensagem durante escalation → "já está sendo verificada"
- [ ] Sessão expira → humano tenta responder → recebe aviso

---

## Próximos Passos

### Imediato (finalizar esta fase)

1. Integrar métodos Redis no `RedisSession` (copiar do redis_escalation.py)
2. Testar fluxo completo com agente de teste
3. Escrever testes unitários

### Futuro (outras fases)

- **Hábitos**: quando implementado, `ESCALATE` consulta hábitos antes de escalonar
- **Takeover completo**: fluxo de transbordo (já tem Redis, falta lógica de fluxo)
- **Múltiplos responsáveis**: round-robin, disponibilidade, horário de atendimento
- **Notificação via N8N**: canal alternativo para notificação
- **Histórico de escalations**: log pra análise (quais perguntas mais escalonam)

---

*Zuper AI — Álef Souza — 24/03/2026*
