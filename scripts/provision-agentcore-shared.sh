#!/usr/bin/env bash
# Idempotently provision the account-shared CodeInterpreter + Browser
# resources for Agent Studio sub-agents. Writes their IDs to .env.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/.env"

source "${SCRIPT_DIR}/lib/env-utils.sh"

if [[ -f "$ENV_FILE" ]]; then
  set -a; source "$ENV_FILE"; set +a
fi

REGION="${AGENT_STUDIO_REGION:?AGENT_STUDIO_REGION is required}"
ACCOUNT_ID="${AGENT_STUDIO_ACCOUNT_ID:?AGENT_STUDIO_ACCOUNT_ID is required}"

ROLE_ARN="${AGENT_STUDIO_SUBAGENT_BASIC_ROLE_ARN:-arn:aws:iam::${ACCOUNT_ID}:role/AgentStudioSubAgent-basic-${REGION}}"

CI_NAME="${AGENT_STUDIO_CODE_INTERPRETER_NAME:-agentstudio_ci_shared}"
BR_NAME="${AGENT_STUDIO_BROWSER_NAME:-agentstudio_br_shared}"

existing_ci=$(aws bedrock-agentcore-control list-code-interpreters --region "$REGION" \
  --query "codeInterpreters[?name=='$CI_NAME'].codeInterpreterId | [0]" --output text 2>/dev/null || echo "None")
if [[ "$existing_ci" == "None" || -z "$existing_ci" ]]; then
  echo "Creating code interpreter $CI_NAME..."
  ci=$(aws bedrock-agentcore-control create-code-interpreter \
    --region "$REGION" \
    --name "$CI_NAME" \
    --description "Shared Code Interpreter for Agent Studio sub-agents" \
    --execution-role-arn "$ROLE_ARN" \
    --network-configuration networkMode=PUBLIC \
    --query codeInterpreterId --output text)
else
  ci="$existing_ci"
fi

existing_br=$(aws bedrock-agentcore-control list-browsers --region "$REGION" \
  --query "browsers[?name=='$BR_NAME'].browserId | [0]" --output text 2>/dev/null || echo "None")
if [[ "$existing_br" == "None" || -z "$existing_br" ]]; then
  echo "Creating browser $BR_NAME..."
  br=$(aws bedrock-agentcore-control create-browser \
    --region "$REGION" \
    --name "$BR_NAME" \
    --description "Shared Browser for Agent Studio sub-agents" \
    --execution-role-arn "$ROLE_ARN" \
    --network-configuration networkMode=PUBLIC \
    --query browserId --output text)
else
  br="$existing_br"
fi

echo "Code Interpreter: $ci"
echo "Browser: $br"
update_env "$ENV_FILE" AGENT_STUDIO_CODE_INTERPRETER_ID "$ci"
update_env "$ENV_FILE" AGENT_STUDIO_BROWSER_ID "$br"
