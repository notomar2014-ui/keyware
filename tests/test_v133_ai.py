"""v1.3.3 AI binder: off-topic text, several bindings at once, batch conflicts, hostile replies.

The model is always mocked. No network, keys, apps, or keystrokes.
"""

from __future__ import annotations

import json
import random

import pytest

from assist.ai_binder import (
    NOT_BINDING_MESSAGE,
    SYSTEM_PROMPT,
    AIBinderError,
    DescribeBinder,
    Proposal,
    Rejected,
    batch_flags,
    batch_targets,
    looks_like_binding,
    parse_batch,
    parse_reply,
)
from engine.models import Binding, Action, default_config
from engine.nudge import NUDGE_MESSAGE, NUDGE_TITLE
from tests.test_ai_binder import FAKE_KEYS, Transport, binder, good, reply


def item(keys, action=None, mode="tap", profile=None, label="Thing"):
    return {
        "label": label,
        "profile": profile or {"scope": "global"},
        "trigger": {"mode": mode, "keys": keys},
        "action": action or {"type": "open_url", "url": "https://www.youtube.com"},
        "notes": "",
    }


MULTI = {
    "ok": True,
    "bindings": [
        item(["f9"], label="Open YouTube"),
        item(["f10"], {"type": "type_text", "text": "me@example.com"}, label="Type email"),
        item(["f8"], {"type": "auto_click", "button": "left", "cps": 15, "click_mode": "hold"}, label="Autoclick"),
    ],
}


# ---- off-topic --------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "what's the weather tomorrow", "write me a poem about cats", "asdkjh qweoiu zxcmn", "how are you today?",
    "tell me a joke", "who won the world cup", "lorem ipsum dolor sit amet", "!!!???", "   ",
])
def test_off_topic_is_caught_locally_without_any_network_call(text):
    transport = Transport()
    assert looks_like_binding(text) is False
    with pytest.raises(AIBinderError) as caught:
        binder(transport).describe_batch(text, default_config())
    assert transport.calls == []
    if text.strip():
        assert caught.value.not_binding is True and str(caught.value) == NOT_BINDING_MESSAGE


@pytest.mark.parametrize("text", [
    "F9 opens YouTube", "hold R to autoclick left at 15 cps in gaming", "ctrl+shift+y types hello",
    "double tap Q to open notepad", "make caps lock open spotify", "press 5 to type gg", "open youtube",
    "F9 opens YouTube, F10 types my email, and holding F8 autoclicks at 15 cps",
])
def test_binding_like_text_goes_to_the_model(text):
    assert looks_like_binding(text) is True


def test_model_not_binding_reply_is_final_and_creates_nothing():
    transport = Transport(reply({"ok": False, "not_binding": True, "error": "Not a key binding."}), reply(good()))
    with pytest.raises(AIBinderError) as caught:
        binder(transport).describe_batch("press the moon key please", default_config())
    assert caught.value.not_binding and str(caught.value) == NOT_BINDING_MESSAGE
    assert len(transport.calls) == 1  # Groq is not asked to overrule an off-topic verdict


def test_prompt_documents_not_binding_and_batches():
    assert '"not_binding": true' in SYSTEM_PROMPT and '"bindings": [' in SYSTEM_PROMPT


def test_groq_fallback_still_works_for_batches():
    from assist.ai_binder import DEFAULT_PROVIDERS

    gemini = sum(1 for p in DEFAULT_PROVIDERS if p.name == "gemini")
    transport = Transport(*([OSError("down")] * gemini), reply(MULTI))
    items = binder(transport).describe_batch("F9 opens YouTube, F10 types my email", default_config())
    assert "groq.com" in transport.calls[-1]["url"] and "googleapis" in transport.calls[0]["url"]
    assert all(isinstance(i, Proposal) and i.model for i in items)


# ---- several bindings ---------------------------------------------------------------

def test_multi_reply_gives_one_proposal_per_binding():
    items = parse_batch(json.dumps(MULTI), default_config())
    assert [i.binding.keys for i in items] == [["f9"], ["f10"], ["f8"]]
    assert [i.binding.action.type for i in items] == ["open_url", "type_text", "auto_click"]
    assert items[2].binding.action.cps == 15 and items[2].binding.action.click_mode == "hold"
    assert len({i.binding.id for i in items}) == 3


def test_single_object_reply_still_works():
    items = parse_batch(json.dumps(good()), default_config())
    assert len(items) == 1 and isinstance(items[0], Proposal)


def test_bad_item_is_rejected_but_others_survive():
    data = {"ok": True, "bindings": [item(["f9"]), item(["ctrl", "alt", "shift", "esc"]), item(["nope_key"])]}
    items = parse_batch(json.dumps(data), default_config())
    assert isinstance(items[0], Proposal)
    assert isinstance(items[1], Rejected) and "panic" in items[1].error.lower()
    assert isinstance(items[2], Rejected) and "Unknown key" in items[2].error
    with pytest.raises(AIBinderError):
        parse_reply(json.dumps(data), default_config())  # the single path never half-applies


def test_all_items_bad_raises():
    data = {"ok": True, "bindings": [item(["ctrl", "alt", "delete"])]}
    with pytest.raises(AIBinderError):
        parse_batch(json.dumps(data), default_config())


def test_too_many_bindings_refused():
    data = {"ok": True, "bindings": [item([f"f{n}"]) for n in range(1, 11)]}
    with pytest.raises(AIBinderError, match="Too many"):
        parse_batch(json.dumps(data), default_config())


