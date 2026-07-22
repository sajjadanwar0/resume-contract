#!/usr/bin/env python3
"""
151_p11_openai_sdk_deterministic.py  (E-M1, first row)  -- TEMPLATE.
LLM-free harness twin of the live OpenAI Agents SDK cell (probe 121): the
paper flags that this cell currently has no deterministic probe.

Design: a SCRIPTED custom Model (the SDK's model-provider interface) whose
responses are fixed: turn 1 -> tool_call(charge_once), turn 2 -> final
text. No network, no sampling. Then:
  C1 EO across session restore: run turn 1 (tool fires, counter 1), persist
     SQLiteSession; NEW Runner + NEW scripted model restoring the same
     session for turn 2 -> counter must stay 1 (the live cell's claim,
     now deterministic).
  C2 CO / stray input: re-deliver turn-2 input byte-identically -> counter
     unchanged; record disposition (silent-inert vs loud).
  C3 CV: tamper the session store (invalid item JSON) -> load: loud error
     or silent acceptance? (AutoGen row's contrast point.)
  C4 crash-mid-turn PC: SIGKILL between tool effect and session write
     (probe-133 barrier pattern) -> fresh process restore -> does the tool
     re-fire?
Pin the SDK version; document the mechanism quote for the D/U/X rule
(silence => U under the revised rule -- never default to X).
VERIFY-API sites: custom Model registration, SQLiteSession class name/path,
Runner construction -- confirm against the pinned SDK before trusting.
"""
raise SystemExit("TEMPLATE: fill VERIFY-API sites against the pinned openai-agents release, then remove this line.")