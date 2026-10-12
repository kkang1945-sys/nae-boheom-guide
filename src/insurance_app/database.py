from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def create_session_factory(database_path: Path) -> sessionmaker[Session]:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    return sessionmaker(bind=engine, expire_on_commit=False)


def create_schema(factory: sessionmaker[Session]) -> None:
    Base.metadata.create_all(factory.kw["bind"])


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory.begin() as session:
        yield session
