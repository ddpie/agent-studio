# Notes: adding Kiro CLI to base/deployment.zip

`scripts/build-base-zip.sh` needs a new step to fetch Kiro CLI fresh on every
build and include it in `base/deployment.zip`. This file documents the intended
change — the script itself is not yet patched.

## Fetch step (to add before the final `zip -rqy` call on staged-slim)

```bash
fetch_kiro_cli() {
  local stage_dir="$1"
  local channel="stable"
  local base_url="https://prod.download.cli.kiro.dev"
  local manifest_url="${base_url}/${channel}/latest/manifest.json"
  local zip_name="kirocli-aarch64-linux.zip"
  local zip_url="${base_url}/${channel}/latest/${zip_name}"

  local tmp; tmp=$(mktemp -d)
  trap "rm -rf '$tmp'" RETURN

  echo "--- Fetch Kiro CLI (latest stable, aarch64-linux) ---"
  curl -fsSL -o "$tmp/$zip_name" "$zip_url"
  local manifest; manifest=$(curl -fsSL "$manifest_url")

  # Expected sha256 from manifest (mirrors install.sh get_checksum logic)
  local expected
  expected=$(echo "$manifest" | python3 -c "
import json,sys
m=json.load(sys.stdin)
for p in m.get('packages', []):
    if p.get('download','').endswith('$zip_name'):
        print(p['sha256']); break
")
  local actual; actual=$(sha256sum "$tmp/$zip_name" | cut -d' ' -f1)
  [[ "$actual" == "$expected" ]] || {
    echo "Kiro CLI checksum mismatch: expected $expected got $actual" >&2
    exit 1
  }

  unzip -q "$tmp/$zip_name" -d "$tmp/extract"
  # Payload layout: kirocli/{kiro-cli, kiro-cli-chat, install.sh, ...}
  # Copy the binaries into stage_dir/kiro-bin/ so main.py can locate them.
  mkdir -p "$stage_dir/kiro-bin"
  cp "$tmp/extract/kirocli/kiro-cli"      "$stage_dir/kiro-bin/kiro-cli"
  cp "$tmp/extract/kirocli/kiro-cli-chat" "$stage_dir/kiro-bin/kiro-cli-chat"
  chmod +x "$stage_dir/kiro-bin/"*

  echo "Kiro CLI installed to $stage_dir/kiro-bin (sha256=$expected)"
}
```

## Where to call

Inside `build_zip()`, after `pip install` and the `__pycache__` cleanup, before
the final `zip -rqy`. Only for the slim base (Meta-Agent); sub-agent zip does
not need Kiro.

## Size impact

- Slim base today: ~25MB (pip deps only)
- Kiro CLI: ~102MB (kiro-cli binary, per Test 1 confirmation)
- Plus kiro-cli-chat: unknown, likely similar
- **Expected slim base after Kiro: ~230MB**

Action item: verify AgentCore Runtime's zip upload limit. If it rejects >200MB,
fallback is to download Kiro at Runtime cold-start from S3 into /tmp instead
of baking it into the zip.

## KIRO_API_KEY plumbing

The API key is a secret, not shipped in the zip. It must be injected as an env
var on the Runtime via `AGENT_STUDIO_KIRO_API_KEY` → `KIRO_API_KEY` mapping in
`scripts/deploy-agentcore.sh`'s env_vars dict.
