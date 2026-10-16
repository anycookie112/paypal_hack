"""The agent base class, with a fake chat model standing in for the LLM (no network)."""

import asyncio
import json
import time
from dataclasses import dataclass, field

import pytest
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel

from app.ai import AgentError, BaseAgent, prompts
from app.core.requests import ActionError
from tests.fakes import FakeChatModel, call, tool_names

prompts.define("test_desk", 1, "Be brief.", changes="First version.")
prompts.define("test_desk", 2, "Be brief. Never guess.", changes="v1 guessed.")


@dataclass
class Chat:
    member: str
    asked: list[str | int] = field(default_factory=list)  # what the tools were called with


class Desk(BaseAgent):
    name = "desk"
    prompt = "test_desk"
    context = Chat

    def setup(self):
        self.register_tool(self.whoami)
        self.register_tool(self.get_gloves)
        self.register_tool(self.broken)

    def instructions(self, chat: Chat) -> str:
        return f"You're talking to {chat.member}."

    def whoami(self, runtime: ToolRuntime[Chat]) -> str:
        """Who is chatting."""
        runtime.context.asked.append("whoami")
        return runtime.context.member

    def get_gloves(self) -> str:
        """Get the gloves."""
        raise ActionError("Marco is already getting them (by 1 pm).")

    def broken(self) -> str:
        """Has a bug."""
        raise KeyError("oops")


def replies(*steps):
    """A fake model that gives these replies in order, and keeps every message list it was sent."""
    seen, steps = [], iter(steps)

    def respond(messages, tools):
        seen.append(messages)
        return next(steps)
    return FakeChatModel(respond=respond), seen


def test_prompt_tools_and_context():
    model, seen = replies(call("whoami"), "Hi Priya!")
    desk, chat = Desk(model=model), Chat("Priya")
    assert asyncio.run(desk.run("who am I?", context=chat)) == "Hi Priya!"
    assert chat.asked == ["whoami"]
    assert isinstance(seen[0][0], SystemMessage)
    assert seen[0][0].content == "Be brief. Never guess.\n\n<context>\nYou're talking to Priya.\n</context>"
    assert seen[1][-1].content == "Priya"
    assert tool_names(model.calls[0]["tools"]) == ["whoami", "get_gloves", "broken"]
    assert desk.prompt_id == "test_desk@v2"


def test_pinning_a_prompt_version():
    model, seen = replies("hi")
    desk = Desk(model=model, prompt_version=1)
    asyncio.run(desk.run("hi", context=Chat("Priya")))
    assert desk.prompt_id == "test_desk@v1"
    assert seen[0][0].content.startswith("Be brief.\n\n")


def test_a_rule_saying_no_goes_back_to_the_model():
    model, seen = replies(call("get_gloves"), "Marco has it covered.")
    assert asyncio.run(Desk(model=model).run("I'll get gloves", context=Chat("Priya"))) == "Marco has it covered."
    result = seen[1][-1]
    assert isinstance(result, ToolMessage) and result.status == "error"
    assert "Marco is already getting them" in result.content


def test_a_bug_in_a_tool_is_logged_and_the_model_is_told(caplog):
    model, seen = replies(call("broken"), "Sorry, something went wrong.")
    assert asyncio.run(Desk(model=model).run("go", context=Chat("Priya"))) == "Sorry, something went wrong."
    result = seen[1][-1]
    assert result.status == "error" and "broken failed unexpectedly (KeyError)" in result.content
    assert "Tool broken failed" in caplog.text


def test_runaway_runs_are_stopped():
    desk = Desk(model=FakeChatModel(respond=lambda messages, tools: call("get_gloves")), max_model_calls=3)
    with pytest.raises(AgentError, match="desk"):
        asyncio.run(desk.run("loop", context=Chat("Priya")))


def test_limits_are_set_per_agent():
    desk = Desk(model=FakeChatModel(respond=lambda m, t: "hi"), max_model_calls=4, max_repeats=1, timeout_seconds=30)
    assert (desk.max_model_calls, desk.max_tool_calls, desk.max_repeats, desk.timeout_seconds) == (4, 10, 1, 30)
    assert (Desk.max_model_calls, Desk.timeout_seconds) == (8, None)  # the class defaults are untouched


def test_the_same_call_again_and_again_is_refused():
    model, seen = replies(call("whoami"), call("whoami"), call("whoami"), "Priya.")
    chat = Chat("Priya")
    asyncio.run(Desk(model=model).run("who?", context=chat))
    assert chat.asked == ["whoami", "whoami"]  # the third identical call didn't run
    assert seen[3][-1].status == "error" and "already called 2 times" in seen[3][-1].content


def test_running_out_of_time():
    model = FakeChatModel(respond=lambda messages, tools: time.sleep(0.2) or "late")
    with pytest.raises(AgentError, match="took longer than 0.05 s"):
        asyncio.run(Desk(model=model, timeout_seconds=0.05).run("hi", context=Chat("Priya")))


