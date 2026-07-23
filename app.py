#!/usr/bin/env python3
"""RASA rerank-brain — aiguilleur de tri (Spec 133 compatible).

Parle le protocole `/rerank` attendu par le dashboard RASA
(`scraper/src/dashboard/rerank_local.py`) et, en interne, fait classer les
candidats par un cerveau Ollama. Chaine de repli configurable :

    BRAINS = "nom|url|modele , nom|url|modele , ..."

essayes dans l'ordre. Si tous echouent -> HTTP 503 : le dashboard retombe
alors sur son classement fusionne (RASA_RERANK_LOCAL_STRICT=1), aucune donnee
ne sort. OVH n'est ajoute a la chaine QUE si OVH_FALLBACK=1 (desactive par
defaut : l'ajouter fait sortir titres+attributs vers un tiers).

stdlib only — 0 dependance, image minuscule.
"""
import json
import os
import re
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8077"))
TIMEOUT = float(os.environ.get("BRAIN_TIMEOUT", "20"))

_DEFAULT_BRAINS = (
    "mac-mini|http://100.94.82.104:11434|gemma4:latest,"
    "macbook-pro|http://100.86.151.96:11434|gemma4:latest"
)


def _load_brains():
    raw = os.environ.get("BRAINS", _DEFAULT_BRAINS)
    out = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split("|")]
        if len(parts) >= 3 and parts[1]:
            out.append({"name": parts[0], "url": parts[1].rstrip("/"), "model": parts[2],
                        "kind": "ollama"})
    if os.environ.get("OVH_FALLBACK", "0").strip() in ("1", "true", "yes"):
        base = os.environ.get("RASA_OVH_AI_BASE",
                              "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1").rstrip("/")
        key = os.environ.get("RASA_OVH_AI_KEY", "")
        model = os.environ.get("RASA_OVH_AI_MODEL", "gpt-oss-120b")
        if key:
            out.append({"name": "ovh", "url": base, "model": model, "kind": "openai", "key": key})
    return out


BRAINS = _load_brains()


def _doc_text(c):
    attrs = c.get("attrs") or {}
    bits = " . ".join(str(v) for v in attrs.values() if v not in (None, ""))
    title = (c.get("title") or "").strip()
    base = f"{title} — {bits}" if bits else title
    desc = (c.get("description") or "").strip()
    return (f"{base}\n{desc}" if desc else base) or "(sans titre)"


def _prompt(query, cands):
    lines = []
    for i, c in enumerate(cands, 1):
        txt = _doc_text(c).replace("\n", " ")[:280]
        lines.append(f"{i}. {txt}")
    docs = "\n".join(lines)
    return (
        "Tu es un expert en art indien et himalayen. On te donne une requete et une liste "
        "numerotee de lots d'encheres. Classe TOUS les numeros du plus pertinent au moins "
        "pertinent pour la requete.\n\n"
        f"Requete : {query}\n\nLots :\n{docs}\n\n"
        'Reponds UNIQUEMENT en JSON : {"order":[<numeros du meilleur au pire>]}. '
        "Inclus chaque numero exactement une fois."
    )


