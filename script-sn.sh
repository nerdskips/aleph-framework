# ============================================================
# REBRANDING: zuper → aleph
# ============================================================

# --- Prefixo Redis (mais crítico — afeta keys em produção) ---
# Se tiver agentes rodando com keys "zuper:" no Redis, eles vão
# perder estado. Se não tem agente em produção, segue direto.

# --- Python files: docstrings, loggers, prefixes ---
find core/ tests/ -name "*.py" -exec sed -i \
  -e 's/Zuper Agent Framework/Aleph Framework/g' \
  -e 's/zuper-agent-framework/aleph-framework/g' \
  -e 's/zuper-agent/aleph/g' \
  -e 's/zuper\.agent/aleph\.agent/g' \
  -e 's/"zuper\./"aleph\./g' \
  -e "s/'zuper\./'aleph\./g" \
  -e 's/zuper:{/aleph:{/g' \
  -e 's/f"zuper:/f"aleph:/g' \
  -e 's/prefix: zuper/prefix: aleph/g' \
  -e 's/Zuper AI/Aleph AI/g' \
  {} +

# --- CLI templates ---
find core/cli/templates/ -type f -exec sed -i \
  -e 's/Zuper Agent Framework/Aleph Framework/g' \
  -e 's/zuper-agent/aleph/g' \
  -e 's/Zuper Agent/Aleph/g' \
  -e 's/zuper\.com\.br/aleph\.dev/g' \
  -e 's/zuper-ai/aleph-framework/g' \
  {} +

# --- CLI main.py (container names, image names, help text) ---
sed -i \
  -e 's/zuper-agent/aleph/g' \
  -e 's/Zuper Agent Framework/Aleph Framework/g' \
  core/cli/main.py

# --- Docker compose ---
sed -i \
  -e 's/Zuper Agent Framework/Aleph Framework/g' \
  -e 's/zuper-agent/aleph/g' \
  -e 's/zuper_net/aleph_net/g' \
  docker-compose.yml

# --- pyproject.toml ---
sed -i \
  -e 's/name = "zuper-agent"/name = "aleph-agent"/g' \
  -e 's/Zuper Agent Framework/Aleph Framework/g' \
  -e 's/zuper-agent/aleph/g' \
  -e 's/zuper-ai/aleph-framework/g' \
  -e 's/zuper\.com\.br/aleph\.dev/g' \
  -e 's/Álef Souza/Álef Souza/g' \
  pyproject.toml

# --- Docs (informacional, não afeta runtime) ---
find docs/ -name "*.md" -exec sed -i \
  -e 's/Zuper Agent Framework/Aleph Framework/g' \
  -e 's/zuper-agent/aleph/g' \
  {} +
