"""Blizzard OAuth (client_credentials). Gizli değerler asla yazdırılmaz."""
import os
import time

import requests
from dotenv import load_dotenv

TOKEN_URL = "https://oauth.battle.net/token"

_cache = {"token": None, "exp": 0.0}


def get_token() -> str:
    if _cache["token"] and time.time() < _cache["exp"] - 60:
        return _cache["token"]
    load_dotenv()
    cid = os.getenv("BLIZZARD_CLIENT_ID")
    secret = os.getenv("BLIZZARD_CLIENT_SECRET")
    if not cid or not secret:
        raise RuntimeError(".env içinde BLIZZARD_CLIENT_ID / BLIZZARD_CLIENT_SECRET eksik")
    r = requests.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(cid, secret),
        timeout=30,
    )
    if r.status_code != 200:
        # Yanıt gövdesi gizli bilgi içermez ama yine de kısaltılmış göster
        raise RuntimeError(f"Token alınamadı: HTTP {r.status_code} {r.text[:200]}")
    j = r.json()
    _cache["token"] = j["access_token"]
    _cache["exp"] = time.time() + j.get("expires_in", 0)
    return _cache["token"]


if __name__ == "__main__":
    load_dotenv()
    print("BLIZZARD_CLIENT_ID mevcut:", bool(os.getenv("BLIZZARD_CLIENT_ID")))
    print("BLIZZARD_CLIENT_SECRET mevcut:", bool(os.getenv("BLIZZARD_CLIENT_SECRET")))
    tok = get_token()
    print("Token alındı. Uzunluk:", len(tok), "| geçerlilik (sn):", int(_cache["exp"] - time.time()))
