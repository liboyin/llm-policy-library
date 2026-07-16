"""The three agents of the policy-question pipeline.

Each module owns one step and nothing else: `planner` decomposes the question,
`retrieval` searches the index, `response` writes the grounded answer. None of
them knows about the others; `llm_policy_library.orchestrator` wires them into a
Microsoft Agent Framework workflow and is the only module that does.

Every agent takes its Azure clients as arguments rather than building them, so
each is exercised in tests with a stub in place of the network.
"""
