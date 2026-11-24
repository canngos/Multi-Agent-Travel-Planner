import json, glob, re, sys, os
sys.stdout.reconfigure(encoding="utf-8")
rows = []
for f in sorted(glob.glob(os.environ.get("STRESS_RUNS", "runs") + "/*/result.json")):
    r = json.load(open(f, encoding="utf-8")); d = os.path.dirname(f)
    if not r.get("n_days"): continue
    s = json.load(open(d + "/state.json", encoding="utf-8"))
    plan = s.get("itinerary"); 
    try: plan = json.loads(re.sub(r"^```json|```$", "", plan.strip(), flags=re.M)) if isinstance(plan, str) else plan
    except Exception: plan = {}
    b = r["brief"]; days = plan.get("days") or []
    names = [i.get("name","") for dd in days for i in dd.get("items") or []]
    ph = [n for n in names if re.search(r"(?i)leisure|^free |snack|lunch near|dinner near|coffee break|check-?in|taxi to|metro tip|indoor activity|rest$", n)]
    last = days[-1]["items"] if days else []
    last_real = [i for i in last if i.get("kind") != "transport"]
    first = days[0]["items"] if days else []
    stay = plan.get("stay") or {}
    guide = s.get("final_guide") or ""
    rows.append(dict(id=r["id"][:24], secs=r["seconds"], tier=b.get("budget_tier"), rounds=len(r.get("review") or []),
        approved=any(v=="APPROVED" for _,v in r.get("review") or []), mustfix=len(r.get("final_must_fix") or []),
        shouldfix=len(r.get("final_should_fix") or []), placeholders=len(ph), last_day_items=len(last_real),
        stay=(stay.get("name") or "-")[:30], hostel=bool(re.search(r"(?i)hostel|hostal", stay.get("name") or "")),
        nulllinks=guide.count("](null)"), over=(r.get("cost_total") or 0) > (b.get("total_budget_eur") or 1e9), ph=ph[:4]))
print(f"{'id':24} {'sec':>5} tier rnd appr must should ph last hostel null over stay")
for x in rows:
    print(f"{x['id']:24} {x['secs']:5.0f} {x['tier']:4} {x['rounds']:3} {str(x['approved'])[0]:>4} {x['mustfix']:4} {x['shouldfix']:6} {x['placeholders']:2} {x['last_day_items']:4} {str(x['hostel'])[0]:>6} {x['nulllinks']:4} {str(x['over'])[0]:>4} {x['stay']}  {x['ph']}")
n=len(rows)
print(f"\nfull plans: {n}; approved by critic: {sum(x['approved'] for x in rows)}; ended 3xREVISE: {sum(not x['approved'] for x in rows)}; final must_fix left: {sum(x['mustfix']>0 for x in rows)}; with placeholders: {sum(x['placeholders']>0 for x in rows)}; last day w/o any activity: {sum(x['last_day_items']==0 for x in rows)}; hostel on mid/high: {sum(x['hostel'] and x['tier']!='low' for x in rows)}; null links: {sum(x['nulllinks']>0 for x in rows)}; avg secs {sum(x['secs'] for x in rows)/n:.0f}")
