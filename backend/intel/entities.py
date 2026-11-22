"""Classify bulk/block-deal client names into entity classes.

Rules are name patterns, applied in order; a manual override (``entities`` table with
``verified_by='manual'``, or ``backend/entity_overrides.csv``: name,entity_class,notes)
always wins. Every classification records how it was made, and the UI shows it: a rule
match is a strong hint, not proof of identity.

Only these classes count as *institutions* for class-A evidence:
mutual_fund, insurer, fpi, bank, pension.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import pandas as pd
from sqlalchemy import select

from . import db
from .config import BACKEND

INSTITUTIONAL = {"mutual_fund", "insurer", "fpi", "bank", "pension"}
OVERRIDES_FILE = BACKEND / "entity_overrides.csv"

# (class, rule name, regex) — first match wins; order matters (MF before "bank" etc.)
RULES: list[tuple[str, str, str]] = [
    ("mutual_fund", "mf_name", r"\bMUTUAL FUND\b|\bMF\b|\bA/C\s.*\bFUND\b.*\bSCHEME\b|\bASSET MANAGEMENT\b|\bAMC\b"),
    ("insurer", "insurance_name", r"\bLIFE INSURANCE\b|\bINSURANCE CO|\bASSURANCE\b|\bGENERAL INSURANCE\b"),
    ("pension", "pension_name", r"\bPENSION\b|\bPROVIDENT FUND\b|\bEPFO\b|\bNPS TRUST\b"),
    ("fpi", "fpi_name", r"\bFPI\b|\bFII\b|\bSINGAPORE\b|\bMAURITIUS\b|\bCAYMAN\b|\bLUXEMBOURG\b|\bIRELAND\b|"
                        r"\bGOVERNMENT OF SINGAPORE\b|\bNORGES\b|\bABU DHABI\b|\bSOCIETE GENERALE\b|"
                        r"\bMORGAN STANLEY ASIA\b|\bGOLDMAN SACHS (?:SINGAPORE|INVESTMENTS)\b|\bCITIGROUP GLOBAL\b|"
                        r"\bBNP PARIBAS (?:ARBITRAGE|FINANCIAL MARKETS)\b|\bVANGUARD\b|\bBLACKROCK\b|\bFUND\b.*\bLTD\b|"
                        r"\bPLC\b|\bLLC\b|\bL\.?P\.?$|\bLP\b"),
    ("bank", "bank_name", r"\bBANK\b(?!.*\bSECURITIES\b)"),
    ("broker_prop", "broker_name", r"\bSECURITIES\b|\bSTOCK ?BROKING\b|\bBROKING\b|\bCAPITAL MARKETS\b|"
                                   r"\bCOMMODITIES\b|\bFINVEST\b|\bTRADING\b|\bSHARE ?BROKERS?\b|\bHUF\b(?=.*TRADING)"),
    ("corporate", "corporate_name", r"\bPRIVATE LIMITED\b|\bPVT\.? LTD\b|\bLIMITED\b|\bLTD\b|\bLLP\b|\bTRUST\b|\bHOLDINGS?\b"),
    ("individual", "individual_name", r"^[A-Z]+(?: [A-Z]+){1,3}$|\bHUF\b"),
]
_COMPILED = [(c, n, re.compile(p)) for c, n, p in RULES]


def normalise(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "").upper().replace(".", ". ").replace(",", " ")).replace(" .", ".").strip()


def classify_name(name: str) -> tuple[str, str]:
    n = normalise(name)
    for cls, rule, rx in _COMPILED:
        if rx.search(n):
            return cls, f"rule:{rule}"
    return "unknown", "rule:none"


def load_overrides(eng, path: Optional[Path] = None) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    p = Path(path or OVERRIDES_FILE)
    if p.exists():
        f = pd.read_csv(p, dtype=str).fillna("")
        for r in f.itertuples():
            out[normalise(r.name)] = (r.entity_class.strip(), "manual")
    with eng.connect() as c:
        for r in c.execute(select(db.entities).where(db.entities.c.verified_by == "manual")):
            out[normalise(r.name)] = (r.entity_class, "manual")
    return out


def classify(eng, names, overrides: Optional[dict] = None) -> pd.DataFrame:
    """DataFrame name → entity_class, method, institutional (bool)."""
    ov = load_overrides(eng) if overrides is None else overrides
    rows = []
    for n in pd.unique(pd.Series(list(names), dtype=object).dropna()):
        cls, how = ov.get(normalise(n)) or classify_name(n)
        rows.append({"name": n, "entity_class": cls, "method": how, "institutional": cls in INSTITUTIONAL})
    return pd.DataFrame(rows, columns=["name", "entity_class", "method", "institutional"])


def set_manual(eng, name: str, entity_class: str, notes: str = "") -> None:
    db.replace_rows(eng, db.entities, pd.DataFrame([{"name": normalise(name), "entity_class": entity_class,
                                                     "verified_by": "manual", "notes": notes}]),
                    [db.entities.c.name == normalise(name)])
