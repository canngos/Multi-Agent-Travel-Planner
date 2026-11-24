"""REVIEW.md A6: the user approves the reviewed plan before the guide is written."""
from types import SimpleNamespace

from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import Field

from tests.test_replan import _ctx, _finished
from tests.test_review import _state, fixed_plan
from travel_planner.agents import approval, replan
from travel_planner.agents.concierge import build_concierge, concierge_instruction, require_confirmation


def test_present_plan_shows_the_days_and_waits_for_approval():
    state = _state(fixed_plan())
    content = approval.present_plan(SimpleNamespace(state=state))
    text = content.parts[0].text
    assert state["plan_status"] == "awaiting_approval" and state["plan_summary"] in text
    assert "**Day 2 (Wed 2027-05-12):** 10:30 MUDE - Museu do Design · 12:30 Taberna Nearby" in text
    assert "- **Stay:** Cheap Guesthouse (Baixa), 150 EUR in total" in text and "looks good" in text
    assert "A REVIEWED PLAN IS WAITING" in approval.awaiting_block(state)


def test_present_plan_without_a_plan_says_so():
    state = {"itinerary": None}
    content = approval.present_plan(SimpleNamespace(state=state))
    assert "did not produce" in content.parts[0].text and state["plan_status"] is None


def test_approve_plan_needs_a_waiting_plan():
    ctx = _ctx({"plan_status": None})
    assert approval.approve_plan(ctx)["status"] == "error" and ctx.actions.transfer_to_agent is None
    ctx = _ctx({"plan_status": "awaiting_approval"})
    assert approval.approve_plan(ctx)["status"] == "ok"
    assert ctx.actions.transfer_to_agent == "guide_writer" and ctx.state["plan_status"] == "approved"
    assert approval.approve_plan(ctx)["status"] == "error"  # approving twice writes no second guide


def test_concierge_cannot_transfer_to_the_guide_writer_directly():
    ctx = SimpleNamespace(state={"plan_status": "awaiting_approval"}, invocation_id="i")
    refused = require_confirmation(SimpleNamespace(name="transfer_to_agent"), {"agent_name": "guide_writer"}, ctx)
    assert "approve_plan" in refused["reason"]


def test_a_change_before_approval_can_reuse_the_research():
    state = {**_finished(), "final_guide": None, "plan_status": "awaiting_approval"}  # reviewed, no guide yet
    ctx = _ctx(state)
    assert replan.request_replan(["culture"], ctx, change_note="more museums")["status"] == "ok"
    assert state["replan_scope"]["rerun"] == ["culture"]


class ApprovalLlm(BaseLlm):
    model: str = "approval"
    log: list = Field(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False):
        system = str(llm_request.config.system_instruction or "")
        last = llm_request.contents[-1].parts[0] if llm_request.contents else None
        if system.startswith("Write the guide."):
            self.log.append("guide_writer")
            part = types.Part(text="Saved the guide.")
        elif last is not None and last.function_response:
            part = types.Part(text="Writing your guide now.")
        elif "A REVIEWED PLAN IS WAITING" in system and "looks good" in (last.text or ""):
            self.log.append("concierge:approve")
            part = types.Part(function_call=types.FunctionCall(name="approve_plan", args={}))
        else:
            self.log.append("concierge:answer")
            part = types.Part(text="You're welcome!")
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


async def test_approval_starts_the_guide_writer_and_the_next_message_returns_to_the_concierge():
    llm = ApprovalLlm()
    writer = LlmAgent(name="guide_writer", model=llm, instruction="Write the guide.",
                      disallow_transfer_to_parent=True, disallow_transfer_to_peers=True)
    concierge = build_concierge(LlmAgent(name="planning_pipeline", model=llm, instruction="Plan."), writer)
    concierge.model = llm
    state = _state(fixed_plan())
    approval.present_plan(SimpleNamespace(state=state))
    assert "A REVIEWED PLAN IS WAITING" in concierge_instruction(SimpleNamespace(state=state))
    runner = InMemoryRunner(agent=concierge, app_name="t")
    session = await runner.session_service.create_session(app_name="t", user_id="u", state=state)
    for text in ("That looks good, please write the guide.", "Thanks!"):
        async for _ in runner.run_async(user_id="u", session_id=session.id,
                                        new_message=types.Content(role="user", parts=[types.Part(text=text)])):
            pass
    state = (await runner.session_service.get_session(app_name="t", user_id="u", session_id=session.id)).state
    await runner.close()
    assert llm.log == ["concierge:approve", "guide_writer", "concierge:answer"]
    assert state["plan_status"] == "approved"


def test_concierge_sees_the_presented_plan_as_its_own_turn_and_nothing_else_from_the_pipeline():
    from travel_planner.agents.concierge import hide_pipeline_events
    relay = lambda author, text: types.Content(role="user", parts=[  # noqa: E731  (as ADK relays another agent)
        types.Part(text="For context: below is a transcript of what another agent did, quoted ..."),
        types.Part(text=f"[{author}] said:\n<<<BEGIN_QUOTED_AGENT_CONTENT>>>{text}<<<END_QUOTED_AGENT_CONTENT>>>")])
    request = SimpleNamespace(contents=[
        types.Content(role="user", parts=[types.Part(text="Yes, go ahead.")]),
        relay("culture_agent", '{"results": []}'),
        relay("planning_pipeline", "**Your reviewed plan for Lisbon** ..."),
        types.Content(role="user", parts=[types.Part(text="Looks good, please write the guide.")])])
    hide_pipeline_events(None, request)
    assert [(c.role, c.parts[0].text) for c in request.contents] == [
        ("user", "Yes, go ahead."), ("model", "**Your reviewed plan for Lisbon** ..."),
        ("user", "Looks good, please write the guide.")]
