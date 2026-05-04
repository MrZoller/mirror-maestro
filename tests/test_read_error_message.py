"""
Tests for the JS frontend helper ``readErrorMessage`` in ``app/static/js/app.js``.

That helper centralizes the logic that turns a non-OK fetch ``Response`` into a
human-readable error string for the backup/restore flows. It must handle:

  - JSON error bodies (FastAPI's standard ``{"detail": ...}`` shape, both
    string and object form).
  - Non-JSON error bodies (HTML pages from nginx, plain text, empty bodies)
    without throwing "Unexpected token '<'" — that bug is the whole reason
    this helper exists.
  - 502/504 responses, where we append a hint about proxy timeouts because
    the backup endpoint can take longer than the default 60s nginx timeout.

Since the project has no dedicated JS test runner, these tests exec a small
Node script that loads the helper from ``app.js`` (via ``vm``) and invokes
it against fake ``Response`` objects.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


APP_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "app.js"


def _node_available() -> bool:
    return shutil.which("node") is not None


def _run_helper(case: dict) -> str:
    """Invoke the JS helper in Node with the given fake Response and return its result string."""
    script = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');

        const src = fs.readFileSync({json.dumps(str(APP_JS))}, 'utf8');
        // Pull just the readErrorMessage function out of app.js. We anchor on
        // the function signature comment to keep the slice resilient to other
        // code being added before/after it.
        const startMarker = 'async function readErrorMessage(';
        const startIdx = src.indexOf(startMarker);
        if (startIdx === -1) {{
            console.error('Could not locate readErrorMessage in app.js');
            process.exit(2);
        }}
        // Walk braces to find the matching close.
        let depth = 0;
        let i = startIdx;
        let firstBrace = -1;
        for (; i < src.length; i++) {{
            const ch = src[i];
            if (ch === '{{') {{
                if (firstBrace === -1) firstBrace = i;
                depth++;
            }} else if (ch === '}}') {{
                depth--;
                if (depth === 0) {{ i++; break; }}
            }}
        }}
        const fnSrc = src.slice(startIdx, i);

        const ctx = {{}};
        vm.createContext(ctx);
        vm.runInContext(fnSrc + '\\nthis.readErrorMessage = readErrorMessage;', ctx);

        const caseInput = {json.dumps(case)};
        const fakeResponse = {{
            status: caseInput.status,
            statusText: caseInput.statusText || '',
            text: async () => caseInput.body,
        }};

        (async () => {{
            try {{
                const result = await ctx.readErrorMessage(fakeResponse, caseInput.fallback);
                process.stdout.write(result);
            }} catch (e) {{
                console.error('Helper threw:', e && e.message);
                process.exit(3);
            }}
        }})();
        """
    )

    proc = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"node helper exited {proc.returncode}\nstdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
        )
    return proc.stdout


pytestmark = pytest.mark.skipif(not _node_available(), reason="node is required for JS helper tests")


def test_read_error_message_json_string_detail():
    """Standard FastAPI ``{"detail": "..."}`` body is surfaced as the message."""
    out = _run_helper({
        "status": 400,
        "statusText": "Bad Request",
        "body": json.dumps({"detail": "Invalid backup file"}),
        "fallback": "Failed to create backup",
    })
    assert "HTTP 400" in out
    assert "Invalid backup file" in out


def test_read_error_message_json_object_detail():
    """Object-shaped detail (e.g. fork-network error format) extracts ``message``."""
    out = _run_helper({
        "status": 400,
        "statusText": "Bad Request",
        "body": json.dumps({"detail": {"message": "Backup is invalid", "reason": "x"}}),
        "fallback": "Failed",
    })
    assert "Backup is invalid" in out


def test_read_error_message_html_body_does_not_throw():
    """
    Regression: an HTML body (nginx 504 page) used to make the caller throw
    'Unexpected token <, "<html>..." is not valid JSON'. The helper must
    instead extract a readable snippet.
    """
    html = (
        "<html>\n<head><title>504 Gateway Time-out</title></head>\n"
        "<body><center><h1>504 Gateway Time-out</h1></center>"
        "<hr><center>nginx</center></body>\n</html>\n"
    )
    out = _run_helper({
        "status": 504,
        "statusText": "Gateway Time-out",
        "body": html,
        "fallback": "Failed to create backup",
    })
    # No raw HTML angle brackets in the surfaced message.
    assert "<html>" not in out
    assert "<h1>" not in out
    # The status line is included.
    assert "HTTP 504" in out
    # The textual content of the HTML is preserved.
    assert "504 Gateway Time-out" in out
    # 504 specifically gets a proxy timeout hint.
    assert "proxy timeout" in out.lower()


def test_read_error_message_502_adds_proxy_timeout_hint():
    out = _run_helper({
        "status": 502,
        "statusText": "Bad Gateway",
        "body": "<html>oops</html>",
        "fallback": "Failed",
    })
    assert "HTTP 502" in out
    assert "proxy timeout" in out.lower()


def test_read_error_message_empty_body_falls_back_to_status():
    out = _run_helper({
        "status": 500,
        "statusText": "Internal Server Error",
        "body": "",
        "fallback": "Failed to create backup",
    })
    assert "HTTP 500" in out
    assert "Failed to create backup" in out


def test_read_error_message_truncates_long_html():
    long_text = "x" * 1000
    out = _run_helper({
        "status": 500,
        "statusText": "",
        "body": f"<html><body>{long_text}</body></html>",
        "fallback": "Failed",
    })
    # Should be truncated with an ellipsis.
    assert "…" in out
    # And shouldn't contain the entire 1000-character payload.
    assert "x" * 500 not in out
