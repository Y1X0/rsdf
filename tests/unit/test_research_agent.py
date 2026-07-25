import json

import pytest

from content_factory.agents.research_agent import ResearchAgent
from content_factory.db.models.agent_run import AgentRun
from content_factory.db.models.campaign import Campaign
from content_factory.db.models.enums import ProcessingStatus
from content_factory.llm.providers.fake_provider import FakeLLMClient


def _campaign(db_session) -> Campaign:
    campaign = Campaign(brand_name="Acme", rules_text="Must disclose sponsorship.")
    db_session.add(campaign)
    db_session.flush()
    return campaign


def test_generate_brief_parses_structured_response_and_logs_agent_run(db_session):
    canned = {
        "brief_text": "summary",
        "competitor_hooks": [{"hook_text": "hook 1", "hook_type": "question"}],
        "competitor_patterns": [{"pattern_type": "hook", "description": "pattern 1"}],
    }
    llm = FakeLLMClient(response_builder=lambda system, prompt: json.dumps(canned))
    agent = ResearchAgent(llm)
    campaign = _campaign(db_session)

    brief = agent.generate_brief(db_session, campaign=campaign, raw_notes="some competitor notes")

    assert brief.status == ProcessingStatus.COMPLETED
    assert brief.brief_text == "summary"
    assert brief.structured_data == canned
    assert brief.agent_run_id is not None

    run = db_session.get(AgentRun, brief.agent_run_id)
    assert run.status == ProcessingStatus.COMPLETED
    assert run.agent_name == "research_agent"
    assert run.prompt is not None
    assert run.model == "fake-llm"
    assert run.model_version == "v1"


def test_generate_brief_handles_unparseable_response_without_crashing(db_session):
    llm = FakeLLMClient(response_builder=lambda system, prompt: "not valid json at all")
    agent = ResearchAgent(llm)
    campaign = _campaign(db_session)

    brief = agent.generate_brief(db_session, campaign=campaign, raw_notes="notes")

    assert brief.status == ProcessingStatus.COMPLETED
    assert brief.structured_data == {}


def test_generate_brief_marks_failed_and_reraises_on_llm_error(db_session):
    def _boom(system, prompt):
        raise RuntimeError("provider unavailable")

    llm = FakeLLMClient(response_builder=_boom)
    agent = ResearchAgent(llm)
    campaign = _campaign(db_session)

    with pytest.raises(RuntimeError):
        agent.generate_brief(db_session, campaign=campaign, raw_notes="notes")

    from content_factory.db.models.content import ResearchBrief

    brief = db_session.query(ResearchBrief).filter(ResearchBrief.campaign_id == campaign.id).one()
    assert brief.status == ProcessingStatus.FAILED

    run = db_session.query(AgentRun).filter(AgentRun.agent_name == "research_agent").one()
    assert run.status == ProcessingStatus.FAILED
    assert run.error_message == "provider unavailable"