def test_batch_flags_duplicate_trigger_inside_the_list():
    data = {"ok": True, "bindings": [item(["f9"]), item(["f9"], {"type": "type_text", "text": "x"})]}
    config = default_config()
    items = parse_batch(json.dumps(data), config)
    flags = batch_flags(items, config, config.global_profile().id)
    assert flags[0] == "" and "Same trigger as #1" in flags[1]


def test_batch_flags_key_already_bound_and_never_overwrites():
    config = default_config()
    glob = config.global_profile()
    glob.bindings.append(Binding(id="b_old", label="Old F9", enabled=True, mode="tap", keys=["f9"],
                                 action=Action(type="open_url", url="https://example.com")))
    items = parse_batch(json.dumps(MULTI), config)
    flags = batch_flags(items, config, glob.id)
    assert "already used" in flags[0] and flags[1] == "" and flags[2] == ""
    assert [b.id for b in glob.bindings if b.keys == ["f9"]] == ["b_old"]


def test_batch_flags_panic_chord_trigger():
    data = {"ok": True, "bindings": [item(["f9"]), item(["ctrl", "alt", "shift", "esc"])]}
    config = default_config()
    items = parse_batch(json.dumps(data), config)
    flags = batch_flags(items, config, config.global_profile().id)
    assert flags[0] == "" and "panic" in flags[1].lower()


def test_same_key_in_different_profiles_is_not_a_clash():
    data = {"ok": True, "bindings": [item(["f9"]), item(["f9"], profile={"scope": "app", "name": "Gaming",
                                                                           "processes": ["javaw.exe"]})]}
    config = default_config()
    items = parse_batch(json.dumps(data), config)
    targets = batch_targets(items, config, config.global_profile().id)
    assert targets[1] == "gaming" and batch_flags(items, config, config.global_profile().id) == ["", ""]


# ---- hostile / malformed replies (fuzz) ---------------------------------------------

HOSTILE = [
    "", "null", "[]", "42", '"str"', "{", "}{", '{"ok": true}', '{"ok": "yes"}', '{"bindings": 5}',
    '{"ok": true, "bindings": []}', '{"ok": true, "bindings": [null, 1, "x"]}', '{"ok": true, "bindings": {}}',
    '{"ok": true, "trigger": [], "action": {}}', '{"ok": true, "trigger": {"mode": "tap", "keys": "f9"}}',
    '{"ok": true, "trigger": {"mode": "tap", "keys": [{"a": 1}]}, "action": {"type": "open_url"}}',
    '{"ok": true, "__proto__": {}, "trigger": {}}', '{"ok": true, "trigger": {"mode": "tap", "keys": ["f9"]}, '
    '"action": {"type": "run_command", "command": "rm -rf /"}}',
    '{"ok": true, "trigger": {"mode": "tap", "keys": ["f9"]}, "action": {"type": "type_text", "text": "powershell -enc AAA"}}',
    '{"ok": true, "trigger": {"mode": "tap", "keys": ["f9"]}, "action": {"type": "open_url", "url": "file:///C:/Windows"}}',
    '{"ok": true, "trigger": {"mode": "tap", "keys": ["f9"]}, "action": {"type": "auto_click", "cps": "NaN"}}',
    '{"ok": true, "trigger": {"mode": "tap", "keys": ["f9"]}, "action": {"type": "auto_click", "cps": 1e309}}',
    '{"ok": true, "trigger": {"mode": "hold", "keys": ["f9"], "hold_ms": -5}, "action": {"type": "open_url", "url": "https://x.com"}}',
    "[" * 5000 + "]" * 5000, '{"a":' * 3000 + "1" + "}" * 3000, "```json\n{\"ok\": false}\n```",
    '{"ok": false, "not_binding": "yes"}', '{"ok": true, "bindings": [' + ",".join(['{"label": 1}'] * 9) + "]}",
]


@pytest.mark.parametrize("text", HOSTILE)
def test_hostile_replies_only_ever_raise_aibindererror(text):
    for parse in (parse_batch, parse_reply):
        try:
            result = parse(text, default_config())
        except AIBinderError:
            continue
        items = result if isinstance(result, list) else [result]
        for entry in items:
            if isinstance(entry, Proposal):
                assert entry.binding.action.type != "run_command"


def test_random_json_fuzz_never_crashes():
    rng = random.Random(1337)
    atoms = [None, True, False, 0, -1, 10**12, 1.5, "", "f9", "ctrl", "auto_click", "open_url", "tap", "hold",
             "https://x.com", "javascript:alert(1)", [], {}]
    keys = ["ok", "label", "profile", "trigger", "action", "notes", "bindings", "mode", "keys", "type", "url", "cps",
            "button", "click_mode", "max_seconds", "scope", "processes", "not_binding", "error", "text"]

    def build(depth):
        if depth <= 0 or rng.random() < 0.3:
            return rng.choice(atoms)
        if rng.random() < 0.3:
            return [build(depth - 1) for _ in range(rng.randint(0, 4))]
        return {rng.choice(keys): build(depth - 1) for _ in range(rng.randint(0, 6))}

    for _ in range(3000):
        data = build(5)
        if isinstance(data, dict) and rng.random() < 0.5:
            data["ok"] = True
        try:
            parse_batch(json.dumps(data), default_config())
        except AIBinderError:
            pass


# ---- donation copy -------------------------------------------------------------------

def test_donation_copy_is_warm_and_honest():
    text = NUDGE_MESSAGE.lower()
    assert "solo developer" in text and "alive" in text and "free" in text and "no ads" in text
    assert "one-time or monthly" in text and NUDGE_TITLE