def test_tools_are_registered_before_first_use():
    desk = Desk(model=FakeChatModel(respond=lambda messages, tools: "hi"))
    asyncio.run(desk.run("hi", context=Chat("Priya")))
    with pytest.raises(RuntimeError, match="already initialized"):
        desk.register_tool(desk.whoami)


def test_streamed_output():
    model, _ = replies(call("whoami"), "Hi Priya, you're all set.")

    async def collect():
        return [e async for e in Desk(model=model).stream("who am I?", context=Chat("Priya"))]
    events = asyncio.run(collect())
    assert [(e.type, e.name) for e in events if e.type != "text"] == [
        ("tool_call", "whoami"), ("tool_result", "whoami"), ("output", "")]
    assert "".join(e.text for e in events if e.type == "text") == "Hi Priya, you're all set."
    assert events[-1].data == "Hi Priya, you're all set."
    assert events[1].to_json()["data"] == "Priya"


class Lookup(BaseAgent):
    name = "lookup"
    description = "Looks things up."
    context = Chat

    def setup(self):
        self.register_tool(self.whoami)

    def whoami(self, runtime: ToolRuntime[Chat]) -> str:
        """Who is chatting."""
        runtime.context.asked.append("lookup.whoami")
        return runtime.context.member


def test_a_subagent_answers_the_coordinator():
    lookup_model, lookup_seen = replies(call("whoami"), "The member is Priya.")
    desk_model, desk_seen = replies(call("lookup", {"request": "who is this?"}), "You're Priya.")
    desk = Desk(model=desk_model)
    desk.register_tool(Lookup(model=lookup_model).as_tool())
    chat = Chat("Priya")
    assert asyncio.run(desk.run("who am I?", context=chat)) == "You're Priya."
    assert lookup_seen[0][-1].content == "who is this?"  # only the request, not the whole chat
    assert chat.asked == ["lookup.whoami"]               # same context as the coordinator
    assert desk_seen[1][-1].content == "The member is Priya."
    [described] = [t["function"]["description"] for t in desk_model.calls[0]["tools"] if t["function"]["name"] == "lookup"]
    assert described.startswith("Looks things up.\n- It sees nothing of this conversation")
    assert "The member never sees its result" in described


class Reading(BaseModel):
    store: str
    total_cents: int


class Reader(BaseAgent):
    name = "reader"
    output = Reading


def test_structured_output():
    model, _ = replies(call("Reading", {"store": "Riverside Hardware", "total_cents": 1840}))
    assert asyncio.run(Reader(model=model).run("receipt text")) == Reading(store="Riverside Hardware",
                                                                            total_cents=1840)


def test_an_agent_with_an_output_must_finish_with_it():
    model, seen = replies("Just text.", call("Reading", {"store": "Riverside Hardware", "total_cents": 1840}))
    assert asyncio.run(Reader(model=model).run("receipt text")).store == "Riverside Hardware"
    assert all(c["tool_choice"] == "any" for c in model.calls)  # every model call has to call a tool
    assert len(seen) == 2                                      # the plain-text answer was sent back


def test_a_bad_result_goes_back_to_be_fixed():
    model, seen = replies(call("Reading", {"store": "Riverside Hardware"}),
                          call("Reading", {"store": "Riverside Hardware", "total_cents": 1840}))
    assert asyncio.run(Reader(model=model).run("receipt text")).total_cents == 1840
    assert "total_cents" in str(seen[1][-1].content)


def test_a_subagent_that_fails_tells_the_coordinator():
    lookup = Lookup(model=FakeChatModel(respond=lambda m, t: call("whoami")), max_model_calls=2)
    desk_model, desk_seen = replies(call("lookup", {"request": "who?"}), "The lookup failed, sorry.")
    desk = Desk(model=desk_model)
    desk.register_tool(lookup.as_tool())
    assert asyncio.run(desk.run("who am I?", context=Chat("Priya"))) == "The lookup failed, sorry."
    failure = json.loads(desk_seen[1][-1].content)
    assert failure["status"] == "failed" and failure["reason"].startswith("lookup:")


def test_a_subagent_result_reaches_the_coordinator_as_json():
    reader = Reader(model=replies(call("Reading", {"store": "Riverside Hardware", "total_cents": 1840}))[0])
    desk_model, desk_seen = replies(call("reader", {"request": "read it"}), "Riverside Hardware, $18.40.")
    desk = Desk(model=desk_model)
    desk.register_tool(reader.as_tool())
    asyncio.run(desk.run("what did it say?", context=Chat("Priya")))
    assert json.loads(desk_seen[1][-1].content) == {"store": "Riverside Hardware", "total_cents": 1840}


def test_an_agent_that_never_finishes_is_stopped():
    model = FakeChatModel(respond=lambda messages, tools: "Still just text.")
    with pytest.raises(AgentError, match="reader"):
        asyncio.run(Reader(model=model, max_model_calls=3).run("receipt text"))
    assert len(model.calls) == 3


