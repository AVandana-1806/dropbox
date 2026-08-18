@pytest.mark.asyncio
async def test_create_environment_duplicate_rank(
    test_client: AsyncClient, existing_environment
):
    """
    Tests conflict when creating an environment with an already used rank.
    """
    payload = {
        "name": fake.company(),
        "description": "Duplicate rank",
        "is_active": True,
        "rank": existing_environment["rank"],
    }

    response = await test_client.post("/environments/", json=payload)
    assert response.status_code == 409

    error = response.json()
    assert "error" in error
    assert (
        error["error"]["message"]
        == f"Environment with rank '{payload['rank']}' already exists"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("rank", [0, -1, "abc", None])
async def test_create_environment_invalid_rank(
    test_client: AsyncClient, environment_payload, rank
):
    """
    Tests that invalid rank values are rejected.
    """
    environment_payload["rank"] = rank

    response = await test_client.post("/environments/", json=environment_payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_environment_missing_rank(
    test_client: AsyncClient, environment_payload
):
    """
    Tests that rank is required on creation.
    """
    del environment_payload["rank"]

    response = await test_client.post("/environments/", json=environment_payload)
    assert response.status_code == 422




@pytest.mark.asyncio
async def test_update_environment_duplicate_rank(
    test_client: AsyncClient, existing_environment, environment_payload
):
    """
    Tests conflict when updating an environment to an already used rank.
    """
    other = await test_client.post("/environments/", json=environment_payload)
    assert other.status_code == 201
    other_environment = other.json()

    response = await test_client.patch(
        f"/environments/{existing_environment['id']}",
        json={"rank": other_environment["rank"]},
    )
    assert response.status_code == 409

    error = response.json()
    assert "already exists" in error["error"]["message"]


@pytest.mark.asyncio
async def test_update_environment_rank_only(
    test_client: AsyncClient, existing_environment
):
    """
    Tests updating only the rank of an environment.
    """
    response = await test_client.patch(
        f"/environments/{existing_environment['id']}", json={"rank": 8888}
    )
    assert response.status_code == 200
    assert response.json()["rank"] == 8888


@pytest.mark.asyncio
async def test_update_environment_same_rank_allowed(
    test_client: AsyncClient, existing_environment
):
    """
    Tests that updating an environment with its own rank is not a conflict.
    """
    response = await test_client.patch(
        f"/environments/{existing_environment['id']}",
        json={"rank": existing_environment["rank"]},
    )
    assert response.status_code == 200



@pytest.mark.asyncio
async def test_recreate_environment_after_delete(
    test_client: AsyncClient, existing_environment, environment_payload
):
    """
    Tests that a soft-deleted environment's name and rank can be reused.
    """
    await test_client.delete(f"/environments/{existing_environment['id']}")

    environment_payload["name"] = existing_environment["name"]
    environment_payload["rank"] = existing_environment["rank"]

    response = await test_client.post("/environments/", json=environment_payload)
    assert response.status_code == 201
