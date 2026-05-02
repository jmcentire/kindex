# Security Audit Report

**Generated:** 2026-03-05T22:08:42.368962

## Summary

- Critical: 3
- High: 1
- Medium: 0
- Low: 4
- Info: 0
- **Total: 8**

## CRITICAL (3)

- **_extract_session_text_quick** (src/kindex/daemon.py:443) [NOT COVERED]
  - Pattern: variable: role
  - Complexity: 11
  - Suggestion: Ensure branch on 'role' is tested with both truthy and falsy values
- **_extract_session_text** (src/kindex/ingest.py:263) [NOT COVERED]
  - Pattern: variable: role
  - Complexity: 11
  - Suggestion: Ensure branch on 'role' is tested with both truthy and falsy values
- **_linear_query** (src/kindex/adapters/linear.py:19) [NOT COVERED]
  - Pattern: variable: api_key
  - Complexity: 5
  - Suggestion: Ensure branch on 'api_key' is tested with both truthy and falsy values

## HIGH (1)

- **get_client** (src/kindex/llm.py:32) [NOT COVERED]
  - Pattern: variable: api_key
  - Complexity: 4
  - Suggestion: Ensure branch on 'api_key' is tested with both truthy and falsy values

## LOW (4)

- **send** (src/kindex/notify.py:164) [covered]
  - Pattern: variable: password
  - Complexity: 7
  - Suggestion: Ensure branch on 'password' is tested with both truthy and falsy values
- **send** (src/kindex/notify.py:223) [covered]
  - Pattern: variable: token
  - Complexity: 6
  - Suggestion: Ensure branch on 'token' is tested with both truthy and falsy values
- **send** (src/kindex/notify.py:223) [covered]
  - Pattern: variable: token
  - Complexity: 6
  - Suggestion: Ensure branch on 'token' is tested with both truthy and falsy values
- **fts_search** (src/kindex/store.py:548) [covered]
  - Pattern: variable: tokens
  - Complexity: 3
  - Suggestion: Ensure branch on 'tokens' is tested with both truthy and falsy values
