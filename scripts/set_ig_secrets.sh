#!/usr/bin/env bash
# Save an account's Instagram credentials as repo secrets.
#
#   bash scripts/set_ig_secrets.sh <account id>        e.g. islam, hinduism
#
# Reads the token without showing it, asks Instagram which account it belongs to (a token
# generated while logged in to the wrong account is the easiest mistake to make), and only
# after you confirm saves the two secrets named in accounts/<id>.json. Needs the GitHub CLI.
set -euo pipefail

account=${1:?usage: bash scripts/set_ig_secrets.sh <account id, e.g. islam>}
cd "$(dirname "$0")/.."
[[ -f "accounts/$account.json" ]] || { echo "There's no accounts/$account.json" >&2; exit 1; }

secret_name() {
  python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["secrets"][sys.argv[2]])' "accounts/$account.json" "$1"
}
id_secret=$(secret_name user_id)
token_secret=$(secret_name access_token)

read -rsp "Paste the Instagram token for '$account' (it won't show): " token
echo

me=$(curl -sS -H "Authorization: Bearer $token" "https://graph.instagram.com/v26.0/me?fields=user_id,username")
field() { python3 -c 'import json, sys; print(json.loads(sys.argv[1])[sys.argv[2]])' "$me" "$1" 2>/dev/null; }
user_id=$(field user_id) || { echo "Instagram rejected the token: $me" >&2; exit 1; }
username=$(field username || echo "?")

read -rp "That token is for @$username. Save it as the '$account' account's secrets? [y/N] " ok
[[ $ok == [yY] ]] || { echo "Nothing saved."; exit 1; }

gh secret set "$id_secret" --body "$user_id"
printf %s "$token" | gh secret set "$token_secret"
unset token
echo "Saved $id_secret and $token_secret."
