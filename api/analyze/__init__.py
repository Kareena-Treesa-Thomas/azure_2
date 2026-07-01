import json
import logging
import os
import time
import urllib.error
import urllib.request

import azure.functions as func

API_VERSION = "2023-04-01"
TIMEOUT_SECONDS = 20
MAX_CHARS = 5000


def main(req: func.HttpRequest) -> func.HttpResponse:
    endpoint = os.environ.get("LANGUAGE_ENDPOINT", "").rstrip("/")
    key = os.environ.get("LANGUAGE_KEY", "")
    if not endpoint or not key:
        return _json_response(
            {"error": "Server is missing LANGUAGE_ENDPOINT / LANGUAGE_KEY environment variables."},
            500,
        )

    try:
        body = req.get_json()
    except ValueError:
        body = {}

    text = (body.get("text") or "").strip()
    if not text:
        return _json_response({"error": 'Request body must include non-empty "text".'}, 400)
    if len(text) > MAX_CHARS:
        return _json_response({"error": f"Text must be {MAX_CHARS} characters or fewer."}, 400)

    try:
        sentiment_doc  = _call_language(endpoint, key, "SentimentAnalysis",    text)["results"]["documents"][0]
        keyphrase_doc  = _call_language(endpoint, key, "KeyPhraseExtraction",  text)["results"]["documents"][0]
        entity_doc     = _call_language(endpoint, key, "EntityRecognition",    text)["results"]["documents"][0]
        language_doc   = _call_language(endpoint, key, "LanguageDetection",    text)["results"]["documents"][0]
        pii_doc        = _call_language(endpoint, key, "PiiEntityRecognition", text)["results"]["documents"][0]
    except Exception as exc:
        logging.exception("Azure AI Language call failed")
        # TEMPORARY: show the real error so we can debug. Remove this before final submission.
        return _json_response(
            {"error": "Azure AI Language request failed.", "debug_detail": str(exc)}, 502
        )

    # Abstractive summarization uses a separate async jobs API
    summary_text = ""
    try:
        summary_text = _call_summarization(endpoint, key, text)
    except Exception:
        logging.exception("Summarization call failed — continuing without it")
        summary_text = "(Summarization unavailable)"

    detected_lang = language_doc.get("detectedLanguage", {})

    result = {
        # --- original 3 features ---
        "sentiment": sentiment_doc["sentiment"],
        "confidenceScores": sentiment_doc["confidenceScores"],
        "keyPhrases": keyphrase_doc.get("keyPhrases", []),
        "entities": [
            {"text": e["text"], "category": e["category"]}
            for e in entity_doc.get("entities", [])
        ],
        # --- 3 new features ---
        "language": detected_lang.get("name", "Unknown"),
        "languageIso": detected_lang.get("iso6391Name", "??"),
        "languageConfidence": detected_lang.get("confidenceScore", 0),
        "redactedText": pii_doc.get("redactedText", text),
        "piiEntities": [
            {"text": e["text"], "category": e["category"]}
            for e in pii_doc.get("entities", [])
        ],
        "summary": summary_text,
    }
    return _json_response(result, 200)


def _call_language(endpoint: str, key: str, kind: str, text: str) -> dict:
    url = f"{endpoint}/language/:analyze-text?api-version={API_VERSION}"
    payload = {
        "kind": kind,
        "parameters": {"modelVersion": "latest"},
        "analysisInput": {"documents": [{"id": "1", "language": "en", "text": text}]},
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Ocp-Apim-Subscription-Key": key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(f"{kind} failed: {exc.code} {detail}") from exc


def _call_summarization(endpoint: str, key: str, text: str) -> str:
    """Submit an abstractive summarization job and poll until done."""
    submit_url = f"{endpoint}/language/analyze-text/jobs?api-version={API_VERSION}"
    payload = {
        "displayName": "summarize",
        "analysisInput": {
            "documents": [{"id": "1", "language": "en", "text": text}]
        },
        "tasks": [{
            "kind": "AbstractiveSummarization",
            "taskName": "summary",
            "parameters": {"sentenceCount": 3}
        }]
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        submit_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Ocp-Apim-Subscription-Key": key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            op_location = resp.headers.get("operation-location")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(f"Summarization submit failed: {exc.code} {detail}") from exc

    if not op_location:
        raise RuntimeError("No operation-location header returned from summarization submit.")

    # Poll every 2 seconds, up to 20 attempts (40s)
    for _ in range(20):
        time.sleep(2)
        poll_req = urllib.request.Request(
            op_location,
            headers={"Ocp-Apim-Subscription-Key": key},
            method="GET",
        )
        with urllib.request.urlopen(poll_req, timeout=TIMEOUT_SECONDS) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        status = result.get("status")
        if status == "succeeded":
            summaries = result["tasks"]["items"][0]["results"]["documents"][0]["summaries"]
            return " ".join(s["text"] for s in summaries)
        if status == "failed":
            raise RuntimeError("Summarization job failed on Azure side.")

    raise RuntimeError("Summarization timed out.")


def _json_response(payload: dict, status: int) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(payload),
        status_code=status,
        mimetype="application/json",
    )