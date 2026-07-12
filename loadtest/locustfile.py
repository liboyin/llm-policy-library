"""Load test for `POST /query`, driven by the golden set.

Run it against a uvicorn already serving the app (see `samples/loadtest_results.md`
for the exact command the committed run used):

    locust -f loadtest/locustfile.py --headless -u 10 -r 2 --run-time 6m \
        --host http://localhost:8000 --csv <fresh-dir>/loadtest

This module is deliberately only the locust driver. The query mix and the rules
that decide whether a response counts as a failure live in `loadtest/checks.py`,
which does not import locust — importing locust monkey-patches `ssl` via gevent
and cannot be done inside a pytest process, so that split is what lets the pass/
fail logic be unit-tested at all.
"""

import random

from locust import HttpUser, between, task

from loadtest.checks import (
    ON_TOPIC_NAME,
    ON_TOPIC_QUERIES,
    OUT_OF_DOMAIN_NAME,
    OUT_OF_DOMAIN_QUERIES,
    on_topic_defect,
    out_of_domain_defect,
)


class PolicyQueryUser(HttpUser):
    """An analyst asking the policy library one question at a time."""

    # A real user reads the answer before asking again. The mean (3.5 s) is the
    # think time the capacity extrapolation must use; assuming a rounder number
    # would silently change the arrival rate the whole model is built on.
    wait_time = between(2, 5)

    @task(3)
    def ask_on_topic_query(self) -> None:
        """Ask a policy question, which runs the full three-agent pipeline."""
        query = random.choice(ON_TOPIC_QUERIES)
        with self.client.post(
            "/query", json={"query": query}, name=ON_TOPIC_NAME, catch_response=True
        ) as response:
            if response.status_code != 200:
                # Left to locust, which fails a non-2xx itself and names the status.
                return
            defect = on_topic_defect(response.json(), query)
            if defect:
                response.failure(defect)

    @task(1)
    def ask_out_of_domain_query(self) -> None:
        """Ask a question the catalog cannot answer, which must return the safe fallback."""
        query = random.choice(OUT_OF_DOMAIN_QUERIES)
        with self.client.post(
            "/query", json={"query": query}, name=OUT_OF_DOMAIN_NAME, catch_response=True
        ) as response:
            if response.status_code != 200:
                return
            defect = out_of_domain_defect(response.json(), query)
            if defect:
                response.failure(defect)
