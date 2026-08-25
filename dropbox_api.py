@pytest.mark.asyncio
async def test_append_execution_result(
    test_client: AsyncClient, existing_execution_state: dict
):
    """Tests appending a single result to an execution state."""
    execution_id = existing_execution_state["execution_id"]
    payload = {
        "result": {
            "Env": "qa", "Repo": "orders", "App": "payments",
            "Version": "1.4.0", "Status": "SUCCESS",
        }
    }

    response = await test_client.post(f"{BASE}/{execution_id}/results", json=payload)
    assert response.status_code == 201

    state = await test_client.get(f"{BASE}/{execution_id}")
    body = state.json()
    assert len(body["results"]) == 1
    assert body["results"][0]["App"] == "payments"
    assert body["blocked_apps"] == []


@pytest.mark.asyncio
async def test_append_multiple_results_accumulate(
    test_client: AsyncClient, existing_execution_state: dict
):
    """Tests that successive appends accumulate rather than overwrite."""
    execution_id = existing_execution_state["execution_id"]

    for app in ("payments", "checkout", "shipping"):
        payload = {
            "result": {
                "Env": "qa", "Repo": "orders", "App": app,
                "Version": "1.0.0", "Status": "SUCCESS",
            }
        }
        response = await test_client.post(
            f"{BASE}/{execution_id}/results", json=payload
        )
        assert response.status_code == 201

    state = await test_client.get(f"{BASE}/{execution_id}")
    apps = [r["App"] for r in state.json()["results"]]
    assert apps == ["payments", "checkout", "shipping"]


@pytest.mark.asyncio
async def test_append_execution_result_with_blocked(
    test_client: AsyncClient, existing_execution_state: dict
):
    """Tests appending a failed result also records the blocked app."""
    execution_id = existing_execution_state["execution_id"]
    payload = {
        "result": {
            "Env": "qa", "Repo": "orders", "App": "payments",
            "Version": "1.4.0", "Status": "FAILURE",
        },
        "blocked": {"Env": "qa", "Repo": "orders", "App": "payments"},
    }

    response = await test_client.post(f"{BASE}/{execution_id}/results", json=payload)
    assert response.status_code == 201

    body = (await test_client.get(f"{BASE}/{execution_id}")).json()
    assert len(body["results"]) == 1
    assert len(body["blocked_apps"]) == 1
    assert body["blocked_apps"][0]["App"] == "payments"


@pytest.mark.asyncio
async def test_append_execution_result_not_found(test_client: AsyncClient):
    """Tests appending to a non-existent execution state returns 404."""
    payload = {
        "result": {
            "Env": "qa", "Repo": "orders", "App": "payments",
            "Version": "1.4.0", "Status": "SUCCESS",
        }
    }

    response = await test_client.post(f"{BASE}/{uuid4()}/results", json=payload)
    assert response.status_code == 404
    assert "not found" in extract_message(response.json())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_result",
    [
        {"Env": "qa", "Repo": "orders", "App": "payments", "Version": "1.0.0"},
        {"Env": "qa", "Repo": "orders", "App": "payments", "Status": "SUCCESS"},
        {},
    ],
)
async def test_append_execution_result_invalid_payload(
    test_client: AsyncClient, existing_execution_state: dict, bad_result: dict
):
    """Tests that incomplete result payloads are rejected."""
    execution_id = existing_execution_state["execution_id"]

    response = await test_client.post(
        f"{BASE}/{execution_id}/results", json={"result": bad_result}
    )
    assert response.status_code == 422
