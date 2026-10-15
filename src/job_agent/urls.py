"""Canonicalize job posting URLs before storing or matching tracker entries."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_TRACKING_QUERY_KEYS = {
    "fbclid", "gclid", "dclid", "mc_cid", "mc_eid", "gh_src", "trk",
    "trackingid", "ref_src", "_ga",
}


def normalize_job_url(value: str) -> str:
    """Normalize common URL variants while preserving job-identifying query parameters."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("A job URL is required.")

    parsed = urlsplit(value.strip())
    scheme = parsed.scheme.lower()
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Use a valid http:// or https:// job URL.") from exc

    if scheme not in {"http", "https"} or not hostname:
        raise ValueError("Use a complete http:// or https:// job URL.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Use a complete http:// or https:// job URL without embedded credentials.")

    try:
        host = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("Use a valid hostname in the job URL.") from exc
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None
    netloc = host if port is None else f"{host}:{port}"

    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"

    query_pairs = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_key = key.casefold()
        if normalized_key.startswith("utm_") or normalized_key in _TRACKING_QUERY_KEYS:
            continue
        # Preserve meaningful parameter order and duplicate keys; some URLs are signed
        # and some applications assign meaning to repeated query parameters.
        query_pairs.append((key, item))

    # Fragments are browser-only and do not identify a distinct server-side job.
    return urlunsplit((scheme, netloc, path, urlencode(query_pairs, doseq=True), ""))
