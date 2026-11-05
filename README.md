# Statuses Connect returns while workers start, rebalance or hand over leadership; the same call succeeds later.
RETRY_STATUSES = frozenset({409, 500, 503})
# 400 while the validating worker hasn't loaded the plugin yet (e.g. just after a rollout).
PLUGIN_NOT_LOADED = "Failed to find any class that implements Connector"


    def put_config(self, connector_config: dict):
        """Create or update the connector, retrying while workers start or rebalance."""
        deadline = time.monotonic() + self.config.ready_timeout_seconds
        while True:
            status, body = self.client.request("PUT", self.path + "/config", connector_config)
            if status in (200, 201):
                LOGGER.info("Connector '%s' %s.", self.config.connector_name,
                            "created" if status == 201 else "updated")
                return
            message = (body or {}).get("message", "")
            retryable = status in RETRY_STATUSES or (status == 400 and PLUGIN_NOT_LOADED in message)
            if not retryable or time.monotonic() > deadline:
                raise DeployError(f"PUT config failed with {status}: {body}")
            LOGGER.info("Connect not ready for this config (%s): %s; retrying", status, message)
            time.sleep(self.config.poll_seconds)