def _post(url, body, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _ask_ollama(brain, prompt):
    d = _post(f"{brain['url']}/api/chat",
              {"model": brain["model"], "stream": False, "format": "json",
               "options": {"temperature": 0},
               "messages": [{"role": "user", "content": prompt}]},
              {}, TIMEOUT)
    return d.get("message", {}).get("content", "")


def _ask_openai(brain, prompt):
    d = _post(f"{brain['url']}/chat/completions",
              {"model": brain["model"], "temperature": 0, "max_tokens": 800,
               "messages": [{"role": "user", "content": prompt}]},
              {"Authorization": f"Bearer {brain['key']}"}, TIMEOUT)
    m = d["choices"][0]["message"]
    return m.get("content") or m.get("reasoning_content") or ""


def _parse_order(txt, k):
    m = re.search(r"\[[\d,\s]*\]", txt)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return None
    seen, order = set(), []
    for x in arr:
        try:
            n = int(x)
        except Exception:
            continue
        if 1 <= n <= k and n not in seen:
            seen.add(n)
            order.append(n)
    for n in range(1, k + 1):
        if n not in seen:
            order.append(n)
    return order


def _alive(b, timeout=1.5):
    """Ping rapide : evite qu'un cerveau mort (Mac eteint) brule tout le delai
    avant le repli. Un cerveau injoignable est ecarte en ~1,5 s."""
    try:
        if b["kind"] == "ollama":
            urllib.request.urlopen(f"{b['url']}/api/tags", timeout=timeout).read()
        else:
            req = urllib.request.Request(f"{b['url']}/models",
                                         headers={"Authorization": f"Bearer {b['key']}"})
            urllib.request.urlopen(req, timeout=timeout).read()
        return True
    except Exception:
        return False


def rerank(query, cands):
    k = len(cands)
    if k == 0:
        return [], "vide"
    prompt = _prompt(query, cands)
    last = "aucun cerveau configure"
    for b in BRAINS:
        if not _alive(b):
            last = f"{b['name']}: hors ligne"
            continue
        try:
            txt = _ask_openai(b, prompt) if b["kind"] == "openai" else _ask_ollama(b, prompt)
            order = _parse_order(txt, k)
            if not order:
                last = f"{b['name']}: reponse illisible"
                continue
            scores = []
            for rank, num in enumerate(order):
                idx = cands[num - 1].get("idx", num)
                scores.append({"idx": int(idx), "p_yes": (k - rank) / k})
            return scores, b["name"]
        except Exception as e:
            last = f"{b['name']}: {type(e).__name__}"
            continue
    return None, last


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj, ctype="application/json"):
        body = (json.dumps(obj) if ctype == "application/json" else obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/health":
            st = []
            for b in BRAINS:
                try:
                    if b["kind"] == "ollama":
                        urllib.request.urlopen(f"{b['url']}/api/tags", timeout=6).read()
                    else:
                        req = urllib.request.Request(f"{b['url']}/models",
                                                     headers={"Authorization": f"Bearer {b['key']}"})
                        urllib.request.urlopen(req, timeout=6).read()
                    st.append({"name": b["name"], "model": b["model"], "up": True})
                except Exception as e:
                    st.append({"name": b["name"], "model": b["model"], "up": False,
                               "err": type(e).__name__})
            self._send(200, {"ok": any(s["up"] for s in st), "brains": st})
        else:
            n = ", ".join(f"{b['name']} ({b['model']})" for b in BRAINS) or "aucun"
            self._send(200, f"rasa-rerank-brain — chaine : {n}", "text/plain; charset=utf-8")

    def do_POST(self):
        if self.path.rstrip("/") != "/rerank":
            self._send(404, {"error": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._send(400, {"error": "bad json"})
            return
        scores, info = rerank(body.get("query", ""), body.get("candidates", []) or [])
        if scores is None:
            self._send(503, {"error": "aucun cerveau disponible", "detail": info})
            return
        self._send(200, {"scores": scores, "via": info})


def _selftest():
    for b in BRAINS:
        try:
            if b["kind"] == "ollama":
                urllib.request.urlopen(f"{b['url']}/api/tags", timeout=8).read()
            else:
                req = urllib.request.Request(f"{b['url']}/models",
                                             headers={"Authorization": f"Bearer {b['key']}"})
                urllib.request.urlopen(req, timeout=8).read()
            print(f"[selftest] {b['name']} ({b['url']}) -> JOIGNABLE", flush=True)
        except Exception as e:
            print(f"[selftest] {b['name']} ({b['url']}) -> INJOIGNABLE ({type(e).__name__})", flush=True)


if __name__ == "__main__":
    print(f"rasa-rerank-brain sur :{PORT} — chaine : "
          + ", ".join(f"{b['name']}={b['model']}" for b in BRAINS), flush=True)
    _selftest()
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
