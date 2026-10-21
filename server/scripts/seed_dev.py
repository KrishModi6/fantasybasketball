"""Create a local development account with a demo league (idempotent).

Usage:  python -m scripts.seed_dev
Signs in at http://localhost:5173 with the credentials below. Development only.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.orm import Activity, League, LeagueMember, User  # noqa: E402
from app.security import encrypt_json, hash_password  # noqa: E402
from app.services.sync import run_sync  # noqa: E402

DEV_EMAIL = "dev@courtside.test"
DEV_PASSWORD = "courtside-dev-2027"
DEV_NAME = "Dev Commissioner"


def main() -> None:
    init_db()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == DEV_EMAIL))
        if user is None:
            user = User(email=DEV_EMAIL, display_name=DEV_NAME, password_hash=hash_password(DEV_PASSWORD))
            db.add(user)
            db.commit()
        has_league = db.scalar(select(LeagueMember).where(LeagueMember.user_id == user.id))
        if has_league:
            print(f"{DEV_EMAIL} already has a league; nothing to do.")
            return
        lg = League(name="Demo League", season=2027, source="demo", owner_id=user.id,
                    connector_config=encrypt_json({"team_count": 10, "seed": 7}), sync_interval_hours=4)
        db.add(lg)
        db.flush()
        db.add(LeagueMember(league_id=lg.id, user_id=user.id, role="owner", team_id=1))
        db.add(Activity(league_id=lg.id, kind="league", message=f"{DEV_NAME} created the league"))
        db.commit()
        league_id = lg.id
    run_sync(league_id, reason="seed")
    print(f"Seeded {DEV_EMAIL} with demo league {league_id}")


if __name__ == "__main__":
    main()
