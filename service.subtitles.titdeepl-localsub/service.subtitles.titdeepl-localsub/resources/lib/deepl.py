# -*- coding: utf-8 -*-
import json

try:
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen
except ImportError:  # pragma: no cover
    from urllib2 import HTTPError, URLError, Request, urlopen


class DeepLError(Exception):
    pass


class DeepLTranslator(object):
    MAX_TEXTS_PER_REQUEST = 50
    MAX_REQUEST_BYTES = 128 * 1024
    REQUEST_SAFETY_MARGIN = 8 * 1024

    def __init__(
        self,
        auth_key,
        base_url,
        timeout=30,
        preserve_formatting=True,
        model_type="prefer_quality_optimized",
    ):
        self.auth_key = (auth_key or "").strip()
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self.preserve_formatting = preserve_formatting
        self.model_type = model_type

        if not self.auth_key:
            raise DeepLError("DeepL API key is empty")
        if not self.base_url.startswith("https://"):
            raise DeepLError("DeepL API base URL must start with https://")

    def translate_texts(self, texts, source_lang=None, target_lang="UK"):
        cleaned = [text for text in texts if text is not None]
        if not cleaned:
            return []

        results = []
        batch = []
        for text in cleaned:
            candidate = batch + [text]
            if batch and (
                len(candidate) > self.MAX_TEXTS_PER_REQUEST
                or self._payload_size(candidate, source_lang, target_lang)
                > (self.MAX_REQUEST_BYTES - self.REQUEST_SAFETY_MARGIN)
            ):
                results.extend(self._translate_batch(batch, source_lang, target_lang))
                batch = [text]
            else:
                batch = candidate

        if batch:
            if self._payload_size(batch, source_lang, target_lang) > self.MAX_REQUEST_BYTES:
                raise DeepLError("A subtitle segment is too large for one DeepL request")
            results.extend(self._translate_batch(batch, source_lang, target_lang))

        return results

    def _payload_size(self, texts, source_lang, target_lang):
        payload = self._build_payload(texts, source_lang, target_lang)
        return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _build_payload(self, texts, source_lang, target_lang):
        payload = {
            "text": texts,
            "target_lang": (target_lang or "UK").upper(),
            "split_sentences": "0",
            "preserve_formatting": bool(self.preserve_formatting),
        }
        if self.model_type:
            payload["model_type"] = self.model_type
        if source_lang:
            payload["source_lang"] = source_lang.upper()
        return payload

    def _translate_batch(self, texts, source_lang, target_lang):
        payload = self._build_payload(texts, source_lang, target_lang)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = "%s/v2/translate" % self.base_url
        request = Request(url=url, data=body)
        request.add_header("Authorization", "DeepL-Auth-Key %s" % self.auth_key)
        request.add_header("Content-Type", "application/json")
        request.add_header("User-Agent", "KodiDeepLSubtitles/0.2.0")

        try:
            response = urlopen(request, timeout=self.timeout)
            data = response.read().decode("utf-8")
        except HTTPError as exc:
            raise DeepLError(self._format_http_error(exc))
        except URLError as exc:
            raise DeepLError("Could not reach DeepL API: %s" % exc)

        try:
            parsed = json.loads(data)
        except ValueError:
            raise DeepLError("DeepL API returned invalid JSON")

        translations = parsed.get("translations") or []
        result = [item.get("text", "") for item in translations]
        if len(result) != len(texts):
            raise DeepLError("DeepL API returned an unexpected translation count")
        return result

    def _format_http_error(self, exc):
        code = getattr(exc, "code", "unknown")
        if code == 456:
            return "DeepL quota exceeded. Your free 500,000 character limit has been reached. Resets next month."
        if code == 403:
            return "DeepL API key is invalid or disabled. Check your API key in settings."
        message = "DeepL API error %s" % code
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            return message

        try:
            parsed = json.loads(body)
        except ValueError:
            return "%s: %s" % (message, body.strip() or exc.reason)

        detail = parsed.get("message") or parsed.get("detail")
        if detail:
            return "%s: %s" % (message, detail)
        return message
