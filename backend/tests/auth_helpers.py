from sqlalchemy import select

from agromech_api.security.auth import create_access_token, create_database_user
from agromech_api.core.config import Settings
from agromech_api.db.enums import UserRole
from agromech_api.db.models import users


def seed_auth_user(
    engine,
    *,
    username: str,
    password: str = "secret",
    role: UserRole = UserRole.USER,
) -> None:
    with engine.connect() as connection:
        existing_user = connection.execute(
            select(users.c.id).where(users.c.username == username)
        ).one_or_none()
    if existing_user is None:
        create_database_user(engine, username=username, password=password, role=role)


def auth_token_for_user(
    engine,
    settings: Settings,
    *,
    username: str,
    role: UserRole,
    password: str = "secret",
) -> str:
    seed_auth_user(engine, username=username, password=password, role=role)
    with engine.connect() as connection:
        user = connection.execute(
            select(users.c.id, users.c.token_version).where(users.c.username == username)
        ).mappings().one()
    return create_access_token(
        username=username,
        role=role,
        settings=settings,
        user_id=user["id"],
        token_version=user["token_version"],
    )
