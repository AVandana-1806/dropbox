    def wait_until_plugin_loaded(self, connector_class: str):
        """Wait until the worker lists the connector class, so the config passes validation."""
        deadline = time.monotonic() + self.config.ready_timeout_seconds
        while True:
            status, body = self.client.request("GET", "/connector-plugins")
            if status == 200 and any(p.get("class") == connector_class for p in body or []):
                LOGGER.info("Plugin %s is loaded", connector_class)
                return
            LOGGER.info("Plugin %s not loaded yet (%s); waiting", connector_class, status)
            if time.monotonic() > deadline:
                raise DeployError(f"plugin {connector_class} never appeared in /connector-plugins")
            time.sleep(self.config.poll_seconds)
