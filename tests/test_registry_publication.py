"""Acceptance contract for bounded official MCP registry confirmation.

No registry network requests run: public urllib and time seams are controlled.
The verifier must distinguish publication confirmation from transport failure
or registry propagation lag.
"""

import importlib.util
import io
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check-mcp-registry.py"
SERVER = "io.github.wandercom/kindex"
ENDPOINT = (
    "https://registry.modelcontextprotocol.io/v0.1/servers/"
    "io.github.wandercom%2Fkindex/versions/latest"
)


def receipt(version="0.48.0", *, name=SERVER, latest=True):
    return {"server": {"name": name, "version": version},
            "_meta": {"io.modelcontextprotocol.registry/official": {"isLatest": latest}}}


@pytest.fixture
def verifier(transport):
    spec = importlib.util.spec_from_file_location("registry_publication_verifier", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def transport(monkeypatch):
    import time
    import urllib.request

    calls = []
    delays = []
    outcomes = []

    class Response(io.BytesIO):
        status = 200

        def getcode(self):
            return self.status

    def urlopen(request, *args, **kwargs):
        url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
        calls.append((url, args, kwargs))
        assert outcomes, "Verifier exceeded its finite transport fixture"
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        body = outcome if isinstance(outcome, bytes) else json.dumps(outcome).encode("utf-8")
        return Response(body)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(time, "sleep", delays.append)
    return outcomes, calls, delays


@pytest.mark.parametrize("tag", ["v0.48.0", "0.48.0"])
def test_exact_official_latest_receipt_confirms_release(verifier, transport, tag):
    outcomes, calls, delays = transport
    outcomes.append(receipt())
    assert verifier.verify(tag, timeout=12.5) is None
    assert len(calls) == 1
    assert calls[0][0] == ENDPOINT
    assert calls[0][2]["timeout"] == 12.5
    assert delays == []


@pytest.mark.parametrize("failure", [
    TimeoutError("synthetic registry timeout"),
    URLError("synthetic unavailable registry"),
    HTTPError(ENDPOINT, 404, "propagation delay", None, None),
    HTTPError(ENDPOINT, 429, "rate limit", None, None),
    HTTPError(ENDPOINT, 500, "server failure", None, None),
    HTTPError(ENDPOINT, 502, "gateway failure", None, None),
    HTTPError(ENDPOINT, 503, "service unavailable", None, None),
    HTTPError(ENDPOINT, 504, "gateway timeout", None, None),
    receipt("0.47.0"), {}, {"server": {}},
    {"server": {"name": SERVER, "version": "0.48.0"}},
])
def test_transient_transport_and_propagation_failures_retry_then_succeed(
        verifier, transport, failure):
    outcomes, calls, delays = transport
    outcomes.extend([failure, receipt()])
    assert verifier.verify("v0.48.0", attempts=3, delay=0.25, timeout=7.0) is None
    assert len(calls) == 2
    assert all(url == ENDPOINT and options["timeout"] == 7.0
               for url, _args, options in calls)
    assert delays == [0.25]


@pytest.mark.parametrize("wrong", [
    receipt(name="io.github.jmcentire/kindex"),
    receipt(name="io.github.wandercom/kindex-extra"),
    receipt(name="other/io.github.wandercom/kindex"),
    receipt("0.47.0"),
    receipt("v0.48.0"),
    receipt(latest=False),
    receipt(latest="true"),
    receipt(latest=1),
    receipt(latest=None),
    {"server": {"name": SERVER, "version": "0.48.0"},
     "_meta": {"unofficial": {"isLatest": True}}},
    {"servers": [receipt()]},
    {},
])
def test_foreign_stale_or_unconfirmed_data_never_counts_as_success(verifier, transport, wrong):
    outcomes, calls, delays = transport
    outcomes.extend([wrong, wrong, wrong])
    with pytest.raises(RuntimeError):
        verifier.verify("v0.48.0", attempts=3, delay=0.5, timeout=4.0)
    assert len(calls) == 3
    assert delays == [0.5, 0.5]


def test_default_retry_budget_is_exactly_eight_attempts(verifier, transport):
    outcomes, calls, delays = transport
    outcomes.extend(URLError("still unavailable") for _ in range(8))
    with pytest.raises(RuntimeError):
        verifier.verify("v0.48.0")
    assert len(calls) == 8
    assert delays == [5.0] * 7
    assert all(options["timeout"] == 15.0 for _url, _args, options in calls)


def test_success_on_final_attempt_is_accepted_without_extra_sleep(verifier, transport):
    outcomes, calls, delays = transport
    outcomes.extend([receipt("0.47.0"), TimeoutError("late"), receipt()])
    assert verifier.verify("v0.48.0", attempts=3, delay=0.125) is None
    assert len(calls) == 3
    assert delays == [0.125, 0.125]


@pytest.mark.parametrize("code", [400, 401, 403, 405, 410, 422])
def test_non_retryable_client_error_fails_immediately(verifier, transport, code):
    outcomes, calls, delays = transport
    outcomes.extend([HTTPError(ENDPOINT, code, "permanent request error", None, None), receipt()])
    with pytest.raises(RuntimeError):
        verifier.verify("v0.48.0", attempts=8, delay=5.0)
    assert len(calls) == 1
    assert delays == []


@pytest.mark.parametrize("options", [
    {"attempts": 0}, {"attempts": -1}, {"attempts": 1.5},
    {"delay": -1.0}, {"delay": float("inf")}, {"delay": float("nan")},
    {"timeout": 0.0}, {"timeout": -1.0}, {"timeout": float("inf")},
    {"timeout": float("nan")},
])
def test_invalid_retry_bounds_fail_before_network_or_sleep(verifier, transport, options):
    _outcomes, calls, delays = transport
    with pytest.raises((ValueError, RuntimeError)):
        verifier.verify("v0.48.0", **options)
    assert calls == []
    assert delays == []


def test_one_attempt_never_sleeps_after_failure(verifier, transport):
    outcomes, calls, delays = transport
    outcomes.append(TimeoutError("single attempt"))
    with pytest.raises(RuntimeError):
        verifier.verify("v0.48.0", attempts=1, delay=0.0, timeout=1.0)
    assert len(calls) == 1
    assert delays == []


@pytest.mark.parametrize("tag", ["v0.48.0", "0.48.0"])
def test_cli_dispatches_positional_tag_to_verifier_without_network(tmp_path, tag):
    # Run the actual __main__ entry point with an in-process fake standard-library
    # transport. This tests argparse/exit behavior without any live registry I/O.
    runner = (
        "import io,json,runpy,sys,urllib.request; "
        f"payload={receipt()!r}; "
        "urllib.request.urlopen=lambda *a,**k: io.BytesIO(json.dumps(payload).encode()); "
        f"sys.argv=[{str(SCRIPT)!r},{tag!r}]; "
        f"runpy.run_path({str(SCRIPT)!r},run_name='__main__')"
    )
    result = subprocess.run([sys.executable, "-c", runner], capture_output=True,
                            text=True, cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr


def test_cli_non_retryable_failure_has_nonzero_exit(tmp_path):
    runner = (
        "import runpy,sys,urllib.request; from urllib.error import HTTPError; "
        f"error=HTTPError({ENDPOINT!r},403,'forbidden',None,None); "
        "urllib.request.urlopen=lambda *a,**k: (_ for _ in ()).throw(error); "
        f"sys.argv=[{str(SCRIPT)!r},'v0.48.0']; "
        f"runpy.run_path({str(SCRIPT)!r},run_name='__main__')"
    )
    result = subprocess.run([sys.executable, "-c", runner], capture_output=True,
                            text=True, cwd=tmp_path, timeout=10)
    assert result.returncode != 0


def test_registry_publication_workflow_uses_shared_verifier_for_release_tag():
    workflow = (ROOT / ".github/workflows/publish-mcp.yml").read_text()
    assert re.search(
        r'python(?:3)?\s+\.registry-verifier/scripts/check-mcp-registry\.py\s+["\x27]?\$\{?RELEASE_TAG\}?',
        workflow,
    ), "Registry confirmation must use the workflow-pinned helper with RELEASE_TAG"


def test_registry_backfill_keeps_release_and_verifier_checkouts_separate():
    workflow = yaml.safe_load((ROOT / ".github/workflows/publish-mcp.yml").read_text())
    confirmation_jobs = [
        job for job in workflow["jobs"].values()
        if any(".registry-verifier/scripts/check-mcp-registry.py" in step.get("run", "")
               for step in job.get("steps", []))
    ]
    assert confirmation_jobs, "No registry confirmation job uses the pinned verifier"
    for job in confirmation_jobs:
        steps = job["steps"]
        checkout_steps = [
            (index, step.get("with", {}))
            for index, step in enumerate(steps)
            if step.get("uses", "").startswith("actions/checkout@")
        ]
        release_checkouts = [
            (index, config) for index, config in checkout_steps
            if config.get("path", ".") == "."
        ]
        assert len(release_checkouts) == 1
        assert release_checkouts[0][1].get("ref") == "${{ inputs.tag }}"
        verifier_checkouts = [
            (index, config) for index, config in checkout_steps
            if config.get("path") == ".registry-verifier"
        ]
        assert len(verifier_checkouts) == 1
        assert verifier_checkouts[0][1].get("ref") == "${{ github.workflow_sha }}"
        confirmation_index = next(
            index for index, step in enumerate(steps)
            if ".registry-verifier/scripts/check-mcp-registry.py" in step.get("run", "")
        )
        assert release_checkouts[0][0] < confirmation_index
        assert verifier_checkouts[0][0] < confirmation_index
