# AI Engineer Assessment

Language: Python

Platform: Azure AI (Azure OpenAI, Azure AI Search, MS Foundry, Microsoft Agent Framework)

please use your personal azure subscription.

Our team builds internal AI systems that support policy interpretation and compliance
workflows in a regulated environment. These systems must provide accurate, grounded
responses, support multi-step reasoning, and be deterministic, secure, and production ready.

## Task Overview

Build a multi-agent AI system that can answer user questions about enterprise security policies
using retrieval, reasoning, and orchestration.

## Dataset

Use a publicly available cybersecurity or policy datasets such as NIST Cybersecurity Framework
(CSF) (https://huggingface.co/datasets/AYI-NEDJIMI/nist-csf-en). Ingest at least 500 records into
your solution. Each record should include a title, description, and category. You may preprocess
data as needed.

## Use Case

The system should support queries such as:

- What controls apply to API security?
- How should sensitive data be protected in cloud systems?
- Summarise requirements for access control
- What policies relate to logging and monitoring?

## Core Requirements

1. Retrieval-Augmented Generation

Create an index in Azure AI Search and ingest at least 500 records. Implement semantic search
and retrieve the top 3–5 results relevant to a query.

2. Multi-Agent System

Using the Microsoft Agent Framework (Python), implement a multi-agent system including:

- Planner Agent responsible for decomposing user queries into structured steps.
- Retrieval Agent responsible for querying Azure AI Search and returning structured results.
- Response Agent responsible for generating the final grounded response.

Agents must communicate through structured data and demonstrate clear orchestration.

3. Grounding and Determinism

Responses must be based strictly on retrieved data. Use deterministic configuration and ensure
the system does not hallucinate. If no relevant results are found, return a safe fallback response.

4. Evaluation

Provide at least 3–5 test queries and demonstrate retrieval results and final responses. Include a
simple evaluation of relevance and grounding quality.

5. Production Readiness

Demonstrate modular code structure, structured logging of inputs and outputs, error handling
for missing results and failures, and configuration through environment variables.

6. Architecture Documentation

Provide a 1–2 page architecture document that includes system design, agent interaction flow,
security considerations, scalability considerations, and governance controls.

Deliverables

- GitHub repository
- Source code
- README with setup and execution instructions
- Sample execution outputs
- Architecture document

## Evaluation Criteria

System design and architecture (30%), agent framework usage (25%), engineering quality (20%),
AI output quality (15%), and production-readiness considerations (10%).