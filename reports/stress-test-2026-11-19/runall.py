import subprocess, sys, time, os
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scenarios import SCENARIOS
PY = r"C:\Users\canba\Desktop\university\AI-Engineering\Multi-Agent-Travel-Planner\.venv\Scripts\python.exe"
here = os.path.dirname(os.path.abspath(__file__))
RUNS = os.environ.get("STRESS_RUNS") or os.path.join(here, "runs")  # e.g. runs-after, to keep the first runs
os.makedirs(RUNS, exist_ok=True)
ids = sys.argv[2:] or [s for s in SCENARIOS if s != "21_off_topic"]
workers = int(sys.argv[1])
# long full plans first so the pool drains evenly
def run(sid):
    t = time.time()
    with open(os.path.join(RUNS, sid + ".stderr.txt"), "w", encoding="utf-8") as err:
        p = subprocess.run([PY, os.path.join(here, "driver.py"), sid, RUNS], stdout=err, stderr=err, timeout=7200)
    print(f"{time.strftime('%H:%M:%S')} {sid} rc={p.returncode} {time.time()-t:.0f}s", flush=True)
with ThreadPoolExecutor(workers) as ex:
    list(ex.map(run, ids))
print("ALL DONE", flush=True)
