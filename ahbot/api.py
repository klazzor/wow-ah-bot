"""Blizzard WoW Forever API istemcisi (ince sarmalayıcı)."""
import time

import requests

from ahbot.auth import get_token

HOST = "https://eu.api.blizzard.com"
NS_DYNAMIC = "dynamic-forever-eu"
NS_STATIC = "static-forever-eu"
LOCALE = "en_US"

_session = requests.Session()


def get(path: str, namespace: str = NS_DYNAMIC, timeout: int = 120, **params):
    """GET isteği; (status_code, json|None, headers) döndürür."""
    params = {"namespace": namespace, "locale": LOCALE, **params}
    for attempt in range(4):
        try:
            r = _session.get(
                HOST + path,
                params=params,
                headers={"Authorization": f"Bearer {get_token()}"},
                timeout=timeout,
            )
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
            continue
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            time.sleep(2 ** attempt)   # hız sınırı / geçici sunucu hatası → bekle, tekrar dene
            continue
        break
    try:
        data = r.json()
    except ValueError:
        data = None
    return r.status_code, data, r.headers