def test_a_failure_says_what_was_already_done():
    model = FakeChatModel(respond=lambda messages, tools: call("whoami", {"x": len(messages)}) if len(messages) < 4
                          else time.sleep(0.3) or "late")
    with pytest.raises(AgentError, match="took longer than 0.2 s") as failure:
        asyncio.run(Desk(model=model, timeout_seconds=0.2).run("who?", context=Chat("Priya")))
    assert [c["tool"] for c in failure.value.completed] == ["whoami"]
    assert failure.value.completed[0]["result"] == "Priya"


def test_refusals_are_not_counted_as_done():
    model, _ = replies(call("get_gloves"), call("whoami"), call("whoami"), call("whoami"))
    with pytest.raises(AgentError) as failure:
        asyncio.run(Desk(model=model, max_model_calls=4).run("go", context=Chat("Priya")))
    assert [c["tool"] for c in failure.value.completed] == ["whoami", "whoami"]  # not get_gloves, not the repeat


def test_an_unexpected_failure_is_logged_and_still_reports_progress(caplog):
    def respond(messages, tools):
        if len(messages) > 2:
            raise ValueError("provider sent garbage")
        return call("whoami")
    with pytest.raises(AgentError, match="desk: failed unexpectedly \(ValueError\)") as failure:
        asyncio.run(Desk(model=FakeChatModel(respond=respond)).run("who?", context=Chat("Priya")))
    assert failure.value.completed == [{"tool": "whoami", "result": "Priya"}]
    assert "desk failed unexpectedly" in caplog.text


def test_the_coordinator_hears_what_a_failed_subagent_did():
    lookup = Lookup(model=FakeChatModel(respond=lambda m, t: call("whoami", {"n": len(m)})), max_model_calls=2)
    desk_model, desk_seen = replies(call("lookup", {"request": "who?"}), "Partly done.")
    desk = Desk(model=desk_model)
    desk.register_tool(lookup.as_tool())
    asyncio.run(desk.run("who am I?", context=Chat("Priya")))
    failure = json.loads(desk_seen[1][-1].content)
    assert failure["status"] == "failed"
    assert failure["completed"] == [{"tool": "whoami", "result": "Priya"}, {"tool": "whoami", "result": "Priya"}]


def test_the_finish_section_is_written_from_the_schema():
    prompt = Reader(model=FakeChatModel(respond=lambda m, t: "x")).system_prompt(None)
    assert prompt.startswith("<finish>\n- When you're done, call Reading exactly once.")
    assert "  - store\n  - total_cents" in prompt  # every field, in order
    assert prompt.endswith("</finish>")


def test_plain_tools_run_on_the_callers_thread():
    """Tools share one database session, which isn't thread-safe: LangChain's thread pool must not run them."""
    import threading
    threads = []

    class Where(BaseAgent):
        def setup(self):
            self.register_tool(self.here)

        def here(self) -> str:
            """Where this runs."""
            threads.append(threading.get_ident())
            return "here"

    model, _ = replies(call("here"), call("here", {}), "done")
    asyncio.run(Where(model=model, max_repeats=5).run("go"))
    assert threads == [threading.get_ident()] * 2


class Answer(BaseModel):
    answer: str


class Finder(BaseAgent):
    """A subagent with a tool and a result schema, like the lookup agent."""
    name = "finder"
    context = Chat
    output = Answer

    def setup(self):
        self.register_tool(self.whoami)

    def whoami(self, n: int, runtime: ToolRuntime[Chat]) -> str:
        """Who is chatting.

        Args:
            n: Any number.
        """
        runtime.context.asked.append(n)
        return runtime.context.member


def test_finishing_before_seeing_the_results_is_not_accepted():
    """A live run answered with the schema's example while its lookups were still running."""
    both = AIMessage("", tool_calls=[{"name": "whoami", "args": {"n": 1}, "id": "a"},
                                     {"name": "Answer", "args": {"answer": "made up"}, "id": "b"}])
    model, seen = replies(both, call("Answer", {"answer": "It's Priya."}))
    chat = Chat("Priya")
    assert asyncio.run(Finder(model=model).run("who?", context=chat)) == Answer(answer="It's Priya.")
    assert chat.asked == [1]                     # the tool still ran
    assert seen[1][-1].content == "Priya"        # and the model saw its result before finishing


def test_a_runaway_reply_is_cut_down():
    """A live run asked for 247 tool calls in one reply."""
    flood = AIMessage("", tool_calls=[{"name": "whoami", "args": {"n": i % 8}, "id": f"c{i}"} for i in range(40)])
    model, _ = replies(flood, call("Answer", {"answer": "Priya"}))
    chat = Chat("Priya")
    asyncio.run(Finder(model=model).run("who?", context=chat))
    assert sorted(chat.asked) == [0, 1, 2, 3, 4]  # repeats dropped, then only the first five (run at once: any order)
