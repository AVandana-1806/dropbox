@pytest.mark.asyncio
async def test_append_execution_result_calls_repo():
    """Test appending a result delegates to the repository."""
    fake_db = object()
    execution_id = "test-exec-id"
    payload = ExecutionResultAppend(
        result=DeploymentResult(
            Env="qa", Repo="orders", App="payments",
            Version="1.4.0", Status="SUCCESS",
        )
    )

    with patch(
        "app.routes.execution_state.ExecutionStateRepository.append_result",
        new_callable=AsyncMock,
    ) as mock_append:
        result = await append_execution_result(execution_id, payload, db=fake_db)

    mock_append.assert_awaited_once_with(
        fake_db, execution_id, payload.result.model_dump(), None
    )
    assert result == {"appended": True}


@pytest.mark.asyncio
async def test_append_execution_result_with_blocked():
    """Test appending a result that also blocks the app."""
    fake_db = object()
    execution_id = "test-exec-id"
    payload = ExecutionResultAppend(
        result=DeploymentResult(
            Env="qa", Repo="orders", App="payments",
            Version="1.4.0", Status="FAILURE",
        ),
        blocked=BlockedApp(Env="qa", Repo="orders", App="payments"),
    )

    with patch(
        "app.routes.execution_state.ExecutionStateRepository.append_result",
        new_callable=AsyncMock,
    ) as mock_append:
        await append_execution_result(execution_id, payload, db=fake_db)

    mock_append.assert_awaited_once_with(
        fake_db, execution_id, payload.result.model_dump(), payload.blocked.model_dump()
    )


@pytest.mark.asyncio
async def test_append_execution_result_not_found_raises():
    """Test appending to a non-existent execution state raises NotFoundException."""
    fake_db = object()
    payload = ExecutionResultAppend(
        result=DeploymentResult(
            Env="qa", Repo="orders", App="payments",
            Version="1.4.0", Status="SUCCESS",
        )
    )

    with patch(
        "app.routes.execution_state.ExecutionStateRepository.append_result",
        new_callable=AsyncMock,
    ) as mock_append:
        mock_append.side_effect = NotFoundException(detail="Execution state not found")

        with pytest.raises(NotFoundException) as exc_info:
            await append_execution_result("missing-id", payload, db=fake_db)

    assert "not found" in str(exc_info.value).lower()
