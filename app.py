# SPDX-License-Identifier: AGPL-3.0-only
# Anonymia Redactor — service de rédaction PDF.
# Copyright (C) 2026 Cyril Heurtebize
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, version 3.
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY. See the LICENSE file for details.
"""API du service de rédaction.

Conformité AGPL art. 13 : le endpoint /health expose en permanence le lien
vers le code source de la version en cours d'exécution (REDACTOR_SOURCE_URL),
relayé également dans le pied de page du frontend Anonymia.
"""
from __future__ import annotations

import base64
import json
import logging
import os

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from redactor import apply_plan, restore_plan

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "info").upper(),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("redactor.api")

SOURCE_URL = os.getenv(
    "REDACTOR_SOURCE_URL",
    "https://example.invalid/ANONYMIA-REDACTOR-SOURCES-A-CONFIGURER",
)
VERSION = os.getenv("REDACTOR_VERSION", "1.0.0")
MAX_BYTES = int(os.getenv("MAX_FILE_MB", "10")) * 1024 * 1024

app = FastAPI(title="Anonymia Redactor (AGPL-3.0-only)", version=VERSION)


@app.get("/health")
def health() -> dict:
    return {
        "status": "up",
        "service": "anonymia-redactor",
        "version": VERSION,
        "license": "AGPL-3.0-only",
        "source": SOURCE_URL,
    }


@app.post("/redact")
async def redact(
    file: UploadFile = File(...),
    plan: str = Form(...),
) -> dict:
    data = await file.read()
    if not data.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail={"code": "NOT_A_PDF"})
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail={"code": "FILE_TOO_LARGE"})

    try:
        parsed = json.loads(plan)
        assert isinstance(parsed, dict) and isinstance(parsed.get("pages"), list)
    except (json.JSONDecodeError, AssertionError):
        raise HTTPException(status_code=400, detail={"code": "INVALID_PLAN"})

    try:
        pdf_bytes, not_found, applied = await run_in_threadpool(apply_plan, data, parsed)
    except Exception:
        logger.exception("Rédaction en échec")
        raise HTTPException(status_code=500, detail={"code": "REDACTION_FAILED"})

    logger.info("Rédaction : %d appliquée(s), %d non localisée(s)", applied, len(not_found))
    return {
        "pdf_base64": base64.b64encode(pdf_bytes).decode(),
        "not_found": not_found,
        "applied": applied,
    }


@app.post("/restore")
async def restore(
    file: UploadFile = File(...),
    restorations: str = Form(...),
) -> dict:
    """Réécrit les valeurs d'origine à la place des pseudonymes d'un PDF.

    Symétrique de /redact, et soumis à la même frontière de licence : c'est
    ici, dans le service AGPL, que vit tout le code PyMuPDF. L'anonymizer
    propriétaire fournit la liste {label -> valeur} (issue du dico déchiffré
    par la clé de l'utilisateur) et ne touche jamais à `fitz`.

    Le service ne conserve rien : ni le PDF, ni les valeurs. La liste
    `restorations` transite en mémoire le temps de la requête.
    """
    data = await file.read()
    if not data.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail={"code": "NOT_A_PDF"})
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail={"code": "FILE_TOO_LARGE"})

    try:
        parsed = json.loads(restorations)
        assert isinstance(parsed, list)
    except (json.JSONDecodeError, AssertionError):
        raise HTTPException(status_code=400, detail={"code": "INVALID_RESTORATIONS"})

    try:
        pdf_bytes, replaced, warnings = await run_in_threadpool(
            restore_plan, data, parsed)
    except Exception:
        logger.exception("Restitution en échec")
        raise HTTPException(status_code=500, detail={"code": "RESTORE_FAILED"})

    # On ne journalise QUE des comptages : jamais un label, jamais une valeur.
    logger.info("Restitution : %d remplacement(s)", replaced)
    return {
        "pdf_base64": base64.b64encode(pdf_bytes).decode(),
        "replaced": replaced,
        "warnings": warnings,
    }
