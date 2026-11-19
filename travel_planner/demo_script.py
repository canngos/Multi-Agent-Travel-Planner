"""The messages of the recorded Lisbon demo (DEMO=1 replays only these, in this order).

Used by scripts/run_trip.py, scripts/record_demo.py, scripts/record_ui_replay.py and scripts/check_demo_api.py;
the web UI suggests the same lines (travel-planner-ui/src/components/Chat.tsx, DEMO_LINES; a test keeps the two
lists equal). The demo shows a whole session: the brief, the reviewed plan and its approval, a follow-up question,
then a change that the concierge scopes (a cheaper stay and a slower pace: only the stay is researched again).
"""

APPROVE = "Looks good, please write the guide."

DEMO_TURNS = [
    "Plan 5 days in Lisbon for two, mid budget, we love history and street food.",
    "We fly from Helsinki, 10-14 May. Total budget about 2500 euros.",
    "Yes, that's right, go ahead.",
    APPROVE,
    "Thanks! Which day do we visit Belém, and what does the tower cost?",
    "Could we take it a bit slower, with a relaxed pace, and stay somewhere cheaper?",
    "Yes, please do that.",
    APPROVE,
]
QUESTION_TURN = 4  # the follow-up question the concierge answers from the guide
