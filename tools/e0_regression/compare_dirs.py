import sys, hashlib, json, os
def h(p): return hashlib.sha256(open(p,'rb').read()).hexdigest()
a, b = sys.argv[1], sys.argv[2]
ok = True
for f in ("events.jsonl", "trades.jsonl", "summary.json"):
    ha, hb = h(f"{a}/{f}"), h(f"{b}/{f}")
    same = ha == hb; ok &= same
    n = sum(1 for _ in open(f"{a}/{f}"))
    print(f"{f:14} lines={n:6} sha256 A={ha[:12]} B={hb[:12]} {'IDENTICAL' if same else 'DIFFERENT'}")
print("RESULT:", "BYTE-IDENTICAL" if ok else "DIFFERENCES")
sys.exit(0 if ok else 1)
