# Anonymia Redactor

Micro-service de **rédaction (caviardage) de PDF** : il reçoit un PDF et une
liste d'instructions (extraits de texte à localiser, libellé de substitution
ou caviardage noir) et renvoie le PDF avec le texte **réellement supprimé du
content stream** (redaction annotations PyMuPDF, pas un rectangle décoratif).

Ce service ne connaît **rien** aux données personnelles : pas de NER, pas de
Presidio, pas de logique métier. Il localise des chaînes et les rédige.
C'est volontaire — voir « Licence » ci-dessous.

## API

- `GET /health` → `{"status": "up", "license": "AGPL-3.0-only", "source": "<url>"}`
- `POST /redact` (multipart) :
  - `file` : le PDF
  - `plan` : JSON —
    ```json
    {
      "pages": [
        { "page": 0,
          "redactions": [
            { "ref": "r1", "snippets": ["Jean", "DUPONT,"], "label": "PERSONNE_1", "black": false }
          ] }
      ],
      "fontsize": 7
    }
    ```
  - Réponse : `{"pdf_base64": "...", "not_found": ["ref…"], "applied": 3}`

`snippets` contient les lignes de l'extrait (une entité coupée par un retour
à la ligne est fournie ligne par ligne). `black: true` force un caviardage
noir sans libellé. `not_found` liste les refs non localisées graphiquement.

## Lancer seul

```bash
docker build -t anonymia-redactor .
docker run -p 5001:5001 -e REDACTOR_SOURCE_URL=https://github.com/vous/anonymia-redactor anonymia-redactor
```
