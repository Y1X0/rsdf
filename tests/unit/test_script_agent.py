import json

import pytest

from content_factory.agents.script_agent import ScriptAgent
from content_factory.db.models.campaign import Campaign
from content_factory.db.models.content import ContentIdea
from content_factory.db.models.enums import ProcessingStatus
from content_factory.llm.providers.fake_provider import FakeLLMClient


def _idea(db_session) -> ContentIdea:
    campaign = Campaign(brand_name="Acme")
    db_session.add(campaign)
    db_session.flush()
    idea = ContentIdea(campaign_id=campaign.id, concept_summary="budgeting tips")
    db_session.add(idea)
    db_session.flush()
    return idea


def test_generate_variants_creates_scripts_with_experiment_groups(db_session):
    canned = [
        {"variant_label": "v1", "hook_text": "hook 1", "full_text": "full 1", "cta_text": "cta 1", "target_duration_s": 20},
        {"variant_label": "v2", "hook_text": "hook 2", "full_text": "full 2", "cta_text": "cta 2", "target_duration_s": 25},
        {"variant_label": "v3", "hook_text": "hook 3", "full_text": "full 3", "cta_text": "cta 3", "target_duration_s": 30},
    ]
    llm = FakeLLMClient(response_builder=lambda system, prompt: json.dumps(canned))
    agent = ScriptAgent(llm)
    idea = _idea(db_session)

    scripts = agent.generate_variants(db_session, idea=idea, retrieved_hooks=[], num_variants=3)

    assert len(scripts) == 3
    assert [s.experiment_group for s in scripts] == ["A", "B", "C"]
    assert all(s.generation_status == ProcessingStatus.COMPLETED for s in scripts)
    assert all(s.agent_run_id is not None for s in scripts)


def test_generate_variants_returns_empty_list_on_unparseable_response(db_session):
    llm = FakeLLMClient(response_builder=lambda system, prompt: "nonsense")
    agent = ScriptAgent(llm)
    idea = _idea(db_session)

    scripts = agent.generate_variants(db_session, idea=idea, retrieved_hooks=[], num_variants=3)
    assert scripts == []


def test_generate_variants_marks_agent_run_failed_and_reraises_on_error(db_session):
    def _boom(system, prompt):
        raise RuntimeError("rate limited")

    llm = FakeLLMClient(response_builder=_boom)
    agent = ScriptAgent(llm)
    idea = _idea(db_session)

    with pytest.raises(RuntimeError):
        agent.generate_variants(db_session, idea=idea, retrieved_hooks=[], num_variants=3)

    from content_factory.db.models.agent_run import AgentRun

    run = db_session.query(AgentRun).filter(AgentRun.agent_name == "script_agent").one()
    assert run.status == ProcessingStatus.FAILED
    assert run.error_message == "rate limited"


def test_generate_variants_skips_malformed_items_instead_of_crashing_the_whole_batch(db_session):
    """Regression test for a real production incident: a genuinely valid,
    non-empty JSON array response (so parse_json_response's own
    empty/unparseable-response fallback never triggers) can still contain
    an item that doesn't match the requested schema exactly - a real LLM's
    per-item output isn't guaranteed uniform even when the outer array is
    well-formed. That used to raise an unhandled KeyError/TypeError deep in
    the script-construction loop, surfacing as a bare 500 with no usable
    detail anywhere the operator could see. It must now skip just the bad
    item(s) and keep whatever's usable."""
    canned = [
        {"variant_label": "v1", "hook_text": "hook 1", "full_text": "full 1"},
        {"variant_label": "v2", "hook_text": "hook 2"},  # missing full_text
        "just a string, not an object",  # wrong shape entirely
        {"variant_label": "v4", "full_text": "full 4"},  # missing hook_text
        {"variant_label": "v5", "hook_text": "hook 5", "full_text": "full 5"},
    ]
    llm = FakeLLMClient(response_builder=lambda system, prompt: json.dumps(canned))
    agent = ScriptAgent(llm)
    idea = _idea(db_session)

    scripts = agent.generate_variants(db_session, idea=idea, retrieved_hooks=[], num_variants=5)

    assert len(scripts) == 2
    assert {s.hook_text for s in scripts} == {"hook 1", "hook 5"}


def test_generate_variants_returns_empty_list_when_every_item_is_malformed(db_session):
    canned = [{"variant_label": "v1"}, "nonsense", {"hook_text": "only a hook, no full_text"}]
    llm = FakeLLMClient(response_builder=lambda system, prompt: json.dumps(canned))
    agent = ScriptAgent(llm)
    idea = _idea(db_session)

    scripts = agent.generate_variants(db_session, idea=idea, retrieved_hooks=[], num_variants=3)
    assert scripts == []


def test_default_fake_llm_response_yields_empty_list_when_no_builder_given(db_session):
    """Exercises the production safe-degradation default (no API key
    configured) directly — must not crash, must produce an empty, clearly
    logged result."""
    llm = FakeLLMClient()
    agent = ScriptAgent(llm)
    idea = _idea(db_session)

    scripts = agent.generate_variants(db_session, idea=idea, retrieved_hooks=[], num_variants=3)
    assert scripts == []
