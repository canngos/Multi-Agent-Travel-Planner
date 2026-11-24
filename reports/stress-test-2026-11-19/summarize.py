import json, sys, glob, os
sys.stdout.reconfigure(encoding="utf-8")
for f in sorted(glob.glob(os.environ.get("STRESS_RUNS", "runs") + "/*/result.json")):
    if len(sys.argv) > 1 and not any(a in f for a in sys.argv[1:]): continue
    r = json.load(open(f, encoding="utf-8"))
    b = r.get("brief") or {}
    print(f"== {r['id']}: {r['title']}")
    print(f"   {r['seconds']}s, tools={r['tool_calls']}, plan_status={r.get('plan_status')}, days={r.get('n_days')}, cost={r.get('cost_total')}, budget={b.get('total_budget_eur')}, review={r.get('review')}")
    if b: print(f"   brief: {b.get('destination')} {b.get('start_date')}..{b.get('end_date')} from {b.get('origin')} x{b.get('travellers')} tier={b.get('budget_tier')} avoid={b.get('avoid_modes')} req={b.get('requirements')} own_acc={b.get('own_accommodation')} own_car={b.get('own_car')}")
    print(f"   checks: {r.get('checks')}")
    if r.get("final_must_fix"): print(f"   FINAL MUST_FIX: {r['final_must_fix']}")
    if r.get("final_should_fix"): print(f"   final should_fix: {[str(x)[:160] for x in r['final_should_fix']]}")
    if r["errors"]: print(f"   ERRORS: {r['errors']}")
    te = r["tool_errors"]
    nc = [t for t in te if "needs_confirmation" not in t]
    if nc: print(f"   tool errors ({len(nc)}): " + " | ".join(t[:150] for t in nc[:8]))
