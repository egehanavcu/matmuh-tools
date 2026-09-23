import re
import time
from urllib.parse import quote

import requests

from .config import CMS_BASE, LOCALIZED, read_secret


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class AuthError(ApiError):
    pass


class SystemicError(RuntimeError):
    pass


class Breaker:
    def __init__(self, limit: int = 3):
        self.limit = limit
        self.last = None
        self.count = 0

    def record(self, error: Exception) -> None:
        text = str(error).split(" -> ", 1)[-1]
        text = re.sub(r'"instance":"[^"]*"', "", text)
        text = re.sub(r"[0-9a-f-]{8,}", "", text)
        signature = (getattr(error, "status", None), text[:120])
        self.count = self.count + 1 if signature == self.last else 1
        self.last = signature
        if self.count >= self.limit:
            raise SystemicError(f"aynı hata {self.count} kez üst üste, durdu: {error}")

    def ok(self) -> None:
        self.last, self.count = None, 0


class CmsClient:
    def __init__(self, base: str = CMS_BASE, write: bool = False):
        self.base = base.rstrip("/")
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "matmuhbot/0.1"
        self.key = read_secret("cms.key", "CMS_SERVICE_KEY") if write else None

    def request(self, method: str, path: str, *, auth: bool = False, json=None, params=None, tries: int = 4):
        headers = {"Authorization": f"Bearer {self.key}"} if auth and self.key else {}
        for attempt in range(1, tries + 1):
            try:
                res = self.session.request(method, f"{self.base}{path}", headers=headers, json=json, params=params, timeout=30)
            except requests.RequestException:
                if attempt == tries:
                    raise
                time.sleep(1.5 * attempt)
                continue
            if res.status_code >= 500 and attempt < tries:
                time.sleep(1.5 * attempt)
                continue
            if res.status_code in (401, 403):
                raise AuthError(res.status_code, f"{method} {path} -> {res.status_code}")
            if not res.ok:
                raise ApiError(res.status_code, f"{method} {path} -> {res.status_code} {res.text[:300]}")
            return res.json() if res.content else None

    def probe_service_key(self) -> None:
        try:
            self.request("POST", "/lecture-offerings/import", auth=True, json={"rows": []})
        except AuthError:
            raise
        except ApiError as error:
            if error.status == 400:
                return
            raise
        raise ApiError(200, "Boş import 200 döndü; beklenen 400")

    def upload_media(self, content: bytes, filename: str, content_type: str, public: bool = True) -> str:
        res = self.session.post(
            f"{self.base}/cms/media",
            headers={"Authorization": f"Bearer {self.key}"},
            files={"file": (filename, content, content_type)},
            data={"publicAccess": "true" if public else "false"},
            timeout=120,
        )
        if res.status_code in (401, 403):
            raise AuthError(res.status_code, f"POST /cms/media -> {res.status_code}")
        if not res.ok:
            raise ApiError(res.status_code, f"POST /cms/media -> {res.status_code} {res.text[:300]}")
        body = res.json()
        url = (body.get("data") or body).get("url")
        if not url:
            raise ApiError(res.status_code, f"POST /cms/media yanıtında url yok: {str(body)[:200]}")
        return url

    def schema(self, key: str) -> dict:
        return self.request("GET", f"/cms/collections/{quote(key)}/schema")

    def list_items(self, key: str, locale: str | None = None) -> list[dict]:
        out: list[dict] = []
        offset = 0
        while True:
            params = {"limit": 100, "offset": offset}
            if locale:
                params["locale"] = locale
            page = self.request("GET", f"/cms/collections/{quote(key)}", params=params) or {}
            items = page.get("items") or []
            out.extend(items)
            if not items or len(out) >= (page.get("total") or 0):
                return out
            offset += 100

    def all_items(self, key: str) -> list[dict]:
        if key not in LOCALIZED:
            return self.list_items(key)
        seen, out = set(), []
        for locale in ("tr", "en"):
            for item in self.list_items(key, locale):
                if item.get("id") not in seen:
                    seen.add(item.get("id"))
                    out.append(item)
        return out
