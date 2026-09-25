#!/usr/bin/env bash
# Reproduce the VM deployment tree from two pinned commits.
#
#   usage: deploy_recipe.sh <parent-workspace> <platform-repo> <platform-commit> <dest> [instance-commit] [deploy-commit]
#
# source_tree_sha256 is a function of (file set, deploy commit SHA): the guard
# appends a synthetic .deployment_commit record holding sha256(commit + newline).
# Pass the recorded deploy commit to verify the identity of an existing
# deployment; omit it and the recipe commits the composed tree itself and
# reports the identity of THAT commit.
#
# The canonical deployment workspace is the RESEARCH repository
# (leo-direct-sim), which owns LITERATURE/PAPER/AGENTS.md/DECISIONS.md/
# NOTES.md/LICENSE and satisfies deployment_guard's full-workspace contract.
# The platform repository (leo-experiment-platform) owns CODE/, but its history
# does not contain the research repository's commits and vice versa: they are
# two lineages.  The deployment tree is therefore defined as
#
#     clone of the research workspace at its HEAD
#       with CODE/ replaced by the platform tree at a pinned commit
#
# which is exactly what push-remote.sh then stages, hashes and installs.  This
# script reproduces that tree and prints its identity so the deployment is
# reproducible from two SHAs instead of from a narrative.
set -euo pipefail

PARENT="${1:?parent workspace path}"
PLATFORM="${2:?platform repo path}"
COMMIT="${3:?platform commit}"
DEST="${4:?destination}"
INSTANCE="${5:-}"
DEPLOY_COMMIT="${6:-}"

rm -rf "$DEST"
git clone -q "$PARENT" "$DEST"
# pin the research side too: record what it was
PARENT_HEAD="$(git -C "$DEST" rev-parse HEAD)"
rm -rf "$DEST/CODE"
git -C "$PLATFORM" archive "$COMMIT" CODE | tar -x -C "$DEST"
if [[ -n "$INSTANCE" ]]; then
    # Only the compiled + authorized experiment instances, NOT the platform's
    # own EXPERIMENTS/ tree: the research workspace owns EXPERIMENTS/contracts,
    # experiment-program.yaml and templates, and overwriting them changes the
    # deployed identity (caught by diffing the composed tree against the
    # recorded deployment: exactly one file differed).
    INSTANCE_LIST="$(mktemp)"
    git -C "$PLATFORM" ls-tree -r --name-only "$INSTANCE" \
        | grep -E '^(EXPERIMENTS/EXP-LEO-V2-ACCEPT-[^/]+|CODE/work/WP-LEO-V2-ACCEPT-[^/]+)/' \
        > "$INSTANCE_LIST" || true
    if [[ ! -s "$INSTANCE_LIST" ]]; then
        echo "instance commit carries no acceptance artifacts" >&2
        exit 1
    fi
    # shellcheck disable=SC2046  # the paths come from git ls-tree and never contain spaces
    git -C "$PLATFORM" archive "$INSTANCE" -- $(cat "$INSTANCE_LIST") | tar -x -C "$DEST"
    rm -f "$INSTANCE_LIST"
    echo "instance_commit=$INSTANCE"
fi
cp "$PLATFORM/CODE/scripts/remote/remote.env" "$DEST/CODE/scripts/remote/remote.env"

cd "$DEST"
if [[ -z "$DEPLOY_COMMIT" ]]; then
    git add -A CODE EXPERIMENTS
    git -c user.name="leo-platform-agent" -c user.email="agent@local" \
        commit -q -m "chore: composed deployment tree (verified by deploy_recipe.sh)"
    DEPLOY_COMMIT="$(git rev-parse HEAD)"
    echo "composed_deploy_commit=$DEPLOY_COMMIT"
fi
echo "parent_commit=$PARENT_HEAD"
echo "platform_commit=$COMMIT"
echo "code_sha256=$(PYTHONDONTWRITEBYTECODE=1 python3 -c 'from CODE.leo_sim.receipt import code_sha256; print(code_sha256())')"
# deployment_guard computes source_tree_sha256 over the staged file set; ask it
# Use the real archive builder rather than re-deriving the hash: source_tree_sha256
# covers the file records PLUS a synthetic witness record that carries the source
# commit, so a hand-rolled hash of the files alone does not reproduce it.
PYTHONDONTWRITEBYTECODE=1 python3 - "$DEPLOY_COMMIT" <<'PY'
import json, pathlib, sys, tempfile
sys.path.insert(0, "CODE/scripts/remote")
import deployment_guard as dg
root = pathlib.Path(".").resolve()
deploy_commit = sys.argv[1]
with tempfile.TemporaryDirectory() as tmp:
    archive = pathlib.Path(tmp) / "a.tar.gz"
    manifest = dg.build_archive(root, archive, deploy_commit, "main")
print("file_count=%d" % len(manifest["deployed_files"]))
print("source_tree_sha256=%s" % manifest["source_tree_sha256"])
PY
