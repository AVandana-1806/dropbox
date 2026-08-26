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
