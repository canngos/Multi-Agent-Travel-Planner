# Trip planner web UI

A browser front end for the multi-agent travel planner. Chat with the concierge, then watch the thirteen
agents work on a transit-map view of the real agent tree:

- the stations light up while an agent is thinking;
- tool calls rise out of the stations as chips;
- trains run along the tracks when work is handed over;
- the review loop counts its rounds.

Click any station, tool call or state key to see the arguments, results, reasoning and outputs. A recorded
run can be replayed at any speed without a server.

## Run it

You need Node.js 18+ and the Python project set up (see the main README). Use two terminals in the project
root.

**1. The planner (ADK's API server)**

```bash
# live planning (needs the keys in .env)
.venv/Scripts/adk api_server .

# or: replay the recorded Lisbon demo, no network or LLM key needed; DEMO_PACE slows the replay to a
# watchable speed (0 = instant, 0.2 = about 2 minutes for the whole demo)
DEMO=1 DEMO_PACE=0.2 .venv/Scripts/adk api_server .
```

In PowerShell, set the variables first: `$env:DEMO="1"; $env:DEMO_PACE="0.2"; .venv\Scripts\adk api_server .`

**2. The UI**

```bash
cd travel-planner-ui
npm install
npm run dev          # http://localhost:5173
```

The dev server forwards `/adk/*` to the API server on port 8000 (change it with `ADK_API=http://host:port`), so no
CORS settings are needed.

In demo mode the replay only works with the recorded messages. The chat offers each of them as a one-click
suggestion: "Send demo message 1 of 4".

## Modes

- **Plan a trip:** talks to the API server through `/run_sse` and shows every event as it arrives. "Save this
  run" downloads the conversation with all events as a replay file.
- **Watch a recorded run:** plays `public/replays/lisbon-demo.json`, or a file you open, through the same
  code, with play, pause, seek and speed controls. No server is needed.

## How it works

| File | What it does |
|---|---|
| `src/generated/agent-graph.json` | The agent tree, exported from the Python code by `scripts/export_agent_graph.py` |
| `src/lib/graph.ts` | Lays the tree out as a transit map: sequential = one line, parallel = forking lines, loop = return track |
| `src/lib/run.ts` | Turns ADK events into agent states, tool calls, chat and state writes (see below) |
| `src/lib/adk.ts` | API server client: sessions and the `/run_sse` event stream |
| `src/lib/hooks.ts` | Live chat controller and the replay player (virtual clock) |
| `src/components/` | Route map, chat, and the Activity, Guide, Review, Details and Shared state panels |

ADK sends an event only after a model answer, not when an agent starts thinking. So `run.ts` takes start times
from the tree:

- when a step of a sequential agent finishes, the next step starts;
- a parallel agent starts all of its branches at once;
- a loop starts another round until a step escalates (`exit_loop`) or the round limit is reached.

This is why all six research agents light up at the moment of the hand-over.

## Keep it in sync

```bash
# after changing the agent tree
.venv/Scripts/python scripts/export_agent_graph.py

# record a new demo replay (with a paced DEMO=1 API server running on port 8766)
.venv/Scripts/python scripts/record_ui_replay.py 8766 travel-planner-ui/public/replays/lisbon-demo.json
```

## Tests

```bash
npm test             # Vitest: layout and event reducer, checked against the recorded demo
npm run build        # type check and production build
```
