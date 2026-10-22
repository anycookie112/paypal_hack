"""The receipt reader: one model call that reads a receipt photo or PDF into fixed fields.

    reading, prompt_id = await read_receipt(data, ".jpg", what="Gloves")

Not an agent: it has no tools and no loop, it only reads. The reading (app/core/receipts.py `Reading`) fills in the
claim form; code compares it with what the member enters when they hand it in, and a PIC sees any difference.
"""

import base64
import time

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.ai import prompts
from app.ai.model import AgentError, default_model
from app.core.receipts import Reading, for_reading


async def read_receipt(data: bytes, kind: str, *, what: str, model: BaseChatModel | None = None,
                       version: int | None = None) -> tuple[Reading, dict]:
    """The reading of one receipt (`kind`: the file's real type, claims.file_kind) and how the call went
    ({"prompt_id", "ms", "input_tokens", "output_tokens"}). `what` is what the member says they bought.
    AgentError if the model isn't configured or the call fails."""
    prompt = prompts.get("receipt", version)
    media_type, payload = for_reading(data, kind)
    encoded = base64.b64encode(payload).decode()
    block = ({"type": "file", "base64": encoded, "mime_type": media_type} if media_type == "application/pdf"
             else {"type": "image", "base64": encoded, "mime_type": media_type})
    reader = (model or default_model()).with_structured_output(Reading, include_raw=True)
    started = time.monotonic()
    try:
        result = await reader.ainvoke([SystemMessage(prompt.text.strip()), HumanMessage(
            [block, {"type": "text", "text": f"The member says they bought: {what}. Read this receipt."}])])
    except Exception as exc:  # the provider's own errors: the member types the values instead
        raise AgentError(f"receipt: couldn't read it ({type(exc).__name__})") from exc
    if result.get("parsed") is None:
        raise AgentError(f"receipt: the reading didn't fit the fields ({result.get('parsing_error')})")
    usage = getattr(result["raw"], "usage_metadata", None) or {}
    return result["parsed"], {"prompt_id": prompt.id, "ms": round((time.monotonic() - started) * 1000),
                              "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")}
