    def append_result(
        self, execution_id: str, result: dict, blocked: Optional[dict] = None
    ) -> bool:
        """Atomically append one app's result via the Release API.

        Server does the append inside one UPDATE, so parallel AppMap
        writers cannot overwrite each other.
        """
        url = f"{self.api_url}/execution-states/{execution_id}/results"
        payload = {"result": result, "blocked": blocked}
        headers = self._sign_request("POST", url, payload)

        try:
            resp = httpx.post(
                url, content=json.dumps(payload), headers=headers, timeout=self.timeout
            )
            resp.raise_for_status()
            return True
        except httpx.HTTPStatusError as e:
            logger.error(
                "HTTP %s appending result for %s: %s",
                e.response.status_code, execution_id, e.response.text,
            )
            raise

    def accumulate(self) -> Dict[str, Any]:
        """Persist one app's result via atomic server-side append."""
        app = self.event.get("app", {})
        outcome = self.event.get("outcome", {})
        execution_id = self.event.get("execution_id")

        env = app.get("EnvName")
        repo = app.get("RepoName")
        name = app.get("Name")
        version = app.get("Version")
        status = outcome.get("status", "UNKNOWN")

        result_entry = {
            "Env": env, "Repo": repo, "App": name,
            "Version": version, "Status": status,
        }

        block_entry = None
        if status in ("FAILURE", "ROLLBACK"):
            block_entry = {"Env": env, "Repo": repo, "App": name}
            logger.info("Blocked %s for subsequent environments", name)

        if execution_id:
            self.release_api.append_result(execution_id, result_entry, block_entry)

        logger.info("Recorded %s/%s/%s - %s", env, repo, name, status)
        return result_entry
