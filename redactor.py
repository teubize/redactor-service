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

MIN_SNIPPET_LEN = 4


def _locate(page: "fitz.Page", snippets: List[str]) -> List["fitz.Rect"]:
    """Recherche textuelle de SECOURS (quand le plan ne fournit pas de rects).

    v1.1 : la recherche par sous-chaîne n'est plus le mécanisme principal —
    elle caviardait « EUR » au milieu de « HEURTEBIZE » et détruisait le
    document. Les plans v1.1 transportent des rectangles ; ce chemin ne
    sert qu'aux plans anciens ou aux entités sans coordonnées, avec un
    minimum de longueur relevé pour limiter les dégâts.
    """
    rects: List[fitz.Rect] = []
    for raw in snippets:
        snippet = (raw or "").strip()
        if len(snippet) >= MIN_SNIPPET_LEN:
            rects.extend(page.search_for(snippet))
    return rects


def _rects_from_plan(page: "fitz.Page", raw: Any) -> List["fitz.Rect"]:
    """Convertit les rectangles du plan (x0, top, x1, bottom) en fitz.Rect.

    pdfplumber (émetteur du plan) et PyMuPDF partagent la même convention :
    origine en haut à gauche, y vers le bas, points PDF. On borne au cadre
    de la page par sécurité.
    """
    rects: List[fitz.Rect] = []
    if not isinstance(raw, list):
        return rects
    bounds = page.rect
    for spec in raw:
        try:
            rect = fitz.Rect(*[float(v) for v in spec]) & bounds
        except (TypeError, ValueError):
            continue
        if not rect.is_empty and rect.is_valid:
            rects.append(rect)
    return rects


def _scrub_metadata(doc: "fitz.Document") -> None:
    """Neutralise les métadonnées : Title/Author d'un acte réel portent
    souvent les noms des parties — une fuite invisible à l'écran."""
    doc.set_metadata({
        "title": "Document anonymisé",
        "author": "", "subject": "", "keywords": "",
        "creator": "Anonymia", "producer": "Anonymia Redactor",
    })
    try:
        doc.del_xml_metadata()
    except Exception:  # certaines versions PyMuPDF n'exposent pas l'appel
        pass


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
                rects = _rects_from_plan(page, item.get("rects"))
                if not rects:
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

        _scrub_metadata(doc)
        return doc.tobytes(garbage=3, deflate=True), not_found, applied
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Restitution (désanonymisation) d'un PDF déjà caviardé
# ---------------------------------------------------------------------------
#
# POURQUOI C'EST POSSIBLE — et pourquoi ce n'est pas contradictoire avec le
# caviardage destructif.
#
# `apply_plan` supprime physiquement le texte source du content stream, puis
# ÉCRIT le pseudonyme (PERSONNE_1) par-dessus le rectangle. Ce pseudonyme est
# du VRAI TEXTE PDF, pas une image : il est donc localisable et remplaçable.
# Ce qui est irrémédiablement perdu, c'est la VALEUR D'ORIGINE — elle ne vit
# plus que dans le dico chiffré, détenu par l'utilisateur seul.
#
# Restituer = relire le dico (côté anonymizer), puis réécrire la valeur à la
# place du pseudonyme. Deux limites, honnêtes et affichées à l'utilisateur :
#
#  1. LA PLACE. Le rectangle a la largeur du texte ORIGINAL, mais le
#     pseudonyme y a été écrit en petit (fontsize 7). « ADRESSE_2 » (9 car.)
#     peut valoir « 14 rue des Arènes, 40270 Grenade-sur-l'Adour » (44 car.).
#     On adapte donc la taille de police pour tenir dans la largeur
#     disponible, avec un plancher de lisibilité (MIN_FONTSIZE) : en dessous,
#     on laisse déborder plutôt que de rendre illisible, et on le signale.
#
#  2. LES IRRÉVERSIBLES. NIR, IBAN, téléphones sont caviardés en NOIR PLEIN,
#     sans label (`black=True`). Il n'y a aucun texte à retrouver : ces
#     zones restent noires, définitivement. C'est un choix de conception,
#     pas une limite technique.
#
# La typographie : on relève la police et la taille des caractères VOISINS
# (même ligne, hors zone restituée) pour que la valeur restituée se fonde
# dans le document au lieu de trancher.

MIN_FONTSIZE = 5.0     # en dessous, illisible : sert de garde pour la typo voisine
SEUIL_CONFORT = 8.0    # plancher de RÉDUCTION : on ne rétrécit pas en deçà
DEFAULT_FONT = "helv"


