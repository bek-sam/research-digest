#!/usr/bin/env python3
"""Stand-in for `claude -p`: reads the prompt, returns deterministic structured_output.

It answers whichever schema it is given: if the schema has items[] with id/relevance it scores every
item id found in the prompt. Behaviour knobs via env:
  FAKE_CLAUDE_LOG   append each call (argv + prompt) as JSON lines here
  FAKE_CLAUDE_FAIL  'always' -> is_error result; 'once' -> fail first call only (uses a marker file)
  FAKE_CLAUDE_REL   JSON {id: relevance} overrides
  FAKE_CLAUDE_OMIT  JSON [ids] to leave out of the answer
  FAKE_CLAUDE_FAIL_MODEL  fail only calls whose --model equals this
"""
import hashlib, json, os, re, sys

argv = sys.argv[1:]
prompt = sys.stdin.read()
schema = json.loads(argv[argv.index("--json-schema") + 1])
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps({"argv": argv, "prompt": prompt}) + "\n")

model = argv[argv.index("--model") + 1] if "--model" in argv else ""
if os.environ.get("FAKE_CLAUDE_FAIL_MODEL") == model:
    print(json.dumps({"type": "result", "is_error": True, "result": "simulated model failure"}))
    sys.exit(1)
fail = os.environ.get("FAKE_CLAUDE_FAIL")
if fail == "always" or (fail == "once" and not os.path.exists(log + ".failed")):
    if fail == "once":
        open(log + ".failed", "w").close()
    print(json.dumps({"type": "result", "is_error": True, "result": "simulated failure"}))
    sys.exit(1)


def h(s, mod):
    return int(hashlib.md5(s.encode()).hexdigest(), 16) % mod


def fill(sch, key=""):
    t = sch.get("type")
    if "enum" in sch:
        return sch["enum"][0]
    if t == "object":
        return {k: fill(v, k) for k, v in sch.get("properties", {}).items()}
    if t == "array":
        return [f"+ fake {key}"] if sch.get("items", {}).get("type") == "string" else []
    if t == "integer":
        return 5
    if t == "number":
        return 0.5
    if t == "boolean":
        return False
    return f"fake {key}"


ids = re.findall(r'"id":\s*"([^"]+)"', prompt)
out = fill(schema)
item_schema = schema.get("properties", {}).get("items", {}).get("items")
if item_schema:
    rows = []
    rel = json.loads(os.environ.get("FAKE_CLAUDE_REL", "{}"))
    omit = set(json.loads(os.environ.get("FAKE_CLAUDE_OMIT", "[]")))
    for i in dict.fromkeys(ids):
        if i in omit:
            continue
        row = fill(item_schema)
        row["id"] = i
        if "relevance" in row:
            row["relevance"] = 3 + h(i, 8)       # 3..10
            row["quality"] = 3 + h(i + "q", 8)
            if i in rel:
                row["relevance"] = rel[i]
        rows.append(row)
    out["items"] = rows
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "result": json.dumps(out), "structured_output": out,
                  "usage": {"output_tokens": 42}, "duration_ms": 10}))
