#!/bin/bash
# Usage: ./scripts/aws-session.sh <MFA_CODE>
# Reuses the Voice_S2S role + MFA device (see its assume-role.sh) but writes the
# temporary credentials to .local/aws-session.env (git-ignored, mode 600) so
# the harness can load them without pasting secrets into a chat.
set -euo pipefail
SRC="$HOME/Code_Projects/AntiGravity_projects/Voice_S2S/assume-role.sh"
ROLE_ARN=$(sed -n 's/^ROLE_ARN="\(.*\)"/\1/p' "$SRC")
MFA_SERIAL=$(sed -n 's/^MFA_SERIAL="\(.*\)"/\1/p' "$SRC")
[ -n "${1:-}" ] || { echo "usage: $0 <MFA_CODE>" >&2; exit 2; }
OUT="$(cd "$(dirname "$0")/.." && pwd)/.local/aws-session.env"
CREDS=$(aws sts assume-role --profile voices2s-source --role-arn "$ROLE_ARN" \
  --role-session-name voice-harness-dev --serial-number "$MFA_SERIAL" \
  --token-code "$1" --duration-seconds 43200 --query Credentials --output json)
umask 077
python3 - "$CREDS" > "$OUT" <<'PY'
import json,sys
c=json.loads(sys.argv[1])
print(f"AWS_ACCESS_KEY_ID={c['AccessKeyId']}")
print(f"AWS_SECRET_ACCESS_KEY={c['SecretAccessKey']}")
print(f"AWS_SESSION_TOKEN={c['SessionToken']}")
print("AWS_REGION=us-east-1")
print(f"# expires {c['Expiration']}")
PY
echo "Credentials written to .local/aws-session.env (expire: $(python3 -c "import json,sys;print(json.loads(sys.argv[1])['Expiration'])" "$CREDS"))"