def _typo_voisine(page: "fitz.Page", rect: "fitz.Rect") -> Tuple[str, float]:
    """Police et taille des caractères entourant `rect`, sur la même ligne.

    On cherche le span de texte le plus proche horizontalement dont la
    verticale recouvre celle du rectangle. À défaut (ligne entièrement
    caviardée), on retombe sur la taille dominante de la page, puis sur
    Helvetica 10.
    """
    milieu_y = (rect.y0 + rect.y1) / 2
    candidats: List[Tuple[float, str, float]] = []
    tailles: List[float] = []
    try:
        brut = page.get_text("dict")
    except Exception:  # pragma: no cover - PDF exotique
        return DEFAULT_FONT, 10.0

    for bloc in brut.get("blocks", []):
        for ligne in bloc.get("lines", []):
            for span in ligne.get("spans", []):
                taille = float(span.get("size", 0) or 0)
                if taille > 0:
                    tailles.append(taille)
                sx0, sy0, sx1, sy1 = span.get("bbox", (0, 0, 0, 0))
                # Même ligne : la verticale du span recouvre le milieu du rect.
                if not (sy0 - 2 <= milieu_y <= sy1 + 2):
                    continue
                # Le pseudonyme lui-même est DANS le rect : on l'écarte.
                if sx0 >= rect.x0 - 1 and sx1 <= rect.x1 + 1:
                    continue
                distance = min(abs(sx0 - rect.x1), abs(rect.x0 - sx1))
                police = str(span.get("font") or DEFAULT_FONT)
                candidats.append((distance, police, taille))

    if candidats:
        candidats.sort(key=lambda c: c[0])
        _, police, taille = candidats[0]
        if taille >= MIN_FONTSIZE:
            return police, taille
    if tailles:
        # Taille dominante de la page (le corps de texte).
        tailles.sort()
        return DEFAULT_FONT, tailles[len(tailles) // 2]
    return DEFAULT_FONT, 10.0


def _police_utilisable(page: "fitz.Page", nom: str) -> str:
    """Renvoie `nom` si PyMuPDF sait l'écrire, sinon une base 14 équivalente.

    Les polices embarquées portent des noms comme « ABCDEE+Calibri-Bold » :
    inutilisables telles quelles pour insérer du texte neuf. On retombe sur
    une base 14 en conservant gras/italique, ce qui suffit à ne pas trancher.
    """
    n = (nom or "").lower()
    gras = "bold" in n or "black" in n or "heavy" in n
    italique = "italic" in n or "oblique" in n
    serif = any(k in n for k in ("times", "serif", "georgia", "garamond", "roman"))
    if serif:
        return "tibo" if (gras and italique) else "tibi" if italique else "tibo" if gras else "times-roman"
    if gras and italique:
        return "hebi"
    if gras:
        return "hebo"
    if italique:
        return "heit"
    return "helv"


def restore_plan(
    pdf_bytes: bytes,
    restorations: List[Dict[str, Any]],
    ) -> Tuple[bytes, int, List[str]]:
    """Réécrit les valeurs d'origine à la place des pseudonymes.

    `restorations` : [{"label": "PERSONNE_1", "value": "Jean DUPONT"}, ...]
    Retourne (pdf, nombre_de_remplacements, avertissements).

    Le texte du pseudonyme est retiré (add_redact_annot sans texte) puis la
    valeur est insérée dans le même rectangle, à la typographie des voisins.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    warnings: List[str] = []
    remplaces = 0
    deborde = 0

    try:
        # Les plus longs d'abord : PERSONNE_10 avant PERSONNE_1, sinon
        # « PERSONNE_10 » deviendrait « Jean DUPONT0 » (même piège que
        # utils/deanonymize.py côté texte).
        items = sorted(
            [r for r in restorations if r.get("label") and r.get("value")],
            key=lambda r: -len(str(r["label"])),
        )
        if not items:
            return doc.tobytes(garbage=3, deflate=True), 0, [
                "Aucune valeur restituable : ce document ne contient que des "
                "zones masquées de façon irréversible (NIR, IBAN, téléphones)."
            ]

        for page in doc:
            # 1) Localiser, relever la typo, retirer les pseudonymes.
            a_ecrire: List[Tuple[fitz.Rect, str, str, float]] = []
            for item in items:
                label, valeur = str(item["label"]), str(item["value"])
                for rect in page.search_for(label, quads=False):
                    police, taille = _typo_voisine(page, rect)
                    a_ecrire.append((rect, valeur, police, taille))
                    page.add_redact_annot(rect, fill=None)
            if not a_ecrire:
                continue
            # `fill=None` : on efface le texte SANS peindre de fond, pour ne
            # pas masquer le document autour.
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

            # 2) Réécrire les valeurs à la place libérée.
            for rect, valeur, police_brute, taille in a_ecrire:
                police = _police_utilisable(page, police_brute)
                largeur = rect.width
                # Adapter la taille pour tenir dans le rectangle d'origine.
                #
                # Arbitrage assumé : entre « illisible mais dans les clous »
                # et « lisible mais qui déborde », on choisit LISIBLE. Un
                # document restitué sert à être relu par un professionnel ;
                # du 4 pt ne se relit pas. On réduit donc jusqu'à
                # SEUIL_CONFORT (8 pt) au maximum ; en dessous, on garde
                # 8 pt et on laisse déborder — en le signalant.
                taille_ok = taille
                try:
                    besoin = fitz.get_text_length(valeur, fontname=police, fontsize=taille)
                except Exception:
                    police = DEFAULT_FONT
                    besoin = fitz.get_text_length(valeur, fontname=police, fontsize=taille)
                if besoin > largeur and besoin > 0:
                    ideale = taille * largeur / besoin
                    taille_ok = max(SEUIL_CONFORT, min(taille, ideale))
                    if ideale < SEUIL_CONFORT:
                        deborde += 1
                # Ligne de base : bas du rectangle, remonté du jambage.
                point = fitz.Point(rect.x0, rect.y1 - taille_ok * 0.25)
                try:
                    page.insert_text(point, valeur, fontname=police,
                                     fontsize=taille_ok, color=(0, 0, 0))
                except Exception:
                    page.insert_text(point, valeur, fontname=DEFAULT_FONT,
                                     fontsize=taille_ok, color=(0, 0, 0))
                remplaces += 1

        if deborde:
            warnings.append(
                f"{deborde} valeur(s) plus longue(s) que l'espace libéré : le "
                "texte peut chevaucher le contenu voisin. Un pseudonyme court "
                "(ADRESSE_2) remplacé par une adresse complète ne tient pas "
                "toujours dans le rectangle d'origine — relisez le document."
            )
        warnings.append(
            "Les NIR, IBAN et téléphones sont masqués de façon irréversible : "
            "ils restent noirs, aucune restitution n'est possible."
        )
        return doc.tobytes(garbage=3, deflate=True), remplaces, warnings
    finally:
        doc.close()
