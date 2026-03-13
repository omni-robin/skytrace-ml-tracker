"""Thin NGVA Data Bus adapter (placeholder).

NGVA (STANAG 4754) is a vehicle architecture standard; deployments vary.
This adapter is intentionally minimal and transport-agnostic.

It takes the Kafka detection envelope (event_type=detections_computed) and forwards it
via one of a few simple transports that technicians can wire into their NGVA Data Bus
integration point.

Configure with env vars:
- NGVA_ADAPTER_KIND: udp_json | http_json | stdout   (default: stdout)

udp_json:
- NGVA_UDP_HOST (default 127.0.0.1)
- NGVA_UDP_PORT (default 5555)

http_json:
- NGVA_HTTP_URL (default http://127.0.0.1:8081/ngva/detections)
- NGVA_HTTP_TIMEOUT_S (default 2.0)

Common:
- NGVA_SCHEMA_VERSION (default 1)
- NGVA_NODE_ID (default "edgehub")

Note: This is *not* a full NGVA profile implementation. It is a thin bridge so you can
swap transports/protocols later without changing the detector pipeline.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.request


def _env(name: str, default: str) -> str:
    v = os.getenv(name)
    return v if v is not None and v != "" else default


def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def to_ngva_message(detection_event: dict) -> dict:
    """Map internal event envelope to a stable, NGVA-facing JSON payload.

    Keep this mapping narrow and versioned.
    """

    artifact = detection_event.get("artifact") or {}
    det = detection_event.get("detections") or {}

    return {
        "ngva_schema_version": int(_env("NGVA_SCHEMA_VERSION", "1")),
        "node_id": _env("NGVA_NODE_ID", "edgehub"),
        "message_type": "rf_detections",
        "computed_at": detection_event.get("computed_at"),
        "source_event": detection_event.get("source_event"),
        "artifact": {
            "base_path": artifact.get("base_path"),
            "meta_file_path": artifact.get("meta_file_path"),
            "data_file_path": artifact.get("data_file_path"),
        },
        "detector": detection_event.get("detector"),
        "rf": {
            "controllers": det.get("controllers", []),
            "detections": det.get("detections", []),
        },
    }


def send(detection_event: dict) -> None:
    kind = _env("NGVA_ADAPTER_KIND", "stdout").lower()
    msg = to_ngva_message(detection_event)

    if kind == "stdout":
        print("NGVA_ADAPTER:", json.dumps(msg))
        return

    if kind == "udp_json":
        host = _env("NGVA_UDP_HOST", "127.0.0.1")
        port = int(_env("NGVA_UDP_PORT", "5555"))
        data = json.dumps(msg).encode("utf-8")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(data, (host, port))
        finally:
            sock.close()
        return

    if kind == "http_json":
        url = _env("NGVA_HTTP_URL", "http://127.0.0.1:8081/ngva/detections")
        timeout = _env_float("NGVA_HTTP_TIMEOUT_S", 2.0)
        data = json.dumps(msg).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # read to completion to avoid connection reuse issues
            resp.read()
        return

    raise ValueError(f"Unknown NGVA_ADAPTER_KIND: {kind}")
