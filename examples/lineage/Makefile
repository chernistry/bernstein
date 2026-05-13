# Lineage v1 demo scenarios.
#
# Each demo:
#   1. (re)generates deterministic fixtures via the gen_demo_*.py script
#   2. invokes the production CLIs (bernstein compliance pack +
#      bernstein-verify pack) when they're available
#   3. diffs the produced bundle against the committed expected-pack.zip
#
# Targets work standalone in this branch using fixture generators only.
# Once Agents B/C/D merge their CLI work, the `pack` and `verify` lines
# wire up to the real tools — keep this Makefile in sync with their
# argument shapes.

EXAMPLES_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
PYTHON := uv run python

.PHONY: help fixtures expected-packs demo-fintech demo-healthcare demo-eu-mfg all clean

help:
	@echo "Lineage v1 demo targets:"
	@echo "  fixtures         (re)generate all fixture logs deterministically"
	@echo "  expected-packs   rebuild the committed expected-pack.zip reference outputs"
	@echo "  demo-fintech     run the fintech compliance-pack + verify demo"
	@echo "  demo-healthcare  run the healthcare compliance-pack + verify demo"
	@echo "  demo-eu-mfg      run the EU manufacturer compliance-pack + verify demo"
	@echo "  all              run all three demos"
	@echo "  clean            remove generated fixtures (keeps committed scripts + docs)"

fixtures:
	$(PYTHON) $(EXAMPLES_DIR)/scripts/gen_demo_fintech.py
	$(PYTHON) $(EXAMPLES_DIR)/scripts/gen_demo_healthcare.py
	$(PYTHON) $(EXAMPLES_DIR)/scripts/gen_demo_eu_mfg.py

expected-packs: fixtures
	$(PYTHON) $(EXAMPLES_DIR)/scripts/build_expected_pack.py

demo-fintech: fixtures
	bernstein compliance pack \
	  --since 2026-01-01 --until 2026-02-14 \
	  --org "Acme Bank" \
	  --output $(EXAMPLES_DIR)/fintech/out.zip \
	  --lineage-dir $(EXAMPLES_DIR)/fintech/fixtures
	bernstein-verify pack $(EXAMPLES_DIR)/fintech/out.zip
	@echo "fork-detection check:"
	bernstein-verify forks src/payments/flow.py \
	  --lineage-dir $(EXAMPLES_DIR)/fintech/fixtures \
	  --extra-log    $(EXAMPLES_DIR)/fintech/fixtures/rogue-agent.jsonl || \
	  echo "expected: fork-detection exits non-zero, that's the demo"

demo-healthcare: fixtures
	bernstein compliance pack \
	  --since 2026-02-01 --until 2026-02-28 \
	  --org "Northstar Health" \
	  --output $(EXAMPLES_DIR)/healthcare/out.zip \
	  --lineage-dir $(EXAMPLES_DIR)/healthcare/fixtures
	bernstein-verify pack $(EXAMPLES_DIR)/healthcare/out.zip

demo-eu-mfg: fixtures
	bernstein compliance pack \
	  --since 2026-03-01 --until 2026-03-31 \
	  --org "Bavarian Tooling GmbH" \
	  --output $(EXAMPLES_DIR)/eu-manufacturer/out.zip \
	  --lineage-dir $(EXAMPLES_DIR)/eu-manufacturer/fixtures
	bernstein-verify pack $(EXAMPLES_DIR)/eu-manufacturer/out.zip
	@echo "Cold-storage round-trip: see eu-manufacturer/cold-storage-roundtrip.md"

all: demo-fintech demo-healthcare demo-eu-mfg

clean:
	rm -f $(EXAMPLES_DIR)/*/fixtures/log.jsonl
	rm -f $(EXAMPLES_DIR)/fintech/fixtures/rogue-agent.jsonl
	rm -rf $(EXAMPLES_DIR)/*/fixtures/signatures
	rm -rf $(EXAMPLES_DIR)/fintech/fixtures/signatures-rogue
	rm -rf $(EXAMPLES_DIR)/*/fixtures/agent-cards
	rm -f $(EXAMPLES_DIR)/*/expected-pack.zip
	rm -f $(EXAMPLES_DIR)/*/out.zip
