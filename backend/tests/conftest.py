import os

os.environ["MOCK_DELAY_SEC"] = "0"
os.environ["HEARTBEAT_TICK_SEC"] = "3600"
os.environ["AGENT_RUNTIME_DEFAULT"] = "mock"

import pytest  # noqa: E402

import db  # noqa: E402
import org  # noqa: E402
import seed  # noqa: E402
from heartbeat import Scheduler  # noqa: E402


@pytest.fixture
async def company():
    """Fresh in-memory org: seeded director, approval off unless a test turns it on."""
    db.connect(":memory:")
    await seed.seed_if_empty()
    c = db.one("SELECT * FROM companies")
    db.update("companies", c["id"], require_board_approval_for_new_agents=0)
    return db.get("companies", c["id"])


@pytest.fixture
async def scheduler():
    s = Scheduler()
    s.start()
    yield s
    await s.stop()


@pytest.fixture
def ceo(company):
    return db.one("SELECT * FROM agents WHERE role = 'ceo'")


async def hire(company_id: str, **data) -> dict:
    agent = org.hire_agent(company_id, data)
    if agent["status"] != "pending_approval":
        agent = await org.activate_agent(agent["id"])
    return agent
