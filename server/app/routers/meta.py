from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from fbb.connectors.demo import DemoConnector
from fbb.connectors.espn import EspnConnector
from fbb.connectors.fileimport import FileConnector, example_csv
from fbb.connectors.flaim import FlaimConnector
from fbb.history import DATA_DIR

from ..deps import current_user

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/model")
def model_card(_=Depends(current_user)):
    path = DATA_DIR / "model" / "metrics.json"
    if not path.exists():
        return {"trained": False}
    m = json.loads(path.read_text())
    m.pop("lgb_params", None)
    return {"trained": True, **m}


@router.get("/connectors")
def connectors(_=Depends(current_user)):
    return [vars(c.info()) for c in (EspnConnector, FileConnector, FlaimConnector, DemoConnector)]


@router.get("/import/example.csv", response_class=PlainTextResponse)
def example():
    return example_csv()
