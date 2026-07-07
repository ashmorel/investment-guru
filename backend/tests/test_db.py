import pytest
from sqlalchemy import select

from app.models.user import User

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_create_and_read_user(db_session):
    user = User(email="t@example.com", password_hash="x")
    db_session.add(user)
    await db_session.commit()

    result = await db_session.execute(select(User).where(User.email == "t@example.com"))
    found = result.scalar_one()
    assert found.id is not None
    assert found.created_at is not None
