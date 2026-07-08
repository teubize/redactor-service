# SPDX-License-Identifier: AGPL-3.0-only
# Anonymia Redactor — rédaction (caviardage) de PDF par redaction annotations.
# Copyright (C) 2026 Cyril Heurtebize
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, version 3.
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY. See the LICENSE file for details.
"""Cœur du service : applique un plan de rédaction sur un PDF.

Ce module est délibérément « bête » : il ne sait pas ce qu'est une donnée
personnelle. Il reçoit des extraits de texte à localiser (page par page) et
les remplace par un libellé ou un caviardage noir, en utilisant les
redaction annotations de PyMuPDF — le texte est retiré du content stream
du PDF, pas simplement recouvert.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import fitz  # PyMuPDF (AGPL v3)

MIN_SNIPPET_LEN = 2


def _locate(page: "fitz.Page", snippets: List[str]) -> List["fitz.Rect"]:
    """Localise chaque ligne d'un extrait sur la page (entités multi-lignes)."""
    rects: List[fitz.Rect] = []
    for raw in snippets:
        snippet = (raw or "").strip()
        if len(snippet) >= MIN_SNIPPET_LEN:
            rects.extend(page.search_for(snippet))
    return rects


def apply_plan(
    pdf_bytes: bytes,
    plan: Dict[str, Any],
) -> Tuple[bytes, List[str], int]:
    """Applique le plan de rédaction.

    plan = {
      "pages": [{"page": int, "redactions": [
          {"ref": str, "snippets": [str], "label": str|None, "black": bool}
      ]}],
      "fontsize": int (optionnel, défaut 7)
    }

    Retourne (pdf_bytes, refs_non_localisées, nb_rédactions_appliquées).
    """
    fontsize = int(plan.get("fontsize", 7))
    not_found: List[str] = []
    applied = 0

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for page_spec in plan.get("pages", []):
            index = int(page_spec.get("page", -1))
            if not 0 <= index < len(doc):
                for item in page_spec.get("redactions", []):
                    not_found.append(str(item.get("ref", "")))
                continue
            page = doc[index]

            for item in page_spec.get("redactions", []):
                rects = _locate(page, item.get("snippets", []))
                if not rects:
                    not_found.append(str(item.get("ref", "")))
                    continue
                applied += 1
                black = bool(item.get("black", False))
                label = item.get("label") or ""
                for rect in rects:
                    if black or not label:
                        page.add_redact_annot(rect, fill=(0, 0, 0))
                    else:
                        page.add_redact_annot(
                            rect,
                            text=label,
                            fill=(1, 1, 1),
                            fontsize=fontsize,
                            text_color=(0.3, 0.3, 0.3),
                        )
            # Retire réellement le texte du content stream et nettoie les
            # images chevauchantes.
            page.apply_redactions()

        return doc.tobytes(garbage=3, deflate=True), not_found, applied
    finally:
        doc.close()
