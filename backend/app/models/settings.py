from sqlmodel import Field, SQLModel


class AppSetting(SQLModel, table=True):
    """A single-row-per-key store for runtime toggles the user can flip.

    Deliberately not in `config.py`: that is env/.env configuration, read once
    at import and not writable from the UI. Anything the settings dialog can
    change has to survive a restart, which means the database.

    Values are text, and every reader is expected to coerce and to have a
    default — a key that has never been written must behave exactly like the
    factory setting rather than crashing on a `None`.
    """

    key: str = Field(primary_key=True)
    value: str = ""
