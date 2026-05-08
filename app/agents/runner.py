import asyncio
import logging
import aiosqlite
from collections import defaultdict
from pathlib import Path
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from app.agents.graph import build_graph

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "conversation.db"

_compiled = None
_conn = None
# Per-user lock verhindert Race Conditions bei parallelen SQLite-Checkpoint-Writes
_user_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


async def get_runner():
    global _compiled, _conn
    if _compiled is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = await aiosqlite.connect(str(DB_PATH))
        saver = AsyncSqliteSaver(_conn)
        _compiled = build_graph().compile(checkpointer=saver)
        logger.info(f"LangGraph Runner initialisiert | DB: {DB_PATH}")
    return _compiled


async def run(user_id: int, text: str, history: list) -> dict | str:
    async with _user_locks[user_id]:
        runner = await get_runner()
        config = {"configurable": {"thread_id": str(user_id)}}
        state = {
            "user_id": user_id,
            "text": text,
            "messages": history,
            "agent": "",
            "response": "",
        }
        result = await runner.ainvoke(state, config=config)

        if result.get("response") == "__CHART__":
            return result
        return result["response"]