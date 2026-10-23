import re, pathlib
BT = chr(96)
p = pathlib.Path("/Users/lge/Desktop/topic/.diag-ta-20261002/study-design-20261009/review-20261017/INDEPENDENT-REVIEW.md")
lines = p.read_text(encoding="utf-8").splitlines()
# drop any existing index block
start = None; end = None
for i, ln in enumerate(lines):
    if ln.startswith("### 12 section index") or ln.startswith("### \u00a712 "):
        start = i
    elif start is not None and end is None and i > start and not ln.startswith("- **12.") and ln.strip():
        end = i
        break
if start is not None:
    lines = lines[:start] + lines[end:]
heads = []
for ln in lines:
    m = re.match(r"^### (12\.(\d+)([a-z]?))\s+(.*)$", ln)
    if m:
        heads.append((int(m.group(2)), m.group(3), m.group(1), m.group(4).strip()))
heads.sort(key=lambda h: (h[0], h[1]))
block = ["", "### 12 section index (sorted by number; the body is append-at-top, so the two orders differ)", ""]
for num, suf, full, title in heads:
    block.append("- **" + full + "** " + title.replace("**", "").replace(BT, ""))
block.append("")
anchor = None
for i, ln in enumerate(lines):
    if ln.startswith("## 12. 2026-10-19"):
        anchor = i
        break
assert anchor is not None
lines = lines[:anchor+1] + block + lines[anchor+1:]
p.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("index rebuilt with", len(heads), "entries")
