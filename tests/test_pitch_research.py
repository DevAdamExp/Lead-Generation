"""Tests for pitch_research — mocked Ollama, no network."""
import json
from unittest.mock import MagicMock, patch

from backend.services import pitch_research


def _fake_response(payload: dict):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"response": json.dumps(payload)}
    return resp


def test_research_lead_parses_ollama_json():
    payload = {
        "pain_points": ["No website", "Slow phone follow-up"],
        "pitch_angle": "Offer a fast lead-capture site.",
        "opener": "Noticed you have no site — losing customers to competitors?",
    }
    with patch.object(pitch_research.httpx, "post", return_value=_fake_response(payload)) as post:
        out = pitch_research.research_lead({"name": "Acme Plumbing"})

    assert out["pitch_angle"] == "Offer a fast lead-capture site."
    assert out["pain_points"] == "No website; Slow phone follow-up"  # list joined
    assert out["opener"].startswith("Noticed you have no site")
    # posts to the generate endpoint with json format + stream off
    body = post.call_args.kwargs["json"]
    assert body["stream"] is False
    assert body["format"] == "json"


def test_research_leads_sync_sets_attrs_and_isolates_failures():
    good = MagicMock(name="Good Biz", business_category=None, website=None,
                     services=None, description=None, review_weaknesses=None)
    good.name = "Good Biz"
    bad = MagicMock()
    bad.name = "Bad Biz"
    after = MagicMock()
    after.name = "After Biz"

    payload = {"pain_points": ["p1"], "pitch_angle": "angle", "opener": "hi there"}

    call = {"n": 0}

    def fake_post(*a, **k):
        # second lead raises; first and third succeed
        call["n"] += 1
        if call["n"] == 2:
            raise RuntimeError("ollama down")
        return _fake_response(payload)

    # Force the local (serial) backend so the test is deterministic regardless
    # of whether a NVIDIA key is present in the ambient .env.
    with patch.object(pitch_research.httpx, "post", side_effect=fake_post), \
         patch.object(pitch_research, "_resolve_backend",
                      return_value=(dict(provider="ollama"), 1)):
        pitch_research.research_leads_sync([good, bad, after])

    # good + after got set despite bad raising mid-batch
    assert good.pitch_angle == "angle"
    assert good.pain_points == "p1"
    assert good.opener == "hi there"
    assert after.pitch_angle == "angle"
    # bad never had attributes assigned (failure isolated, batch continued)
    assert "pitch_angle" not in bad.__dict__


def _fake_nvidia_response(content: str):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


def test_research_lead_nvidia_path_and_lenient_parse():
    # Reasoning model wraps the JSON in prose + a code fence — must still parse.
    payload = {"pain_points": ["no ssl"], "pitch_angle": "sell an SSL + redesign",
               "opener": "Your site loads on http — spooking customers?"}
    wrapped = "Here is my analysis:\n```json\n" + json.dumps(payload) + "\n```"
    with patch.object(pitch_research.httpx, "post",
                      return_value=_fake_nvidia_response(wrapped)) as post:
        out = pitch_research.research_lead(
            {"name": "Acme"}, provider="nvidia", api_key="nvapi-x",
            base_url="https://integrate.api.nvidia.com/v1",
            models=["nvidia/llama-3.3-nemotron-super-49b-v1"])

    assert out["pitch_angle"] == "sell an SSL + redesign"
    assert out["pain_points"] == "no ssl"
    body = post.call_args.kwargs["json"]
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][-1]["role"] == "user"


def test_nvidia_model_fallback_chain():
    # First model 500s, second answers — chain must advance and succeed.
    payload = {"pain_points": ["x"], "pitch_angle": "y", "opener": "z"}
    ok = _fake_nvidia_response(json.dumps(payload))
    err = MagicMock(); err.status_code = 500
    err.raise_for_status.side_effect = RuntimeError("500")
    with patch.object(pitch_research.httpx, "post", side_effect=[err, ok]):
        out = pitch_research.research_lead(
            {"name": "Acme"}, provider="nvidia", api_key="k",
            base_url="http://x/v1", models=["m1", "m2"])
    assert out["opener"] == "z"


def _fake_404():
    resp = MagicMock(); resp.status_code = 404
    err = pitch_research.httpx.HTTPStatusError("404", request=MagicMock(), response=resp)
    resp.raise_for_status.side_effect = err
    return resp


def test_nvidia_dead_model_memoized_and_skipped():
    # A 404 (unknown/deprecated model) marks the model dead so the NEXT lead
    # skips it instead of re-paying the failed call.
    pitch_research._DEAD_MODELS.clear()
    payload = {"pain_points": ["x"], "pitch_angle": "y", "opener": "z"}
    kw = dict(provider="nvidia", api_key="k", base_url="http://x/v1", models=["m1", "m2"])

    # Lead 1: m1 404s (→ dead), m2 answers.
    with patch.object(pitch_research.httpx, "post",
                      side_effect=[_fake_404(), _fake_nvidia_response(json.dumps(payload))]):
        pitch_research.research_lead({"name": "A"}, **kw)
    assert "m1" in pitch_research._DEAD_MODELS

    # Lead 2: m1 is skipped entirely — only m2 is called (a single POST).
    with patch.object(pitch_research.httpx, "post",
                      side_effect=[_fake_nvidia_response(json.dumps(payload))]) as post2:
        out = pitch_research.research_lead({"name": "B"}, **kw)
    assert post2.call_count == 1
    assert out["opener"] == "z"
    pitch_research._DEAD_MODELS.clear()


def test_nvidia_429_does_not_mark_dead():
    # A 429 is transient — it must NOT poison the model for later leads.
    pitch_research._DEAD_MODELS.clear()
    payload = {"pain_points": ["x"], "pitch_angle": "y", "opener": "z"}
    r429 = MagicMock(); r429.status_code = 429
    ok = _fake_nvidia_response(json.dumps(payload))
    # m1 429s (transient) → chain advances to m2 which answers. m1 stays alive.
    with patch.object(pitch_research.httpx, "post", side_effect=[r429, ok]), \
         patch.object(pitch_research.time, "sleep"):
        pitch_research.research_lead({"name": "A"}, provider="nvidia", api_key="k",
                                     base_url="http://x/v1", models=["m1", "m2"])
    assert "m1" not in pitch_research._DEAD_MODELS
    pitch_research._DEAD_MODELS.clear()
