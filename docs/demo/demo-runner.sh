#!/usr/bin/env bash
# Bernstein README demo runner - drives docs/demo/demo.tape (vhs).
#
# Simulates a real run for recording purposes. Output is timed to a 60s
# window with: manager decompose -> 3 parallel agents -> audit chain ->
# janitor verify -> PR opened.
#
# Run directly to preview:
#   bash docs/demo/demo-runner.sh

set -euo pipefail

RESET='\033[0m'
BOLD='\033[1m'
DIM='\033[2m'
GREEN='\033[32m'
YELLOW='\033[33m'
BLUE='\033[34m'
CYAN='\033[36m'
MAGENTA='\033[35m'
WHITE='\033[97m'

s() { sleep "${1:-0.3}"; return 0; }

# ---- Banner ---------------------------------------------------------------
echo ""
printf "${BLUE}${BOLD}bernstein${RESET} ${DIM}v1.9.0${RESET}\n"
s 0.3
printf "${DIM}goal:${RESET} ${WHITE}\"Add auth, tests, and docs\"${RESET}\n"
s 0.4

# ---- Manager decompose ----------------------------------------------------
printf "\n${BOLD}${WHITE}[manager]${RESET} decomposing goal...\n"
s 0.6
printf "  ${CYAN}T-001${RESET}  ${WHITE}implement JWT auth middleware${RESET}    ${DIM}role=backend  files=src/auth/${RESET}\n"
s 0.2
printf "  ${CYAN}T-002${RESET}  ${WHITE}unit + integration tests${RESET}         ${DIM}role=qa       files=tests/${RESET}\n"
s 0.2
printf "  ${CYAN}T-003${RESET}  ${WHITE}API docs with usage examples${RESET}     ${DIM}role=docs     files=docs/api/${RESET}\n"
s 0.5

# ---- Spawn 3 parallel agents ----------------------------------------------
printf "\n${BOLD}${WHITE}[scheduler]${RESET} spawning 3 agents in parallel worktrees\n"
s 0.4
printf "  ${GREEN}>${RESET} ${WHITE}claude-backend${RESET}  ${DIM}claude-sonnet-4-6${RESET}  ${MAGENTA}.sdd/wt/T-001${RESET}\n"
s 0.25
printf "  ${GREEN}>${RESET} ${WHITE}codex-qa${RESET}        ${DIM}gpt-5${RESET}              ${MAGENTA}.sdd/wt/T-002${RESET}\n"
s 0.25
printf "  ${GREEN}>${RESET} ${WHITE}gemini-docs${RESET}     ${DIM}gemini-2.5-pro${RESET}     ${MAGENTA}.sdd/wt/T-003${RESET}\n"
s 0.5

# ---- Live activity feed ---------------------------------------------------
printf "\n${DIM}-----------------------------------------------------------${RESET}\n"
s 0.2
printf "${DIM}[00:04]${RESET} ${CYAN}backend${RESET}  src/auth/jwt.py created\n"
s 0.3
printf "${DIM}[00:05]${RESET} ${CYAN}docs${RESET}     scanning routes in src/api/...\n"
s 0.3
printf "${DIM}[00:07]${RESET} ${CYAN}qa${RESET}       drafting tests/test_auth.py (12 cases)\n"
s 0.4
printf "${DIM}[00:11]${RESET} ${CYAN}backend${RESET}  middleware mounted on FastAPI app\n"
s 0.3
printf "${DIM}[00:14]${RESET} ${CYAN}docs${RESET}     writing docs/api/auth.md\n"
s 0.3
printf "${DIM}[00:17]${RESET} ${CYAN}backend${RESET}  refresh-token endpoint added\n"
s 0.3
printf "${DIM}[00:21]${RESET} ${CYAN}qa${RESET}       pytest -q ... ${GREEN}12 passed${RESET}\n"
s 0.4
printf "${DIM}[00:24]${RESET} ${CYAN}backend${RESET}  commit a3f9c1b feat(auth): JWT middleware\n"
s 0.3
printf "${DIM}[00:26]${RESET} ${CYAN}qa${RESET}       commit b8e2d44 test(auth): 12 cases\n"
s 0.3
printf "${DIM}[00:28]${RESET} ${CYAN}docs${RESET}     commit c1a5e7f docs(api): auth reference\n"
s 0.5

# ---- Audit chain ----------------------------------------------------------
printf "\n${BOLD}${WHITE}[audit]${RESET} HMAC chain (head -> tail)\n"
s 0.4
printf "  ${YELLOW}#012${RESET} ${DIM}spawn   T-001 -> claude-backend${RESET}  ${DIM}sha=4f1a..b2${RESET}\n"
s 0.15
printf "  ${YELLOW}#013${RESET} ${DIM}spawn   T-002 -> codex-qa${RESET}        ${DIM}sha=8d33..1c${RESET}\n"
s 0.15
printf "  ${YELLOW}#014${RESET} ${DIM}spawn   T-003 -> gemini-docs${RESET}     ${DIM}sha=e91c..7a${RESET}\n"
s 0.15
printf "  ${YELLOW}#015${RESET} ${DIM}commit  a3f9c1b on T-001${RESET}         ${DIM}sha=2b4f..d9${RESET}\n"
s 0.15
printf "  ${YELLOW}#016${RESET} ${DIM}commit  b8e2d44 on T-002${RESET}         ${DIM}sha=6c0e..52${RESET}\n"
s 0.15
printf "  ${YELLOW}#017${RESET} ${DIM}commit  c1a5e7f on T-003${RESET}         ${DIM}sha=9a72..04${RESET}\n"
s 0.4
printf "  ${GREEN}chain verified${RESET}  ${DIM}prev-hash links ok, no gaps${RESET}\n"
s 0.5

# ---- Janitor verify -------------------------------------------------------
printf "\n${BOLD}${YELLOW}[janitor]${RESET} verifying gates...\n"
s 0.3
printf "  ${GREEN}ok${RESET}  tests pass               ${DIM}12/12 + 124 regressions${RESET}\n"
s 0.2
printf "  ${GREEN}ok${RESET}  ruff + mypy clean\n"
s 0.2
printf "  ${GREEN}ok${RESET}  pii scan clean\n"
s 0.2
printf "  ${GREEN}ok${RESET}  cross-model review     ${DIM}codex reviewed claude's diff${RESET}\n"
s 0.4

# ---- PR opened ------------------------------------------------------------
printf "\n${BOLD}${WHITE}[pr]${RESET} opening pull request\n"
s 0.4
printf "  ${GREEN}->${RESET} ${WHITE}https://github.com/your-org/your-repo/pull/247${RESET}\n"
s 0.3
printf "     ${DIM}body: cost \$0.42 | tokens 38k in / 12k out | 3 commits | audit-chain attached${RESET}\n"
s 0.5

# ---- Final summary --------------------------------------------------------
printf "\n${BOLD}${GREEN}done${RESET}  ${DIM}3 tasks, 47s, \$0.42${RESET}\n"
s 0.3
