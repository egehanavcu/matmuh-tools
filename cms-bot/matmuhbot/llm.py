import json
import os
import time

from pydantic import BaseModel

from .config import ROOT, read_secret

SETTINGS_FILE = ROOT / "bot.json"


class LlmError(RuntimeError):
    pass


def settings() -> dict:
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    return {}


def model_name() -> str:
    name = os.environ.get("GEMINI_MODEL") or settings().get("geminiModel")
    if not name:
        raise LlmError("Gemini modeli seçilmedi: bot.json'da geminiModel ya da GEMINI_MODEL. Seçenekler için: bot.py models")
    return name


_client = None


def client():
    global _client
    if _client is None:
        from google import genai

        _client = genai.Client(api_key=read_secret("gemini.key", "GEMINI_API_KEY"))
    return _client


def list_models() -> list[str]:
    names = []
    for model in client().models.list():
        actions = getattr(model, "supported_actions", None) or []
        if "generateContent" in actions:
            names.append(model.name.removeprefix("models/"))
    return sorted(names)


def pdf_part(data: bytes):
    from google.genai import types

    return types.Part.from_bytes(data=data, mime_type="application/pdf")


class Overloaded(LlmError):
    pass


def extract(contents: list, schema: type[BaseModel], system: str, *, temperature: float = 0.0, tries: int = 4) -> tuple[BaseModel, dict]:
    fallback = os.environ.get("GEMINI_FALLBACK_MODEL") or settings().get("geminiFallbackModel")
    try:
        return _extract(contents, schema, system, model_name(), temperature, tries)
    except Overloaded:
        if not fallback or fallback == model_name():
            raise
        print(f"    {model_name()} kullanılamıyor (yoğun ya da günlük kota), {fallback} ile deneniyor")
        return _extract(contents, schema, system, fallback, temperature, tries)


def _extract(contents, schema, system, model, temperature, tries):
    from google.genai import errors, types

    config = types.GenerateContentConfig(
        system_instruction=system,
        temperature=temperature,
        response_mime_type="application/json",
        response_schema=schema,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    for attempt in range(1, tries + 1):
        try:
            response = client().models.generate_content(model=model, contents=contents, config=config)
        except errors.APIError as error:
            if getattr(error, "code", None) == 429 and "limit: 0" in str(error):
                raise LlmError(f"{model} bu anahtarın katmanında kullanılamıyor (kota 0)") from error
            if getattr(error, "code", None) == 429 and "PerDay" in str(error):
                raise Overloaded(f"{model} günlük kotası doldu") from error
            if getattr(error, "code", None) in (429, 500, 502, 503, 504) and attempt < tries:
                time.sleep(min(60, 5 * 2 ** attempt))
                continue
            if getattr(error, "code", None) == 503:
                raise Overloaded(f"{model} yoğun (503)") from error
            raise LlmError(f"Gemini hatası: {error}") from error

        candidate = (response.candidates or [None])[0]
        finish = str(getattr(candidate, "finish_reason", "") or "")
        text = response.text or ""
        meta = {"model": model, "finishReason": finish, "usage": _usage(response)}
        if not text or ("STOP" not in finish and finish):
            if attempt < tries:
                continue
            raise LlmError(f"Gemini yanıtı tamamlanmadı (finish_reason={finish})")
        try:
            parsed = response.parsed if isinstance(response.parsed, schema) else schema.model_validate_json(text)
        except ValueError as error:
            if attempt < tries:
                continue
            raise LlmError(f"Gemini yanıtı şemaya uymadı: {error}") from error
        return parsed, meta
    raise LlmError("Gemini'den yanıt alınamadı")


def _usage(response) -> dict:
    usage = getattr(response, "usage_metadata", None)
    if not usage:
        return {}
    return {
        "input": getattr(usage, "prompt_token_count", None),
        "output": getattr(usage, "candidates_token_count", None),
    }
