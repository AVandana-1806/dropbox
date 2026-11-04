    @classmethod
    def from_args(cls, argv: list[str] | None = None) -> "DeployConfig":
        """Build the config from command-line arguments."""
        parser = argparse.ArgumentParser(description="Roll out an image and apply the connector config.")
        parser.add_argument("--stack-name", required=True)
        parser.add_argument("--image-uri", required=True)
        parser.add_argument("--rest-url", required=True, type=lambda url: url.rstrip("/"))
        parser.add_argument("--connector-name", required=True)
        parser.add_argument("--properties", required=True, dest="properties_path")
        parser.add_argument("--ready-timeout", type=int, default=300, dest="ready_timeout_seconds")
        parser.add_argument("--running-timeout", type=int, default=300, dest="running_timeout_seconds")
        parser.add_argument("--poll-seconds", type=int, default=10)
        return cls(**vars(parser.parse_args(argv)))
