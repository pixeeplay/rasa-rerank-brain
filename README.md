# rasa-rerank-brain

Aiguilleur de tri pour le moteur de recherche RASA. Parle le protocole `/rerank`
attendu par le dashboard (`scraper/src/dashboard/rerank_local.py`) et fait
classer les candidats par un cerveau **Ollama privé**.

## Chaîne de repli (solution 3)

`Mac mini` (allumé) → `MacBook Pro` (secours) → **503** → le dashboard retombe
sur son classement fusionné (aucune IA, aucune donnée qui sort).

## Réglages

| Variable | Défaut | Rôle |
|---|---|---|
| `BRAINS` | `mac-mini\|http://100.94.82.104:11434\|gemma4:latest,macbook-pro\|http://100.86.151.96:11434\|gemma4:latest` | chaîne `nom\|url\|modèle`, essayés dans l'ordre |
| `OVH_FALLBACK` | `0` | `1` ajoute OVH en dernier ressort — **fait sortir titres+attributs vers un tiers** |
| `BRAIN_TIMEOUT` | `20` | secondes par cerveau |
| `PORT` | `8077` | port d'écoute |

## Routes

- `GET /health` — état de chaque cerveau
- `POST /rerank` — `{query, candidates:[{idx,title,attrs,description}]}` → `{scores:[{idx,p_yes}], via}`

## Brancher le dashboard

```
RASA_RERANK_BACKEND=local
RASA_RERANK_LOCAL_URL=http://<hôte-interne>:8077
RASA_RERANK_LOCAL_STRICT=1     # repli = fusionné, jamais d'egress
```
